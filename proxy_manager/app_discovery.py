from __future__ import annotations

import configparser
import os
import re
from dataclasses import dataclass
from pathlib import Path

import psutil

from proxy_manager.browser_proxy import is_content_process
from proxy_manager.models import AppRule

_EXEC_FIELD_CODES = re.compile(r"%[fFuUdDnNickvm]")

_NOISE_NAMES = frozenset({
    "bash", "sh", "zsh", "fish", "dash", "su", "sudo", "pkexec", "dbus-run-session",
    "systemd", "systemd-userwork", "dbus-daemon", "dbus-broker", "dbus-launch",
    "kwin_x11", "kwin_wayland", "plasmashell", "gnome-shell", "mutter", "xorg", "Xorg",
    "Xwayland", "wayland", "pipewire", "wireplumber", "pulseaudio", "pipewire-pulse",
    "gmain", "dconf-worker", "pool-spawner", "sd-pam", "at-spi-bus-launcher",
    "at-spi2-registryd", "gvfsd", "gvfs-udisks2-volume-monitor", "xdg-desktop-portal",
    "xdg-document-portal", "xdg-permission-store", "kded5", "kded6", "ksmserver",
    "kwalletd5", "kwalletd6", "nautilus", "tracker-miner-fs", "evolution-alarm-notify",
    "proxy-manager", "gost", "pproxy", "python", "python3", "node", "ruby", "java",
    "sleep", "cat", "grep", "rg", "less", "more", "tail", "head", "watch",
})


@dataclass
class InstalledApp:
    name: str
    command: str
    comment: str = ""


def _strip_exec(exec_str: str) -> str:
    return _EXEC_FIELD_CODES.sub("", exec_str).strip()


def _exec_basename(cmdline: list[str], fallback_name: str) -> str:
    if cmdline:
        token = cmdline[0]
        if token.startswith("env"):
            for part in cmdline[1:]:
                if not part.startswith("-") and "=" not in part:
                    token = part
                    break
        return _resolve_exec_basename(token)
    return fallback_name


_SNAP_WRAPPERS = frozenset({"snap", "snap-confine", "flatpak", "flatpak-bwrap"})


def _resolve_exec_basename(token: str) -> str:
    path = Path(token)
    logical = path.name
    if path.is_file() or path.is_symlink():
        try:
            resolved_name = path.resolve().name
            if resolved_name not in _SNAP_WRAPPERS:
                return resolved_name
        except OSError:
            pass
    return logical


def _exec_aliases(base: str) -> set[str]:
    if not base:
        return set()
    aliases = {base}
    if base.endswith("-bin"):
        aliases.add(base[:-4])
    else:
        aliases.add(f"{base}-bin")
    return aliases


def _desktop_app_dirs() -> list[Path]:
    dirs = [
        Path("/usr/share/applications"),
        Path("/usr/local/share/applications"),
        Path.home() / ".local/share/applications",
        Path("/var/lib/flatpak/exports/share/applications"),
        Path.home() / ".local/share/flatpak/exports/share/applications",
        Path("/var/lib/snapd/desktop/applications"),
    ]
    for part in os.environ.get("XDG_DATA_DIRS", "").split(":"):
        if part:
            dirs.append(Path(part) / "applications")
    seen: set[Path] = set()
    unique: list[Path] = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            unique.append(d)
    return unique


def patterns_for_installed_app(app: InstalledApp) -> list[str]:
    """Gera patterns a partir dos executáveis do .desktop (inclui variantes -bin)."""
    patterns: list[str] = []
    seen: set[str] = set()
    for token in app.command.split():
        if "=" in token or token.startswith("-"):
            continue
        base = _resolve_exec_basename(token).lower()
        for alias in sorted(_exec_aliases(base)):
            if alias not in seen:
                seen.add(alias)
                patterns.append(alias)
    return patterns


