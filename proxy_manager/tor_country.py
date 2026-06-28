from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from proxy_manager.local_proxy import is_port_open

CONFIG_DIR = Path.home() / ".config/proxy-manager"
TOR_DATA_DIR = Path.home() / ".local/share/proxy-manager/tor-data"
TORRC_FILE = CONFIG_DIR / "torrc"
TOR_PID_FILE = CONFIG_DIR / "tor-instance.pid"
TOR_LOG_FILE = CONFIG_DIR / "tor-instance.log"

DEFAULT_SYSTEM_TOR_PORT = 9050
MANAGED_TOR_PORT = 9051

# Saída Tor por país (ExitNodes) — desligado até testes; usa Tor do sistema :9050.
TOR_EXIT_COUNTRY_ENABLED = False


def _read_pid() -> int | None:
    if not TOR_PID_FILE.exists():
        return None
    try:
        pid = int(TOR_PID_FILE.read_text(encoding="utf-8").strip())
    except ValueError:
        return None
    if pid > 0 and Path(f"/proc/{pid}").exists():
        return pid
    return None


def _write_pid(pid: int) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOR_PID_FILE.write_text(str(pid), encoding="utf-8")


def _clear_pid() -> None:
    TOR_PID_FILE.unlink(missing_ok=True)


def stop_managed_tor() -> None:
    pid = _read_pid()
    if pid:
        try:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.4)
            if Path(f"/proc/{pid}").exists():
                os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    _clear_pid()


def write_torrc(exit_country: str = "") -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOR_DATA_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        f"SocksPort {MANAGED_TOR_PORT}",
        f"DataDirectory {TOR_DATA_DIR}",
        "AvoidDiskWrites 1",
        "Log notice stdout",
    ]
    code = exit_country.strip().lower()
    if code:
        lines.append(f"ExitNodes {{{code}}}")
        lines.append("StrictNodes 1")
    TORRC_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return TORRC_FILE


def start_managed_tor(exit_country: str = "") -> tuple[bool, str]:
    if is_port_open("127.0.0.1", MANAGED_TOR_PORT):
        pid = _read_pid()
        if pid:
            return True, f"Tor gerenciado em 127.0.0.1:{MANAGED_TOR_PORT}"

    stop_managed_tor()
    torrc = write_torrc(exit_country)

    try:
        which = subprocess.run(["which", "tor"], capture_output=True, text=True, check=False)
        if which.returncode != 0:
            return False, "Comando `tor` não encontrado. Instale: sudo apt install tor"
    except OSError as exc:
        return False, str(exc)

    TOR_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    log_handle = open(TOR_LOG_FILE, "a", encoding="utf-8")
    proc = subprocess.Popen(
        ["tor", "-f", str(torrc)],
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    _write_pid(proc.pid)

    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            _clear_pid()
            tail = TOR_LOG_FILE.read_text(encoding="utf-8")[-500:] if TOR_LOG_FILE.exists() else ""
            return False, f"Tor encerrou ao iniciar.\n{tail}"
        if is_port_open("127.0.0.1", MANAGED_TOR_PORT):
            cc = exit_country.upper() if exit_country else "qualquer"
            return True, f"Tor em 127.0.0.1:{MANAGED_TOR_PORT} (saída: {cc})"
        time.sleep(0.2)

    return False, "Timeout aguardando Tor na porta 9051."


def ensure_tor_for_country(exit_country: str = "") -> tuple[bool, str, int]:
    """Retorna (ok, mensagem, porta_socks). Com país → Tor gerenciado :9051; senão tenta :9050."""
    code = exit_country.strip().upper()
    if code:
        ok, msg = start_managed_tor(code)
        return ok, msg, MANAGED_TOR_PORT if ok else DEFAULT_SYSTEM_TOR_PORT

    if is_port_open("127.0.0.1", DEFAULT_SYSTEM_TOR_PORT):
        return True, f"Tor do sistema em 127.0.0.1:{DEFAULT_SYSTEM_TOR_PORT}", DEFAULT_SYSTEM_TOR_PORT

    ok, msg = start_managed_tor("")
    return ok, msg, MANAGED_TOR_PORT if ok else DEFAULT_SYSTEM_TOR_PORT
