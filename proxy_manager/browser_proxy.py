from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from proxy_manager.models import LOCAL_HOST, LOCAL_PORT, AppRule, ProxySettings

MARKER = "# proxy-manager-auto"

BROWSER_IDS = frozenset({"firefox", "chrome"})

BROWSER_COMMAND_CANDIDATES: dict[str, list[str]] = {
    "chrome": ["google-chrome-stable", "google-chrome", "chromium", "chromium-browser"],
    "firefox": ["firefox"],
}

# Cor usada para marcar visualmente a janela do navegador que está com proxy.
PROXY_MARK_COLOR = "#f97316"

_BROWSER_COUNTERPART: dict[str, str] = {
    "chrome": "firefox",
    "firefox": "chrome",
}


def browser_counterpart_id(app_id: str) -> str | None:
    """Id do outro navegador gerido (chrome<->firefox), usado para abrir com proxy
    sem mexer no navegador que já está aberto."""
    return _BROWSER_COUNTERPART.get(app_id)


# Apps Electron que também têm "chrome" no caminho — não são o navegador Google/Chromium.
_CHROME_NOISE_MARKERS = (
    "cursor",
    "electron",
    "chrome_crashpad",
    "chrome-sandbox",
    "/usr/share/code/",
    "slack",
    "discord",
    "spotify",
    "teams",
    "obs",
)


def resolve_browser_command(app: AppRule) -> str:
    """Resolve o executável instalado (ex.: chromium quando google-chrome não existe)."""
    configured = app.command.strip()
    if configured:
        exe = configured.split()[0]
        if shutil.which(exe):
            return configured
    for candidate in BROWSER_COMMAND_CANDIDATES.get(app.id, []):
        path = shutil.which(candidate)
        if path:
            return path
    return configured


def is_main_browser_process(app: AppRule, name: str, cmdline: str) -> bool:
    """Ignora content-process do Firefox e binários Electron (Cursor, etc.)."""
    if is_content_process(cmdline):
        return False
    hay = f"{name} {cmdline}".lower()
    if app.id == "firefox":
        return "firefox" in hay
    if app.id == "chrome":
        if any(marker in hay for marker in _CHROME_NOISE_MARKERS):
            return False
        return any(
            token in hay
            for token in (
                "google-chrome",
                "chromium",
                "chromium-browser",
                "/opt/google/chrome",
            )
        )
    return True


def find_main_browser_pid(app: AppRule) -> int | None:
    """PID do processo principal do navegador, ou None se não estiver aberto."""
    import psutil

    best_pid: int | None = None
    best_score = -1
    for proc in psutil.process_iter(["pid", "name", "cmdline", "status"]):
        try:
            info = proc.info
            if info.get("status") == psutil.STATUS_ZOMBIE:
                continue
            name = info.get("name") or ""
            cmdline = " ".join(info.get("cmdline") or [])
            if not is_main_browser_process(app, name, cmdline):
                continue
            hay = f"{name} {cmdline}".lower()
            if not any(p.lower() in hay for p in app.patterns):
                continue
            score = len(cmdline)
            if name.lower() in ("firefox", "chrome", "chromium", "google-chrome", "chromium-browser"):
                score += 5000
            if score > best_score:
                best_score = score
                best_pid = info["pid"]
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return best_pid


def browser_is_running(app: AppRule) -> bool:
    return find_main_browser_pid(app) is not None


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
        f'user_pref("toolkit.legacyUserProfileCustomizations.stylesheets", true);\n'
        f"{MARKER}-end\n"
    )


def _firefox_disable_block() -> str:
    return (
        f"{MARKER}\n"
        f'user_pref("network.proxy.type", 0);\n'
        f"{MARKER}-end\n"
    )


def _orange_user_chrome_css_block() -> str:
    return (
        f"{MARKER}\n"
        f'#TabsToolbar, #nav-bar, #titlebar {{ background-color: {PROXY_MARK_COLOR} !important; }}\n'
        f'#tabbrowser-tabs .tabbrowser-tab[selected="true"] .tab-background {{ background-color: {PROXY_MARK_COLOR} !important; }}\n'
        f"{MARKER}-end\n"
    )


def _set_firefox_marking(profile_dir: Path, *, enabled: bool) -> None:
    """Pinta a toolbar/abas de laranja via userChrome.css enquanto o proxy estiver ativo."""
    chrome_dir = profile_dir / "chrome"
    css_path = chrome_dir / "userChrome.css"
    existing = css_path.read_text(encoding="utf-8", errors="replace") if css_path.exists() else ""
    cleaned = _strip_managed_block(existing)

    if enabled:
        chrome_dir.mkdir(parents=True, exist_ok=True)
        block = _orange_user_chrome_css_block()
        css_path.write_text(cleaned + ("\n" if cleaned and not cleaned.endswith("\n") else "") + block, encoding="utf-8")
    elif css_path.exists():
        if cleaned.strip():
            css_path.write_text(cleaned, encoding="utf-8")
        else:
            css_path.unlink(missing_ok=True)


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

    _set_firefox_marking(profile_dir, enabled=enabled)


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
    """Verifica se o processo principal do navegador tem conexão TCP ao proxy local."""
    import psutil

    main_pid = find_main_browser_pid(app)
    if main_pid is None:
        return False
    try:
        for conn in psutil.Process(main_pid).connections(kind="tcp"):
            if not conn.raddr:
                continue
            if conn.raddr.port == local_port and conn.raddr.ip in ("127.0.0.1", "::1"):
                return True
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return False


