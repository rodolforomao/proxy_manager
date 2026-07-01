from __future__ import annotations

import threading
from typing import Callable

from proxy_manager.brand_icon import make_brand_icon

_tray_available = False
try:
    import pystray
    from PIL import Image
    _tray_available = True
except ImportError:
    pass


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
        self._proxy_on = False

    def start(self, proxy_on: bool = False) -> None:
        if not _tray_available:
            return
        self._proxy_on = proxy_on
        self._icon = pystray.Icon(
            "proxy-manager",
            make_brand_icon(64, proxy_on=proxy_on),
            self._title(),
            menu=self._build_menu(),
        )
        threading.Thread(target=self._icon.run, daemon=True, name="tray").start()

    def update(self, proxy_on: bool) -> None:
        if not self._icon:
            return
        self._proxy_on = proxy_on
        self._icon.icon = make_brand_icon(64, proxy_on=proxy_on)
        self._icon.title = self._title()
        self._icon.menu = self._build_menu()

    def stop(self) -> None:
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None

    def _title(self) -> str:
        return f"Proxy Manager — {'ativo' if self._proxy_on else 'desligado'}"

    def _build_menu(self) -> "pystray.Menu":
        label_toggle = "Desligar proxy" if self._proxy_on else "Ligar proxy"
        return pystray.Menu(
            pystray.MenuItem("Abrir Proxy Manager", self._on_show, default=True),
            pystray.MenuItem(label_toggle, lambda: self._on_toggle()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Sair", self._quit),
        )

    def _quit(self) -> None:
        self.stop()
        self._on_quit()
