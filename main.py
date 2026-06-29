#!/usr/bin/env python3
"""Proxy Manager - GUI para controlar proxy por aplicativo."""

import atexit
import signal
import sys

from proxy_manager.gui import run


def _shutdown(signum=None, frame=None) -> None:
    try:
        from proxy_manager.local_proxy import stop_watchdog, stop_local_proxy
        stop_watchdog()
        stop_local_proxy()
    except Exception:
        pass
    sys.exit(0)


def main() -> None:
    atexit.register(_shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    run()


if __name__ == "__main__":
    main()