def _is_noise_process(name: str, cmdline: str, cmdline_list: list[str]) -> bool:
    if not cmdline_list:
        return True
    if cmdline.startswith("[") or cmdline_list[0].startswith("["):
        return True
    if name.isdigit() or len(name) < 2:
        return True
    lowered_name = name.lower()
    if lowered_name in _NOISE_NAMES:
        return True
    if lowered_name.startswith(("kworker/", "migration/", "rcu_")):
        return True
    if "[kthreadd]" in cmdline or "[ksoftirqd" in cmdline:
        return True
    return False


def is_already_configured(candidate: InstalledApp, apps: list[AppRule]) -> bool:
    exec_base = _exec_basename(candidate.command.split(), candidate.command)
    hay = f"{candidate.name} {candidate.command} {exec_base}".lower()
    for app in apps:
        if candidate.name.lower() == app.name.lower():
            return True
        if app.matches_process(exec_base, candidate.command):
            return True
        if any(p.lower() in hay for p in app.patterns):
            return True
    return False


def scan_installed_apps() -> list[InstalledApp]:
    """Lê arquivos .desktop e retorna apps instalados, ordenados por nome."""
    seen: dict[str, InstalledApp] = {}
    cp = configparser.RawConfigParser()
    for d in _desktop_app_dirs():
        if not d.is_dir():
            continue
        for f in d.glob("*.desktop"):
            try:
                cp.read(str(f), encoding="utf-8")
                if not cp.has_section("Desktop Entry"):
                    continue
                de = dict(cp["Desktop Entry"])
                if de.get("nodisplay", "").lower() == "true":
                    continue
                if de.get("type", "").lower() != "application":
                    continue
                name = de.get("name", "").strip()
                exec_ = _strip_exec(de.get("exec", "").strip())
                if not name or not exec_:
                    continue
                if name not in seen:
                    seen[name] = InstalledApp(
                        name=name,
                        command=exec_,
                        comment=de.get("comment", ""),
                    )
            except Exception:
                pass
            finally:
                cp.clear()
    return sorted(seen.values(), key=lambda a: a.name.lower())


def _index_installed_by_exec(apps: list[InstalledApp]) -> dict[str, InstalledApp]:
    index: dict[str, InstalledApp] = {}
    for app in apps:
        keys: set[str] = set()
        for token in app.command.split():
            if "=" in token or token.startswith("-"):
                continue
            keys.update(_exec_aliases(_resolve_exec_basename(token)))
        for key in keys:
            if key and key not in index:
                index[key] = app
    return index


def scan_running_unconfigured(configured: list[AppRule]) -> list[InstalledApp]:
    """Apps instalados (.desktop) que estão em execução e ainda não estão configurados."""
    installed_index = _index_installed_by_exec(scan_installed_apps())
    running_exec_bases: set[str] = set()

    for proc in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            info = proc.info
            name = info.get("name") or ""
            cmdline_list = info.get("cmdline") or []
            cmdline = " ".join(cmdline_list)
            if not name and not cmdline:
                continue
            if is_content_process(cmdline):
                continue
            if _is_noise_process(name, cmdline, cmdline_list):
                continue
            if any(app.matches_process(name, cmdline) for app in configured if app.enabled):
                continue

            exec_base = _exec_basename(cmdline_list, name)
            if exec_base and exec_base in installed_index:
                running_exec_bases.add(exec_base)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            continue

    results: list[InstalledApp] = []
    seen_names: set[str] = set()
    for exec_base in sorted(running_exec_bases):
        matched = installed_index[exec_base]
        key = matched.name.lower()
        if key in seen_names or is_already_configured(matched, configured):
            continue
        seen_names.add(key)
        results.append(
            InstalledApp(
                name=matched.name,
                command=matched.command,
                comment="Em execução",
            )
        )

    return sorted(results, key=lambda a: a.name.lower())


def list_add_app_candidates(configured: list[AppRule]) -> tuple[list[InstalledApp], list[InstalledApp]]:
    """Retorna (em execução não configurados, instalados não configurados)."""
    running = scan_running_unconfigured(configured)
    running_names = {a.name.lower() for a in running}

    installed: list[InstalledApp] = []
    for app in scan_installed_apps():
        if app.name.lower() in running_names:
            continue
        if is_already_configured(app, configured):
            continue
        installed.append(app)

    return running, installed
