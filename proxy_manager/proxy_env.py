from __future__ import annotations

import os
from typing import Mapping

from proxy_manager.models import AppRule, ProxySettings

PROXY_KEYS = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "NO_PROXY",
    "no_proxy",
)


def build_proxy_env(
    proxy: ProxySettings,
    use_proxy: bool,
    base_env: Mapping[str, str] | None = None,
    app_upstream: str = "",
) -> dict[str, str]:
    env = dict(base_env or os.environ)
    for key in PROXY_KEYS:
        env.pop(key, None)

    if use_proxy:
        # Se app tem upstream próprio, apontar diretamente para ele
        url = app_upstream.strip() if app_upstream.strip() else proxy.url
        if proxy.scheme == "socks5" and not app_upstream:
            url = url.replace("socks5://", "socks5h://", 1)
        env["HTTP_PROXY"] = url
        env["HTTPS_PROXY"] = url
        env["http_proxy"] = url
        env["https_proxy"] = url
        if proxy.scheme.startswith("socks") and not app_upstream:
            env["ALL_PROXY"] = url
            env["all_proxy"] = url
        env["NO_PROXY"] = proxy.no_proxy
        env["no_proxy"] = proxy.no_proxy
        if proxy.extra_ca_certs.strip() and not app_upstream:
            ca = proxy.extra_ca_certs.strip()
            env["NODE_EXTRA_CA_CERTS"] = ca
            env["SSL_CERT_FILE"] = ca
            env["REQUESTS_CA_BUNDLE"] = ca

    return env


def read_process_proxy_env(pid: int) -> dict[str, str]:
    environ_path = f"/proc/{pid}/environ"
    if not os.path.exists(environ_path):
        return {}

    try:
        with open(environ_path, "rb") as f:
            raw = f.read()
    except (OSError, PermissionError):
        return {}

    result: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if b"=" not in item:
            continue
        key, _, value = item.partition(b"=")
        key_str = key.decode(errors="replace")
        if key_str in PROXY_KEYS or key_str.upper().endswith("_PROXY"):
            result[key_str] = value.decode(errors="replace")
    return result


def has_active_proxy(proxy_env: dict[str, str]) -> bool:
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        value = proxy_env.get(key, "").strip()
        if value:
            return True
    return False


def classify_process_status(
    app: AppRule | None,
    proxy_env: dict[str, str],
    proxy: ProxySettings,
) -> tuple[bool, str]:
    active = has_active_proxy(proxy_env)

    if app is None:
        if active:
            return True, "proxy ativo (não catalogado)"
        return False, "sem proxy"

    if not app.enabled:
        return active, "regra desativada"

    if active:
        return True, "proxy ativo ✓"
    return False, "sem proxy"
