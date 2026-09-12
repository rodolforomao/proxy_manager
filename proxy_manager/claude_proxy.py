from __future__ import annotations

import json
import subprocess
from pathlib import Path

from proxy_manager.models import LOCAL_HOST, LOCAL_PORT, AppRule, ProxySettings
from proxy_manager.proxy_env import build_proxy_env

ANTHROPIC_API_HOST = "api.anthropic.com"

CLAUDE_SETTINGS = Path.home() / ".claude" / "settings.json"
CLAUDE_APP_ID = "claude"

# Chaves que o Proxy Manager grava em settings.json (env).
_MANAGED_ENV_KEYS = frozenset(
    {
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "http_proxy",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
        "NODE_EXTRA_CA_CERTS",
        "SSL_CERT_FILE",
        "REQUESTS_CA_BUNDLE",
    }
)


def is_claude_app(app: AppRule) -> bool:
    return app.id == CLAUDE_APP_ID


def list_claude_settings_paths() -> list[Path]:
    """Todos os settings.json do Claude Code que o Proxy Manager sincroniza.

    Inclui o default (~/.claude) e contas alternativas (~/.claude-conta*),
    usadas por wrappers como claude-dseclabs (CLAUDE_CONFIG_DIR).
    Em testes, monkeypatch de CLAUDE_SETTINGS isola a descoberta ao tmp.
    """
    paths: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = str(path)
        if key not in seen:
            seen.add(key)
            paths.append(path)

    add(CLAUDE_SETTINGS)

    # Contas extras ao lado do diretório default (ex.: ~/.claude-conta1).
    home = CLAUDE_SETTINGS.parent.parent
    for directory in sorted(home.glob(".claude-conta*")):
        if directory.is_dir():
            add(directory / "settings.json")

    return paths


