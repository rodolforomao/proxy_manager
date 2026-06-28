from __future__ import annotations

import json
import re
import socket
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

from proxy_manager.countries import country_label
from proxy_manager.local_proxy import is_port_open
from proxy_manager.models import LOCAL_PORT, ProxyScheme, ProxySettings
from proxy_manager.proxy_health import country_code_for, flag_emoji
from proxy_manager.tor_country import ensure_tor_for_country

ProxySource = str  # custom | free | paid | tor

SOURCE_LABELS = {
    "custom": "Personalizado",
    "free": "Gratuito (lista pública)",
    "paid": "Pago (assinatura)",
    "tor": "Tor",
    "direct": "Direto (rápido)",
}

AUTO_PROXY_MODE_LABELS = {
    "fast": "Rápido — proxy internacional (sem Tor)",
    "tor": "Tor (mais lento, mais anônimo)",
    "auto": "Auto: rápido → Tor",
}

AUTO_PROXY_MODE_ICONS = {
    "fast": "⚡",
    "tor": "🧅",
}

DEFAULT_TOR_PORT = 9050

_EXIT_COUNTRY_URL = "http://ip-api.com/json/?fields=status,countryCode,country"


def upstream_exit_country(settings: ProxySettings, *, timeout: float = 8.0) -> str:
    proxy_url = settings_upstream_proxy_url(settings)
    try:
        result = subprocess.run(
            [
                "curl",
                "-fsSL",
                "--max-time",
                str(max(1, int(timeout))),
                "-x",
                proxy_url,
                _EXIT_COUNTRY_URL,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        if result.returncode != 0:
            return ""
        data = json.loads(result.stdout)
        if data.get("status") == "success":
            return str(data.get("countryCode", "")).upper()
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return ""


def _matches_target_country(settings: ProxySettings, trial: ProxySettings) -> bool:
    target = getattr(settings, "target_country", "").strip().upper()
    if not target:
        return True
    return upstream_exit_country(trial, timeout=8) == target

FREE_PROXY_URLS = [
    ("http", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt"),
    ("socks5", "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks5.txt"),
    ("http", "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt"),
    ("http", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"),
    ("socks5", "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt"),
]

_LINE_RE = re.compile(
    r"^(?P<host>\d{1,3}(?:\.\d{1,3}){3})\s*:\s*(?P<port>\d{1,5})$"
)
_IP4_RE = re.compile(r"^\d{1,3}(?:\.\d{1,3}){3}$")

_CURL_TEST_URLS = (
    "https://api.ipify.org",
    "https://icanhazip.com",
    "http://api.ipify.org",
    "http://icanhazip.com",
    "http://ifconfig.me/ip",
)


def candidate_proxy_url(candidate: ProxyCandidate) -> str:
    if candidate.scheme == "socks5":
        return f"socks5h://{candidate.host}:{candidate.port}"
    return f"http://{candidate.host}:{candidate.port}"


def settings_upstream_proxy_url(settings: ProxySettings) -> str:
    host = settings.upstream_host.strip()
    port = settings.upstream_port
    if settings.scheme == "socks5":
        return f"socks5h://{host}:{port}"
    auth = ""
    if settings.username:
        from urllib.parse import quote

        user = quote(settings.username, safe="")
        if settings.password:
            auth = f"{user}:{quote(settings.password, safe='')}@"
        else:
            auth = f"{user}@"
    return f"http://{auth}{host}:{port}"


def curl_via_proxy(proxy_url: str, *, timeout: float = 8.0) -> str | None:
    for url in _CURL_TEST_URLS:
        try:
            result = subprocess.run(
                [
                    "curl",
                    "-fsSL",
                    "--max-time",
                    str(max(1, int(timeout))),
                    "-x",
                    proxy_url,
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=timeout + 2,
            )
            if result.returncode != 0:
                continue
            ip = result.stdout.strip()
            if _IP4_RE.match(ip):
                return ip
        except (OSError, subprocess.TimeoutExpired):
            continue
    return None


def verify_upstream_settings(settings: ProxySettings, *, timeout: float = 8.0) -> bool:
    host = settings.upstream_host.strip()
    if not host:
        return False
    return curl_via_proxy(settings_upstream_proxy_url(settings), timeout=timeout) is not None


def verify_local_proxy_chain(*, timeout: float = 12.0) -> bool:
    proxy_url = f"http://127.0.0.1:{LOCAL_PORT}"
    for url in ("https://api.ipify.org", "http://api.ipify.org"):
        try:
            result = subprocess.run(
                [
                    "curl",
                    "-fsSL",
                    "--max-time",
                    str(max(1, int(timeout))),
                    "-x",
                    proxy_url,
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=timeout + 2,
            )
            if result.returncode == 0 and _IP4_RE.match(result.stdout.strip()):
                return True
        except (OSError, subprocess.TimeoutExpired):
            continue
    return False


@dataclass
class ProxyCandidate:
    host: str
    port: int
    scheme: ProxyScheme = "http"
    country: str = ""
    latency_ms: float | None = None
    source: str = ""

    @property
    def key(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}"

    def label(self) -> str:
        code = country_code_for(country=self.country)
        extra = f" {flag_emoji(code)}" if code else ""
        lat = f" — {self.latency_ms:.0f}ms" if self.latency_ms is not None else ""
        return f"{self.scheme}://{self.host}:{self.port}{extra}{lat}"


@dataclass
class PaidProvider:
    id: str
    name: str
    host: str
    port: int
    scheme: ProxyScheme = "http"
    notes: str = ""
    username_hint: str = "usuário da sua conta"
    password_hint: str = "senha da sua conta"


PAID_PROVIDERS: dict[str, PaidProvider] = {
    "smartproxy": PaidProvider(
        id="smartproxy",
        name="Smartproxy",
        host="gate.smartproxy.com",
        port=10000,
        notes="Residential — use login da área do cliente.",
    ),
    "brightdata": PaidProvider(
        id="brightdata",
        name="Bright Data",
        host="brd.superproxy.io",
        port=22225,
        username_hint="brd-customer-XXX-zone-YYY",
        password_hint="senha da zona",
    ),
    "oxylabs": PaidProvider(
        id="oxylabs",
        name="Oxylabs",
        host="pr.oxylabs.io",
        port=7777,
        username_hint="customer-XXX",
    ),
    "webshare": PaidProvider(
        id="webshare",
        name="Webshare",
        host="p.webshare.io",
        port=80,
        notes="Use proxy e credenciais do painel Webshare.",
    ),
    "iproyal": PaidProvider(
        id="iproyal",
        name="IPRoyal",
        host="geo.iproyal.com",
        port=12321,
    ),
    "custom_paid": PaidProvider(
        id="custom_paid",
        name="Outro provedor pago",
        host="",
        port=8080,
        notes="Informe host e porta do seu serviço pago.",
    ),
}


def _fetch_text(url: str, timeout: float = 8.0) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "proxy-manager/1.0"},
    )
    try:
        context = ssl.create_default_context()
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            return response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, ssl.SSLError):
        try:
            result = subprocess.run(
                ["curl", "-fsSL", "--max-time", str(int(timeout)), url],
                capture_output=True,
                text=True,
                timeout=timeout + 2,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout
        except (OSError, subprocess.TimeoutExpired):
            pass
        # listas públicas — último recurso sem verificação SSL
        insecure = ssl._create_unverified_context()
        with urllib.request.urlopen(request, timeout=timeout, context=insecure) as response:
            return response.read().decode("utf-8", errors="replace")


def _parse_proxy_lines(text: str, scheme: ProxyScheme, source: str) -> list[ProxyCandidate]:
    found: list[ProxyCandidate] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _LINE_RE.match(line)
        if not match:
            continue
        host = match.group("host")
        port = int(match.group("port"))
        if port < 1 or port > 65535:
            continue
        key = f"{scheme}://{host}:{port}"
        if key in seen:
            continue
        seen.add(key)
        found.append(ProxyCandidate(host=host, port=port, scheme=scheme, source=source))
    return found


def fetch_free_proxies(max_per_source: int = 40) -> tuple[list[ProxyCandidate], str]:
    all_candidates: list[ProxyCandidate] = []
    errors: list[str] = []

    for scheme, url in FREE_PROXY_URLS:
        try:
            text = _fetch_text(url)
            source = url.split("/")[2]
            batch = _parse_proxy_lines(text, scheme, source=source)[:max_per_source]
            all_candidates.extend(batch)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            errors.append(f"{url}: {exc}")

    # dedupe preserving order
    unique: list[ProxyCandidate] = []
    seen: set[str] = set()
    for item in all_candidates:
        if item.key not in seen:
            seen.add(item.key)
            unique.append(item)

    if not unique:
        detail = "\n".join(errors[:3]) if errors else "nenhuma lista respondeu"
        return [], f"Não foi possível obter proxies gratuitos.\n{detail}"

    return unique, f"{len(unique)} proxies encontrados."


def probe_proxy(candidate: ProxyCandidate, timeout: float = 8.0) -> ProxyCandidate | None:
    start = time.monotonic()
    proxy_url = candidate_proxy_url(candidate)
    ip = None
    for url in ("https://api.ipify.org", "https://icanhazip.com"):
        try:
            result = subprocess.run(
                [
                    "curl",
                    "-fsSL",
                    "--max-time",
                    str(max(1, int(timeout))),
                    "-x",
                    proxy_url,
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=timeout + 2,
            )
            if result.returncode == 0 and _IP4_RE.match(result.stdout.strip()):
                ip = result.stdout.strip()
                break
        except (OSError, subprocess.TimeoutExpired):
            continue
    if not ip:
        return None
    latency = (time.monotonic() - start) * 1000
    return ProxyCandidate(
        host=candidate.host,
        port=candidate.port,
        scheme=candidate.scheme,
        country=candidate.country,
        latency_ms=latency,
        source=candidate.source,
    )


def test_free_proxies(
    candidates: list[ProxyCandidate],
    *,
    limit: int = 40,
    workers: int = 12,
) -> list[ProxyCandidate]:
    to_test = candidates[:limit]
    working: list[ProxyCandidate] = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(probe_proxy, c): c for c in to_test}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                working.append(result)
    working.sort(key=lambda c: c.latency_ms or 9999)
    return working


def apply_candidate(settings: ProxySettings, candidate: ProxyCandidate) -> None:
    settings.scheme = candidate.scheme
    settings.upstream_host = candidate.host
    settings.upstream_port = candidate.port
    settings.username = ""
    settings.password = ""


def apply_paid_provider(
    settings: ProxySettings,
    provider_id: str,
    username: str = "",
    password: str = "",
) -> None:
    provider = PAID_PROVIDERS.get(provider_id)
    if not provider:
        return
    settings.source = "paid"
    settings.paid_provider = provider_id
    settings.scheme = provider.scheme
    if provider.host:
        settings.upstream_host = provider.host
        settings.upstream_port = provider.port
    settings.username = username.strip()
    settings.password = password


def apply_tor(settings: ProxySettings, port: int = DEFAULT_TOR_PORT) -> None:
    settings.source = "tor"
    settings.scheme = "socks5"
    settings.upstream_host = "127.0.0.1"
    settings.upstream_port = port
    settings.username = ""
    settings.password = ""


def apply_fast_direct(settings: ProxySettings) -> None:
    """Internet normal pela rede local — sem Tor e sem proxy externo."""
    settings.source = "direct"
    settings.scheme = "http"
    settings.upstream_host = ""
    settings.upstream_port = 0
    settings.username = ""
    settings.password = ""


def tor_status(port: int = DEFAULT_TOR_PORT) -> tuple[bool, str]:
    if is_port_open("127.0.0.1", port):
        return True, f"Tor ativo em 127.0.0.1:{port}"
    return (
        False,
        f"Tor não detectado em 127.0.0.1:{port}.\n"
        "Instale: sudo apt install tor\n"
        "Inicie: sudo systemctl start tor",
    )


def try_start_tor(port: int = DEFAULT_TOR_PORT) -> tuple[bool, str]:
    if is_port_open("127.0.0.1", port):
        return True, tor_status(port)[1]

    for cmd in (
        ["systemctl", "start", "tor"],
        ["pkexec", "systemctl", "start", "tor"],
    ):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                time.sleep(1.0)
                if is_port_open("127.0.0.1", port):
                    return True, f"Tor iniciado em 127.0.0.1:{port}"
        except (OSError, subprocess.TimeoutExpired):
            continue

    return False, tor_status(port)[1]


def _auto_configure_tor(settings: ProxySettings) -> tuple[bool, str]:
    from proxy_manager.tor_country import TOR_EXIT_COUNTRY_ENABLED, stop_managed_tor

    if TOR_EXIT_COUNTRY_ENABLED:
        country = getattr(settings, "target_country", "").strip()
        ok, msg, port = ensure_tor_for_country(country)
        if not ok:
            return False, msg
        apply_tor(settings, port)
        if country:
            return True, f"Tor {country.upper()} — socks5://127.0.0.1:{port}"
        return True, f"Tor — socks5://127.0.0.1:{port}"

    stop_managed_tor()
    ok, msg = try_start_tor(DEFAULT_TOR_PORT)
    if not ok:
        return False, msg
    apply_tor(settings, DEFAULT_TOR_PORT)
    return True, f"Tor — socks5://127.0.0.1:{DEFAULT_TOR_PORT}"


def _auto_configure_fast(settings: ProxySettings) -> tuple[bool, str]:
    """Rápido = proxy público internacional (opcional por país) ou internet direta."""
    target = getattr(settings, "target_country", "").strip().upper()
    candidates, _fetch_msg = fetch_free_proxies(max_per_source=15)
    if candidates:
        working = test_free_proxies(candidates, limit=16, workers=8)
        for candidate in working[:12]:
            trial = ProxySettings(
                source="free",
                scheme=candidate.scheme,
                upstream_host=candidate.host,
                upstream_port=candidate.port,
            )
            if not verify_upstream_settings(trial, timeout=8):
                continue
            if not _matches_target_country(settings, trial):
                continue
            cc = upstream_exit_country(trial, timeout=6)
            if cc:
                candidate = ProxyCandidate(
                    host=candidate.host,
                    port=candidate.port,
                    scheme=candidate.scheme,
                    country=cc,
                    latency_ms=candidate.latency_ms,
                    source=candidate.source,
                )
            settings.source = "free"
            apply_candidate(settings, candidate)
            label = country_label(cc) if cc else candidate.label()
            return True, f"Rápido: {candidate.host}:{candidate.port} ({label})"

    if target:
        apply_fast_direct(settings)
        return True, f"Rápido: nenhum proxy em {country_label(target)} — internet direta"

    apply_fast_direct(settings)
    return True, "Rápido: internet direta (sem Tor)"


def auto_configure_proxy(
    settings: ProxySettings,
    *,
    mode: str | None = None,
) -> tuple[bool, str]:
    """Configura proxy automaticamente conforme preferência (rápido / Tor)."""
    pref = mode or getattr(settings, "auto_proxy_mode", "fast")

    if pref == "tor":
        return _auto_configure_tor(settings)
    if pref == "fast":
        return _auto_configure_fast(settings)

    ok, msg = _auto_configure_fast(settings)
    if ok:
        return ok, msg
    ok_tor, msg_tor = _auto_configure_tor(settings)
    if ok_tor:
        return ok_tor, msg_tor
    return False, f"{msg}\n\nFallback Tor: {msg_tor}"


def proxy_source_badge(proxy: ProxySettings) -> str:
    if proxy.source == "tor":
        return "🧅 Tor"
    if getattr(proxy, "auto_proxy_mode", "fast") == "fast" or proxy.source == "direct":
        return "⚡ Rápido"
    if proxy.source == "free":
        return "Gratuito"
    if proxy.source == "paid":
        prov = PAID_PROVIDERS.get(proxy.paid_provider)
        return prov.name if prov else "Pago"
    return SOURCE_LABELS.get("custom", "Personalizado")


def settings_configured(settings: ProxySettings) -> tuple[bool, str]:
    if settings.source == "direct":
        return True, "ok"
    if settings.source == "tor":
        ok, msg = tor_status(settings.upstream_port or DEFAULT_TOR_PORT)
        if not ok:
            return False, msg
        return True, "ok"

    host = settings.upstream_host.strip()
    if not host:
        if settings.source == "free":
            return False, "Busque proxies gratuitos e clique em Usar selecionado."
        if settings.source == "paid":
            return False, "Escolha um provedor pago e clique em Aplicar provedor pago."
        return False, "Configure host e porta na aba Configurações."

    if settings.source == "paid" and settings.paid_provider != "custom_paid":
        if not settings.username.strip():
            return False, "Informe o usuário do seu provedor pago."

    return True, "ok"
