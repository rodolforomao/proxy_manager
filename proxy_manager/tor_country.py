from __future__ import annotations

import os
import re
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
TOR_EXIT_COUNTRY_ENABLED = True

_MISSING_PROTO_RE = re.compile(r"The missing protocols are:\s*(.+)")
_STUCK_RE = re.compile(r"Stuck at (\d+)% \(([^)]+)\)")
_TIMEOUT_RE = re.compile(r"Tried for (\d+) seconds to get a connection")
_RESET_RE = re.compile(
    r"\((CONNECTRESET|TIMEOUT|CONNREFUSED|NET_UNREACHABLE|HOSTUNREACH)"
    r"(?:;\s*(?:CONNECTRESET|TIMEOUT|CONNREFUSED|NET_UNREACHABLE|HOSTUNREACH))*"
    r";\s*count\s+(\d+)",
    re.IGNORECASE,
)
_GUARD_RE = re.compile(
    r"Guard (\S+) \(\$[^)]+\) is failing an extremely large amount of circuits\."
    r".*?Success counts are (\d+)/(\d+)"
)
_VERSION_RE = re.compile(r"(?:Tor version |Tor )(\d+\.\d+\.\d+(?:\.\d+)?)")
_SESSION_RE = re.compile(r"Opening Socks listener on 127\.0\.0\.1:")
_BIND_RE = re.compile(
    r"((?:Could not bind to|Failed to bind|Address already in use).+)",
    re.IGNORECASE,
)
_HINT_TTL = 30.0
_hint_cache: dict[tuple[int, bool], tuple[float, str]] = {}
_version_cache: str | None = None


