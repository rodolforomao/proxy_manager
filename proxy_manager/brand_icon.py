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


def _draw_tor_layers(draw: "ImageDraw.ImageDraw", cx: int, cy: int, r: int) -> None:
    """Camadas da cebola do Tor, centradas em (cx, cy) com raio máximo `r`."""
    layers = [
        (196, 181, 253, 255),
        (139, 92, 246, 255),
        (109, 40, 217, 255),
    ]
    step = max(1, r // len(layers))
    for i, color in enumerate(layers):
        rr = r - i * step
        draw.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=color)


def _draw_lightning_bolt(draw: "ImageDraw.ImageDraw", box: tuple[float, float, float, float], color=(250, 204, 21, 255)) -> None:
    """Raio desenhado dentro de `box` (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0

    def pt(fx: float, fy: float) -> tuple[float, float]:
        return (x0 + w * fx, y0 + h * fy)

    points = [
        pt(0.55, 0.15), pt(0.30, 0.58), pt(0.47, 0.58),
        pt(0.42, 0.88), pt(0.72, 0.42), pt(0.53, 0.42), pt(0.58, 0.15),
    ]
    draw.polygon(points, fill=color)


def make_tor_icon(size: int = 64) -> "Image.Image":
    """Cebola do Tor (círculos concêntricos) — bandeja quando a rota Tor está ativa."""
    from PIL import Image, ImageDraw

    bg = (30, 41, 59, 255)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = max(1, size // 16)
    draw.ellipse((pad, pad, size - pad - 1, size - pad - 1), fill=bg)

    cx, cy = size // 2, size // 2
    _draw_tor_layers(draw, cx, cy, size * 3 // 8)

    return img


def make_lightning_icon(size: int = 64, *, active: bool = True) -> "Image.Image":
    """Raio — bandeja quando o modo Rápido (proxy externo) está ativo."""
    from PIL import Image, ImageDraw

    bg = (30, 41, 59, 255)
    bolt = (250, 204, 21, 255) if active else (100, 116, 139, 255)

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = max(1, size // 16)
    draw.ellipse((pad, pad, size - pad - 1, size - pad - 1), fill=bg)
    _draw_lightning_bolt(draw, (pad, pad, size - pad - 1, size - pad - 1), color=bolt)

    return img


STATUS_COLORS = {
    "green":  (74, 222, 128, 255),
    "yellow": (250, 204, 21, 255),
    "red":    (248, 113, 113, 255),
    "grey":   (100, 116, 139, 255),
}


def make_tray_icon(size: int, mode: str, *, status: str = "grey") -> "Image.Image":
    """Ícone da bandeja dividido ao meio: metade esquerda = símbolo do modo
    (cebola do Tor ou raio do Rápido) sobre fundo escuro; metade direita =
    bloco sólido com a cor de status (verde=ok, amarelo=conectando,
    vermelho=erro). Modo 'local'/desligado usa o gateway de sempre."""
    from PIL import Image, ImageDraw

    if mode not in ("tor", "fast"):
        return make_brand_icon(size, proxy_on=status == "green")

    color = STATUS_COLORS.get(status, STATUS_COLORS["grey"])
    bg = (30, 41, 59, 255)

    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    pad = max(1, size // 16)
    radius = max(2, size // 6)
    mid = size // 2

    # Base: bloco inteiro na cor do status (fica visível na metade direita).
    draw.rounded_rectangle((pad, pad, size - pad - 1, size - pad - 1), radius=radius, fill=color)
    # Metade esquerda: fundo escuro com o símbolo do modo, arredondada só à esquerda.
    draw.rounded_rectangle(
        (pad, pad, mid, size - pad - 1),
        radius=radius,
        fill=bg,
        corners=(True, False, True, False),
    )

    cx, cy = (pad + mid) // 2, size // 2
    if mode == "tor":
        r = (mid - pad) // 2 - max(1, size // 20)
        _draw_tor_layers(draw, cx, cy, max(3, r))
    else:
        margin = max(1, size // 10)
        _draw_lightning_bolt(draw, (pad + margin, pad + margin, mid - margin, size - pad - 1 - margin))

    return img


def apply_window_icon(window, *, proxy_on: bool = False) -> None:
    """Define o ícone da janela (barra de tarefas / Alt+Tab), inclusive em `python main.py`."""
    from PIL import ImageTk

    sizes = (16, 32, 48, 64, 128, 256)
    photos = [ImageTk.PhotoImage(make_brand_icon(s, proxy_on=proxy_on)) for s in sizes]
    window.iconphoto(True, *photos)
    window._brand_icon_photos = photos  # noqa: SLF001 — evita GC do Tk
