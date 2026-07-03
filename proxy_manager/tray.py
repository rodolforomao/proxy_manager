from __future__ import annotations

import threading
from typing import Callable

from proxy_manager.brand_icon import make_tray_icon
from proxy_manager.version import app_version

_tray_available = False
try:
    import pystray
    from PIL import Image
    _tray_available = True
except Exception:
    pass


_MODE_LABELS = {
    "tor": "🧅 Tor",
    "fast": "⚡ Rápido",
    "local": "proxy local",
}

_STATUS_LABELS = {
    "green": "ok",
    "yellow": "conectando",
    "red": "erro",
    "grey": "desligado",
}


def is_available() -> bool:
    return _tray_available


class ProxyTray:
    def __init__(
        self,
        on_show: Callable[[], None],
        on_quit: Callable[[], None],
        on_toggle_proxy: Callable[[], None],
    ) -> None:
        self._on_show = on_show
        self._on_quit = on_quit
        self._on_toggle = on_toggle_proxy
        self._icon: "pystray.Icon | None" = None
        self._mode = "local"
        self._status = "grey"

    def start(self, mode: str = "local", status: str = "grey") -> None:
        if not _tray_available:
            return
        self._mode = mode
        self._status = status
        self._icon = pystray.Icon(
            "proxy-manager",
            make_tray_icon(64, mode, status=status),
            self._title(),
            menu=self._build_menu(),
        )
        threading.Thread(target=self._icon.run, daemon=True, name="tray").start()

    def update(self, mode: str = "local", status: str = "grey") -> None:
        if not self._icon:
            return
        self._mode = mode
        self._status = status
        self._icon.icon = make_tray_icon(64, mode, status=status)
        self._icon.title = self._title()
        self._icon.menu = self._build_menu()

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None

    def _proxy_on(self) -> bool:
        return self._status != "grey"

    def _title(self) -> str:
        ver = app_version()
        if not self._proxy_on():
            return f"Proxy Manager {ver} — desligado"
        mode_label = _MODE_LABELS.get(self._mode, "ativo")
        status_label = _STATUS_LABELS.get(self._status, "")
        return f"Proxy Manager {ver} — {mode_label} ({status_label})"

    def _build_menu(self) -> "pystray.Menu":
        label_toggle = "Desligar proxy" if self._proxy_on() else "Ligar proxy"
        return pystray.Menu(
            pystray.MenuItem("Abrir Proxy Manager", self._on_show, default=True),
            pystray.MenuItem(label_toggle, lambda: self._on_toggle()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", self._quit),
        )

    def _quit(self) -> None:
        self.stop()
        self._on_quit()
