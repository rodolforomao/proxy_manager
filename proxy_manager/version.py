"""Versão do app — última tag git (gravada no build) ou consulta git em dev."""

from __future__ import annotations

import subprocess
import sys
from functools import lru_cache
from pathlib import Path

_VERSION_FILE = Path(__file__).with_name("_version.txt")
_REPO_ROOT = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def app_version() -> str:
    if _VERSION_FILE.is_file():
        text = _VERSION_FILE.read_text(encoding="utf-8").strip()
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
