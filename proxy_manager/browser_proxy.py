from __future__ import annotations

import re
from pathlib import Path

from proxy_manager.models import LOCAL_HOST, LOCAL_PORT, AppRule, ProxySettings

MARKER = "# proxy-manager-auto"

BROWSER_IDS = frozenset({"firefox", "chrome"})


def is_browser_app(app: AppRule) -> bool:
    return app.category == "browser" or app.id in BROWSER_IDS


def _firefox_profiles_ini() -> Path | None:
    for path in (
        Path.home() / "snap/firefox/common/.mozilla/firefox/profiles.ini",
        Path.home() / ".mozilla/firefox/profiles.ini",
    ):
        if path.is_file():
            return path
    return None


def default_firefox_profile_dir() -> Path | None:
    ini = _firefox_profiles_ini()
    if not ini:
        return None

    text = ini.read_text(encoding="utf-8", errors="replace")
    default_path: str | None = None
    current_path: str | None = None
    current_default = False

    for line in text.splitlines():
        line = line.strip()
        if line == "[Profile0]":
            current_path = None
            current_default = False
        elif line.startswith("Path="):
            current_path = line.split("=", 1)[1].strip()
        elif line.startswith("Default=1"):
            current_default = True
        elif line.startswith("[") and line.endswith("]"):
            if current_default and current_path:
                default_path = current_path
            current_path = None
            current_default = False

    if current_default and current_path:
        default_path = current_path

    if not default_path:
        return None

    profile_dir = ini.parent / default_path
    return profile_dir if profile_dir.is_dir() else None


def _strip_managed_block(content: str) -> str:
    pattern = re.compile(
        rf"(\n?{re.escape(MARKER)}.*?(?=\n{re.escape(MARKER)}-end|\Z))",
        re.DOTALL,
    )
    cleaned = pattern.sub("", content)
    return cleaned.rstrip() + ("\n" if cleaned.strip() else "")


def _firefox_user_js_block(host: str, port: int) -> str:
    return (
        f"{MARKER}\n"
        f'user_pref("network.proxy.type", 1);\n'
        f'user_pref("network.proxy.http", "{host}");\n'
        f'user_pref("network.proxy.http_port", {port});\n'
        f'user_pref("network.proxy.ssl", "{host}");\n'
        f'user_pref("network.proxy.ssl_port", {port});\n'
        f'user_pref("network.proxy.share_proxy_settings", true);\n'
        f'user_pref("network.proxy.no_proxies_on", "localhost,127.0.0.1");\n'
        f"{MARKER}-end\n"
    )


def _firefox_disable_block() -> str:
    return (
        f"{MARKER}\n"
        f'user_pref("network.proxy.type", 0);\n'
        f"{MARKER}-end\n"
    )


def set_firefox_proxy(profile_dir: Path, *, host: str, port: int, enabled: bool) -> None:
    user_js = profile_dir / "user.js"
    existing = user_js.read_text(encoding="utf-8", errors="replace") if user_js.exists() else ""
    cleaned = _strip_managed_block(existing)

    if enabled:
        block = _firefox_user_js_block(host, port)
        user_js.write_text(cleaned + ("\n" if cleaned and not cleaned.endswith("\n") else "") + block, encoding="utf-8")
    else:
        block = _firefox_disable_block()
        user_js.write_text(cleaned + ("\n" if cleaned and not cleaned.endswith("\n") else "") + block, encoding="utf-8")


def firefox_proxy_active(profile_dir: Path | None = None, *, host: str = LOCAL_HOST, port: int = LOCAL_PORT) -> bool:
    profile_dir = profile_dir or default_firefox_profile_dir()
    if not profile_dir:
        return False
    user_js = profile_dir / "user.js"
    if not user_js.is_file():
        return False
    text = user_js.read_text(encoding="utf-8", errors="replace")
    if MARKER not in text:
        return False
    if 'user_pref("network.proxy.type", 0)' in text:
        return False
    return f'user_pref("network.proxy.http", "{host}")' in text


def clear_firefox_profile_lock(profile_dir: Path | None = None) -> None:
    profile_dir = profile_dir or default_firefox_profile_dir()
    if not profile_dir:
        return
    for name in ("lock", ".parentlock"):
        path = profile_dir / name
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass


def browser_connects_local_proxy(app: AppRule, local_port: int = LOCAL_PORT) -> bool:
    """Verifica se algum processo do app tem conexão TCP ao proxy local."""
    import psutil

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = proc.info.get("name") or ""
            cmdline = " ".join(proc.info.get("cmdline") or [])
            haystack = f"{name} {cmdline}".lower()
            if not any(p.lower() in haystack for p in app.patterns):
                continue
            for conn in psutil.Process(proc.info["pid"]).connections(kind="tcp"):
                if not conn.raddr:
                    continue
                if conn.raddr.port == local_port and conn.raddr.ip in ("127.0.0.1", "::1"):
                    return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False


def prepare_browser_proxy(app: AppRule, proxy: ProxySettings, use_proxy: bool) -> None:
    if app.id == "firefox":
        profile = default_firefox_profile_dir()
        if profile is None:
            raise RuntimeError(
                "Perfil do Firefox não encontrado.\n"
                "Abra o Firefox pelo menos uma vez ou configure manualmente em about:preferences."
            )
        set_firefox_proxy(profile, host=LOCAL_HOST, port=LOCAL_PORT, enabled=use_proxy)


def wrap_browser_command(
    app: AppRule,
    cmd: list[str],
    *,
    use_proxy: bool,
) -> list[str]:
    if not cmd:
        return cmd

    executable = Path(cmd[0]).name.lower()
    result = [arg for arg in cmd if not arg.startswith("--proxy-server=")]

    if not use_proxy:
        return result

    if app.id == "chrome" or "chrome" in executable:
        return [*result, f"--proxy-server=http://{LOCAL_HOST}:{LOCAL_PORT}"]

    return result


def is_content_process(cmdline: str) -> bool:
    lowered = cmdline.lower()
    return "-contentproc" in lowered or " --type=" in lowered


def main_browser_command(app: AppRule, cmd: list[str]) -> list[str]:
    if is_content_process(" ".join(cmd)):
        base = app.command.strip().split()
        if base:
            return base
    return cmd


def browser_proxy_active(app: AppRule, cmdline: str) -> bool | None:
    if app.id == "firefox":
        return firefox_proxy_active()
    if app.id == "chrome":
        needle = f"--proxy-server=http://{LOCAL_HOST}:{LOCAL_PORT}"
        return needle in cmdline
    return None
