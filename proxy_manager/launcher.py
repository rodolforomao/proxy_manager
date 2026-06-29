from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from proxy_manager.browser_proxy import is_browser_app, prepare_browser_proxy, wrap_browser_command
from proxy_manager.claude_proxy import is_claude_app, prepare_claude_proxy
from proxy_manager.models import AppRule, ProxySettings
from proxy_manager.network import AUTO_INTERFACE
from proxy_manager.proxy_env import build_proxy_env

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
LAUNCH_SCRIPT = SCRIPTS_DIR / "launch_on_iface.sh"


def _systemd_bind_supported() -> bool:
    if not shutil.which("systemd-run"):
        return False
    try:
        proc = subprocess.run(
            ["systemd-run", "--user", "--scope", "-p", "BindInterfaces=lo", "true"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


_SYSTEMD_BIND = _systemd_bind_supported()


def launch_command(
    cmd: list[str],
    *,
    proxy: ProxySettings,
    use_proxy: bool,
    network_interface: str = AUTO_INTERFACE,
    base_env: dict[str, str] | None = None,
    app: AppRule | None = None,
) -> subprocess.Popen[bytes]:
    if not cmd:
        raise ValueError("comando vazio")

    if app and is_claude_app(app):
        prepare_claude_proxy(proxy, use_proxy)
    elif app and is_browser_app(app):
        prepare_browser_proxy(app, proxy, use_proxy)
        cmd = wrap_browser_command(app, cmd, use_proxy=use_proxy)

    app_upstream = getattr(app, "upstream_proxy", "") if app else ""
    env = build_proxy_env(proxy, use_proxy, base_env, app_upstream=app_upstream)

    if network_interface == AUTO_INTERFACE:
        return subprocess.Popen(
            cmd,
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    if _SYSTEMD_BIND:
        wrapper = ["systemd-run", "--user", "--scope", f"-pBindInterfaces={network_interface}"]
        for key, value in env.items():
            wrapper.append(f"-E{key}={value}")
        wrapper.extend(["--", *cmd])
        return subprocess.Popen(
            wrapper,
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    if LAUNCH_SCRIPT.exists():
        wrapper = [str(LAUNCH_SCRIPT), network_interface, *cmd]
        return subprocess.Popen(
            wrapper,
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    raise RuntimeError(
        "Roteamento por interface requer pkexec/sudo. "
        f"Execute: sudo {SCRIPTS_DIR / 'setup-network.sh'}"
    )


def launch_app_rule(app, proxy: ProxySettings) -> subprocess.Popen[bytes] | None:
    command = app.command.strip()
    if not command:
        return None
    cmd = command.split()
    return launch_command(
        cmd,
        proxy=proxy,
        use_proxy=app.use_proxy,
        network_interface=app.network_interface,
    )
