"""Ícone do Proxy Manager — gateway com setas (identificável na barra de tarefas)."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image


def make_brand_icon(size: int = 64, *, proxy_on: bool = True) -> "Image.Image":
    """Desenha um gateway de proxy: cliente → [caixa] → destino."""
    from PIL import Image, ImageDraw

    accent = (74, 222, 128, 255) if proxy_on else (100, 116, 139, 255)
    bg = (30, 41, 59, 255)
    node = (148, 163, 184, 255)

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = max(1, size // 16)
    draw.ellipse((pad, pad, size - pad - 1, size - pad - 1), fill=bg)

    cx, cy = size // 2, size // 2
    box_w = max(7, size * 5 // 16)
    box_h = max(9, size * 7 // 16)
    radius = max(1, size // 20)
    line_h = max(2, size // 14)
    node_r = max(2, size // 14)
    arrow_len = max(3, size // 10)

    bx0, by0 = cx - box_w // 2, cy - box_h // 2
    bx1, by1 = bx0 + box_w, by0 + box_h
    draw.rounded_rectangle((bx0, by0, bx1, by1), radius=radius, fill=accent)

    inner_pad = max(1, size // 24)
    draw.rounded_rectangle(
        (bx0 + inner_pad, by0 + inner_pad, bx1 - inner_pad, by1 - inner_pad),
        radius=max(1, radius - 1),
        fill=bg,
    )

    left_x = pad + node_r + 1
    right_x = size - pad - node_r - 1
    pipe_y0, pipe_y1 = cy - line_h // 2, cy + line_h // 2

    draw.ellipse(
        (left_x - node_r, cy - node_r, left_x + node_r, cy + node_r),
        fill=node,
    )
    draw.ellipse(
        (right_x - node_r, cy - node_r, right_x + node_r, cy + node_r),
        fill=node,
    )

    pipe_left_x0 = left_x + node_r
    pipe_left_x1 = bx0 - arrow_len
    if pipe_left_x1 > pipe_left_x0:
        draw.rectangle((pipe_left_x0, pipe_y0, pipe_left_x1, pipe_y1), fill=accent)

    pipe_right_x0 = bx1 + arrow_len
    pipe_right_x1 = right_x - node_r
    if pipe_right_x1 > pipe_right_x0:
        draw.rectangle((pipe_right_x0, pipe_y0, pipe_right_x1, pipe_y1), fill=accent)

    ah = max(2, line_h)
    if bx0 - arrow_len >= pad:
        draw.polygon(
            [
                (bx0, cy),
                (bx0 - arrow_len, cy - ah),
                (bx0 - arrow_len, cy + ah),
            ],
            fill=accent,
        )
    if bx1 + arrow_len <= size - pad:
        draw.polygon(
            [
                (bx1, cy),
                (bx1 + arrow_len, cy - ah),
                (bx1 + arrow_len, cy + ah),
            ],
            fill=accent,
        )

    return img


def apply_window_icon(window, *, proxy_on: bool = False) -> None:
    """Define o ícone da janela (barra de tarefas / Alt+Tab), inclusive em `python main.py`."""
    from PIL import ImageTk

    sizes = (16, 32, 48, 64, 128, 256)
    photos = [ImageTk.PhotoImage(make_brand_icon(s, proxy_on=proxy_on)) for s in sizes]
    window.iconphoto(True, *photos)
    window._brand_icon_photos = photos  # noqa: SLF001 — evita GC do Tk
