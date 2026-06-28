from __future__ import annotations

import http.client
import json
import re
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable

from proxy_manager.local_proxy import is_port_open, is_running
from proxy_manager.models import LOCAL_PORT, ProxySettings

_IP4_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s*$")
_IP_CACHE_TTL = 90.0
_geo_cache: dict[str, tuple["PublicIpInfo", float]] = {}
_ip_lock = threading.Lock()

IP_CHECK_URLS = (
    "http://api.ipify.org",
    "https://api.ipify.org",
    "http://ifconfig.me/ip",
    "http://icanhazip.com",
)

GEO_API_URL = "http://ip-api.com/json/?fields=status,query,country,countryCode"

COUNTRY_FLAG_FONT_FAMILIES = ("Noto Color Emoji", "Segoe UI Emoji", "Apple Color Emoji")


@dataclass(frozen=True)
class PublicIpInfo:
    ip: str | None
    country: str = ""
    country_code: str = ""
    message: str = ""

    @property
    def flag(self) -> str:
        return flag_emoji(country_code_for(country_code=self.country_code, country=self.country))

    @property
    def has_country(self) -> bool:
        return bool(country_code_for(country_code=self.country_code, country=self.country))


def country_code_for(*, country_code: str = "", country: str = "") -> str:
    code = country_code.strip().upper()
    if len(code) == 2 and code.isalpha():
        return code
    name = country.strip().upper()
    if len(name) == 2 and name.isalpha():
        return name
    return ""


def country_tooltip_text(*, country_code: str = "", country: str = "") -> str:
    name = country.strip()
    if name and not (len(name) == 2 and name.isalpha()):
        return name
    code = country_code_for(country_code=country_code, country=country)
    return name or code


def flag_emoji(country_code: str) -> str:
    code = country_code.strip().upper()
    if len(code) != 2 or not code.isalpha():
        return "🌐"
    return chr(0x1F1E6 + ord(code[0]) - 65) + chr(0x1F1E6 + ord(code[1]) - 65)


def format_ip_with_flag(info: PublicIpInfo) -> str:
    if not info.ip:
        return "—"
    return info.ip


def _cache_key(proxy: ProxySettings) -> str:
    return (
        f"{proxy.source}|{proxy.local_url}|{proxy.scheme}|{proxy.upstream_host}|"
        f"{proxy.upstream_port}|{proxy.username}"
    )


def _parse_ip(body: str) -> str | None:
    text = body.strip()
    match = _IP4_RE.match(text)
    if match:
        return match.group(1)
    if ":" in text and len(text) <= 45:
        return text
    return None


