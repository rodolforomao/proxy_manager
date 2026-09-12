from __future__ import annotations

from proxy_manager.models import AppRule


def default_apps() -> list[AppRule]:
    return [
        AppRule(
            id="claude",
            name="Claude Code",
            patterns=["claude", "anthropic"],
            use_proxy=False,
            category="ai",
            command="claude",
            notes="CLI — proxy via settings.json (~/.claude e ~/.claude-conta*)",
        ),
        AppRule(
            id="cursor",
            name="Cursor",
            patterns=["cursor"],
            use_proxy=False,
            category="ai",
            command="cursor",
            notes="Funciona sem proxy",
        ),
        AppRule(
            id="code",
            name="VS Code",
            patterns=["code", "vscode"],
            use_proxy=False,
            category="dev",
            command="code",
        ),
        AppRule(
            id="chrome",
            name="Google Chrome",
            patterns=["google-chrome", "chromium", "chromium-browser"],
            use_proxy=False,
            category="browser",
            command="google-chrome",
            notes="Chrome ou Chromium — reinicia com --proxy-server apontando para o proxy local.",
        ),
        AppRule(
            id="firefox",
            name="Firefox",
            patterns=["firefox"],
            use_proxy=False,
            category="browser",
            command="firefox",
            notes="Configura proxy no perfil (user.js) e reinicia o navegador.",
        ),
        AppRule(
            id="discord",
            name="Discord",
            patterns=["discord"],
            use_proxy=False,
            category="social",
            command="discord",
        ),
        AppRule(
            id="slack",
            name="Slack",
            patterns=["slack"],
            use_proxy=False,
            category="social",
            command="slack",
        ),
        AppRule(
            id="telegram",
            name="Telegram",
            patterns=["telegram"],
            use_proxy=False,
            category="social",
            command="telegram-desktop",
        ),
        AppRule(
            id="rustdesk",
            name="RustDesk",
            patterns=["rustdesk"],
            use_proxy=False,
            use_socks5=True,
            category="tools",
            command="rustdesk",
            notes=(
                "NÃO usa o proxy gost (:7890). Toggle S5 do card (ou o botão S5 "
                "do header) liga o túnel SSH SOCKS5 em 127.0.0.1:1080 — configure "
                "no RustDesk: Settings → Network → Socks5."
            ),
        ),
        AppRule(
            id="spotify",
            name="Spotify",
            patterns=["spotify"],
            use_proxy=False,
            category="media",
            command="spotify",
        ),
        AppRule(
            id="docker",
            name="Docker",
            patterns=["docker", "dockerd", "containerd"],
            use_proxy=False,
            category="dev",
            command="",
            notes="Containers geralmente usam config separada",
        ),
        AppRule(
            id="git",
            name="Git",
            patterns=["git"],
            use_proxy=False,
            category="dev",
            command="git",
        ),
        AppRule(
            id="npm",
            name="npm / Node",
            patterns=["npm", "node", "pnpm", "yarn"],
            use_proxy=False,
            category="dev",
            command="npm",
        ),
        AppRule(
            id="pip",
            name="pip / Python",
            patterns=["pip", "pip3", "python", "python3"],
            use_proxy=False,
            category="dev",
            command="pip3",
        ),
        AppRule(
            id="wget",
            name="wget / curl",
            patterns=["wget", "curl"],
            use_proxy=False,
            category="tools",
            command="curl",
        ),
        AppRule(
            id="openai",
            name="ChatGPT Desktop",
            patterns=["chatgpt", "openai"],
            use_proxy=False,
            category="ai",
            command="",
            notes="Funciona sem proxy",
        ),
        AppRule(
            id="liquid-testnet",
            name="Liquid Testnet",
            patterns=["/config/elements.conf", "liquid-elementsd"],
            use_proxy=False,
            enabled=False,
            category="blockchain",
            command="elements-tor",
            notes=(
                "Tor nativo no elementsd (proxy= SOCKS5). "
                "Não usa gost :7890. Edita elements.conf e recreia o container se estiver up."
            ),
        ),
        AppRule(
            id="liquid-mainnet",
            name="Liquid Mainnet",
            patterns=["elements-mainnet.conf", "liquid-elementsd-mainnet"],
            use_proxy=False,
            enabled=False,
            category="blockchain",
            command="elements-tor",
            notes=(
                "Tor nativo no elementsd mainnet full. "
                "Não usa gost :7890. Edita elements-mainnet.conf e recreia se estiver up."
            ),
        ),
        AppRule(
            id="liquid-mainnet-light",
            name="Liquid Mainnet (leve)",
            patterns=["elements-mainnet-light.conf", "liquid-elementsd-mainnet-light"],
            use_proxy=False,
            enabled=False,
            category="blockchain",
            command="elements-tor",
            notes=(
                "Tor nativo no elementsd mainnet pruned. "
                "Não usa gost :7890. Edita elements-mainnet-light.conf e recreia se estiver up."
            ),
        ),
    ]
