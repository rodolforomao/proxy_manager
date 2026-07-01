#!/usr/bin/env python3
"""Processo filho dedicado ao pproxy (usado pelo bundle PyInstaller)."""

from __future__ import annotations


def main() -> None:
    from pproxy.server import main as pproxy_main

    pproxy_main()


if __name__ == "__main__":
    main()
