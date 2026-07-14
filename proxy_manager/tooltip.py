from __future__ import annotations

import tkinter as tk
from typing import Any


class Tooltip:
    def __init__(self, widget: Any, text: str = "") -> None:
        self.widget = widget
        self.text = text
        self._tip: tk.Toplevel | None = None
        self._hide_job: str | None = None
        self.bind_extra(widget)

    def bind_extra(self, widget: Any) -> None:
        """Vincula Enter/Leave também em widgets filhos (labels/ícones por cima
        do frame) — sem isso, o tooltip só dispara quando o cursor passa pela
        área do frame que não está coberta por um filho, e "pisca"/não aparece
        ao passar sobre o texto/ícone."""
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text.strip()
        if self._tip and self.text:
            for child in self._tip.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(text=self.text)

    def _show(self, _event: object = None) -> None:
        if self._hide_job is not None:
            self.widget.after_cancel(self._hide_job)
            self._hide_job = None
        if not self.text or self._tip is not None:
            return
        x = self.widget.winfo_rootx() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 4
        self._tip = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tk.Label(
            tw,
            text=self.text,
            justify="left",
            background="#1e293b",
            foreground="#e2e8f0",
            relief="solid",
            borderwidth=1,
            font=("Segoe UI", 10),
            padx=8,
            pady=4,
        ).pack()

    def _hide(self, _event: object = None) -> None:
        """Some depois de um pequeno atraso — evita 'piscar' quando o cursor
        transita entre o frame e um filho (Leave+Enter em sequência rápida)."""
        if self._hide_job is not None:
            self.widget.after_cancel(self._hide_job)

        def _do_hide() -> None:
            self._hide_job = None
            if self._tip is not None:
                self._tip.destroy()
                self._tip = None

        self._hide_job = self.widget.after(120, _do_hide)