def default_chrome_user_data_dir(app: AppRule) -> Path | None:
    """Resolve o user-data-dir real do Chrome/Chromium instalado (snap ou pacote).

    O nome da pasta de perfil dentro do user-data-dir varia (``Default``, ``Profile 1``,
    ...), então a checagem usa o ``Local State`` (sempre na raiz do user-data-dir) em vez
    de assumir um nome fixo de subpasta.
    """
    candidates = (
        Path.home() / "snap/chromium/common/chromium",
        Path.home() / "snap/google-chrome/common/config/google-chrome",
        Path.home() / ".config/chromium",
        Path.home() / ".config/google-chrome",
    )
    for path in candidates:
        local_state = path / "Local State"
        if not local_state.is_file():
            continue
        try:
            data = json.loads(local_state.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        if data.get("profile", {}).get("info_cache"):
            return path
    for path in candidates:
        if path.is_dir():
            return path
    return None


def _argb_signed(hex_color: str) -> int:
    """Converte '#rrggbb' no inteiro ARGB signed 32-bit usado pelo Chrome em Local State."""
    rgb = int(hex_color.lstrip("#"), 16)
    value = 0xFF000000 | rgb
    return value - 0x1_0000_0000 if value >= 0x8000_0000 else value


_CHROME_COLOR_BACKUP_NAME = ".proxymgr-color-backup.json"
_CHROME_MARK_FIELDS = ("profile_highlight_color", "profile_color_seed", "is_using_default_avatar", "name")


def set_chrome_profile_marking(user_data_dir: Path, *, enabled: bool) -> None:
    """Marca (ou restaura) a cor de perfil do Chrome/Chromium — melhor esforço, não
    deve impedir o lançamento se o formato do Local State mudar entre versões."""
    local_state_path = user_data_dir / "Local State"
    backup_path = user_data_dir / _CHROME_COLOR_BACKUP_NAME
    try:
        if not local_state_path.is_file():
            return
        data = json.loads(local_state_path.read_text(encoding="utf-8", errors="replace"))
        info_cache = data.get("profile", {}).get("info_cache", {})
        if not info_cache:
            return
        profile_key = "Default" if "Default" in info_cache else next(iter(info_cache))
        entry = info_cache[profile_key]

        if enabled:
            if not backup_path.exists():
                backup = {k: entry.get(k) for k in _CHROME_MARK_FIELDS if k in entry}
                backup_path.write_text(json.dumps(backup), encoding="utf-8")
            entry["profile_highlight_color"] = _argb_signed(PROXY_MARK_COLOR)
            entry["profile_color_seed"] = _argb_signed(PROXY_MARK_COLOR)
            entry["is_using_default_avatar"] = False
            entry["name"] = "Proxy"
        elif backup_path.exists():
            backup = json.loads(backup_path.read_text(encoding="utf-8", errors="replace"))
            for key in _CHROME_MARK_FIELDS:
                if key in backup:
                    entry[key] = backup[key]
            backup_path.unlink(missing_ok=True)

        local_state_path.write_text(json.dumps(data), encoding="utf-8")
    except (OSError, ValueError, KeyError):
        pass


def prepare_browser_proxy(app: AppRule, proxy: ProxySettings, use_proxy: bool) -> None:
    if app.id == "firefox":
        profile = default_firefox_profile_dir()
        if profile is None:
            raise RuntimeError(
                "Perfil do Firefox não encontrado.\n"
                "Abra o Firefox pelo menos uma vez ou configure manualmente em about:preferences."
            )
        set_firefox_proxy(profile, host=LOCAL_HOST, port=LOCAL_PORT, enabled=use_proxy)
    elif app.id == "chrome":
        user_data_dir = default_chrome_user_data_dir(app)
        if user_data_dir is not None:
            set_chrome_profile_marking(user_data_dir, enabled=use_proxy)


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

    if app.id == "chrome" or executable in ("chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        return [*result, f"--proxy-server=http://{LOCAL_HOST}:{LOCAL_PORT}"]

    return result


def is_content_process(cmdline: str) -> bool:
    lowered = cmdline.lower()
    return "-contentproc" in lowered or " --type=" in lowered


def main_browser_command(app: AppRule, cmd: list[str]) -> list[str]:
    if is_content_process(" ".join(cmd)):
        resolved = resolve_browser_command(app).split()
        if resolved:
            return resolved
    resolved = resolve_browser_command(app).split()
    if resolved and cmd:
        return resolved
    return cmd


def browser_proxy_active(app: AppRule, cmdline: str) -> bool | None:
    if app.id == "firefox":
        if not firefox_proxy_active():
            return False
        return browser_connects_local_proxy(app)
    if app.id == "chrome":
        needle = f"--proxy-server=http://{LOCAL_HOST}:{LOCAL_PORT}"
        if needle in cmdline:
            return True
        return browser_connects_local_proxy(app)
    return None
