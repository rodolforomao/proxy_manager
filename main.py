#!/usr/bin/env python3
"""Proxy Manager - GUI para controlar proxy por aplicativo."""

import atexit
import fcntl
import signal
import sys
import warnings
from pathlib import Path

# pproxy usa strings de escape inválidas em Python 3.12+ (ex: '\[')
warnings.filterwarnings("ignore", category=SyntaxWarning, module="pproxy")

from proxy_manager.gui import run

LOCK_FILE = Path.home() / ".config" / "proxy-manager" / "instance.lock"
_lock_handle = None


def _acquire_single_instance() -> bool:
    """Impede duas GUIs simultâneas (causa travamentos e proxy inconsistente)."""
    global _lock_handle
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    handle = open(LOCK_FILE, "w", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False
    handle.write(str(__import__("os").getpid()))
    handle.flush()
    _lock_handle = handle
    return True


def _release_single_instance() -> None:
    global _lock_handle
    if _lock_handle is None:
        return
    try:
        fcntl.flock(_lock_handle.fileno(), fcntl.LOCK_UN)
        _lock_handle.close()
    except OSError:
        pass
    _lock_handle = None
    LOCK_FILE.unlink(missing_ok=True)


def _shutdown(signum=None, frame=None) -> None:
    try:
        from proxy_manager.local_proxy import stop_watchdog, stop_local_proxy
        stop_watchdog()
        stop_local_proxy()
    except Exception:
        pass
    _release_single_instance()
    sys.exit(0)


def main() -> None:
    if not _acquire_single_instance():
        print(
            "Proxy Manager já está em execução.\n"
            "Feche a outra janela antes de abrir de novo.",
            file=sys.stderr,
        )
        sys.exit(1)

    atexit.register(_shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        run()
    finally:
        _release_single_instance()


if __name__ == "__main__":
    main()
