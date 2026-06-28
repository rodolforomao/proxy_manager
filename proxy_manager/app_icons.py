from __future__ import annotations

from proxy_manager.models import AppRule

# Apps exibidos na barra rápida (ordem de exibição)
FEATURED_APP_IDS: tuple[str, ...] = (
    "firefox",
    "chrome",
    "cursor",
    "claude",
    "discord",
    "telegram",
    "code",
    "spotify",
    "slack",
)

APP_ICONS: dict[str, str] = {
    "firefox": "🦊",
    "chrome": "🌐",
    "cursor": "⌨",
    "claude": "🤖",
    "code": "📝",
    "discord": "💬",
    "slack": "💼",
    "telegram": "✈",
    "spotify": "🎵",
    "docker": "🐳",
    "git": "🔀",
    "npm": "📦",
    "pip": "🐍",
    "wget": "🔗",
    "openai": "💡",
}

APP_SHORT_NAMES: dict[str, str] = {
    "firefox": "Firefox",
    "chrome": "Chrome",
    "cursor": "Cursor",
    "claude": "Claude",
    "code": "VS Code",
    "discord": "Discord",
    "slack": "Slack",
    "telegram": "Telegram",
    "spotify": "Spotify",
    "docker": "Docker",
    "git": "Git",
    "npm": "Node",
    "pip": "Python",
    "wget": "curl",
    "openai": "ChatGPT",
}


def app_icon(app_id: str) -> str:
    return APP_ICONS.get(app_id, "📱")


def app_short_name(app: AppRule) -> str:
    return APP_SHORT_NAMES.get(app.id, app.name.split()[0][:10])
