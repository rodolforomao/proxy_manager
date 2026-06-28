from __future__ import annotations

import os
import signal
import socket
import stat
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from proxy_manager.models import LOCAL_PORT, ProxySettings

APP_DIR = Path.home() / ".local/share/proxy-manager"
BIN_DIR = APP_DIR / "bin"
GOST_BIN = BIN_DIR / "gost"
PID_FILE = Path.home() / ".config/proxy-manager/local-proxy.pid"
LOG_FILE = Path.home() / ".config/proxy-manager/local-proxy.log"

GOST_VERSION = "2.11.5"
GOST_URLS = {
    "x86_64": f"https://github.com/ginuerzh/gost/releases/download/v{GOST_VERSION}/gost-linux-amd64-{GOST_VERSION}.gz",
    "amd64": f"https://github.com/ginuerzh/gost/releases/download/v{GOST_VERSION}/gost-linux-amd64-{GOST_VERSION}.gz",
    "aarch64": f"https://github.com/ginuerzh/gost/releases/download/v{GOST_VERSION}/gost-linux-armv8-{GOST_VERSION}.gz",
    "arm64": f"https://github.com/ginuerzh/gost/releases/download/v{GOST_VERSION}/gost-linux-armv8-{GOST_VERSION}.gz",
}


def _machine() -> str:
    import platform

    return platform.machine().lower()


def _kill_listener_on_port(port: int, host: str = "127.0.0.1") -> None:
    """Encerra qualquer processo escutando na porta (fallback se PID file estiver errado)."""
    try:
        result = subprocess.run(
            ["ss", "-ltnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return
        import re

        for match in re.finditer(r"pid=(\d+)", result.stdout):
            pid = int(match.group(1))
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        time.sleep(0.25)
        for match in re.finditer(r"pid=(\d+)", result.stdout):
            pid = int(match.group(1))
            if Path(f"/proc/{pid}").exists():
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
    except (OSError, subprocess.TimeoutExpired):
        pass
    if is_port_open(host, port):
        try:
            subprocess.run(
                ["fuser", "-k", f"{port}/tcp"],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def is_port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _read_pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    if pid > 0 and Path(f"/proc/{pid}").exists():
        return pid
    return None


def _write_pid(pid: int) -> None:
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid), encoding="utf-8")


def _clear_pid() -> None:
    PID_FILE.unlink(missing_ok=True)


def is_running() -> bool:
    pid = _read_pid()
    if pid is not None:
        return True
    return is_port_open("127.0.0.1", LOCAL_PORT)


def install_gost(force: bool = False) -> tuple[bool, str]:
    if GOST_BIN.exists() and not force:
        return True, str(GOST_BIN)

    arch = _machine()
    url = GOST_URLS.get(arch)
    if not url:
        return False, f"Arquitetura não suportada: {arch}"

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    gz_path = APP_DIR / "gost-download.gz"
    APP_DIR.mkdir(parents=True, exist_ok=True)

    try:
        urllib.request.urlretrieve(url, gz_path)
        import gzip

        with gzip.open(gz_path, "rb") as src, open(GOST_BIN, "wb") as dst:
            dst.write(src.read())
        GOST_BIN.chmod(GOST_BIN.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        gz_path.unlink(missing_ok=True)
        return True, str(GOST_BIN)
    except Exception as exc:
        return False, f"Falha ao baixar gost: {exc}"


def _upstream_forward_url(proxy: ProxySettings) -> str | None:
    if proxy.source == "direct":
        return "direct"

    host = proxy.upstream_host.strip()
    if not host:
        return None
    if proxy.source != "tor" and host in ("127.0.0.1", "localhost", "::1"):
        return None

    scheme = proxy.scheme
    if scheme == "https":
        scheme = "http"
    auth = ""
    if proxy.username:
        from urllib.parse import quote

        user = quote(proxy.username, safe="")
        if proxy.password:
            auth = f"{user}:{quote(proxy.password, safe='')}@"
        else:
            auth = f"{user}@"
    return f"{scheme}://{auth}{host}:{proxy.upstream_port}"


def _pproxy_cmd(forward: str) -> list[str]:
    listen = f"http://127.0.0.1:{LOCAL_PORT}"
    remote = "direct" if forward == "direct" else forward
    return [sys.executable, "-m", "pproxy", f"-l{listen}", f"-r{remote}"]


def _gost_cmd(forward: str) -> list[str]:
    listen = f"http://127.0.0.1:{LOCAL_PORT}"
    upstream = "direct://" if forward == "direct" else forward
    return [str(GOST_BIN), f"-L={listen}", f"-F={upstream}"]


def _launch_backend(cmd: list[str]) -> subprocess.Popen:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(LOG_FILE, "a", encoding="utf-8")
    return subprocess.Popen(
        cmd,
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )


def start_local_proxy(proxy: ProxySettings, wait_seconds: float = 5.0) -> tuple[bool, str]:
    if is_port_open("127.0.0.1", LOCAL_PORT):
        return True, f"Proxy local já ativo em 127.0.0.1:{LOCAL_PORT}"

    forward = _upstream_forward_url(proxy)
    if not forward:
        return (
            False,
            "Configure o proxy externo em Configurações\n"
            "(personalizado, gratuito, pago ou Tor).",
        )

    cmd: list[str] | None = None
    backend = "pproxy"

    if GOST_BIN.exists():
        cmd = _gost_cmd(forward)
        backend = "gost"
    else:
        try:
            import pproxy  # noqa: F401
        except ImportError:
            ok, msg = install_gost()
            if ok:
                cmd = _gost_cmd(forward)
                backend = "gost"
            else:
                return False, f"{msg}\n\nInstale dependências: pip install pproxy"
        else:
            cmd = _pproxy_cmd(forward)

    if cmd is None:
        return False, "Nenhum backend de proxy local disponível."

    proc = _launch_backend(cmd)
    _write_pid(proc.pid)

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            _clear_pid()
            tail = ""
            if LOG_FILE.exists():
                tail = LOG_FILE.read_text(encoding="utf-8")[-400:]
            return False, f"{backend} encerrou com erro.\n{tail}"
        if is_port_open("127.0.0.1", LOCAL_PORT):
            if proxy.source == "direct":
                route = "internet direta"
            else:
                route = f"{proxy.upstream_host}:{proxy.upstream_port}"
            return True, f"Proxy local ativo ({backend}): 127.0.0.1:{LOCAL_PORT} → {route}"
        time.sleep(0.15)

    return False, "Timeout aguardando proxy local na porta 7890."


def stop_local_proxy() -> tuple[bool, str]:
    pid = _read_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.3)
            if Path(f"/proc/{pid}").exists():
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    _clear_pid()

    if is_port_open("127.0.0.1", LOCAL_PORT):
        _kill_listener_on_port(LOCAL_PORT)
        time.sleep(0.2)

    if is_port_open("127.0.0.1", LOCAL_PORT):
        return False, "Porta 7890 ainda em uso por outro processo."
    return True, "Proxy local desligado."


def ensure_local_proxy(proxy: ProxySettings) -> tuple[bool, str]:
    if is_running():
        return True, "ok"
    return start_local_proxy(proxy)


def restart_local_proxy(proxy: ProxySettings) -> tuple[bool, str]:
    stop_local_proxy()
    time.sleep(0.25)
    if is_port_open("127.0.0.1", LOCAL_PORT):
        _kill_listener_on_port(LOCAL_PORT)
        _clear_pid()
        time.sleep(0.25)
    return start_local_proxy(proxy, wait_seconds=8.0)