def _tor_version() -> str:
    global _version_cache
    if _version_cache is not None:
        return _version_cache
    try:
        result = subprocess.run(
            ["tor", "--version"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        match = _VERSION_RE.search(result.stdout or "")
        _version_cache = match.group(1) if match else ""
    except (OSError, subprocess.TimeoutExpired):
        _version_cache = ""
    return _version_cache


def _recent_tor_log(port: int) -> str:
    parts: list[str] = []
    if port == MANAGED_TOR_PORT and TOR_LOG_FILE.exists():
        try:
            parts.append(TOR_LOG_FILE.read_text(encoding="utf-8", errors="replace")[-12000:])
        except OSError:
            pass
    if port == DEFAULT_SYSTEM_TOR_PORT:
        try:
            result = subprocess.run(
                ["journalctl", "-u", "tor@default", "-n", "120", "--no-pager", "-o", "cat"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts.append(result.stdout)
        except (OSError, subprocess.TimeoutExpired):
            pass
    return "\n".join(parts)


def _current_session(text: str) -> str:
    """Descarta o journal do processo anterior. A sessão começa no último Socks listener."""
    lines = text.splitlines()
    start = 0
    for idx, line in enumerate(lines):
        if _SESSION_RE.search(line):
            start = idx
    return "\n".join(lines[start:])


def diagnose_tor_log(text: str, *, version: str = "", traffic_failed: bool = False) -> str:
    """Explica a falha real do Tor a partir do log. Vazio se não houver causa acionável."""
    text = _current_session(text)
    if not text or not text.strip():
        return ""

    missing = ""
    stuck: tuple[str, str] | None = None
    stuck_at = -1
    done_at = -1
    timeout_s = ""
    timeout_at = -1
    guard_fail = False
    guard_at = -1
    guard_name = ""
    guard_counts = ""
    soft_timeout = False
    soft_at = -1
    bind_error = ""
    reset_reason = ""
    reset_count = ""
    reset_at = -1
    for idx, line in enumerate(text.splitlines()):
        match = _MISSING_PROTO_RE.search(line)
        if match:
            missing = match.group(1).strip().rstrip(".")
        match = _STUCK_RE.search(line)
        if match:
            stuck = (match.group(1), match.group(2).replace("_", " "))
            stuck_at = idx
        if "Bootstrapped 100%" in line:
            done_at = idx
        match = _TIMEOUT_RE.search(line)
        if match:
            timeout_s = match.group(1)
            timeout_at = idx
        match = _GUARD_RE.search(line)
        if match:
            guard_fail = True
            guard_at = idx
            guard_name = match.group(1)
            guard_counts = f"{match.group(2)}/{match.group(3)}"
        elif "failing an extremely large amount of circuits" in line:
            guard_fail = True
            guard_at = idx
        if "timed out anyway" in line or "No circuits are opened" in line:
            soft_timeout = True
            soft_at = idx
        match = _RESET_RE.search(line)
        if match:
            reset_reason = match.group(1).upper()
            reset_count = match.group(2)
            reset_at = idx
        match = _VERSION_RE.search(line)
        if match:
            version = match.group(1)
        match = _BIND_RE.search(line)
        if match:
            bind_error = match.group(1).strip()

    def _still_current(at: int) -> bool:
        """Ignora aviso anterior ao último bootstrap concluído."""
        return at > done_at

    ver = f" {version}" if version else ""
    guard_current = guard_fail and _still_current(guard_at)
    timeout_current = bool(timeout_s) and _still_current(timeout_at)
    reset_current = bool(reset_reason) and _still_current(reset_at)
    soft_current = soft_timeout and _still_current(soft_at)
    circuits_bad = bool(
        guard_current or timeout_current or reset_current or (traffic_failed and soft_current)
    )
    stuck_current = stuck is not None and _still_current(stuck_at)
    broken = bool(bind_error or circuits_bad or stuck_current or (traffic_failed and missing))
    if not broken:
        return ""

    if missing:
        extra = ""
        if circuits_bad:
            extra = " Os circuitos expiram antes de fechar."
        elif stuck_current and stuck:
            extra = f" Bootstrap parou em {stuck[0]}% ({stuck[1]})."
        return (
            f"Tor{ver} não suporta os protocolos exigidos pela rede ({missing}). "
            f"A porta SOCKS abre, mas o cliente não consegue sair.{extra} "
            "Atualize o pacote tor e reinicie o serviço (systemctl restart tor@default)."
        )
    if bind_error:
        return f"Tor{ver} não subiu: {bind_error}"
    if stuck_current and stuck:
        if reset_current:
            count_txt = f", {reset_count} tentativas" if reset_count else ""
            return (
                f"Tor{ver} está preso no bootstrap {stuck[0]}% ({stuck[1]}): "
                f"a rede derruba o handshake com os relays ({reset_reason}{count_txt}). "
                "A porta SOCKS abre, mas não há circuito."
            )
        return (
            f"Tor{ver} está preso no bootstrap {stuck[0]}% ({stuck[1]}). "
            "A porta pode responder, mas ainda não há circuito utilizável."
        )
    waited = f" ({timeout_s}s)" if timeout_current and timeout_s else ""
    if reset_current:
        count_txt = f", {reset_count} tentativas" if reset_count else ""
        why = f"A rede derruba o handshake com os relays ({reset_reason}{count_txt})."
    elif guard_current and guard_name and guard_counts:
        why = f"O guard {guard_name} só completou {guard_counts} circuitos."
    elif guard_current or timeout_current:
        why = "Os guards falham ou as conexões expiram antes de sair para a internet."
    else:
        why = "Nenhum circuito ficou aberto para sair à internet."
    return f"Tor{ver} escuta, mas os circuitos não fecham{waited}. {why}"


def tor_failure_hint(port: int, *, traffic_failed: bool = False, force: bool = False) -> str:
    """Causa curta lida do journal (Tor do sistema) ou do log da instância gerenciada."""
    key = (port, traffic_failed)
    now = time.monotonic()
    cached = _hint_cache.get(key)
    if not force and cached and now - cached[0] < _HINT_TTL:
        return cached[1]
    hint = diagnose_tor_log(
        _recent_tor_log(port),
        version=_tor_version(),
        traffic_failed=traffic_failed,
    )
    _hint_cache[key] = (now, hint)
    return hint


def clear_tor_hint_cache() -> None:
    _hint_cache.clear()


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


def _explain_managed_log(fallback: str) -> str:
    text = ""
    if TOR_LOG_FILE.exists():
        try:
            text = TOR_LOG_FILE.read_text(encoding="utf-8", errors="replace")[-12000:]
        except OSError:
            text = ""
    hint = diagnose_tor_log(text, version=_tor_version(), traffic_failed=True)
    return hint or fallback


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
            return False, _explain_managed_log(f"Tor encerrou ao iniciar.\n{tail}")
        if is_port_open("127.0.0.1", MANAGED_TOR_PORT):
            cc = exit_country.upper() if exit_country else "qualquer"
            return True, f"Tor em 127.0.0.1:{MANAGED_TOR_PORT} (saída: {cc})"
        time.sleep(0.2)

    return False, _explain_managed_log(
        "Timeout aguardando Tor na porta 9051. A porta SOCKS não abriu."
    )


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