def _fetch_url_via_proxy(url: str, proxy_url: str, timeout: float) -> str:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "proxy-manager/1.0"},
    )
    handlers = [
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
    ]
    try:
        opener = urllib.request.build_opener(*handlers)
        with opener.open(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except (
        urllib.error.URLError,
        ssl.SSLError,
        http.client.HTTPException,
        OSError,
        subprocess.TimeoutExpired,
    ):
        result = subprocess.run(
            [
                "curl",
                "-fsSL",
                "--max-time",
                str(int(timeout)),
                "-x",
                proxy_url,
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        if result.returncode == 0:
            return result.stdout
        err = (result.stderr or result.stdout or "curl falhou").strip()
        raise OSError(err[:200])


def _parse_geo_json(body: str) -> PublicIpInfo:
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        ip = _parse_ip(body)
        return PublicIpInfo(ip=ip, message="geo indisponível")

    if data.get("status") == "success":
        return PublicIpInfo(
            ip=data.get("query"),
            country=data.get("country", ""),
            country_code=data.get("countryCode", ""),
            message="ok",
        )

    ip = data.get("query") or _parse_ip(body)
    return PublicIpInfo(ip=ip, message=data.get("message", "geo falhou"))


def _fetch_geo_url(url: str, *, proxy_url: str | None, timeout: float) -> PublicIpInfo:
    try:
        if proxy_url:
            body = _fetch_url_via_proxy(url, proxy_url, timeout)
        else:
            request = urllib.request.Request(url, headers={"User-Agent": "proxy-manager/1.0"})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
    except (
        urllib.error.URLError,
        OSError,
        subprocess.TimeoutExpired,
        http.client.HTTPException,
    ) as exc:
        return PublicIpInfo(ip=None, message=str(exc))
    return _parse_geo_json(body)


def _fetch_ip_fallback(*, proxy_url: str | None, timeout: float) -> PublicIpInfo:
    errors: list[str] = []
    for url in IP_CHECK_URLS:
        try:
            if proxy_url:
                body = _fetch_url_via_proxy(url, proxy_url, timeout)
            else:
                request = urllib.request.Request(url, headers={"User-Agent": "proxy-manager/1.0"})
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    body = response.read().decode("utf-8", errors="replace")
            ip = _parse_ip(body)
            if ip:
                return PublicIpInfo(ip=ip, message="ok")
            errors.append(f"{url}: resposta inválida")
        except (
            urllib.error.URLError,
            OSError,
            subprocess.TimeoutExpired,
            http.client.HTTPException,
        ) as exc:
            errors.append(f"{url}: {exc}")
    detail = errors[0] if errors else "sem resposta"
    return PublicIpInfo(ip=None, message=detail)


def fetch_public_ip_info_direct(*, timeout: float = 10.0, force: bool = False) -> PublicIpInfo:
    key = "direct"
    now = time.monotonic()
    with _ip_lock:
        if not force and key in _geo_cache:
            info, ts = _geo_cache[key]
            if now - ts < _IP_CACHE_TTL:
                return info

    try:
        info = _fetch_geo_url(GEO_API_URL, proxy_url=None, timeout=timeout)
        if info.ip:
            with _ip_lock:
                _geo_cache[key] = (info, now)
            return info
    except (urllib.error.URLError, OSError, subprocess.TimeoutExpired, http.client.HTTPException):
        pass

    info = _fetch_ip_fallback(proxy_url=None, timeout=timeout)
    with _ip_lock:
        _geo_cache[key] = (info, now)
    return info


def fetch_public_ip_info_via_proxy(
    proxy: ProxySettings,
    *,
    timeout: float = 10.0,
    force: bool = False,
) -> PublicIpInfo:
    if not is_running() and not is_port_open("127.0.0.1", LOCAL_PORT):
        return PublicIpInfo(ip=None, message="Proxy local desligado.")

    key = _cache_key(proxy)
    now = time.monotonic()
    with _ip_lock:
        if not force and key in _geo_cache:
            info, ts = _geo_cache[key]
            if now - ts < _IP_CACHE_TTL:
                return info

    proxy_url = proxy.local_url
    try:
        info = _fetch_geo_url(GEO_API_URL, proxy_url=proxy_url, timeout=timeout)
        if info.ip:
            with _ip_lock:
                _geo_cache[key] = (info, now)
            return info
    except (urllib.error.URLError, OSError, subprocess.TimeoutExpired, http.client.HTTPException):
        pass

    info = _fetch_ip_fallback(proxy_url=proxy_url, timeout=timeout)
    with _ip_lock:
        _geo_cache[key] = (info, now)
    return info


def fetch_public_ip_direct(*, timeout: float = 10.0, force: bool = False) -> tuple[str | None, str]:
    """Return public IP without using any proxy."""
    info = fetch_public_ip_info_direct(timeout=timeout, force=force)
    if info.ip:
        return info.ip, f"IP original: {info.ip}"
    return None, info.message or "Não foi possível obter IP original."


def fetch_public_ip_via_proxy(
    proxy: ProxySettings,
    *,
    timeout: float = 10.0,
    force: bool = False,
) -> tuple[str | None, str]:
    info = fetch_public_ip_info_via_proxy(proxy, timeout=timeout, force=force)
    if info.ip:
        return info.ip, f"IP na web: {info.ip}"
    return None, info.message or "Não foi possível obter IP."


def clear_public_ip_cache() -> None:
    with _ip_lock:
        _geo_cache.clear()


def check_proxy_reachable(proxy: ProxySettings, timeout: float = 2.0) -> tuple[bool, str]:
    del timeout
    if is_running() or is_port_open("127.0.0.1", LOCAL_PORT):
        return True, f"Proxy local ativo em 127.0.0.1:{LOCAL_PORT}"
    if not proxy.upstream_host.strip():
        return False, "Configure o proxy externo em Configurações (host e porta)."
    return False, "Proxy local desligado. Use o botão LIGAR PROXY."


def test_proxy_api(proxy: ProxySettings, timeout: float = 8.0) -> tuple[bool, str]:
    ok, msg = check_proxy_reachable(proxy)
    if not ok:
        return False, msg

    ip, ip_msg = fetch_public_ip_via_proxy(proxy, timeout=timeout)
    if ip:
        return True, f"Conexão OK.\n{ip_msg}"

    import urllib.request

    proxy_url = proxy.local_url
    handlers = [
        urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url}),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(),
    ]
    opener = urllib.request.build_opener(*handlers)
    request = urllib.request.Request(
        "https://api.anthropic.com",
        method="HEAD",
        headers={"User-Agent": "proxy-manager/1.0"},
    )
    try:
        with opener.open(request, timeout=timeout) as response:
            code = response.status
        if code in (200, 401, 403, 404, 405):
            return True, f"Conexão OK via proxy local (HTTP {code})"
        return True, f"Proxy respondeu (HTTP {code})"
    except Exception as exc:
        err = str(exc)
        if "CERT" in err.upper() or "SSL" in err.upper():
            return (
                False,
                "Falha SSL. Configure CA extra se usar proxy corporativo.\n\n"
                f"Detalhe: {err}",
            )
        return False, f"Teste falhou:\n{err}"


def is_proxy_mode_verified(
    proxy: ProxySettings,
    proxy_info: PublicIpInfo,
    direct_info: PublicIpInfo | None,
) -> tuple[bool, str]:
    """Confirma se o proxy local está entregando o tráfego esperado."""
    if not proxy_info.ip:
        return False, proxy_info.message or "Sem resposta de IP via proxy."

    if proxy.source == "direct":
        return True, f"Conexão direta — {proxy_info.ip}"

    target = getattr(proxy, "target_country", "").strip().upper()
    exit_cc = country_code_for(
        country_code=proxy_info.country_code,
        country=proxy_info.country,
    )
    direct_ip = direct_info.ip if direct_info else None

    if proxy.source == "tor":
        from proxy_manager.tor_country import TOR_EXIT_COUNTRY_ENABLED

        if TOR_EXIT_COUNTRY_ENABLED and target:
            if exit_cc == target:
                return True, f"Tor {target} — {proxy_info.ip}"
            if direct_ip and proxy_info.ip == direct_ip:
                return False, (
                    f"Tor ligado, mas IP ainda é o original ({direct_ip}). "
                    f"Saída esperada: {target}."
                )
            if exit_cc:
                return False, f"Tor saiu em {exit_cc}, esperado {target}."
            return False, f"IP via Tor ({proxy_info.ip}), mas país {target} não confirmado."
        if direct_ip and proxy_info.ip == direct_ip:
            return False, f"Tor ligado, mas IP igual ao original ({direct_ip})."
        cc_txt = f" ({exit_cc})" if exit_cc else ""
        return True, f"Tor ativo — {proxy_info.ip}{cc_txt}"

    if target and exit_cc == target:
        return True, f"Proxy {target} — {proxy_info.ip}"

    if direct_ip:
        if proxy_info.ip != direct_ip:
            cc_txt = f" {exit_cc}" if exit_cc else ""
            return True, f"IP alterado — {proxy_info.ip}{cc_txt}"
        return False, f"Proxy ligado, mas IP igual ao original ({direct_ip})."

    return True, f"IP via proxy — {proxy_info.ip}"


def wait_for_proxy_verification(
    proxy: ProxySettings,
    *,
    direct_info: PublicIpInfo | None = None,
    max_attempts: int = 10,
    poll_interval: float = 2.5,
    timeout: float = 12.0,
    on_attempt: Callable[[int, int, str], None] | None = None,
) -> tuple[bool, PublicIpInfo, PublicIpInfo | None, str]:
    """Poll até confirmar IP ou esgotar tentativas."""
    if direct_info is None or not direct_info.ip:
        direct_info = fetch_public_ip_info_direct(force=True, timeout=timeout)

    last_msg = "Sem resposta"
    proxy_info = PublicIpInfo(ip=None)

    for attempt in range(1, max_attempts + 1):
        clear_public_ip_cache()
        proxy_info = fetch_public_ip_info_via_proxy(proxy, force=True, timeout=timeout)
        ok, last_msg = is_proxy_mode_verified(proxy, proxy_info, direct_info)
        if on_attempt:
            on_attempt(attempt, max_attempts, last_msg)
        if ok:
            return True, proxy_info, direct_info, last_msg
        if attempt < max_attempts:
            time.sleep(poll_interval)

    return False, proxy_info, direct_info, last_msg


def detect_listening_proxy_ports(host: str = "127.0.0.1") -> list[int]:
    ports = (LOCAL_PORT, 7891, 7897, 1080, 8080, 8888)
    return [port for port in ports if is_port_open(host, port)]
