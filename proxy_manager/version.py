"""Versão do app — configurada pelo build.sh ou última tag git em dev."""

from __future__ import annotations

import subprocess
import sys
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _version_file_candidates() -> list[Path]:
    paths: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        paths.append(Path(meipass) / "proxy_manager" / "_version.txt")
    paths.append(Path(__file__).with_name("_version.txt"))
    return paths


@lru_cache(maxsize=1)
def app_version() -> str:
    for path in _version_file_candidates():
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text

    if not getattr(sys, "frozen", False):
        try:
            out = subprocess.run(
                ["git", "describe", "--tags", "--abbrev=0"],
                capture_output=True,
                text=True,
                timeout=2,
                cwd=_REPO_ROOT,
            )
            if out.returncode == 0:
                tag = out.stdout.strip()
                if tag:
                    return tag
        except Exception:
            pass

    from proxy_manager import __version__

    return __version__


def window_title(suffix: str = "") -> str:
    base = f"Proxy Manager {app_version()}"
    return f"{base} — {suffix}" if suffix else base
