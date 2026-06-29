from __future__ import annotations

import os
import signal
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable

from proxy_manager.models import LOCAL_PORT, ProxySettings
import proxy_manager.service_status as svc

APP_DIR = Path.home() / ".local/share/proxy-manager"
BIN_DIR = APP_DIR / "bin"
GOST_BIN = BIN_DIR / "gost"
PID_FILE = Path.home() / ".config/proxy-manager/local-proxy.pid"
LOG_FILE = Path.home() / ".config/proxy-manager/local-proxy.log"

GOST_VERSION = "3.0.0"
GOST_URLS = {
    "x86_64": f"https://github.com/go-gost/gost/releases/download/v{GOST_VERSION}/gost_{GOST_VERSION}_linux_amd64.tar.gz",
    "amd64": f"https://github.com/go-gost/gost/releases/download/v{GOST_VERSION}/gost_{GOST_VERSION}_linux_amd64.tar.gz",
    "aarch64": f"https://github.com/go-gost/gost/releases/download/v{GOST_VERSION}/gost_{GOST_VERSION}_linux_arm64.tar.gz",
    "arm64": f"https://github.com/go-gost/gost/releases/download/v{GOST_VERSION}/gost_{GOST_VERSION}_linux_arm64.tar.gz",
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
        time.sleep(0.1)
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
    if pid is not None and Path(f"/proc/{pid}").exists():
        return True
    if pid is not None:
        _clear_pid()
    return is_port_open("127.0.0.1", LOCAL_PORT)


def _read_process_cmdline(pid: int) -> str:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return ""
    return " ".join(p.decode(errors="replace") for p in raw.split(b"\0") if p)


def running_upstream_label() -> str | None:
    """Rota upstream do processo na 7890 ('direct', 'socks5://…', etc.) ou None."""
    pid = _read_pid()
    if pid is None or not Path(f"/proc/{pid}").exists():
        for match in _port_listener_pids(LOCAL_PORT):
            pid = match
            break
        else:
            return None
    cmd = _read_process_cmdline(pid)
    if not cmd:
        return None
    lowered = cmd.lower()
    if "pproxy" in lowered:
        for part in cmd.split():
            if part.startswith("-r"):
                return part[2:] or "direct"
    if "gost" in lowered:
        for part in cmd.split():
            if part.startswith("-F="):
                return part[3:].replace("direct://", "direct")
    return "desconhecido"


def _port_listener_pids(port: int) -> list[int]:
    import re

    try:
        result = subprocess.run(
            ["ss", "-ltnp", f"sport = :{port}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if result.returncode != 0:
        return []
    return [int(m.group(1)) for m in re.finditer(r"pid=(\d+)", result.stdout)]


def upstream_matches(proxy: ProxySettings) -> bool:
    """True se o proxy local escuta com a mesma rota que a configuração pede."""
    if not is_port_open("127.0.0.1", LOCAL_PORT):
        return False
    expected = _upstream_forward_url(proxy)
    if expected is None:
        return False
    actual = running_upstream_label()
    if actual is None:
        return True  # porta aberta, cmdline ilegível — não forçar restart
    if expected == "direct":
        return actual in ("direct", "direct://", "desconhecido")
    return actual == expected


def get_running_pid() -> int | None:
    """Retorna o PID do proxy local se estiver rodando, None caso contrário."""
    return _read_pid()


def get_backend_name() -> str:
    """Retorna o nome do backend ativo ('gost', 'pproxy' ou 'desconhecido')."""
    if GOST_BIN.exists():
        return "gost"
    try:
        import pproxy  # noqa: F401
        return "pproxy"
    except ImportError:
        return "desconhecido"


def install_gost(force: bool = False) -> tuple[bool, str]:
    if GOST_BIN.exists() and not force:
        # Verificar se é v3 (testa -V)
        try:
            result = subprocess.run(
                [str(GOST_BIN), "-V"],
                capture_output=True, text=True, timeout=3, check=False,
            )
            if GOST_VERSION in result.stdout or GOST_VERSION in result.stderr:
                return True, str(GOST_BIN)
        except (OSError, subprocess.TimeoutExpired):
            pass
        if not force:
            return True, str(GOST_BIN)

    arch = _machine()
    url = GOST_URLS.get(arch)
    if not url:
        return False, f"Arquitetura não suportada: {arch}"

    BIN_DIR.mkdir(parents=True, exist_ok=True)
    archive_path = APP_DIR / "gost-download.tar.gz"
    APP_DIR.mkdir(parents=True, exist_ok=True)

    try:
        urllib.request.urlretrieve(url, archive_path)
        import tarfile

        with tarfile.open(archive_path, "r:gz") as tar:
            member = next(
                (m for m in tar.getmembers() if m.name == "gost" or m.name.endswith("/gost")),
                None,
            )
            if member is None:
                return False, "Binário 'gost' não encontrado no arquivo tar."
            extracted = tar.extractfile(member)
            if extracted is None:
                return False, "Não foi possível extrair o binário 'gost'."
            with extracted as src, open(GOST_BIN, "wb") as dst:
                dst.write(src.read())

        GOST_BIN.chmod(GOST_BIN.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        archive_path.unlink(missing_ok=True)
        return True, str(GOST_BIN)
    except Exception as exc:
        archive_path.unlink(missing_ok=True)
        return False, f"Falha ao baixar gost v3: {exc}"


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


def _pproxy_cmd(forward: str, proxy: ProxySettings) -> list[str]:
    from proxy_manager.network import AUTO_INTERFACE, get_interface_ip

    listen = f"http://127.0.0.1:{LOCAL_PORT}"
    remote = "direct" if forward == "direct" else forward

    # Para modos não-Tor com interface selecionada: pproxy suporta /@<ip> para bind do IP local.
    # Tor usa 127.0.0.1:9050 (loopback) — bind de IP externo é inócuo, mas evitamos por clareza.
    iface = getattr(proxy, "network_interface", AUTO_INTERFACE)
    if iface != AUTO_INTERFACE and proxy.source != "tor" and remote != "direct":
        local_ip = get_interface_ip(iface)
        if local_ip:
            # Formato pproxy: scheme://host:port/@<bind_ip>
            remote = f"{remote}/@{local_ip}"

    return [sys.executable, "-m", "pproxy", f"-l{listen}", f"-r{remote}"]


def _gost_cmd(forward: str, proxy: ProxySettings) -> list[str]:
    from proxy_manager.network import AUTO_INTERFACE

    listen = f"http://127.0.0.1:{LOCAL_PORT}"
    iface = getattr(proxy, "network_interface", AUTO_INTERFACE)

    # Para Tor (127.0.0.1:9050) interface binding é inútil — Tor gerencia sua própria rota.
    apply_iface = iface != AUTO_INTERFACE and proxy.source != "tor"

    if forward == "direct":
        if apply_iface:
            return [str(GOST_BIN), f"-L={listen}", f"-F=direct://?interface={iface}"]
        return [str(GOST_BIN), f"-L={listen}"]

    fwd = f"{forward}?interface={iface}" if apply_iface else forward
    return [str(GOST_BIN), f"-L={listen}", f"-F={fwd}"]


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
        if upstream_matches(proxy):
            route = running_upstream_label() or "?"
            msg = f"Proxy local já ativo em 127.0.0.1:{LOCAL_PORT} → {route}"
            svc.update(svc.SVC_GOST, "ok", msg)
            return True, msg
        expected = _upstream_forward_url(proxy) or "?"
        actual = running_upstream_label() or "?"
        svc.update(
            svc.SVC_GOST,
            "aviso",
            f"Rota incorreta ({actual} ≠ {expected}) — reiniciando…",
        )
        stop_local_proxy()
        if is_port_open("127.0.0.1", LOCAL_PORT):
            _kill_listener_on_port(LOCAL_PORT)
            _clear_pid()

    forward = _upstream_forward_url(proxy)
    if not forward:
        msg = "Configure o proxy externo em Configurações (personalizado, gratuito, pago ou Tor)."
        svc.update(svc.SVC_GOST, "aviso", msg)
        return False, msg

    cmd: list[str] | None = None
    backend = "pproxy"

    if GOST_BIN.exists():
        cmd = _gost_cmd(forward, proxy)
        backend = "gost"
    else:
        try:
            import pproxy  # noqa: F401
        except ImportError:
            svc.update(svc.SVC_GOST, "rodando", "Baixando gost…")
            ok, msg = install_gost()
            if ok:
                cmd = _gost_cmd(forward, proxy)
                backend = "gost"
            else:
                svc.update(svc.SVC_GOST, "erro", msg)
                return False, f"{msg}\n\nInstale dependências: pip install pproxy"
        else:
            cmd = _pproxy_cmd(forward, proxy)

    if cmd is None:
        svc.update(svc.SVC_GOST, "erro", "Nenhum backend disponível.")
        return False, "Nenhum backend de proxy local disponível."

    svc.update(svc.SVC_GOST, "rodando", f"Iniciando {backend}…")
    proc = _launch_backend(cmd)
    _write_pid(proc.pid)

    deadline = time.monotonic() + wait_seconds
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            _clear_pid()
            tail = ""
            if LOG_FILE.exists():
                tail = LOG_FILE.read_text(encoding="utf-8")[-400:]
            msg = f"{backend} encerrou com erro. {tail.strip()}"
            svc.update(svc.SVC_GOST, "erro", msg)
            return False, f"{backend} encerrou com erro.\n{tail}"
        if is_port_open("127.0.0.1", LOCAL_PORT):
            if proxy.source == "direct":
                route = "internet direta"
            else:
                route = f"{proxy.upstream_host}:{proxy.upstream_port}"
            msg = f"{backend} PID={proc.pid} — :{LOCAL_PORT} → {route}"
            svc.update(svc.SVC_GOST, "ok", msg)
            return True, f"Proxy local ativo ({backend}): 127.0.0.1:{LOCAL_PORT} → {route}"
        time.sleep(0.15)

    msg = "Timeout aguardando proxy local na porta 7890."
    svc.update(svc.SVC_GOST, "erro", msg)
    return False, msg


def stop_local_proxy() -> tuple[bool, str]:
    pid = _read_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.1)
            if Path(f"/proc/{pid}").exists():
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    _clear_pid()

    if is_port_open("127.0.0.1", LOCAL_PORT):
        _kill_listener_on_port(LOCAL_PORT)
        time.sleep(0.1)

    if is_port_open("127.0.0.1", LOCAL_PORT):
        msg = "Porta 7890 ainda em uso por outro processo."
        svc.update(svc.SVC_GOST, "aviso", msg)
        return False, msg
    svc.update(svc.SVC_GOST, "parado", "Proxy local desligado.")
    return True, "Proxy local desligado."


def force_stop_local_proxy() -> tuple[bool, str]:
    """Encerra o proxy local com várias tentativas — usado no reset de emergência."""
    stop_watchdog()
    last_msg = ""
    for attempt in range(1, 4):
        ok, last_msg = stop_local_proxy()
        if ok and not is_port_open("127.0.0.1", LOCAL_PORT):
            svc.update(svc.SVC_GOST, "parado", "Proxy local encerrado (reset).")
            return True, "Proxy local encerrado."
        _kill_listener_on_port(LOCAL_PORT)
        time.sleep(0.2 * attempt)
    if is_port_open("127.0.0.1", LOCAL_PORT):
        msg = f"Porta {LOCAL_PORT} ainda ocupada após reset."
        svc.update(svc.SVC_GOST, "erro", msg)
        return False, msg
    return True, last_msg or "Proxy local encerrado."


def ensure_local_proxy(proxy: ProxySettings) -> tuple[bool, str]:
    if is_running():
        return True, "ok"
    return start_local_proxy(proxy)


def restart_local_proxy(proxy: ProxySettings) -> tuple[bool, str]:
    stop_local_proxy()
    if is_port_open("127.0.0.1", LOCAL_PORT):
        _kill_listener_on_port(LOCAL_PORT)
        _clear_pid()
    return start_local_proxy(proxy, wait_seconds=8.0)


# ── Watchdog ────────────────────────────────────────────────────────────────

_WATCHDOG_INTERVAL = 12.0
_watchdog_stop = threading.Event()
_watchdog_thread: threading.Thread | None = None
_watchdog_proxy: ProxySettings | None = None
_watchdog_cb: Callable[[bool, str], None] | None = None


def start_watchdog(
    proxy: ProxySettings,
    on_event: Callable[[bool, str], None],
) -> None:
    """Inicia thread que monitora o proxy local e reinicia se morrer."""
    global _watchdog_thread, _watchdog_stop, _watchdog_proxy, _watchdog_cb
    stop_watchdog()
    _watchdog_proxy = proxy
    _watchdog_cb = on_event
    _watchdog_stop = threading.Event()
    _watchdog_thread = threading.Thread(
        target=_watchdog_loop, daemon=True, name="proxy-watchdog"
    )
    _watchdog_thread.start()


def stop_watchdog() -> None:
    global _watchdog_thread
    _watchdog_stop.set()
    if _watchdog_thread and _watchdog_thread.is_alive():
        _watchdog_thread.join(timeout=2.0)
    _watchdog_thread = None
    _watchdog_stop.clear()


def _watchdog_loop() -> None:
    svc.update(svc.SVC_WATCHDOG, "ok", "Watchdog ativo (intervalo 12s)")
    while not _watchdog_stop.wait(timeout=_WATCHDOG_INTERVAL):
        proxy = _watchdog_proxy
        if proxy is None or not proxy.enabled:
            continue
        pid = _read_pid()
        port_open = is_port_open("127.0.0.1", LOCAL_PORT)
        pid_alive = pid is not None and Path(f"/proc/{pid}").exists()

        if port_open and (pid is None or pid_alive) and upstream_matches(proxy):
            pid_str = f"PID={pid}" if pid else "PID=?"
            route = running_upstream_label() or "?"
            svc.log_only(svc.SVC_WATCHDOG, f"check OK — {pid_str}, rota {route}")
            continue  # tudo ok

        if port_open and upstream_matches(proxy) is False:
            svc.update(svc.SVC_WATCHDOG, "aviso", "Rota upstream incorreta — reiniciando…")
            ok, msg = restart_local_proxy(proxy)
            if ok:
                svc.update(svc.SVC_WATCHDOG, "ok", f"Rota corrigida: {msg}")
            else:
                svc.update(svc.SVC_WATCHDOG, "erro", f"Falha ao corrigir rota: {msg}")
            if _watchdog_cb:
                _watchdog_cb(ok, msg)
            continue

        # Proxy morreu — tentar reiniciar
        svc.update(svc.SVC_WATCHDOG, "aviso", "Proxy caiu — reiniciando…")
        ok, msg = restart_local_proxy(proxy)
        if ok:
            svc.update(svc.SVC_WATCHDOG, "ok", f"Reiniciado: {msg}")
        else:
            svc.update(svc.SVC_WATCHDOG, "erro", f"Falha ao reiniciar: {msg}")
        if _watchdog_cb:
            _watchdog_cb(ok, msg)
    svc.update(svc.SVC_WATCHDOG, "parado", "Watchdog encerrado.")
