from __future__ import annotations

import threading
from typing import Callable

_tray_available = False
try:
    import pystray
    from PIL import Image, ImageDraw
    _tray_available = True
except ImportError:
    pass


def _make_icon(color: str = "#4ade80") -> "Image.Image":
    from PIL import Image, ImageDraw
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
    draw.ellipse((8, 8, 56, 56), fill=(r, g, b, 255))
    draw.ellipse((24, 24, 40, 40), fill=(0, 0, 0, 180))
    return img


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
            _make_icon("#4ade80" if proxy_on else "#64748b"),
            self._title(),
            menu=self._build_menu(),
        )
        threading.Thread(target=self._icon.run, daemon=True, name="tray").start()

    def update(self, proxy_on: bool) -> None:
        if not self._icon:
            return
        self._proxy_on = proxy_on
        self._icon.icon = _make_icon("#4ade80" if proxy_on else "#64748b")
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
