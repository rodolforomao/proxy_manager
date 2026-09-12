from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

from proxy_manager.app_discovery import (
    InstalledApp,
    _desktop_app_dirs,
    _exec_aliases,
    _index_installed_by_exec,
    _resolve_exec_basename,
    patterns_for_installed_app,
    scan_installed_apps,
    scan_running_unconfigured,
)


def test_exec_aliases_mozilla_style():
    assert _exec_aliases("thunderbird") == {"thunderbird", "thunderbird-bin"}
    assert _exec_aliases("thunderbird-bin") == {"thunderbird-bin", "thunderbird"}
    assert _exec_aliases("firefox") == {"firefox", "firefox-bin"}


def test_resolve_exec_basename_ignores_snap_wrapper():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        wrapper = root / "snap"
        wrapper.write_text("#!/bin/sh\n", encoding="utf-8")
        launcher = root / "thunderbird"
        launcher.symlink_to(wrapper)
        assert _resolve_exec_basename(str(launcher)) == "thunderbird"


def test_index_installed_resolves_bin_aliases():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        snap_bin = root / "snap" / "bin"
        snap_bin.mkdir(parents=True)
        real_bin = root / "lib" / "thunderbird-bin"
        real_bin.parent.mkdir(parents=True)
        real_bin.write_text("", encoding="utf-8")
        (snap_bin / "thunderbird").symlink_to(real_bin)

        app = InstalledApp(name="Thunderbird Mail", command=f"{snap_bin / 'thunderbird'} %u")
        index = _index_installed_by_exec([app])

        assert "thunderbird" in index
        assert "thunderbird-bin" in index
        assert index["thunderbird-bin"].name == "Thunderbird Mail"


def test_patterns_for_installed_app_uses_exec_not_display_name():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        snap_bin = root / "snap" / "bin"
        snap_bin.mkdir(parents=True)
        real_bin = root / "lib" / "thunderbird-bin"
        real_bin.parent.mkdir(parents=True)
        real_bin.write_text("", encoding="utf-8")
        (snap_bin / "thunderbird").symlink_to(real_bin)

        app = InstalledApp(name="Thunderbird Mail", command=f"{snap_bin / 'thunderbird'} %u")
        patterns = patterns_for_installed_app(app)

        assert "thunderbird" in patterns
        assert "thunderbird-bin" in patterns
        assert "thunderbirdmail" not in patterns


def test_scan_installed_apps_includes_snap_desktop_dir():
    with tempfile.TemporaryDirectory() as tmp:
        snap_apps = Path(tmp) / "snap" / "applications"
        snap_apps.mkdir(parents=True)
        desktop = snap_apps / "thunderbird_thunderbird.desktop"
        desktop.write_text(
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=Thunderbird Mail\n"
            "Exec=/snap/bin/thunderbird %u\n",
            encoding="utf-8",
        )

        with patch("proxy_manager.app_discovery._desktop_app_dirs", return_value=[snap_apps]):
            apps = scan_installed_apps()

        assert "Thunderbird Mail" in [a.name for a in apps]


def test_scan_running_unconfigured_matches_bin_process():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        snap_bin = root / "snap" / "bin"
        snap_bin.mkdir(parents=True)
        real_bin = root / "lib" / "thunderbird-bin"
        real_bin.parent.mkdir(parents=True)
        real_bin.write_text("", encoding="utf-8")
        (snap_bin / "thunderbird").symlink_to(real_bin)

        installed = [InstalledApp(name="Thunderbird Mail", command=f"{snap_bin / 'thunderbird'} %u")]

        class FakeProc:
            def __init__(self, info):
                self.info = info

        proc_info = {
            "pid": 12345,
            "name": "thunderbird-bin",
            "cmdline": [str(real_bin)],
        }

        with patch("proxy_manager.app_discovery.scan_installed_apps", return_value=installed):
            with patch(
                "proxy_manager.app_discovery.psutil.process_iter",
                return_value=[FakeProc(proc_info)],
            ):
                running = scan_running_unconfigured([])

        assert len(running) == 1
        assert running[0].name == "Thunderbird Mail"
        assert running[0].comment == "Em execução"


def test_desktop_app_dirs_includes_snap_and_xdg():
    with patch.dict(
        os.environ,
        {"XDG_DATA_DIRS": "/custom/share:/var/lib/snapd/desktop"},
        clear=False,
    ):
        paths = [str(d) for d in _desktop_app_dirs()]

    assert "/var/lib/snapd/desktop/applications" in paths
    assert "/custom/share/applications" in paths