def _load_settings(path: Path | None = None) -> dict:
    target = path or CLAUDE_SETTINGS
    if not target.is_file():
        return {}
    try:
        with open(target, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_settings(data: dict, path: Path | None = None) -> None:
    target = path or CLAUDE_SETTINGS
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def _local_proxy_url() -> str:
    return f"http://{LOCAL_HOST}:{LOCAL_PORT}"


def _local_proxy_tcp_hex() -> tuple[str, str]:
    """Endereço remoto 127.0.0.1:LOCAL_PORT em formato /proc/net/tcp."""
    return "0100007F", f"{LOCAL_PORT:04X}"


def _is_claude_process(name: str, cmdline: str) -> bool:
    base = name.lower()
    if base in ("claude", "claude-code") or base.startswith("claude-"):
        return True
    parts = cmdline.split()
    if parts:
        exe = Path(parts[0]).name.lower()
        if exe in ("claude", "claude-code") or exe.startswith("claude-"):
            return True
    return False


def list_claude_pids() -> list[int]:
    """Todos os processos Claude Code em execução (cada terminal = uma sessão)."""
    import psutil

    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = proc.info
            name = info.get("name") or ""
            cmdline = " ".join(info.get("cmdline") or [])
            if _is_claude_process(name, cmdline):
                pids.append(int(info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue
    return sorted(pids)


def claude_session_stats() -> tuple[int, int]:
    """Retorna (sessões com proxy ativo, total de sessões)."""
    pids = list_claude_pids()
    if not pids:
        return 0, 0
    active = sum(1 for pid in pids if process_uses_local_proxy(pid))
    return active, len(pids)


def ensure_claude_settings(proxy: ProxySettings, use_proxy: bool) -> bool:
    """Grava settings.json (todas as contas) e confirma a seção env."""
    prepare_claude_proxy(proxy, use_proxy)
    if use_proxy:
        return claude_settings_proxy_active()
    return not claude_settings_proxy_active()


def process_uses_local_proxy(pid: int) -> bool:
    """True se o processo tem conexão ESTABLISHED com o proxy local."""
    host_hex, port_hex = _local_proxy_tcp_hex()
    needle = f"{host_hex}:{port_hex}"
    for fname in ("tcp", "tcp6"):
        path = Path(f"/proc/{pid}/net/{fname}")
        if not path.is_file():
            continue
        try:
            lines = path.read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) < 4:
                continue
            if parts[3] != "01":  # ESTABLISHED
                continue
            if parts[2].upper().startswith(needle):
                return True
    return False


def _settings_path_proxy_active(path: Path) -> bool:
    env = _load_settings(path).get("env")
    if not isinstance(env, dict):
        return False
    needle = _local_proxy_url()
    for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
        value = env.get(key, "")
        if isinstance(value, str) and value.strip() == needle:
            return True
    return False


def claude_settings_proxy_active() -> bool:
    """True se todos os settings Claude sincronizados apontam para o proxy local."""
    paths = list_claude_settings_paths()
    # Primary sempre conta; contas alternativas só se o diretório já existir.
    checked = False
    for path in paths:
        if path == CLAUDE_SETTINGS or path.parent.is_dir():
            checked = True
            if not _settings_path_proxy_active(path):
                return False
    return checked


def prepare_claude_proxy(proxy: ProxySettings, use_proxy: bool) -> None:
    """Persiste variáveis de proxy em todos os settings.json do Claude Code."""
    for path in list_claude_settings_paths():
        # Não cria contas novas: só grava se o dir default ou a conta já existir.
        if path != CLAUDE_SETTINGS and not path.parent.is_dir():
            continue
        _prepare_claude_proxy_at(path, proxy, use_proxy)


def _prepare_claude_proxy_at(path: Path, proxy: ProxySettings, use_proxy: bool) -> None:
    data = _load_settings(path)
    env = data.get("env")
    if not isinstance(env, dict):
        env = {}

    for key in _MANAGED_ENV_KEYS:
        env.pop(key, None)

    if use_proxy:
        merged = build_proxy_env(proxy, use_proxy=True, base_env={})
        for key in _MANAGED_ENV_KEYS:
            if key in merged:
                env[key] = merged[key]

    if env:
        data["env"] = env
    elif "env" in data:
        del data["env"]

    _save_settings(data, path)


def claude_proxy_reachable(timeout: float = 8.0) -> tuple[bool, str]:
    """Testa HTTPS CONNECT para api.anthropic.com via proxy local."""
    from proxy_manager.local_proxy import is_port_open, is_running

    if not (is_running() or is_port_open(LOCAL_HOST, LOCAL_PORT)):
        return False, "Proxy local desligado."

    proxy_url = _local_proxy_url()
    try:
        result = subprocess.run(
            [
                "curl",
                "-s",
                "--max-time",
                str(max(1, int(timeout))),
                "--connect-timeout",
                "5",
                "-o",
                "/dev/null",
                "-w",
                "%{http_code}",
                "-x",
                proxy_url,
                f"https://{ANTHROPIC_API_HOST}/",
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 2,
        )
        code_str = result.stdout.strip()
        if code_str.isdigit() and int(code_str) > 0:
            # Qualquer resposta HTTP significa que o CONNECT funcionou
            return True, f"{ANTHROPIC_API_HOST} acessível (HTTP {code_str})"
        err = (result.stderr or "").strip()[:200]
        return False, err or f"curl falhou (código {result.returncode})"
    except (subprocess.TimeoutExpired, OSError) as exc:
        return False, str(exc)[:200]


def claude_proxy_active(
    app: AppRule,
    cmdline: str = "",
    proxy_env: dict[str, str] | None = None,
    *,
    pid: int | None = None,
) -> bool | None:
    """Detecção de proxy por sessão Claude (env, TCP ao :7890 — não global)."""
    del cmdline
    if not is_claude_app(app):
        return None

    needle = _local_proxy_url()

    if proxy_env:
        for key in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            if proxy_env.get(key, "").strip() == needle:
                return True

    if pid is not None and process_uses_local_proxy(pid):
        return True

    if pid is not None:
        # Sessão rodando sem proxy — não herdar settings.json de outra sessão.
        return False

    if claude_settings_proxy_active():
        return True

    return False
