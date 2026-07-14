"""Sincroniza configs persistentes (Claude settings.json, Firefox user.js, etc.).

Regra:
- Sem proxy local ativo → limpa tudo (apps voltam ao acesso direto).
- Com proxy local ativo → regrava a config de cada app conforme use_proxy.
"""
from __future__ import annotations

from proxy_manager.browser_proxy import is_browser_app, prepare_browser_proxy
from proxy_manager.claude_proxy import is_claude_app, prepare_claude_proxy
from proxy_manager.models import AppRule, ProxySettings


def clear_persistent_app_proxies(apps: list[AppRule], proxy: ProxySettings) -> list[str]:
    """Remove proxy de Claude/browsers. Retorna IDs alterados."""
    changed: list[str] = []
    for app in apps:
        try:
            if is_claude_app(app):
                prepare_claude_proxy(proxy, use_proxy=False)
                changed.append(app.id)
            elif is_browser_app(app):
                prepare_browser_proxy(app, proxy, use_proxy=False)
                changed.append(app.id)
        except Exception:
            continue
    return changed


def apply_persistent_app_proxies(apps: list[AppRule], proxy: ProxySettings) -> list[str]:
    """Grava proxy só nos apps com use_proxy=True; limpa os demais Claude/browser."""
    changed: list[str] = []
    for app in apps:
        try:
            if is_claude_app(app):
                prepare_claude_proxy(proxy, use_proxy=bool(app.use_proxy))
                changed.append(app.id)
            elif is_browser_app(app):
                prepare_browser_proxy(app, proxy, use_proxy=bool(app.use_proxy))
                changed.append(app.id)
        except Exception:
            continue
    return changed


def sync_persistent_app_proxies(
    apps: list[AppRule],
    proxy: ProxySettings,
    *,
    local_proxy_active: bool,
) -> list[str]:
    """Reconcilia configs externas com o estado real do proxy local."""
    if local_proxy_active:
        return apply_persistent_app_proxies(apps, proxy)
    return clear_persistent_app_proxies(apps, proxy)
