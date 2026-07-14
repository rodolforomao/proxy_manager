"""Restaura tráfego direto à internet (reset de emergência)."""
from __future__ import annotations

import subprocess
import urllib.request
from dataclasses import dataclass, field

from proxy_manager.local_proxy import (
    LOCAL_PORT,
    force_stop_local_proxy,
    is_port_open,
    stop_watchdog,
)
from proxy_manager.models import AppRule, LOCAL_HOST, ProxySettings
from proxy_manager.proxy_env import read_process_proxy_env
from proxy_manager.app_proxy_sync import clear_persistent_app_proxies

_LOCAL_PROXY_NEEDLE = f"http://{LOCAL_HOST}:{LOCAL_PORT}"


@dataclass
class RestoreReport:
    ok: bool
    steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    direct_ip: str | None = None

    def summary(self) -> str:
        lines = list(self.steps)
        if self.direct_ip:
            lines.append(f"Internet direta OK — IP {self.direct_ip}")
        elif self.ok:
            lines.append("Internet direta OK")
        if self.warnings:
            lines.append("")
            lines.append("Atenção:")
            lines.extend(f"• {w}" for w in self.warnings)
        return "\n".join(lines)


def _reset_gnome_system_proxy() -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["gsettings", "get", "org.gnome.system.proxy", "mode"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        mode = result.stdout.strip().strip("'")
        host = ""
        port = 0
        if mode == "manual":
            hr = subprocess.run(
                ["gsettings", "get", "org.gnome.system.proxy.http", "host"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            pr = subprocess.run(
                ["gsettings", "get", "org.gnome.system.proxy.http", "port"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            host = hr.stdout.strip().strip("'")
            try:
                port = int(pr.stdout.strip())
            except ValueError:
                port = 0
        points_here = host in ("127.0.0.1", "localhost", "::1") or port == LOCAL_PORT
        if mode in ("manual", "auto") and (points_here or mode == "auto"):
            subprocess.run(
                ["gsettings", "set", "org.gnome.system.proxy", "mode", "none"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            return True, f"Proxy do sistema GNOME: {mode} → none"
        return True, f"Proxy GNOME inalterado (modo {mode})"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"GNOME proxy: {exc}"


def _verify_direct_internet(timeout: float = 10.0) -> tuple[bool, str | None, str]:
    urls = ("https://api.ipify.org", "http://api.ipify.org")
    errors: list[str] = []
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "proxy-manager-restore/1"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                ip = resp.read().decode().strip()
            if ip:
                return True, ip, "ok"
            errors.append(f"{url}: resposta vazia")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    return False, None, errors[0] if errors else "sem resposta"


def _processes_still_using_local_proxy() -> list[str]:
    needle = _LOCAL_PROXY_NEEDLE
    found: list[str] = []
    import os

    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        pid = int(entry)
        env = read_process_proxy_env(pid)
        uses = any(
            env.get(k, "").strip() in (needle, needle + "/")
            for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")
        )
        if not uses:
            continue
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
            name = cmd.split(b"\0")[0].decode(errors="replace") or f"pid {pid}"
        except OSError:
            name = f"pid {pid}"
        found.append(f"{name} (PID {pid})")
    return found[:8]


# Path import for _processes_still_using_local_proxy
from pathlib import Path  # noqa: E402 — after function for minimal diff


def restore_direct_traffic(
    apps: list[AppRule],
    proxy: ProxySettings,
) -> RestoreReport:
    """Para proxy local, limpa configs e confirma internet direta."""
    report = RestoreReport(ok=False)

    stop_watchdog()
    report.steps.append("Watchdog parado")

    proxy_ok, proxy_msg = force_stop_local_proxy()
    if proxy_ok:
        report.steps.append(proxy_msg)
    else:
        report.warnings.append(proxy_msg)

    if is_port_open(LOCAL_HOST, LOCAL_PORT):
        report.warnings.append(f"Porta {LOCAL_PORT} ainda em uso — reinicie apps que usam proxy")
    else:
        report.steps.append(f"Porta {LOCAL_PORT} livre")

    changed = clear_persistent_app_proxies(apps, proxy)
    if changed:
        report.steps.append(f"Proxy removido de {len(changed)} app(s) (Claude/Firefox)")

    gnome_ok, gnome_msg = _reset_gnome_system_proxy()
    report.steps.append(gnome_msg if gnome_ok else f"Falha — {gnome_msg}")

    still = _processes_still_using_local_proxy()
    if still:
        report.warnings.append(
            "Estes processos ainda têm HTTP_PROXY=7890 — feche e reabra: "
            + ", ".join(still)
        )

    net_ok, direct_ip, net_detail = _verify_direct_internet()
    report.direct_ip = direct_ip
    if net_ok:
        report.steps.append(f"Teste direto: {direct_ip}")
        report.ok = proxy_ok and not is_port_open(LOCAL_HOST, LOCAL_PORT)
    else:
        report.warnings.append(f"Internet direta falhou: {net_detail}")
        report.ok = False

    if report.ok and report.warnings:
        # Porta livre + internet OK, mas apps precisam reinício
        report.ok = True

    return report
