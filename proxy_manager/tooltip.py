from __future__ import annotations

import tkinter as tk
from typing import Any


class Tooltip:
    def __init__(self, widget: Any, text: str = "") -> None:
        self.widget = widget
        self.text = text
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._show, add="+")
        widget.bind("<Leave>", self._hide, add="+")

    def set_text(self, text: str) -> None:
        self.text = text.strip()
        if self._tip and self.text:
            for child in self._tip.winfo_children():
                if isinstance(child, tk.Label):
                    child.configure(text=self.text)

    def _show(self, _event: object = None) -> None:
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
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None
