from __future__ import annotations

import shlex
import shutil
import subprocess

from proxy_manager.models import AppRule, ProxySettings
from proxy_manager.proxy_env import build_proxy_env

TERMINAL_APP_ID = "terminal"

# app-id dedicado: força o gnome-terminal a abrir um servidor GTK isolado
# (novo processo, D-Bus service próprio) em vez de falar com o
# gnome-terminal-server padrão que já hospeda as outras janelas abertas
# (inclusive as do Cursor/Claude). Fechar ou matar essa janela nunca afeta
# as demais.
PROXY_TERMINAL_APP_ID = "com.proxymanager.torterminal"

# Fundo laranja escuro via OSC 11 — funciona em qualquer terminal VTE
# (gnome-terminal, xfce4-terminal, etc.) sem precisar criar/editar perfis.
_ORANGE_BG = "#3a1a06"
_TITLE = "PROXY TOR"


def is_terminal_app(app: AppRule) -> bool:
    return app.id == TERMINAL_APP_ID


def _terminal_binary(app: AppRule | None) -> str:
    candidates = []
    if app and app.command.strip():
        candidates.append(app.command.split()[0])
    candidates.append("gnome-terminal")
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
    raise RuntimeError(
        "gnome-terminal não encontrado. Instale-o ou configure outro comando "
        "para o app Terminal."
    )


def _inner_shell_script(proxy: ProxySettings) -> str:
    env = build_proxy_env(proxy, use_proxy=True, base_env={})
    lines = [
        f"printf '\\033]11;{_ORANGE_BG}\\007'",
        f"printf '\\033]2;{_TITLE}\\007'",
    ]
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy"):
        if key in env:
            lines.append(f"export {key}={shlex.quote(env[key])}")
    lines.append(f"echo {shlex.quote(f'[{_TITLE}] proxy: ' + proxy.local_url)}")
    lines.append("exec bash -l")
    return "\n".join(lines)


def launch_proxy_terminal(
    proxy: ProxySettings, app: AppRule | None = None
) -> subprocess.Popen[bytes]:
    """Abre uma janela de terminal NOVA e isolada com o proxy local (Tor) já
    exportado no ambiente, marcada em laranja (fundo + título).

    Nunca fecha, mata ou reinicia janelas de terminal já abertas — inclusive
    as usadas pelo Cursor, pelo Claude Code ou por qualquer outro programa.
    """
    binary = _terminal_binary(app)
    script = _inner_shell_script(proxy)
    cmd = [
        binary,
        f"--app-id={PROXY_TERMINAL_APP_ID}",
        f"--title={_TITLE}",
        "--",
        "bash",
        "-c",
        script,
    ]
    return subprocess.Popen(
        cmd,
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
