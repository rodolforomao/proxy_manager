"""Túnel SOCKS5 via SSH (-D) — equivalente a tunnel-socks5-ssh.sh do scm_vps_dici.

Abre um proxy SOCKS5 local (padrão 127.0.0.1:1080) através de SSH até o VPS.

IMPORTANTE — isolamento total do Tor / gost:
- Este túnel é só para apps que apontam manualmente (ex.: RustDesk → Socks5).
- NÃO é upstream do proxy local (:7890) e NÃO substitui Tor (:9050/:9051).
- Ligar/desligar aqui não deve alterar source, verificação nem modo Tor/Rápido.

Credenciais vêm do .env do projeto SCM.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from proxy_manager.local_proxy import is_port_open
import proxy_manager.service_status as svc

CONFIG_DIR = Path.home() / ".config" / "proxy-manager"
PID_FILE = CONFIG_DIR / "ssh-socks-tunnel.pid"
LOG_FILE = CONFIG_DIR / "ssh-socks-tunnel.log"

DEFAULT_ENV_FILE = Path("/home/black/enviroment/config/scm_vps_dici/.env")
DEFAULT_LOCAL_PORT = 1080
SVC_ID = "ssh_socks"

# Pré-registra no registry de serviços
svc.register(SVC_ID, "Túnel SOCKS5 (SSH)")


@dataclass(frozen=True)
class SshTunnelConfig:
    host: str
    user: str = "root"
    port: int = 22
    password: str = field(default="", repr=False)
    key_path: str = ""
    local_port: int = DEFAULT_LOCAL_PORT
    env_file: Path = DEFAULT_ENV_FILE


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
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(pid), encoding="utf-8")


def _clear_pid() -> None:
    PID_FILE.unlink(missing_ok=True)


def load_ssh_config(
    env_file: Path | None = None,
    local_port: int = DEFAULT_LOCAL_PORT,
) -> SshTunnelConfig:
    """Carrega SSH_* do .env (mesmo formato de scm_vps_dici/_lib.sh)."""
    path = env_file or DEFAULT_ENV_FILE
    if not path.is_file():
        raise FileNotFoundError(f"Arquivo .env não encontrado: {path}")

    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        if key:
            values[key] = val.strip().strip('"').strip("'")

    host = values.get("SSH_HOST", "").strip()
    if not host:
        raise ValueError(f"Defina SSH_HOST em {path}")

    return SshTunnelConfig(
        host=host,
        user=values.get("SSH_USER", "root").strip() or "root",
        port=int(values.get("SSH_PORT", "22") or "22"),
        password=values.get("SSH_PASSWORD", ""),
        key_path=values.get("SSH_KEY_PATH", "").strip(),
        local_port=local_port,
        env_file=path,
    )


def upstream_url(local_port: int = DEFAULT_LOCAL_PORT) -> str:
    """URL a usar como HTTP(S)_PROXY/ALL_PROXY de um app que deva sair por este túnel."""
    return f"socks5h://127.0.0.1:{local_port}"


def is_running(local_port: int = DEFAULT_LOCAL_PORT) -> bool:
    """True só se o nosso processo SSH (PID file) ainda escuta a porta.

    Porta ocupada por outro processo NÃO conta como túnel nosso — evita
    confundir serviços alheios com o botão S5 e interferir no Tor/gost.
    """
    pid = _read_pid()
    if not pid:
        return False
    if not Path(f"/proc/{pid}").exists():
        _clear_pid()
        return False
    return is_port_open("127.0.0.1", local_port)


def status(local_port: int = DEFAULT_LOCAL_PORT, *, update_svc: bool = True) -> tuple[bool, str]:
    """Retorna (habilitado, mensagem). Opcionalmente atualiza o card de Serviços."""
    pid = _read_pid()
    listening = is_port_open("127.0.0.1", local_port)
    foreign = listening and not (pid and Path(f"/proc/{pid}").exists())

    if pid and Path(f"/proc/{pid}").exists() and listening:
        msg = f"Habilitado — socks5://127.0.0.1:{local_port} (PID {pid})"
        if update_svc:
            svc.update(SVC_ID, "ok", msg)
        return True, msg

    if foreign:
        msg = f"Porta {local_port} em uso (outro processo) — túnel S5 off"
        if update_svc:
            svc.update(SVC_ID, "aviso", msg)
        return False, msg

    if pid and not Path(f"/proc/{pid}").exists():
        _clear_pid()

    msg = "Desabilitado"
    if update_svc:
        svc.update(SVC_ID, "parado", msg)
    return False, msg


def _build_ssh_cmd(cfg: SshTunnelConfig) -> list[str]:
    opts = [
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "ConnectTimeout=15",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        "-o", "ExitOnForwardFailure=yes",
        "-p", str(cfg.port),
        "-D", str(cfg.local_port),
        "-N",
        f"{cfg.user}@{cfg.host}",
    ]
    use_key = bool(cfg.key_path and Path(cfg.key_path).is_file())
    if use_key:
        cmd = ["ssh", "-i", cfg.key_path, *opts]
    else:
        cmd = ["ssh", *opts]

    if not use_key and cfg.password:
        if not shutil.which("sshpass"):
            raise RuntimeError(
                "SSH_PASSWORD definido mas sshpass não está instalado.\n"
                "Instale: sudo apt install sshpass"
            )
        return ["sshpass", "-p", cfg.password, *cmd]

    return cmd


def start_tunnel(
    env_file: Path | None = None,
    local_port: int = DEFAULT_LOCAL_PORT,
) -> tuple[bool, str]:
    """Inicia o túnel SOCKS5 em background. Retorna (ok, mensagem)."""
    if is_running(local_port):
        return status(local_port)

    if is_port_open("127.0.0.1", local_port):
        msg = (
            f"Porta {local_port} já está em uso por outro processo. "
            "Feche-o ou escolha outra porta — não vamos matar processos alheios."
        )
        svc.update(SVC_ID, "erro", msg)
        return False, msg

    try:
        cfg = load_ssh_config(env_file=env_file, local_port=local_port)
    except (FileNotFoundError, ValueError, OSError) as exc:
        msg = str(exc)
        svc.update(SVC_ID, "erro", msg)
        return False, msg

    try:
        cmd = _build_ssh_cmd(cfg)
    except RuntimeError as exc:
        msg = str(exc)
        svc.update(SVC_ID, "erro", msg)
        return False, msg

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    log_handle = open(LOG_FILE, "a", encoding="utf-8")
    try:
        log_handle.write(
            f"\n--- start {time.strftime('%Y-%m-%d %H:%M:%S')} "
            f"→ {cfg.user}@{cfg.host}:{cfg.port} -D {cfg.local_port} ---\n"
        )
        log_handle.flush()
        # Ambiente limpo de proxies — não herdar HTTP(S)_PROXY/ALL_PROXY do app
        # (evita o SSH do túnel S5 passar pelo gost/Tor por acidente).
        clean_env = {
            k: v
            for k, v in os.environ.items()
            if k.lower()
            not in (
                "http_proxy",
                "https_proxy",
                "all_proxy",
                "no_proxy",
                "ftp_proxy",
                "socks_proxy",
            )
        }
        clean_env["SSH_ASKPASS_REQUIRE"] = "never"
        proc = subprocess.Popen(
            cmd,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=clean_env,
        )
    except OSError as exc:
        log_handle.close()
        msg = f"Falha ao iniciar ssh: {exc}"
        svc.update(SVC_ID, "erro", msg)
        return False, msg

    _write_pid(proc.pid)
    svc.update(SVC_ID, "rodando", f"Conectando a {cfg.host}…")

    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            _clear_pid()
            tail = LOG_FILE.read_text(encoding="utf-8")[-400:] if LOG_FILE.exists() else ""
            msg = f"SSH encerrou ao abrir o túnel.\n{tail}".strip()
            svc.update(SVC_ID, "erro", msg[:200])
            return False, msg
        if is_port_open("127.0.0.1", cfg.local_port):
            msg = f"Habilitado — socks5://127.0.0.1:{cfg.local_port} → {cfg.host}"
            svc.update(SVC_ID, "ok", msg)
            return True, msg
        time.sleep(0.25)

    # Processo ainda vivo — pode estar autenticando; considera ok se PID existe
    if Path(f"/proc/{proc.pid}").exists():
        msg = (
            f"SSH iniciado (PID {proc.pid}); aguardando porta {cfg.local_port}. "
            f"Alvo: {cfg.user}@{cfg.host}"
        )
        svc.update(SVC_ID, "aviso", msg)
        return True, msg

    _clear_pid()
    msg = "Timeout aguardando túnel SOCKS5."
    svc.update(SVC_ID, "erro", msg)
    return False, msg


def stop_tunnel(local_port: int = DEFAULT_LOCAL_PORT) -> tuple[bool, str]:
    """Encerra só o nosso túnel SOCKS5 (PID file). Não mata outros processos na porta."""
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

    msg = "Desabilitado"
    svc.update(SVC_ID, "parado", msg)
    return True, msg


def toggle_tunnel(
    env_file: Path | None = None,
    local_port: int = DEFAULT_LOCAL_PORT,
) -> tuple[bool, str]:
    """Liga se estiver off; desliga se estiver on. Retorna (habilitado, mensagem)."""
    if is_running(local_port):
        stop_tunnel(local_port)
        return False, "Desabilitado"
    ok, msg = start_tunnel(env_file=env_file, local_port=local_port)
    return ok, msg
