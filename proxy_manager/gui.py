from __future__ import annotations

import re
import threading
import time
import uuid
from typing import Callable

import customtkinter as ctk
from tkinter import messagebox, ttk

from proxy_manager.app_icons import FEATURED_APP_IDS, app_icon, app_short_name
from proxy_manager.browser_proxy import browser_proxy_active, is_browser_app, prepare_browser_proxy
from proxy_manager.claude_proxy import (
    ANTHROPIC_API_HOST,
    claude_proxy_active,
    claude_proxy_reachable,
    is_claude_app,
    prepare_claude_proxy,
)
from proxy_manager.config import ConfigStore
from proxy_manager.countries import country_code_from_label, country_label, country_option_labels
from proxy_manager.local_proxy import (
    ensure_local_proxy,
    is_running,
    restart_local_proxy,
    start_local_proxy,
    stop_local_proxy,
    start_watchdog,
    stop_watchdog,
)

try:
    from proxy_manager.notifications import (
        notify_proxy_up,
        notify_proxy_down,
        notify_proxy_error,
        notify_proxy_recovered,
    )
    _NOTIFY_AVAILABLE = True
except ImportError:
    _NOTIFY_AVAILABLE = False
    def notify_proxy_up(route: str) -> None: pass  # noqa: E704
    def notify_proxy_down(reason: str = "") -> None: pass  # noqa: E704
    def notify_proxy_error(reason: str) -> None: pass  # noqa: E704
    def notify_proxy_recovered(route: str) -> None: pass  # noqa: E704

try:
    from proxy_manager.tray import ProxyTray, is_available as tray_available
    _TRAY_AVAILABLE = True
except ImportError:
    _TRAY_AVAILABLE = False
    def tray_available() -> bool: return False  # noqa: E704
    class ProxyTray:  # type: ignore[no-redef]
        def __init__(self, *a, **kw): pass
        def start(self, **kw): pass
        def update(self, **kw): pass
        def stop(self): pass
from proxy_manager.models import LOCAL_PORT, AppRule, ProcessInfo
from proxy_manager.network import AUTO_INTERFACE, interface_choices, interface_tooltip, list_interfaces, resolve_interface_label
from proxy_manager.process_actions import read_process_cmdline, relaunch_process
from proxy_manager.process_cache import ProcessScanner
from proxy_manager.process_monitor import find_process_for_app, summary_counts
from proxy_manager.tooltip import Tooltip
from proxy_manager.proxy_env import has_active_proxy, read_process_proxy_env
from proxy_manager.proxy_health import (
    PublicIpInfo,
    check_proxy_reachable,
    clear_public_ip_cache,
    country_tooltip_text,
    detect_listening_proxy_ports,
    fetch_public_ip_info_direct,
    fetch_public_ip_info_via_proxy,
    format_ip_with_flag,
    is_proxy_mode_verified,
    test_proxy_api,
    wait_for_proxy_verification,
)
from proxy_manager.proxy_sources import (
    AUTO_PROXY_MODE_ICONS,
    AUTO_PROXY_MODE_LABELS,
    DEFAULT_TOR_PORT,
    PAID_PROVIDERS,
    SOURCE_LABELS,
    ProxyCandidate,
    apply_candidate,
    apply_paid_provider,
    apply_tor,
    auto_configure_proxy,
    fetch_free_proxies,
    proxy_source_badge,
    settings_configured,
    test_free_proxies,
    tor_status,
    try_start_tor,
    verify_local_proxy_chain,
)

CATEGORY_LABELS = {
    "ai": "IA / Assistentes",
    "browser": "Navegadores",
    "dev": "Desenvolvimento",
    "social": "Comunicação",
    "media": "Mídia",
    "tools": "Ferramentas",
    "custom": "Personalizado",
}
SECTION_PROXY_ON = "● Proxy ligado"
SECTION_RECENT = "◷ Recentes"


class ProxyManagerApp(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        self.store = ConfigStore()
        self._scanner = ProcessScanner()
        self._refresh_job: str | None = None
        self._process_cache: dict[int, ProcessInfo] = {}
        self._iface_choices = interface_choices()
        self._REFRESH_MS = 20_000
        self._modal_open = False
        self._app_proxy_status: dict[str, ctk.CTkLabel] = {}
        self._app_title_labels: dict[str, ctk.CTkLabel] = {}
        self._app_toggle_vars: dict[str, ctk.BooleanVar] = {}
        self._toggle_syncing: set[str] = set()
        self._free_candidates: list[ProxyCandidate] = []
        self._settings_common_row = 0
        self._proxy_ip_info: PublicIpInfo | None = None
        self._proxy_ip_fetching = False
        self._proxy_ip_updated_at = 0.0
        self._direct_ip_info: PublicIpInfo | None = None
        self._direct_ip_fetching = False
        self._PROXY_IP_TTL = 90.0
        self._auto_config_running = False
        self._proxy_verified = False
        self._pending_reapply_app_ids: list[str] = []
        self._quick_app_tiles: dict[str, dict] = {}
        self._mode_tiles: dict[str, ctk.CTkFrame] = {}
        self._apps_list_layout_key: tuple | None = None
        self._apps_refresh_job: str | None = None
        self._scan_debounce_job: str | None = None
        self._scan_min_interval_pending = 4.0
        self._section_font = ctk.CTkFont(size=14, weight="bold")
        self._card_title_font = ctk.CTkFont(size=15, weight="bold")
        self._card_sub_font = ctk.CTkFont(size=12)
        self._card_status_font = ctk.CTkFont(size=12, weight="bold")
        self._iface_labels = [label for _, label in self._iface_choices]
        self._iface_map = {label: value for value, label in self._iface_choices}
        self._iface_kinds = {
            iface.name: iface.kind for iface in list_interfaces(include_virtual=True)
        }
        self._tray: ProxyTray | None = None
        self._iface_refresh_job: str | None = None
        self._apps_render_gen = 0
        self._apps_render_queue: list[tuple[str, object]] = []
        self._apps_render_row = 0
        self._apps_render_by_app: dict[str, ProcessInfo] = {}
        self._apps_loading_label: ctk.CTkLabel | None = None
        self._APPS_RENDER_BATCH = 3

        self.title("Proxy Manager")
        self.geometry("1100x720")
        self.minsize(900, 600)

        self._build_ui()
        self.after(1, self._refresh_apps_list_impl)
        self.after(50, lambda: self._update_header_from_processes([]))
        self._schedule_refresh()
        self.after(500, lambda: self._request_scan(min_interval=2.0))
        self.after(400, self._fetch_direct_public_ip)
        if not settings_configured(self.store.proxy)[0]:
            self.after(300, self._start_auto_configure)
        elif (
            self.store.proxy.auto_proxy_mode == "fast"
            and self.store.proxy.source == "free"
            and self.store.proxy.upstream_host
        ):
            self.after(600, self._fix_broken_fast_upstream)
        elif (
            self.store.proxy.auto_proxy_mode == "fast"
            and self.store.proxy.source != "direct"
            and is_running()
        ):
            self.after(600, self._fix_broken_fast_upstream)
        elif is_running():
            self.after(800, self._verify_running_proxy_on_startup)

        self._start_iface_refresh()
        self.after(1200, self._start_watchdog)
        if tray_available():
            self.after(500, self._start_tray)

    def _verify_running_proxy_on_startup(self) -> None:
        if self._auto_config_running or not is_running():
            return
        self._auto_config_running = True
        self._proxy_verified = False
        self._set_swap_status("Verificando proxy…")

        def worker() -> None:
            def on_attempt(attempt: int, total: int, detail: str) -> None:
                self.after(
                    0,
                    lambda a=attempt, t=total: self._set_swap_status(
                        f"Verificando IP ({a}/{t})…"
                    ),
                )

            ok, proxy_info, direct_info, msg = wait_for_proxy_verification(
                self.store.proxy,
                on_attempt=on_attempt,
            )
            self.after(
                0,
                lambda: self._on_proxy_activation_verified(
                    ok, proxy_info, direct_info, msg, ""
                ),
            )

        threading.Thread(target=worker, daemon=True).start()

    # ── Watchdog ────────────────────────────────────────────────────────────

    def _start_watchdog(self) -> None:
        if not is_running():
            return

        def on_watchdog_event(ok: bool, msg: str) -> None:
            if ok:
                notify_proxy_recovered(msg)
                self.after(0, lambda m=msg: self.status_label.configure(
                    text=f"Proxy recuperado: {m[:80]}"
                ))
                self.after(0, lambda: self._maybe_fetch_public_ip([], force=True))
            else:
                notify_proxy_error(msg)
                self.after(0, lambda m=msg: self.status_label.configure(
                    text=f"Proxy caiu: {m[:80]}"
                ))
            self.after(0, self._sync_global_switch_label)
            self.after(0, lambda: self._request_scan(min_interval=2.0))

        start_watchdog(self.store.proxy, on_watchdog_event)

    # ── Tray ────────────────────────────────────────────────────────────────

    def _start_tray(self) -> None:
        self._tray = ProxyTray(
            on_show=self._tray_show,
            on_quit=self._tray_quit,
            on_toggle_proxy=self._toggle_global_proxy,
        )
        self._tray.start(proxy_on=is_running() and self._proxy_verified)

    def _tray_show(self) -> None:
        self.after(0, lambda: (self.deiconify(), self.lift(), self.focus_force()))

    def _tray_quit(self) -> None:
        self.after(0, self.on_closing)

    # ── Interface refresh ────────────────────────────────────────────────────

    def _start_iface_refresh(self) -> None:
        self._schedule_iface_refresh()

    def _schedule_iface_refresh(self) -> None:
        if self._iface_refresh_job:
            self.after_cancel(self._iface_refresh_job)
        self._iface_refresh_job = self.after(30_000, self._refresh_iface_choices)

    def _refresh_iface_choices(self) -> None:
        new_choices = interface_choices()
        if new_choices != self._iface_choices:
            self._iface_choices = new_choices
            self._iface_labels = [label for _, label in new_choices]
            self._iface_map = {label: value for value, label in new_choices}
            self._iface_kinds = {
                iface.name: iface.kind for iface in list_interfaces(include_virtual=True)
            }
            self._apps_list_layout_key = None
            self._schedule_refresh_apps_list()
        self._schedule_iface_refresh()

    def _reload_proxy_form_from_store(self) -> None:
        if not hasattr(self, "tabview") or self.tabview.get() != "Configurações":
            return
        p = self.store.proxy
        self.source_var.set(SOURCE_LABELS.get(p.source, SOURCE_LABELS["custom"]))
        self._show_source_panel(p.source)
        self.scheme_var.set(p.scheme)
        self.host_entry.delete(0, "end")
        self.host_entry.insert(0, p.upstream_host)
        self.port_entry.delete(0, "end")
        self.port_entry.insert(0, str(p.upstream_port))
        self.user_entry.delete(0, "end")
        self.user_entry.insert(0, p.username)
        self.pass_entry.delete(0, "end")
        self.pass_entry.insert(0, p.password)
        if hasattr(self, "tor_port_entry"):
            self.tor_port_entry.delete(0, "end")
            self.tor_port_entry.insert(0, str(p.upstream_port or DEFAULT_TOR_PORT))
        if p.source == "free" and p.upstream_host:
            candidate = ProxyCandidate(
                host=p.upstream_host,
                port=p.upstream_port,
                scheme=p.scheme,  # type: ignore[arg-type]
            )
            self._free_candidates = [candidate]
            label = candidate.label()
            self.free_combo.configure(values=[label], state="normal")
            self.free_list_var.set(label)
        if hasattr(self, "auto_mode_var"):
            mode = getattr(p, "auto_proxy_mode", "fast")
            self.auto_mode_var.set(
                AUTO_PROXY_MODE_LABELS.get(mode, AUTO_PROXY_MODE_LABELS["fast"])
            )
        if hasattr(self, "target_country_var"):
            self.target_country_var.set(country_label(getattr(p, "target_country", "")))
        self._sync_auto_mode_header()
        self._update_header_from_processes(self._scanner.cache)

    def _clear_browser_proxies_for_apps(self) -> None:
        for app in self.store.apps:
            if is_claude_app(app):
                try:
                    prepare_claude_proxy(self.store.proxy, use_proxy=False)
                    app.use_proxy = False
                    self.store.update_app(app)
                except Exception:
                    pass
                continue
            if not is_browser_app(app):
                continue
            try:
                prepare_browser_proxy(app, self.store.proxy, use_proxy=False)
            except Exception:
                pass

    def _fix_broken_fast_upstream(self) -> None:
        p = self.store.proxy
        if p.auto_proxy_mode != "fast" or self._auto_config_running:
            return
        if p.source == "direct" and is_running():
            return

        def worker() -> None:
            ok, msg = auto_configure_proxy(p, mode="fast")
            if not ok:
                return
            if is_running():
                restart_local_proxy(p)
            self.store.save()
            self.after(0, self._reload_proxy_form_from_store)
            self.after(0, lambda: self.status_label.configure(text=msg[:120]))
            self.after(0, lambda: self._maybe_fetch_public_ip([], force=True))

        threading.Thread(target=worker, daemon=True).start()

    def _start_auto_configure(self, on_done: Callable[[bool], None] | None = None) -> None:
        if self._auto_config_running:
            return
        if settings_configured(self.store.proxy)[0]:
            on_done and on_done(True)
            return

        self._auto_config_running = True
        self.status_label.configure(text="Configurando proxy automaticamente…")

        def worker() -> None:
            ok, msg = auto_configure_proxy(self.store.proxy)
            self.after(0, lambda: self._on_auto_config_done(ok, msg, on_done))

        threading.Thread(target=worker, daemon=True).start()

    def _on_auto_config_done(
        self,
        ok: bool,
        msg: str,
        on_done: Callable[[bool], None] | None,
    ) -> None:
        self._auto_config_running = False
        if ok:
            self.store.save()
            self._reload_proxy_form_from_store()
            self._sync_auto_mode_header()
            self.status_label.configure(text=msg[:120])
        else:
            self._update_header_from_processes(self._scanner.cache)
        if on_done:
            on_done(ok)
        elif not ok:
            self.status_label.configure(
                text="Configure o proxy em Configurações (auto-config falhou)"
            )

    def _ensure_proxy_configured(self, on_ready: Callable[[], None]) -> None:
        if settings_configured(self.store.proxy)[0]:
            on_ready()
            return

        if self._auto_config_running:
            self.global_proxy_var.set(False)
            self._sync_global_switch_label()
            messagebox.showinfo(
                "Aguarde",
                "Configuração automática em andamento. Tente novamente em alguns segundos.",
            )
            return

        def after_auto(ok: bool) -> None:
            if ok:
                on_ready()
            else:
                self.global_proxy_var.set(False)
                self._sync_global_switch_label()
                _, err = settings_configured(self.store.proxy)
                messagebox.showerror("Proxy externo", err)

        self._start_auto_configure(on_done=after_auto)

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(2, weight=1)

        header = ctk.CTkFrame(self, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        header.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            header,
            text="Proxy Manager",
            font=ctk.CTkFont(size=24, weight="bold"),
        ).grid(row=0, column=0, sticky="w")

        controls = ctk.CTkFrame(header, fg_color="transparent")
        controls.grid(row=1, column=0, sticky="w", pady=(8, 0))

        self.global_proxy_var = ctk.BooleanVar(value=is_running())
        self.global_proxy_switch = ctk.CTkSwitch(
            controls,
            text="PROXY DESLIGADO",
            variable=self.global_proxy_var,
            command=self._toggle_global_proxy,
            font=ctk.CTkFont(size=15, weight="bold"),
            width=200,
        )
        self.global_proxy_switch.pack(side="left", padx=(0, 12))
        self._sync_global_switch_label()

        mode_bar = ctk.CTkFrame(controls, fg_color="transparent")
        mode_bar.pack(side="left")
        self._build_mode_header_buttons(mode_bar)
        self._sync_auto_mode_header()

        country_bar = ctk.CTkFrame(controls, fg_color="transparent")
        country_bar.pack(side="left", padx=(10, 0))
        ctk.CTkLabel(
            country_bar,
            text="País:",
            font=ctk.CTkFont(size=12),
            text_color="#94a3b8",
        ).pack(side="left", padx=(0, 4))
        self.target_country_var = ctk.StringVar(
            value=country_label(getattr(self.store.proxy, "target_country", ""))
        )
        ctk.CTkOptionMenu(
            country_bar,
            values=country_option_labels(),
            variable=self.target_country_var,
            width=190,
            command=self._on_target_country_changed,
        ).pack(side="left")
        Tooltip(
            country_bar,
            "⚡ Rápido: proxy público no país escolhido\n"
            "🧅 Tor: usa saída aleatória (Tor do sistema :9050)\n"
            "Saída Tor por país — em breve",
        )

        self.ip_frame = ctk.CTkFrame(header, fg_color="transparent")
        self.ip_frame.grid(row=0, column=1, sticky="e", padx=12, pady=(0, 4))

        ip_font = ctk.CTkFont(size=13)
        ip_value_font = ctk.CTkFont(size=13, weight="bold")
        flag_font = ctk.CTkFont(family="Noto Color Emoji", size=18)
        self._flag_font = flag_font

        ctk.CTkLabel(
            self.ip_frame,
            text="IP original:",
            font=ip_font,
            text_color="#94a3b8",
        ).grid(row=0, column=0, sticky="e", padx=(0, 6))
        self.direct_ip_label = ctk.CTkLabel(
            self.ip_frame,
            text="…",
            font=ip_value_font,
            text_color="#cbd5e1",
        )
        self.direct_ip_label.grid(row=0, column=1, sticky="e")

        self.direct_country_label = ctk.CTkLabel(
            self.ip_frame,
            text="",
            font=flag_font,
            text_color="#64748b",
        )
        self._direct_country_tooltip = Tooltip(self.direct_country_label)

        self.proxy_ip_title = ctk.CTkLabel(
            self.ip_frame,
            text="IP proxy:",
            font=ip_font,
            text_color="#94a3b8",
        )
        self.proxy_ip_label = ctk.CTkLabel(
            self.ip_frame,
            text="",
            font=ip_value_font,
            text_color="#4ade80",
        )
        self.proxy_country_label = ctk.CTkLabel(
            self.ip_frame,
            text="",
            font=flag_font,
            text_color="#94a3b8",
        )
        self._proxy_country_tooltip = Tooltip(self.proxy_country_label)

        self.status_label = ctk.CTkLabel(
            header,
            text="",
            font=ctk.CTkFont(size=13),
            text_color="#94a3b8",
        )
        self.status_label.grid(row=1, column=1, sticky="e", padx=12, pady=(8, 0))

        self._build_quick_apps_bar()

        self.tabview = ctk.CTkTabview(self)
        self.tabview.grid(row=2, column=0, sticky="nsew", padx=16, pady=(0, 16))
        self.tabview.add("Aplicativos")
        self.tabview.add("Processos ativos")
        self.tabview.add("Configurações")

        self._build_apps_tab()
        self._build_processes_tab()
        self._build_settings_tab()

    def _build_mode_header_buttons(self, parent: ctk.CTkFrame) -> None:
        tips = {
            "fast": "Rápido — sua internet normal, sem Tor",
            "tor": "Tor — mais lento, mais anônimo",
        }
        self._mode_tiles.clear()
        for mode in ("fast", "tor"):
            tile = ctk.CTkFrame(
                parent,
                width=44,
                height=44,
                corner_radius=10,
                fg_color="#334155",
                border_width=2,
                border_color="#475569",
                cursor="hand2",
            )
            tile.pack(side="left", padx=(0, 6))
            tile.pack_propagate(False)

            icon_lbl = ctk.CTkLabel(
                tile,
                text=AUTO_PROXY_MODE_ICONS[mode],
                font=ctk.CTkFont(size=22),
            )
            icon_lbl.place(relx=0.5, rely=0.5, anchor="center")
            Tooltip(tile, tips[mode])

            def bind_click(widget, m: str = mode) -> None:
                widget.bind(
                    "<Button-1>",
                    lambda _e, mode_key=m: self._select_auto_proxy_mode(mode_key),
                )

            for w in (tile, icon_lbl):
                bind_click(w)

            self._mode_tiles[mode] = tile

    def _active_auto_mode_key(self) -> str:
        p = self.store.proxy
        if p.source == "tor":
            return "tor"
        mode = getattr(p, "auto_proxy_mode", "fast")
        if mode in ("fast", "tor"):
            return mode
        return "fast"

    def _sync_auto_mode_header(self) -> None:
        if not self._mode_tiles:
            return
        active = self._active_auto_mode_key()
        verified = self._proxy_verified and is_running()
        testing = self._auto_config_running or (is_running() and not self._proxy_verified)
        for mode, tile in self._mode_tiles.items():
            if mode == active and verified:
                tile.configure(border_color="#4ade80", fg_color="#1e3a2f")
            elif mode == active and testing:
                tile.configure(border_color="#fbbf24", fg_color="#3a3420")
            else:
                tile.configure(border_color="#475569", fg_color="#334155")

    def _already_on_mode(self, mode: str) -> bool:
        p = self.store.proxy
        if not is_running() or not self._proxy_verified:
            return False
        if mode == "tor":
            return p.source == "tor"
        return p.source in ("direct", "free")

    def _set_swap_status(self, text: str) -> None:
        self.status_label.configure(text=text)
        if hasattr(self, "proxy_ip_label") and is_running():
            self.proxy_ip_label.configure(text="…", text_color="#fbbf24")
        self._sync_auto_mode_header()

    def _swap_status_for_mode(self, mode: str, phase: str) -> str:
        labels = {
            "fast": "⚡ rápido",
            "tor": "🧅 Tor",
        }
        name = labels.get(mode, mode)
        if phase == "start":
            return f"Iniciando troca para {name}…"
        if phase == "configure":
            return f"Trocando: configurando {name}…"
        if phase == "restart":
            return "Trocando: reiniciando proxy local…"
        if phase == "test":
            return f"Trocando: testando {name}…"
        if phase == "apps":
            return "Trocando: atualizando apps…"
        return f"Trocando: {name}…"

    def _on_target_country_changed(self, _choice: str) -> None:
        code = country_code_from_label(self.target_country_var.get())
        if code == getattr(self.store.proxy, "target_country", ""):
            return
        self.store.proxy.target_country = code
        self.store.save()
        mode = getattr(self.store.proxy, "auto_proxy_mode", "fast")
        # País no Tor fica guardado no config; reaplica só no modo ⚡ por enquanto.
        # Não troca modo se Tor está ativo como source (independente de auto_proxy_mode).
        if mode == "fast" and self.store.proxy.source != "tor":
            self._select_auto_proxy_mode(mode, force=True)

    def _select_auto_proxy_mode(self, mode: str, *, force: bool = False) -> None:
        if mode not in ("fast", "tor"):
            return
        if self._auto_config_running:
            return
        if not force and self._already_on_mode(mode):
            return

        self.store.proxy.target_country = country_code_from_label(self.target_country_var.get())
        self.store.proxy.auto_proxy_mode = mode  # type: ignore[assignment]
        if hasattr(self, "auto_mode_var"):
            self.auto_mode_var.set(AUTO_PROXY_MODE_LABELS[mode])
        self.store.save()
        self._sync_auto_mode_header()

        clear_public_ip_cache()
        self._proxy_ip_info = None
        self._proxy_ip_updated_at = 0.0
        self._proxy_ip_fetching = False
        _prev_verified = self._proxy_verified
        self._proxy_verified = False
        self._pending_reapply_app_ids = [
            proc.matched_app.id
            for proc in self._scanner.cache
            if proc.matched_app and proc.proxy_active
        ]
        self._update_ip_display()
        self._auto_config_running = True
        self._set_swap_status(self._swap_status_for_mode(mode, "start"))

        def _on_configure_fail(prev_v: bool, msg: str) -> None:
            self._auto_config_running = False
            self._proxy_verified = prev_v
            self._sync_auto_mode_header()
            self._update_header_from_processes(self._scanner.cache)
            self._sync_global_switch_label()
            self._request_scan(min_interval=2.0)
            messagebox.showwarning("Proxy não encontrado", msg)

        def worker() -> None:
            try:
                # Configure first — keep existing proxy alive until we know it works
                self.after(
                    0,
                    lambda: self._set_swap_status(self._swap_status_for_mode(mode, "configure")),
                )

                ok, config_msg = auto_configure_proxy(self.store.proxy, mode=mode)
                if not ok:
                    # Old proxy still running; restore verified state
                    self.after(
                        0,
                        lambda m=config_msg, pv=_prev_verified: _on_configure_fail(pv, m),
                    )
                    return

                # Configure succeeded — now replace the running proxy
                self.after(
                    0,
                    lambda: self._set_swap_status(self._swap_status_for_mode(mode, "restart")),
                )
                start_ok, start_msg = restart_local_proxy(self.store.proxy)
                if not start_ok:
                    self.after(
                        0,
                        lambda m=start_msg: self._on_proxy_activation_verified(
                            False, PublicIpInfo(ip=None), None, m, config_msg
                        ),
                    )
                    return

                if not verify_local_proxy_chain(timeout=12):
                    self.after(
                        0,
                        lambda: self._on_proxy_activation_verified(
                            False,
                            PublicIpInfo(ip=None),
                            None,
                            "Proxy local ligado, mas o tráfego não passou.",
                            config_msg,
                        ),
                    )
                    return

                self.after(
                    0,
                    lambda: self._set_swap_status(self._swap_status_for_mode(mode, "test")),
                )

                def on_attempt(attempt: int, total: int, _detail: str) -> None:
                    if attempt == 1 or attempt == total or attempt % 2 == 0:
                        self.after(
                            0,
                            lambda a=attempt, t=total: self._set_swap_status(
                                f"Testando IP ({a}/{t})…"
                            ),
                        )

                verified, proxy_info, direct_info, verify_msg = wait_for_proxy_verification(
                    self.store.proxy,
                    on_attempt=on_attempt,
                    max_attempts=8,
                )
                if not verified:
                    self.after(
                        0,
                        lambda: self._on_proxy_activation_verified(
                            False, proxy_info, direct_info, verify_msg, config_msg
                        ),
                    )
                    return

                self.after(0, lambda: self._set_swap_status(self._swap_status_for_mode(mode, "apps")))
                self.after(
                    0,
                    lambda: self._on_proxy_activation_verified(
                        True, proxy_info, direct_info, verify_msg, config_msg
                    ),
                )
            except Exception as exc:
                self.after(
                    0,
                    lambda e=exc: self._on_proxy_activation_verified(
                        False, PublicIpInfo(ip=None), None, str(e), ""
                    ),
                )

        threading.Thread(target=worker, daemon=True).start()

    def _reapply_proxy_for_enabled_apps(self, app_ids: list[str] | None = None) -> None:
        if not is_running():
            return

        def worker() -> None:
            targets = set(app_ids) if app_ids else None
            for app in self.store.apps:
                # Claude Code: settings.json é atualizado independente de estar rodando,
                # pois o Claude lê o arquivo ao iniciar — não precisa estar ativo agora.
                if is_claude_app(app):
                    if targets is not None and app.id in targets:
                        # Estava rodando com proxy antes do switch → força enable
                        try:
                            prepare_claude_proxy(self.store.proxy, use_proxy=True)
                            app.use_proxy = True
                            app.enabled = True
                            self.store.update_app(app)
                        except Exception:
                            pass
                    elif app.use_proxy:
                        # Não rodando mas usuário havia habilitado → mantém settings.json sync
                        try:
                            prepare_claude_proxy(self.store.proxy, use_proxy=True)
                            app.enabled = True
                            self.store.update_app(app)
                        except Exception:
                            pass
                    continue

                if not app.command.strip():
                    continue
                if targets is not None and app.id not in targets:
                    continue
                proc = find_process_for_app(
                    app, self.store.apps, self.store.proxy, self._scanner.cache
                )
                if not proc:
                    continue
                if targets is None and not proc.proxy_active:
                    continue
                try:
                    relaunch_process(
                        proc.pid,
                        app=app,
                        proxy=self.store.proxy,
                        use_proxy=True,
                        network_interface=app.network_interface,
                    )
                    app.use_proxy = True
                    app.enabled = True
                    self.store.update_app(app)
                except Exception:
                    continue
            self._pending_reapply_app_ids = []
            self._scanner.invalidate()
            self.after(0, lambda: self._request_scan(min_interval=2.0))

        threading.Thread(target=worker, daemon=True).start()

    def _on_proxy_activation_verified(
        self,
        ok: bool,
        proxy_info: PublicIpInfo,
        direct_info: PublicIpInfo | None,
        verify_msg: str,
        config_msg: str,
    ) -> None:
        self._auto_config_running = False
        if direct_info and direct_info.ip:
            self._direct_ip_info = direct_info

        if ok:
            self._proxy_verified = True
            self.store.proxy.enabled = True
            self.store.save()
            self.global_proxy_var.set(True)
            self._proxy_ip_info = proxy_info
            self._proxy_ip_updated_at = time.time()
            self._proxy_ip_fetching = False
            status = verify_msg[:120]
            if config_msg and config_msg not in status:
                status = f"{verify_msg} — {config_msg}"[:120]
            self._set_swap_status(status)
            notify_proxy_up(verify_msg[:80])
            if self._tray:
                self._tray.update(proxy_on=True)
            self._update_ip_display()
            self._sync_global_switch_label()
            self._reapply_proxy_for_enabled_apps(
                self._pending_reapply_app_ids or None
            )
            self._apps_list_layout_key = None
            self._schedule_refresh_apps_list()
            self.after_idle(self._reload_proxy_form_from_store)
            self._request_scan(min_interval=2.0)
        else:
            self._proxy_verified = False
            self._proxy_ip_info = proxy_info if proxy_info.ip else None
            detail = verify_msg or config_msg or "Falha na verificação."
            self._sync_auto_mode_header()
            self._update_header_from_processes(self._scanner.cache, preserve_status=True)
            self._sync_global_switch_label()
            self._request_scan(min_interval=2.0)
            notify_proxy_error(detail[:80])
            if self._tray:
                self._tray.update(proxy_on=False)
            messagebox.showwarning("Proxy", detail)
            return

        self._sync_auto_mode_header()

    def _on_mode_switch_done(self, ok: bool, msg: str) -> None:
        """Legado — redireciona para verificação por IP."""
        info = PublicIpInfo(ip=None, message=msg)
        self._on_proxy_activation_verified(ok, info, None, msg, "")

    def _build_quick_apps_bar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color="#1e293b", corner_radius=10)
        bar.grid(row=1, column=0, sticky="ew", padx=16, pady=(0, 8))
        bar.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            bar,
            text="Acesso rápido — clique para ligar/parar proxy  ·  botão direito: ir à configuração",
            font=ctk.CTkFont(size=11),
            text_color="#64748b",
        ).grid(row=0, column=0, sticky="w", padx=12, pady=(8, 0))

        tiles = ctk.CTkFrame(bar, fg_color="transparent")
        tiles.grid(row=1, column=0, sticky="ew", padx=8, pady=(4, 10))

        self._quick_app_tiles.clear()
        for app_id in FEATURED_APP_IDS:
            app = self.store.get_app(app_id)
            if app is None:
                continue
            self._build_quick_app_tile(tiles, app)

    def _build_quick_app_tile(self, parent: ctk.CTkFrame, app: AppRule) -> None:
        tile = ctk.CTkFrame(
            parent,
            width=76,
            height=76,
            corner_radius=12,
            fg_color="#334155",
            border_width=2,
            border_color="#475569",
        )
        tile.pack(side="left", padx=5, pady=2)
        tile.pack_propagate(False)

        icon_lbl = ctk.CTkLabel(
            tile,
            text=app_icon(app.id),
            font=ctk.CTkFont(size=26),
        )
        icon_lbl.place(relx=0.5, y=6, anchor="n")

        name_lbl = ctk.CTkLabel(
            tile,
            text=app_short_name(app),
            font=ctk.CTkFont(size=10),
            text_color="#cbd5e1",
        )
        name_lbl.place(relx=0.5, rely=0.88, anchor="s")

        dot = ctk.CTkLabel(
            tile,
            text="●",
            font=ctk.CTkFont(size=14),
            text_color="#64748b",
        )
        dot.place(x=4, y=2, anchor="nw")

        tip = Tooltip(tile, f"{app.name}\nClique: ligar/parar proxy\nBotão direito: configuração")

        def bind_click(widget) -> None:
            widget.bind("<Button-1>", lambda _e, a=app: self._quick_app_click(a))
            widget.bind("<Button-3>", lambda _e, a=app: self._focus_app_in_list(a))

        for w in (tile, icon_lbl, name_lbl, dot):
            bind_click(w)

        self._quick_app_tiles[app.id] = {
            "tile": tile,
            "dot": dot,
            "name": name_lbl,
        }

    def _quick_app_click(self, app: AppRule) -> None:
        self._touch_recent_app(app)
        proc = self._scanner.by_app_id().get(app.id)
        if proc and proc.proxy_active:
            self._stop_proxy(app)
        else:
            self._start_with_proxy(app)

    def _focus_app_in_list(self, app: AppRule) -> None:
        self.tabview.set("Aplicativos")
        self.app_filter_var.set(app.name)
        self._refresh_apps_list()

    def _update_quick_apps_status(self, by_app: dict[str, ProcessInfo]) -> None:
        for app_id, widgets in self._quick_app_tiles.items():
            tile = widgets["tile"]
            dot = widgets["dot"]
            proc = by_app.get(app_id)
            _text, color = self._status_from_proc(proc)
            dot.configure(text_color=color)
            truly_proxied = (
                proc is not None
                and proc.proxy_active
                and self._proxy_verified
                and self.store.proxy.source != "direct"
            )
            if truly_proxied:
                tile.configure(border_color="#4ade80", fg_color="#1e3a2f")
            elif proc:
                tile.configure(border_color="#fbbf24", fg_color="#334155")
            else:
                tile.configure(border_color="#475569", fg_color="#334155")

    def _build_settings_tab(self) -> None:
        tab = self.tabview.tab("Configurações")
        tab.grid_columnconfigure(1, weight=1)

        p = self.store.proxy
        row = 0

        ctk.CTkLabel(
            tab,
            text="Proxy externo",
            font=ctk.CTkFont(size=15, weight="bold"),
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=(12, 4))
        ctk.CTkLabel(
            tab,
            text=f"Escolha a fonte abaixo. O app cria proxy local em 127.0.0.1:{LOCAL_PORT} automaticamente.",
            text_color="#94a3b8",
            font=ctk.CTkFont(size=12),
            wraplength=560,
            justify="left",
        ).grid(row=row + 1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 8))
        row += 2

        ctk.CTkLabel(tab, text="Auto-config:").grid(row=row, column=0, sticky="w", padx=12, pady=6)
        mode = getattr(p, "auto_proxy_mode", "fast")
        self.auto_mode_var = ctk.StringVar(
            value=AUTO_PROXY_MODE_LABELS.get(mode, AUTO_PROXY_MODE_LABELS["fast"])
        )
        ctk.CTkOptionMenu(
            tab,
            values=list(AUTO_PROXY_MODE_LABELS.values()),
            variable=self.auto_mode_var,
        ).grid(row=row, column=1, sticky="ew", padx=12, pady=6)
        row += 1

        ctk.CTkLabel(tab, text="Fonte:").grid(row=row, column=0, sticky="w", padx=12, pady=6)
        self.source_var = ctk.StringVar(value=SOURCE_LABELS.get(p.source, SOURCE_LABELS["custom"]))
        ctk.CTkOptionMenu(
            tab,
            values=list(SOURCE_LABELS.values()),
            variable=self.source_var,
            command=self._on_source_changed,
        ).grid(row=row, column=1, sticky="ew", padx=12, pady=6)
        row += 1

        self.settings_detail = ctk.CTkFrame(tab, fg_color="transparent")
        self.settings_detail.grid(row=row, column=0, columnspan=2, sticky="nsew", padx=4)
        self.settings_detail.grid_columnconfigure(1, weight=1)
        row += 1
        self._settings_common_row = row

        self._build_custom_panel(p)
        self._build_free_panel()
        self._build_paid_panel(p)
        self._build_tor_panel(p)
        self._show_source_panel(p.source)

        ctk.CTkLabel(tab, text="NO_PROXY:").grid(
            row=row, column=0, sticky="nw", padx=12, pady=6
        )
        self.no_proxy_entry = ctk.CTkEntry(tab)
        self.no_proxy_entry.insert(0, p.no_proxy)
        self.no_proxy_entry.grid(row=row, column=1, sticky="ew", padx=12, pady=6)
        row += 1

        ctk.CTkLabel(tab, text="CA extra (SSL):").grid(
            row=row, column=0, sticky="nw", padx=12, pady=6
        )
        self.ca_entry = ctk.CTkEntry(tab, placeholder_text="/caminho/para/ca.pem (opcional)")
        self.ca_entry.insert(0, p.extra_ca_certs)
        self.ca_entry.grid(row=row, column=1, sticky="ew", padx=12, pady=6)
        row += 1

        btn_row = ctk.CTkFrame(tab, fg_color="transparent")
        btn_row.grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=12)
        ctk.CTkButton(btn_row, text="Salvar configurações", command=self._save_proxy_settings).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(
            btn_row,
            text="Testar proxy",
            fg_color="#0f766e",
            hover_color="#0d9488",
            command=self._test_proxy,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row,
            text="Auto-configurar agora",
            fg_color="#1d4ed8",
            hover_color="#2563eb",
            command=self._run_auto_configure_now,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(
            btn_row,
            text="Restaurar presets",
            fg_color="#475569",
            hover_color="#334155",
            command=self._reset_presets,
        ).pack(side="left")
        row += 1

        # ── Perfis ──────────────────────────────────────────────────────────
        ctk.CTkLabel(
            tab,
            text="Perfis de proxy",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).grid(row=row, column=0, columnspan=2, sticky="w", padx=12, pady=(16, 4))
        row += 1

        profile_bar = ctk.CTkFrame(tab, fg_color="transparent")
        profile_bar.grid(row=row, column=0, columnspan=2, sticky="ew", padx=12, pady=4)
        profile_bar.grid_columnconfigure(0, weight=1)

        self._profile_list_var = ctk.StringVar()
        self._profile_combo = ctk.CTkComboBox(
            profile_bar,
            variable=self._profile_list_var,
            values=self._get_profile_names(),
            width=220,
        )
        self._profile_combo.grid(row=0, column=0, sticky="ew", padx=(0, 8))

        ctk.CTkButton(
            profile_bar,
            text="Carregar",
            width=80,
            fg_color="#1d4ed8",
            hover_color="#2563eb",
            command=self._load_selected_profile,
        ).grid(row=0, column=1, padx=(0, 4))
        ctk.CTkButton(
            profile_bar,
            text="Salvar como…",
            width=100,
            fg_color="#0f766e",
            hover_color="#0d9488",
            command=self._save_as_profile,
        ).grid(row=0, column=2, padx=(0, 4))
        ctk.CTkButton(
            profile_bar,
            text="Excluir",
            width=70,
            fg_color="#7f1d1d",
            hover_color="#991b1b",
            command=self._delete_selected_profile,
        ).grid(row=0, column=3)

        row += 1
        info = ctk.CTkTextbox(tab, height=140)
        info.grid(row=row, column=0, columnspan=2, sticky="nsew", padx=12, pady=12)
        tab.grid_rowconfigure(row, weight=1)
        info.insert(
            "1.0",
            "Como funciona:\n\n"
            "• Auto-config: Rápido (proxy público) ou Tor (mais lento, mais anônimo).\n"
            "• Fonte: Personalizado, Gratuito, Pago ou Tor.\n"
            "• Gratuito: busca listas públicas (qualidade variável).\n"
            "• Pago: templates Smartproxy, Bright Data, Oxylabs, etc.\n"
            "• Tor: SOCKS5 em 127.0.0.1:9050 (precisa do serviço tor).\n"
            "• Interruptor PROXY LIGADO no topo; por app use Ligar proxy.\n",
        )
        info.configure(state="disabled")

    # ── Perfis ──────────────────────────────────────────────────────────────

    def _get_profile_names(self) -> list[str]:
        try:
            from proxy_manager.profiles import list_profile_names
            return list_profile_names(self.store)
        except (ImportError, Exception):
            return []

    def _refresh_profile_combo(self) -> None:
        names = self._get_profile_names()
        self._profile_combo.configure(values=names)
        if names and not self._profile_list_var.get():
            self._profile_list_var.set(names[0])

    def _load_selected_profile(self) -> None:
        name = self._profile_list_var.get().strip()
        if not name:
            messagebox.showwarning("Perfis", "Selecione um perfil para carregar.")
            return
        try:
            from proxy_manager.profiles import load_profile
            load_profile(self.store, name)
            self.after_idle(self._reload_proxy_form_from_store)
            self.after_idle(self._sync_global_switch_label)
        except Exception as exc:
            messagebox.showerror("Perfis", f"Erro ao carregar perfil:\n{exc}")

    def _save_as_profile(self) -> None:
        dialog = ctk.CTkInputDialog(text="Nome do perfil:", title="Salvar perfil")
        name = dialog.get_input()
        if not name or not name.strip():
            return
        name = name.strip()
        try:
            self._save_proxy_settings(silent=True)
            from proxy_manager.profiles import save_profile
            save_profile(self.store, name)
            self._refresh_profile_combo()
            self._profile_list_var.set(name)
        except Exception as exc:
            messagebox.showerror("Perfis", f"Erro ao salvar perfil:\n{exc}")

    def _delete_selected_profile(self) -> None:
        name = self._profile_list_var.get().strip()
        if not name:
            messagebox.showwarning("Perfis", "Selecione um perfil para excluir.")
            return
        if not messagebox.askyesno("Perfis", f'Excluir o perfil "{name}"?'):
            return
        try:
            from proxy_manager.profiles import delete_profile
            delete_profile(self.store, name)
            self._refresh_profile_combo()
        except Exception as exc:
            messagebox.showerror("Perfis", f"Erro ao excluir perfil:\n{exc}")

    def _auto_mode_key_from_label(self, label: str) -> str:
        for key, text in AUTO_PROXY_MODE_LABELS.items():
            if text == label:
                return key
        return "fast"

    def _source_key_from_label(self, label: str) -> str:
        for key, text in SOURCE_LABELS.items():
            if text == label:
                return key
        return "custom"

    def _on_source_changed(self, _label: str) -> None:
        key = self._source_key_from_label(self.source_var.get())
        self.store.proxy.source = key  # type: ignore[assignment]
        self._show_source_panel(key)
        if key == "tor":
            self._apply_tor_to_form()

    def _show_source_panel(self, source: str) -> None:
        for frame in (self.custom_panel, self.free_panel, self.paid_panel, self.tor_panel):
            frame.grid_remove()
        if source == "free":
            self.free_panel.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=8, pady=4)
        elif source == "paid":
            self.paid_panel.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=8, pady=4)
        elif source == "tor":
            self.tor_panel.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=8, pady=4)
        else:
            self.custom_panel.grid(row=0, column=0, columnspan=2, sticky="nsew", padx=8, pady=4)

    def _build_custom_panel(self, p) -> None:
        self.custom_panel = ctk.CTkFrame(self.settings_detail, fg_color="transparent")
        self.custom_panel.grid_columnconfigure(1, weight=1)
        r = 0

        ctk.CTkLabel(self.custom_panel, text="Tipo:").grid(row=r, column=0, sticky="w", padx=8, pady=4)
        self.scheme_var = ctk.StringVar(value=p.scheme)
        ctk.CTkOptionMenu(
            self.custom_panel,
            values=["http", "https", "socks5"],
            variable=self.scheme_var,
        ).grid(row=r, column=1, sticky="ew", padx=8, pady=4)
        r += 1

        ctk.CTkLabel(self.custom_panel, text="Host:").grid(row=r, column=0, sticky="w", padx=8, pady=4)
        self.host_entry = ctk.CTkEntry(self.custom_panel, placeholder_text="ex: proxy.meuservidor.com")
        self.host_entry.insert(0, p.upstream_host)
        self.host_entry.grid(row=r, column=1, sticky="ew", padx=8, pady=4)
        r += 1

        ctk.CTkLabel(self.custom_panel, text="Porta:").grid(row=r, column=0, sticky="w", padx=8, pady=4)
        self.port_entry = ctk.CTkEntry(self.custom_panel, width=120)
        self.port_entry.insert(0, str(p.upstream_port))
        self.port_entry.grid(row=r, column=1, sticky="w", padx=8, pady=4)
        r += 1

        ctk.CTkLabel(self.custom_panel, text="Usuário:").grid(row=r, column=0, sticky="w", padx=8, pady=4)
        self.user_entry = ctk.CTkEntry(self.custom_panel, placeholder_text="opcional")
        self.user_entry.insert(0, p.username)
        self.user_entry.grid(row=r, column=1, sticky="ew", padx=8, pady=4)
        r += 1

        ctk.CTkLabel(self.custom_panel, text="Senha:").grid(row=r, column=0, sticky="w", padx=8, pady=4)
        self.pass_entry = ctk.CTkEntry(self.custom_panel, placeholder_text="opcional", show="*")
        self.pass_entry.insert(0, p.password)
        self.pass_entry.grid(row=r, column=1, sticky="ew", padx=8, pady=4)

    def _build_free_panel(self) -> None:
        self.free_panel = ctk.CTkFrame(self.settings_detail, fg_color="transparent")
        self.free_panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            self.free_panel,
            text="Proxies públicos — qualidade e segurança variam. Prefira testar antes de usar.",
            text_color="#fbbf24",
            wraplength=520,
            justify="left",
        ).grid(row=0, column=0, sticky="w", padx=8, pady=(0, 8))

        btn_row = ctk.CTkFrame(self.free_panel, fg_color="transparent")
        btn_row.grid(row=1, column=0, sticky="ew", padx=8)
        ctk.CTkButton(btn_row, text="Buscar proxies", command=self._fetch_free_proxies).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(
            btn_row,
            text="Testar lista",
            fg_color="#0f766e",
            hover_color="#0d9488",
            command=self._test_free_list,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Usar selecionado", command=self._apply_free_selection).pack(
            side="left"
        )

        self.free_status = ctk.CTkLabel(self.free_panel, text="Nenhuma busca feita ainda.", anchor="w")
        self.free_status.grid(row=2, column=0, sticky="ew", padx=8, pady=8)

        self.free_list_var = ctk.StringVar(value="")
        self.free_combo = ctk.CTkComboBox(
            self.free_panel,
            values=["— busque proxies primeiro —"],
            variable=self.free_list_var,
            width=480,
        )
        self.free_combo.grid(row=3, column=0, sticky="ew", padx=8, pady=4)

    def _build_paid_panel(self, p) -> None:
        self.paid_panel = ctk.CTkFrame(self.settings_detail, fg_color="transparent")
        self.paid_panel.grid_columnconfigure(1, weight=1)
        r = 0

        names = [prov.name for prov in PAID_PROVIDERS.values()]
        current = PAID_PROVIDERS.get(p.paid_provider, list(PAID_PROVIDERS.values())[0])

        ctk.CTkLabel(self.paid_panel, text="Provedor:").grid(row=r, column=0, sticky="w", padx=8, pady=4)
        self.paid_provider_var = ctk.StringVar(value=current.name)
        ctk.CTkOptionMenu(
            self.paid_panel,
            values=names,
            variable=self.paid_provider_var,
            command=self._on_paid_provider_changed,
        ).grid(row=r, column=1, sticky="ew", padx=8, pady=4)
        r += 1

        self.paid_notes = ctk.CTkLabel(
            self.paid_panel, text=current.notes, text_color="#94a3b8", wraplength=480, justify="left"
        )
        self.paid_notes.grid(row=r, column=0, columnspan=2, sticky="w", padx=8, pady=4)
        r += 1

        ctk.CTkLabel(self.paid_panel, text="Host:").grid(row=r, column=0, sticky="w", padx=8, pady=4)
        self.paid_host_entry = ctk.CTkEntry(self.paid_panel)
        self.paid_host_entry.insert(0, p.upstream_host or current.host)
        self.paid_host_entry.grid(row=r, column=1, sticky="ew", padx=8, pady=4)
        r += 1

        ctk.CTkLabel(self.paid_panel, text="Porta:").grid(row=r, column=0, sticky="w", padx=8, pady=4)
        self.paid_port_entry = ctk.CTkEntry(self.paid_panel, width=120)
        self.paid_port_entry.insert(0, str(p.upstream_port or current.port))
        self.paid_port_entry.grid(row=r, column=1, sticky="w", padx=8, pady=4)
        r += 1

        ctk.CTkLabel(self.paid_panel, text="Usuário:").grid(row=r, column=0, sticky="w", padx=8, pady=4)
        self.paid_user_entry = ctk.CTkEntry(self.paid_panel, placeholder_text=current.username_hint)
        self.paid_user_entry.insert(0, p.username)
        self.paid_user_entry.grid(row=r, column=1, sticky="ew", padx=8, pady=4)
        r += 1

        ctk.CTkLabel(self.paid_panel, text="Senha:").grid(row=r, column=0, sticky="w", padx=8, pady=4)
        self.paid_pass_entry = ctk.CTkEntry(
            self.paid_panel, placeholder_text=current.password_hint, show="*"
        )
        self.paid_pass_entry.insert(0, p.password)
        self.paid_pass_entry.grid(row=r, column=1, sticky="ew", padx=8, pady=4)
        r += 1

        ctk.CTkButton(
            self.paid_panel, text="Aplicar provedor pago", command=self._apply_paid_selection
        ).grid(row=r, column=0, columnspan=2, sticky="w", padx=8, pady=12)

    def _build_tor_panel(self, p) -> None:
        self.tor_panel = ctk.CTkFrame(self.settings_detail, fg_color="transparent")
        self.tor_panel.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            self.tor_panel,
            text=(
                "Usa SOCKS5 do Tor em 127.0.0.1:9050 (serviço do sistema). "
                "O seletor «País» no topo aplica-se ao modo ⚡ Rápido; "
                "saída Tor por país será testada numa próxima versão."
            ),
            wraplength=520,
            justify="left",
            text_color="#94a3b8",
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=8, pady=(0, 8))

        ctk.CTkLabel(self.tor_panel, text="Porta Tor:").grid(row=1, column=0, sticky="w", padx=8, pady=4)
        port = p.upstream_port if p.source == "tor" else DEFAULT_TOR_PORT
        self.tor_port_entry = ctk.CTkEntry(self.tor_panel, width=100)
        self.tor_port_entry.insert(0, str(port))
        self.tor_port_entry.grid(row=1, column=1, sticky="w", padx=8, pady=4)

        self.tor_status_label = ctk.CTkLabel(self.tor_panel, text="", anchor="w")
        self.tor_status_label.grid(row=2, column=0, columnspan=2, sticky="ew", padx=8, pady=8)
        self.after(0, self._refresh_tor_status)

        btn_row = ctk.CTkFrame(self.tor_panel, fg_color="transparent")
        btn_row.grid(row=3, column=0, columnspan=2, sticky="w", padx=8, pady=4)
        ctk.CTkButton(btn_row, text="Verificar Tor", command=self._refresh_tor_status).pack(
            side="left", padx=(0, 8)
        )
        ctk.CTkButton(
            btn_row,
            text="Iniciar Tor",
            fg_color="#0f766e",
            hover_color="#0d9488",
            command=self._start_tor_service,
        ).pack(side="left", padx=(0, 8))
        ctk.CTkButton(btn_row, text="Usar Tor", command=self._apply_tor_selection).pack(side="left")

    def _paid_provider_id(self) -> str:
        name = self.paid_provider_var.get()
        for pid, prov in PAID_PROVIDERS.items():
            if prov.name == name:
                return pid
        return "custom_paid"

    def _on_paid_provider_changed(self, _name: str) -> None:
        pid = self._paid_provider_id()
        prov = PAID_PROVIDERS[pid]
        self.paid_notes.configure(text=prov.notes)
        if prov.host:
            self.paid_host_entry.delete(0, "end")
            self.paid_host_entry.insert(0, prov.host)
            self.paid_port_entry.delete(0, "end")
            self.paid_port_entry.insert(0, str(prov.port))
        self.paid_user_entry.configure(placeholder_text=prov.username_hint)
        self.paid_pass_entry.configure(placeholder_text=prov.password_hint)

    def _tor_port_value(self) -> int:
        try:
            return int(self.tor_port_entry.get().strip())
        except ValueError:
            return DEFAULT_TOR_PORT

    def _refresh_tor_status(self) -> None:
        ok, msg = tor_status(self._tor_port_value())
        color = "#4ade80" if ok else "#f87171"
        self.tor_status_label.configure(text=msg, text_color=color)

    def _start_tor_service(self) -> None:
        self.tor_status_label.configure(text="Iniciando Tor…", text_color="#fbbf24")

        def worker() -> None:
            ok, msg = try_start_tor(self._tor_port_value())
            self.after(0, lambda o=ok, m=msg: self._on_tor_start_done(o, m))

        threading.Thread(target=worker, daemon=True).start()

    def _on_tor_start_done(self, ok: bool, msg: str) -> None:
        self._refresh_tor_status()
        if ok:
            messagebox.showinfo("Tor", msg)
        else:
            messagebox.showwarning("Tor", msg)

    def _apply_tor_to_form(self) -> None:
        apply_tor(self.store.proxy, self._tor_port_value())
        self.scheme_var.set("socks5")
        self.host_entry.delete(0, "end")
        self.host_entry.insert(0, "127.0.0.1")
        self.port_entry.delete(0, "end")
        self.port_entry.insert(0, str(self._tor_port_value()))

    def _apply_tor_selection(self) -> None:
        self.store.proxy.source = "tor"
        apply_tor(self.store.proxy, self._tor_port_value())
        self.store.save()
        self._refresh_tor_status()
        messagebox.showinfo("Tor", f"Tor configurado: socks5://127.0.0.1:{self._tor_port_value()}")

    def _apply_paid_selection(self) -> None:
        try:
            port = int(self.paid_port_entry.get().strip())
        except ValueError:
            messagebox.showerror("Erro", "Porta inválida.")
            return
        pid = self._paid_provider_id()
        prov = PAID_PROVIDERS[pid]
        self.store.proxy.source = "paid"
        self.store.proxy.paid_provider = pid
        self.store.proxy.scheme = prov.scheme
        self.store.proxy.upstream_host = self.paid_host_entry.get().strip() or prov.host
        self.store.proxy.upstream_port = port
        self.store.proxy.username = self.paid_user_entry.get().strip()
        self.store.proxy.password = self.paid_pass_entry.get()
        self.store.save()
        messagebox.showinfo(
            "Provedor pago",
            f"Configurado: {prov.name}\n{self.store.proxy.upstream_scheme_display}",
        )

    def _fetch_free_proxies(self) -> None:
        self.free_status.configure(text="Buscando proxies públicos...")
        self.free_combo.configure(state="disabled")

        def worker() -> None:
            candidates, msg = fetch_free_proxies()
            self.after(0, lambda: self._on_free_fetched(candidates, msg))

        threading.Thread(target=worker, daemon=True).start()

    def _on_free_fetched(self, candidates: list[ProxyCandidate], msg: str) -> None:
        self._free_candidates = candidates
        self.free_combo.configure(state="normal")
        if not candidates:
            self.free_status.configure(text=msg)
            self.free_combo.configure(values=["— nenhum encontrado —"])
            self.free_list_var.set("— nenhum encontrado —")
            return
        labels = [c.label() for c in candidates[:80]]
        self.free_combo.configure(values=labels)
        self.free_list_var.set(labels[0])
        self.free_status.configure(text=msg)

    def _test_free_list(self) -> None:
        if not self._free_candidates:
            messagebox.showwarning("Gratuito", "Busque proxies primeiro.")
            return
        self.free_status.configure(text="Testando conectividade (pode levar alguns segundos)...")

        def worker() -> None:
            working = test_free_proxies(self._free_candidates, limit=30)
            self.after(0, lambda: self._on_free_tested(working))

        threading.Thread(target=worker, daemon=True).start()

    def _on_free_tested(self, working: list[ProxyCandidate]) -> None:
        self._free_candidates = working
        if not working:
            self.free_status.configure(text="Nenhum proxy respondeu ao teste rápido.")
            return
        labels = [c.label() for c in working]
        self.free_combo.configure(values=labels)
        self.free_list_var.set(labels[0])
        self.free_status.configure(text=f"{len(working)} proxies respondendo.")

    def _apply_free_selection(self) -> None:
        label = self.free_list_var.get()
        chosen: ProxyCandidate | None = None
        for c in self._free_candidates:
            if c.label() == label:
                chosen = c
                break
        if not chosen:
            messagebox.showwarning("Gratuito", "Selecione um proxy da lista.")
            return
        self.store.proxy.source = "free"
        apply_candidate(self.store.proxy, chosen)
        self.scheme_var.set(chosen.scheme)
        self.host_entry.delete(0, "end")
        self.host_entry.insert(0, chosen.host)
        self.port_entry.delete(0, "end")
        self.port_entry.insert(0, str(chosen.port))
        self.user_entry.delete(0, "end")
        self.pass_entry.delete(0, "end")
        self.store.save()
        messagebox.showinfo("Gratuito", f"Proxy selecionado:\n{chosen.label()}")

    def _build_apps_tab(self) -> None:
        tab = self.tabview.tab("Aplicativos")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(1, weight=1)

        toolbar = ctk.CTkFrame(tab, fg_color="transparent")
        toolbar.grid(row=0, column=0, sticky="ew", padx=8, pady=8)
        toolbar.grid_columnconfigure(0, weight=1)

        self.app_filter_var = ctk.StringVar()
        self.app_filter_var.trace_add("write", lambda *_: self._refresh_apps_list())
        ctk.CTkEntry(
            toolbar,
            placeholder_text="Filtrar aplicativos...",
            textvariable=self.app_filter_var,
        ).grid(row=0, column=0, sticky="ew", padx=(0, 8))

        ctk.CTkButton(toolbar, text="+ Adicionar", width=110, command=self._add_app_dialog).grid(
            row=0, column=1, padx=(0, 6)
        )
        ctk.CTkButton(
            toolbar,
            text="Atualizar",
            width=90,
            fg_color="#475569",
            hover_color="#334155",
            command=lambda: self._request_scan(min_interval=0, debounce_ms=0),
        ).grid(row=0, column=2)

        container = ctk.CTkFrame(tab)
        container.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))
        container.grid_columnconfigure(0, weight=1)
        container.grid_rowconfigure(0, weight=1)

        self.apps_scroll = ctk.CTkScrollableFrame(container)
        self.apps_scroll.grid(row=0, column=0, sticky="nsew")
        self.apps_scroll.grid_columnconfigure(0, weight=1)

    def _build_processes_tab(self) -> None:
        tab = self.tabview.tab("Processos ativos")
        tab.grid_columnconfigure(0, weight=1)
        tab.grid_rowconfigure(2, weight=1)

        self.proc_summary = ctk.CTkLabel(tab, text="", anchor="w")
        self.proc_summary.grid(row=0, column=0, sticky="ew", padx=12, pady=(8, 4))

        proc_toolbar = ctk.CTkFrame(tab, fg_color="transparent")
        proc_toolbar.grid(row=1, column=0, sticky="ew", padx=8, pady=4)

        ctk.CTkButton(
            proc_toolbar,
            text="Ativar proxy",
            width=120,
            command=lambda: self._apply_process_proxy(True),
        ).pack(side="left", padx=(4, 6))
        ctk.CTkButton(
            proc_toolbar,
            text="Remover proxy",
            width=120,
            fg_color="#475569",
            hover_color="#334155",
            command=lambda: self._apply_process_proxy(False),
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            proc_toolbar,
            text="Trocar rede…",
            width=120,
            command=self._change_process_network,
        ).pack(side="left", padx=6)
        ctk.CTkButton(
            proc_toolbar,
            text="Atualizar",
            width=90,
            fg_color="#475569",
            hover_color="#334155",
            command=self._refresh_processes,
        ).pack(side="right", padx=4)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure(
            "Treeview",
            background="#1e293b",
            foreground="#e2e8f0",
            fieldbackground="#1e293b",
            borderwidth=0,
            rowheight=28,
        )
        style.configure("Treeview.Heading", background="#334155", foreground="#f8fafc")
        style.map("Treeview", background=[("selected", "#2563eb")])

        tree_frame = ctk.CTkFrame(tab)
        tree_frame.grid(row=2, column=0, sticky="nsew", padx=8, pady=(0, 8))
        tree_frame.grid_columnconfigure(0, weight=1)
        tree_frame.grid_rowconfigure(0, weight=1)

        columns = ("app", "pid", "proxy", "ip_web", "network", "status", "cmd")
        self.proc_tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="browse")
        self.proc_tree.heading("app", text="Aplicativo")
        self.proc_tree.heading("pid", text="PID")
        self.proc_tree.heading("proxy", text="Proxy")
        self.proc_tree.heading("ip_web", text="IP na web")
        self.proc_tree.heading("network", text="Rede")
        self.proc_tree.heading("status", text="Status")
        self.proc_tree.heading("cmd", text="Comando")
        self.proc_tree.column("app", width=120, anchor="w")
        self.proc_tree.column("pid", width=55, anchor="center")
        self.proc_tree.column("proxy", width=50, anchor="center")
        self.proc_tree.column("ip_web", width=110, anchor="center")
        self.proc_tree.column("network", width=110, anchor="w")
        self.proc_tree.column("status", width=150, anchor="w")
        self.proc_tree.column("cmd", width=280, anchor="w")
        self.proc_tree.grid(row=0, column=0, sticky="nsew")

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.proc_tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.proc_tree.configure(yscrollcommand=scrollbar.set)

        self.proc_tree.tag_configure("ok", foreground="#4ade80")
        self.proc_tree.tag_configure("warn", foreground="#fbbf24")
        self.proc_tree.tag_configure("bad", foreground="#f87171")

    def _request_scan(self, *, min_interval: float = 4.0, debounce_ms: int = 350) -> None:
        self._scan_min_interval_pending = min(
            self._scan_min_interval_pending, min_interval
        )
        if debounce_ms <= 0:
            self._flush_scan_request()
            return
        if self._scan_debounce_job is not None:
            self.after_cancel(self._scan_debounce_job)
        self._scan_debounce_job = self.after(debounce_ms, self._flush_scan_request)

    def _flush_scan_request(self) -> None:
        self._scan_debounce_job = None
        min_interval = self._scan_min_interval_pending
        self._scan_min_interval_pending = 4.0
        detect_network = self.tabview.get() == "Processos ativos"
        self._scanner.refresh_async(
            self.store.apps,
            self.store.proxy,
            lambda processes: self.after(0, lambda p=processes: self._on_scan_done(p)),
            detect_network=detect_network,
            min_interval=min_interval,
        )

    def _on_scan_done(self, processes: list[ProcessInfo]) -> None:
        if not self.winfo_exists():
            return
        if self.tabview.get() == "Processos ativos":
            self._apply_process_tree(processes)
        self._update_apps_proxy_status_from(processes)
        self._update_header_from_processes(processes)
        if not self._auto_config_running:
            self._maybe_fetch_public_ip(processes)

    def _fetch_direct_public_ip(self, *, force: bool = False) -> None:
        if self._direct_ip_fetching:
            return
        self._direct_ip_fetching = True
        self._update_ip_display()

        def worker() -> None:
            info = fetch_public_ip_info_direct(force=force)
            self.after(0, lambda: self._on_direct_ip_fetched(info))

        threading.Thread(target=worker, daemon=True).start()

    def _on_direct_ip_fetched(self, info: PublicIpInfo) -> None:
        self._direct_ip_fetching = False
        self._direct_ip_info = info
        if self.winfo_exists():
            self._update_ip_display()

    def _ip_display_for_proxy_active(self) -> str:
        if self._proxy_ip_info and self._proxy_ip_info.ip:
            return format_ip_with_flag(self._proxy_ip_info)
        if self._proxy_ip_fetching:
            return "…"
        return "—"

    def _maybe_fetch_public_ip(
        self,
        processes: list[ProcessInfo],
        *,
        force: bool = False,
    ) -> None:
        del processes
        if self._auto_config_running:
            return
        if not is_running():
            self._proxy_ip_info = None
            self._proxy_ip_updated_at = 0.0
            if self.winfo_exists():
                self._update_ip_display()
            return
        if self._proxy_ip_fetching:
            return
        if (
            not force
            and self._proxy_ip_info
            and self._proxy_ip_info.ip
            and time.time() - self._proxy_ip_updated_at < self._PROXY_IP_TTL
        ):
            return

        self._proxy_ip_fetching = True
        self._update_ip_display()
        proxy = self.store.proxy

        def worker() -> None:
            try:
                info = fetch_public_ip_info_via_proxy(proxy, force=force)
            except Exception as exc:
                info = PublicIpInfo(ip=None, message=str(exc))
            self.after(0, lambda: self._on_public_ip_fetched(info))

        threading.Thread(target=worker, daemon=True).start()

    def _on_public_ip_fetched(self, info: PublicIpInfo) -> None:
        self._proxy_ip_fetching = False
        self._proxy_ip_info = info
        self._proxy_ip_updated_at = time.time()
        if not self.winfo_exists():
            return
        if is_running() and not self._auto_config_running:
            was_verified = self._proxy_verified
            ok, _msg = is_proxy_mode_verified(
                self.store.proxy, info, self._direct_ip_info
            )
            self._proxy_verified = ok
            if was_verified != ok:
                self._apps_list_layout_key = None
                self._schedule_refresh_apps_list()
        self._update_ip_display()
        self._sync_auto_mode_header()
        self._update_apps_proxy_status_from(self._scanner.cache)

    def _set_country_flag(self, label: ctk.CTkLabel, tooltip: Tooltip, info: PublicIpInfo, *, color: str) -> None:
        if info.has_country:
            label.configure(text=info.flag, font=self._flag_font, text_color=color)
            tooltip.set_text(
                country_tooltip_text(country_code=info.country_code, country=info.country)
            )
        else:
            label.configure(text="", text_color=color)
            tooltip.set_text("")

    def _update_ip_display(self) -> None:
        if not hasattr(self, "direct_ip_label"):
            return

        if self._direct_ip_fetching:
            self.direct_ip_label.configure(text="…", text_color="#64748b")
        elif self._direct_ip_info and self._direct_ip_info.ip:
            self.direct_ip_label.configure(
                text=self._direct_ip_info.ip,
                text_color="#cbd5e1",
            )
            if self._direct_ip_info.has_country:
                self._set_country_flag(
                    self.direct_country_label,
                    self._direct_country_tooltip,
                    self._direct_ip_info,
                    color="#64748b",
                )
                self.direct_country_label.grid(row=1, column=0, columnspan=2, sticky="e", pady=(2, 0))
            else:
                self.direct_country_label.grid_remove()
                self._direct_country_tooltip.set_text("")
        else:
            self.direct_ip_label.configure(text="—", text_color="#64748b")
            self.direct_country_label.grid_remove()

        proxy_on = is_running()
        if not proxy_on:
            self.proxy_ip_title.grid_remove()
            self.proxy_ip_label.grid_remove()
            self.proxy_country_label.grid_remove()
            return

        source = proxy_source_badge(self.store.proxy)
        self.proxy_ip_title.configure(text=f"{source}:")
        self.proxy_ip_title.grid(row=0, column=2, sticky="e", padx=(16, 6))
        self.proxy_ip_label.grid(row=0, column=3, sticky="e")

        if self._proxy_ip_fetching or self._auto_config_running or not self._proxy_verified:
            self.proxy_ip_label.configure(text="…", text_color="#fbbf24")
            self.proxy_country_label.grid_remove()
            return

        proxy_info = self._proxy_ip_info
        if not proxy_info or not proxy_info.ip:
            self.proxy_ip_label.configure(text="…", text_color="#fbbf24")
            self.proxy_country_label.grid_remove()
            if not self._proxy_ip_fetching and not self._auto_config_running:
                self.after(200, lambda: self._maybe_fetch_public_ip([], force=True))
            return

        direct_ip = self._direct_ip_info.ip if self._direct_ip_info else None
        if self.store.proxy.source == "direct":
            color = "#60a5fa"
        elif direct_ip and proxy_info.ip == direct_ip:
            color = "#f87171"
        else:
            color = "#4ade80"

        self.proxy_ip_label.configure(text=proxy_info.ip, text_color=color)
        if proxy_info.has_country:
            self._set_country_flag(
                self.proxy_country_label,
                self._proxy_country_tooltip,
                proxy_info,
                color=color,
            )
            self.proxy_country_label.grid(row=1, column=2, columnspan=2, sticky="e", pady=(2, 0))
        else:
            self.proxy_country_label.grid_remove()
            self._proxy_country_tooltip.set_text("")

    def _refresh_all(self) -> None:
        self._request_scan(min_interval=0, debounce_ms=0)

    def _schedule_refresh(self) -> None:
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
        self._refresh_job = self.after(self._REFRESH_MS, self._auto_refresh)

    def _auto_refresh(self) -> None:
        if self._modal_open:
            self._schedule_refresh()
            return
        current_tab = self.tabview.get()
        if current_tab in ("Processos ativos", "Aplicativos"):
            self._request_scan()
        self._schedule_refresh()

    def _update_header_from_processes(
        self, processes: list[ProcessInfo], *, preserve_status: bool = False
    ) -> None:
        p = self.store.proxy
        local = "ligado" if is_running() else "desligado"
        route = p.upstream_scheme_display if p.upstream_host else "configure proxy externo"
        source = SOURCE_LABELS.get(p.source, p.source)
        with_proxy = sum(1 for proc in processes if proc.proxy_active)
        if not preserve_status and not self._auto_config_running:
            self.status_label.configure(
                text=(
                    f"[{source}] Local {local} ({LOCAL_PORT}) → {route}"
                    f"  |  {with_proxy} apps com proxy"
                )
            )
        self._update_ip_display()
        if hasattr(self, "global_proxy_var"):
            self.global_proxy_var.set(is_running())
            self._sync_global_switch_label()
        self._sync_auto_mode_header()
        if is_running() and self._proxy_verified:
            self._maybe_fetch_public_ip(processes, force=False)

    def _status_from_proc(self, proc: ProcessInfo | None) -> tuple[str, str]:
        if proc and proc.proxy_active:
            if self._auto_config_running or not self._proxy_verified:
                return "◐ Verificando proxy…", "#fbbf24"
            if self.store.proxy.source == "direct":
                return "○ Direto (sem proxy externo)", "#94a3b8"
            ip = self._ip_display_for_proxy_active()
            if ip and ip != "—" and ip != "…":
                return f"● Proxy ativo\nIP web: {ip}", "#4ade80"
            if self._proxy_ip_fetching:
                return "● Proxy ativo\nIP web: …", "#fbbf24"
            return "● Proxy ativo", "#4ade80"
        if proc:
            return "○ Rodando sem proxy", "#94a3b8"
        return "○ Parado", "#64748b"

    def _touch_recent_app(self, app: AppRule) -> None:
        before = list(self.store.recent_app_ids)
        self.store.touch_recent_app(app.id)
        if before != self.store.recent_app_ids:
            self._apps_list_layout_key = None
            self._schedule_refresh_apps_list()

    def _apps_layout_signature(
        self, by_app: dict[str, ProcessInfo]
    ) -> tuple[frozenset[str], tuple[str, ...], str]:
        proxy_active = frozenset(
            aid for aid, proc in by_app.items() if proc.proxy_active and self._proxy_verified
        )
        filt = self.app_filter_var.get().strip().lower()
        return (proxy_active, tuple(self.store.recent_app_ids), filt)

    def _sort_apps_for_display(
        self,
        apps: list[AppRule],
        by_app: dict[str, ProcessInfo],
    ) -> list[tuple[str, list[AppRule]]]:
        recent_order = {aid: i for i, aid in enumerate(self.store.recent_app_ids)}

        def sort_key(app: AppRule) -> tuple:
            proc = by_app.get(app.id)
            proxy_active = self._app_has_verified_proxy(app.id, by_app)
            recent_rank = recent_order.get(app.id, 9999)
            return (
                0 if proxy_active else (1 if recent_rank < 9999 else 2),
                recent_rank,
                app.category,
                app.name.lower(),
            )

        sorted_apps = sorted(apps, key=sort_key)
        filt = self.app_filter_var.get().strip()
        if filt:
            return [("", sorted_apps)]

        proxy_on: list[AppRule] = []
        recent: list[AppRule] = []
        rest: list[AppRule] = []

        for app in sorted_apps:
            proc = by_app.get(app.id)
            if self._app_has_verified_proxy(app.id, by_app):
                proxy_on.append(app)
            elif app.id in recent_order:
                recent.append(app)
            else:
                rest.append(app)

        sections: list[tuple[str, list[AppRule]]] = []
        if proxy_on:
            sections.append((SECTION_PROXY_ON, proxy_on))
        if recent:
            sections.append((SECTION_RECENT, recent))

        by_category: dict[str, list[AppRule]] = {}
        for app in rest:
            by_category.setdefault(app.category, []).append(app)
        for category in sorted(by_category):
            sections.append(
                (CATEGORY_LABELS.get(category, category.title()), by_category[category])
            )
        return sections

    def _app_has_verified_proxy(self, app_id: str, by_app: dict[str, ProcessInfo]) -> bool:
        if not self._proxy_verified:
            return False
        proc = by_app.get(app_id)
        return bool(proc and proc.proxy_active)

    def _sync_app_toggle(
        self, app_id: str, proxy_active: bool, *, persist: bool = False
    ) -> None:
        toggle_var = self._app_toggle_vars.get(app_id)
        if toggle_var is not None and toggle_var.get() != proxy_active:
            self._toggle_syncing.add(app_id)
            toggle_var.set(proxy_active)
            self._toggle_syncing.discard(app_id)

        if persist:
            app = self.store.get_app(app_id)
            if app and app.enabled != proxy_active:
                app.enabled = proxy_active
                self.store.update_app(app)

    def _update_app_card_runtime(self, by_app: dict[str, ProcessInfo]) -> None:
        for app_id, label in list(self._app_proxy_status.items()):
            try:
                if not label.winfo_exists():
                    continue
            except Exception:
                continue
            text, color = self._status_from_proc(by_app.get(app_id))
            label.configure(text=text, text_color=color)

        for app_id, title in list(self._app_title_labels.items()):
            try:
                if not title.winfo_exists():
                    continue
            except Exception:
                continue
            app = self.store.get_app(app_id)
            if not app:
                continue
            proc = by_app.get(app_id)
            pid_part = f"  ·  PID {proc.pid}" if proc else ""
            proxy_active = self._app_has_verified_proxy(app_id, by_app)
            title.configure(
                text=f"{app_icon(app.id)}  {app.name}{pid_part}",
                text_color="#f8fafc" if proxy_active else "#64748b",
            )
            self._sync_app_toggle(app_id, proxy_active, persist=True)

    def _update_apps_proxy_status_from(self, processes: list[ProcessInfo]) -> None:
        by_app = {p.matched_app.id: p for p in processes if p.matched_app}
        layout_key = self._apps_layout_signature(by_app)
        if layout_key != self._apps_list_layout_key:
            if self._auto_config_running:
                self._update_app_card_runtime(by_app)
                self._update_quick_apps_status(by_app)
                return
            self._schedule_refresh_apps_list()
            return
        self._update_app_card_runtime(by_app)
        self._update_quick_apps_status(by_app)

    def _update_apps_proxy_status(self) -> None:
        self._update_apps_proxy_status_from(self._scanner.cache)

    def _schedule_refresh_apps_list(self) -> None:
        if self._apps_refresh_job is not None:
            self.after_cancel(self._apps_refresh_job)
        self._apps_refresh_job = self.after(400, self._refresh_apps_list_impl)

    def _refresh_apps_list(self) -> None:
        self._apps_list_layout_key = None
        self._schedule_refresh_apps_list()

    def _clear_apps_scroll(self) -> None:
        self._app_proxy_status.clear()
        self._app_title_labels.clear()
        self._app_toggle_vars.clear()
        for child in list(self.apps_scroll.winfo_children()):
            try:
                child.destroy()
            except Exception:
                pass

    def _refresh_apps_list_impl(self) -> None:
        self._apps_refresh_job = None
        self._apps_render_gen += 1
        gen = self._apps_render_gen
        self._clear_apps_scroll()

        filt = self.app_filter_var.get().strip().lower()
        apps = list(self.store.apps)
        if filt:
            apps = [
                a
                for a in apps
                if filt in a.name.lower()
                or filt in a.category.lower()
                or any(filt in p.lower() for p in a.patterns)
            ]

        by_app = self._scanner.by_app_id()
        self._apps_list_layout_key = self._apps_layout_signature(by_app)
        sections = self._sort_apps_for_display(apps, by_app)

        queue: list[tuple[str, object]] = []
        for section_label, section_apps in sections:
            if section_label:
                queue.append(("section", section_label))
            for app in section_apps:
                queue.append(("app", app))

        self._apps_render_queue = queue
        self._apps_render_row = 0
        self._apps_render_by_app = by_app

        if queue:
            self._apps_loading_label = ctk.CTkLabel(
                self.apps_scroll,
                text="Carregando aplicativos…",
                font=self._card_sub_font,
                text_color="#64748b",
            )
            self._apps_loading_label.grid(row=0, column=0, sticky="w", padx=8, pady=8)
            self.after(1, lambda: self._render_apps_chunk(gen))

    def _render_apps_chunk(self, gen: int) -> None:
        if gen != self._apps_render_gen or not self.winfo_exists():
            return

        if self._apps_loading_label is not None:
            try:
                self._apps_loading_label.destroy()
            except Exception:
                pass
            self._apps_loading_label = None

        rendered = 0
        while self._apps_render_queue and rendered < self._APPS_RENDER_BATCH:
            kind, payload = self._apps_render_queue.pop(0)
            if kind == "section":
                ctk.CTkLabel(
                    self.apps_scroll,
                    text=str(payload),
                    font=self._section_font,
                    text_color="#94a3b8",
                ).grid(row=self._apps_render_row, column=0, sticky="w", padx=4, pady=(12, 4))
            else:
                self._render_app_card(
                    payload,  # type: ignore[arg-type]
                    self._apps_render_row,
                    self._apps_render_by_app,
                )
            self._apps_render_row += 1
            rendered += 1

        if self._apps_render_queue:
            self.after(1, lambda g=gen: self._render_apps_chunk(g))

    def _render_app_card(self, app: AppRule, row: int, by_app: dict[str, ProcessInfo]) -> None:
        card = ctk.CTkFrame(self.apps_scroll, corner_radius=8)
        card.grid(row=row, column=0, sticky="ew", padx=4, pady=4)
        card.grid_columnconfigure(1, weight=1)

        proc = by_app.get(app.id)
        proxy_active = self._app_has_verified_proxy(app.id, by_app)
        enabled_var = ctk.BooleanVar(value=proxy_active)
        self._app_toggle_vars[app.id] = enabled_var

        def on_enabled_change() -> None:
            if app.id in self._toggle_syncing:
                return
            want = enabled_var.get()
            if want:
                app.enabled = True
                self.store.update_app(app)
                self._touch_recent_app(app)
                if app.command.strip():
                    self._start_with_proxy(app)
                else:
                    self._sync_app_toggle(app.id, False, persist=True)
                    messagebox.showinfo(
                        "Sem comando",
                        f"{app.name} não tem comando de lançamento.\n"
                        "Use «Ligar proxy» ou configure um comando em Editar.",
                    )
                return

            if not want:
                app.enabled = False
                self.store.update_app(app)
                self._apps_list_layout_key = None
                self._stop_proxy(app, revert_toggle=enabled_var)
                self._schedule_refresh_apps_list()

        ctk.CTkSwitch(
            card,
            text="",
            width=46,
            variable=enabled_var,
            command=on_enabled_change,
        ).grid(row=0, column=0, rowspan=2, padx=(12, 8), pady=12)

        title_color = "#f8fafc" if proxy_active else "#64748b"
        pid_part = f"  ·  PID {proc.pid}" if proc else ""
        title_label = ctk.CTkLabel(
            card,
            text=f"{app_icon(app.id)}  {app.name}{pid_part}",
            font=self._card_title_font,
            text_color=title_color,
        )
        title_label.grid(row=0, column=1, sticky="w", pady=(10, 0))
        self._app_title_labels[app.id] = title_label

        patterns_txt = ", ".join(app.patterns[:4])
        if app.notes:
            sub = f"{patterns_txt} — {app.notes}"
        else:
            sub = patterns_txt
        ctk.CTkLabel(
            card,
            text=sub,
            font=self._card_sub_font,
            text_color="#94a3b8",
        ).grid(row=1, column=1, sticky="w", pady=(0, 10))

        status_txt, status_color = self._status_from_proc(proc)

        status_label = ctk.CTkLabel(
            card,
            text=status_txt,
            font=self._card_status_font,
            text_color=status_color,
            width=175,
            anchor="w",
            justify="left",
        )
        status_label.grid(row=0, column=2, rowspan=2, padx=8, pady=12)
        self._app_proxy_status[app.id] = status_label

        self._render_iface_selector(card, app, row=0, col=3)

        action_values: list[str] = []
        if app.command.strip():
            action_values.append("Ligar proxy")
        action_values.extend(["Parar proxy", "Editar"])
        if app.category == "custom":
            action_values.append("Remover")

        def on_action(choice: str) -> None:
            if choice == "Ligar proxy":
                self._start_with_proxy(app)
            elif choice == "Parar proxy":
                self._stop_proxy(app)
            elif choice == "Editar":
                self._edit_app_dialog(app)
            elif choice == "Remover":
                self._remove_app(app)

        ctk.CTkOptionMenu(
            card,
            values=action_values,
            command=on_action,
            width=110,
        ).grid(row=0, column=4, rowspan=2, sticky="e", padx=(4, 12), pady=12)

    _IFACE_ICONS = {AUTO_INTERFACE: "⚡", "wifi": "📶", "ethernet": "🔌", "other": "◎"}

    def _render_iface_selector(self, parent: ctk.CTkFrame, app: AppRule, *, row: int, col: int) -> None:
        frame = ctk.CTkFrame(parent, fg_color="transparent")
        frame.grid(row=row, column=col, rowspan=2, padx=6, pady=6, sticky="ns")

        tiles: dict[str, ctk.CTkFrame] = {}

        def select(name: str) -> None:
            app.network_interface = name
            self.store.update_app(app)
            for n, t in tiles.items():
                active = n == app.network_interface
                t.configure(
                    border_color="#4ade80" if active else "#475569",
                    fg_color="#1e3a2f" if active else "#334155",
                )

        kind_count: dict[str, int] = {}
        for name, _ in self._iface_choices:
            if name == AUTO_INTERFACE:
                continue
            kind_count[self._iface_kinds.get(name, "other")] = (
                kind_count.get(self._iface_kinds.get(name, "other"), 0) + 1
            )

        kind_idx: dict[str, int] = {}
        for tile_col, (name, _label) in enumerate(self._iface_choices):
            if name == AUTO_INTERFACE:
                icon = self._IFACE_ICONS[AUTO_INTERFACE]
                sub = "Auto"
                tip = "Usa a interface padrão do sistema"
            else:
                kind = self._iface_kinds.get(name, "other")
                icon = self._IFACE_ICONS.get(kind, "◎")
                kind_idx[kind] = kind_idx.get(kind, 0) + 1
                sub = str(kind_idx[kind]) if kind_count.get(kind, 1) > 1 else ""
                tip = interface_tooltip(name)

            tile = ctk.CTkFrame(
                frame,
                width=48,
                height=52,
                corner_radius=8,
                fg_color="#334155",
                border_width=2,
                border_color="#475569",
                cursor="hand2",
            )
            tile.grid(row=0, column=tile_col, padx=2)
            tile.grid_propagate(False)

            icon_lbl = ctk.CTkLabel(tile, text=icon, font=ctk.CTkFont(size=20))
            icon_lbl.place(relx=0.5, y=4, anchor="n")

            sub_lbl = ctk.CTkLabel(tile, text=sub, font=ctk.CTkFont(size=9), text_color="#94a3b8")
            sub_lbl.place(relx=0.5, rely=1.0, anchor="s", y=-3)

            Tooltip(tile, tip)

            def _bind(w: object, n: str = name) -> None:
                w.bind("<Button-1>", lambda _e: select(n))  # type: ignore[union-attr]

            for w in (tile, icon_lbl, sub_lbl):
                _bind(w)

            tiles[name] = tile

        for n, t in tiles.items():
            active = n == app.network_interface
            t.configure(
                border_color="#4ade80" if active else "#475569",
                fg_color="#1e3a2f" if active else "#334155",
            )

    def _build_iface_tiles_widget(
        self, parent: ctk.CTkBaseClass, default_iface: str
    ) -> tuple[ctk.CTkFrame, "Callable[[], str]"]:
        """Build icon-tile network selector for dialogs. Returns (frame, get_selected_fn)."""
        selected: list[str] = [default_iface]
        tiles: dict[str, ctk.CTkFrame] = {}
        frame = ctk.CTkFrame(parent, fg_color="transparent")

        def select(name: str) -> None:
            selected[0] = name
            for n, t in tiles.items():
                active = n == selected[0]
                t.configure(
                    border_color="#4ade80" if active else "#475569",
                    fg_color="#1e3a2f" if active else "#334155",
                )

        kind_count: dict[str, int] = {}
        for name, _ in self._iface_choices:
            if name == AUTO_INTERFACE:
                continue
            kind_count[self._iface_kinds.get(name, "other")] = (
                kind_count.get(self._iface_kinds.get(name, "other"), 0) + 1
            )

        kind_idx: dict[str, int] = {}
        for tile_col, (name, _label) in enumerate(self._iface_choices):
            if name == AUTO_INTERFACE:
                icon = self._IFACE_ICONS[AUTO_INTERFACE]
                sub = "Auto"
                tip = "Usa a interface padrão do sistema"
            else:
                kind = self._iface_kinds.get(name, "other")
                icon = self._IFACE_ICONS.get(kind, "◎")
                kind_idx[kind] = kind_idx.get(kind, 0) + 1
                sub = str(kind_idx[kind]) if kind_count.get(kind, 1) > 1 else ""
                tip = interface_tooltip(name)

            tile = ctk.CTkFrame(
                frame,
                width=54,
                height=58,
                corner_radius=8,
                fg_color="#334155",
                border_width=2,
                border_color="#475569",
                cursor="hand2",
            )
            tile.grid(row=0, column=tile_col, padx=4)
            tile.grid_propagate(False)

            icon_lbl = ctk.CTkLabel(tile, text=icon, font=ctk.CTkFont(size=22))
            icon_lbl.place(relx=0.5, y=5, anchor="n")

            sub_lbl = ctk.CTkLabel(tile, text=sub, font=ctk.CTkFont(size=10), text_color="#94a3b8")
            sub_lbl.place(relx=0.5, rely=1.0, anchor="s", y=-4)

            Tooltip(tile, tip)

            def _bind(w: object, n: str = name) -> None:
                w.bind("<Button-1>", lambda _e: select(n))  # type: ignore[union-attr]

            for w in (tile, icon_lbl, sub_lbl):
                _bind(w)

            tiles[name] = tile

        select(default_iface)
        return frame, lambda: selected[0]

    def _apply_process_tree(self, processes: list[ProcessInfo]) -> None:
        if not hasattr(self, "proc_tree") or not self.proc_tree.winfo_exists():
            return
        selection = self.proc_tree.selection()
        selected_pid = int(selection[0]) if selection else None
        counts = summary_counts(processes, self.store.apps)

        self.proc_summary.configure(
            text=(
                f"Apps rodando: {counts['running']}  |  "
                f"Com proxy: {counts['with_proxy']}  |  "
                f"Sem proxy: {counts['without_proxy']}"
            )
        )

        self._process_cache.clear()
        existing = set(self.proc_tree.get_children())
        new_ids: set[str] = set()
        iface_lbl = dict(self._iface_choices)

        for proc in processes:
            iid = str(proc.pid)
            new_ids.add(iid)
            self._process_cache[proc.pid] = proc
            tag = self._process_tag(proc)
            iface_key = proc.network_interface or "auto"
            net_label = iface_lbl.get(iface_key, iface_key)
            ip_web = self._ip_display_for_proxy_active() if proc.proxy_active else "—"
            values = (
                proc.matched_app.name if proc.matched_app else "?",
                proc.pid,
                "Sim" if proc.proxy_active else "Não",
                ip_web,
                net_label,
                proc.status,
                proc.cmdline,
            )
            if iid in existing:
                try:
                    if self.proc_tree.item(iid, "values") != values:
                        self.proc_tree.item(iid, values=values, tags=(tag,))
                except Exception:
                    self.proc_tree.insert("", "end", iid=iid, values=values, tags=(tag,))
            else:
                self.proc_tree.insert("", "end", iid=iid, values=values, tags=(tag,))

        for iid in existing - new_ids:
            try:
                if self.proc_tree.exists(iid):
                    self.proc_tree.delete(iid)
            except Exception:
                pass

        if selected_pid and str(selected_pid) in new_ids:
            try:
                self.proc_tree.selection_set(str(selected_pid))
            except Exception:
                pass

    def _refresh_processes(self) -> None:
        self._request_scan(min_interval=0, debounce_ms=0)

    def _process_tag(self, proc: ProcessInfo) -> str:
        if proc.matched_app is None:
            return "warn"
        if "✗" in proc.status:
            return "bad"
        if "✓" in proc.status:
            return "ok"
        return "warn"

    def _test_proxy(self) -> None:
        self._save_proxy_settings(silent=True)
        self.status_label.configure(text="Testando proxy…")
        proxy = self.store.proxy

        def worker() -> None:
            ok, msg = test_proxy_api(proxy)
            self.after(0, lambda o=ok, m=msg: self._on_test_proxy_done(o, m))

        threading.Thread(target=worker, daemon=True).start()

    def _on_test_proxy_done(self, ok: bool, msg: str) -> None:
        self._update_header_from_processes(self._scanner.cache)
        if ok:
            messagebox.showinfo("Teste de proxy", msg)
        else:
            ports = detect_listening_proxy_ports()
            extra = f"\n\nPortas abertas em 127.0.0.1: {ports}" if ports else ""
            messagebox.showerror("Teste de proxy", f"{msg}{extra}")

    def _save_proxy_settings(self, silent: bool = False) -> None:
        if not self._apply_settings_from_form():
            return
        self._sync_auto_mode_header()
        self._request_scan(min_interval=0, debounce_ms=0)
        if not silent:
            messagebox.showinfo("Salvo", "Configurações salvas.")

    def _sync_global_switch_label(self) -> None:
        if is_running() and self._proxy_verified:
            self.global_proxy_switch.configure(text="PROXY LIGADO", text_color="#4ade80")
        elif is_running():
            self.global_proxy_switch.configure(text="PROXY TESTANDO", text_color="#fbbf24")
        else:
            self.global_proxy_switch.configure(text="PROXY DESLIGADO", text_color="#94a3b8")

    def _activate_proxy_with_verification(self, start_msg: str) -> None:
        self._auto_config_running = True
        self._proxy_verified = False
        self._set_swap_status("Iniciando…")

        def worker() -> None:
            self._verify_proxy_chain_worker(start_msg)

        threading.Thread(target=worker, daemon=True).start()

    def _verify_proxy_chain_worker(self, start_msg: str) -> None:
        if not verify_local_proxy_chain(timeout=12):
            self.after(
                0,
                lambda: self._on_proxy_activation_verified(
                    False,
                    PublicIpInfo(ip=None),
                    None,
                    "Proxy local ligado, mas o tráfego não passou.",
                    start_msg,
                ),
            )
            return

        def on_attempt(attempt: int, total: int, _detail: str) -> None:
            if attempt == 1 or attempt == total or attempt % 2 == 0:
                self.after(
                    0,
                    lambda a=attempt, t=total: self._set_swap_status(
                        f"Testando IP ({a}/{t})…"
                    ),
                )

        verified, proxy_info, direct_info, verify_msg = wait_for_proxy_verification(
            self.store.proxy,
            on_attempt=on_attempt,
            max_attempts=8,
        )
        self.after(
            0,
            lambda: self._on_proxy_activation_verified(
                verified, proxy_info, direct_info, verify_msg, start_msg
            ),
        )

    def _start_proxy_in_background(self, *, restart: bool) -> None:
        self._auto_config_running = True
        self._proxy_verified = False
        self._set_swap_status("Iniciando…")

        def worker() -> None:
            if restart:
                ok, start_msg = restart_local_proxy(self.store.proxy)
            else:
                ok, start_msg = start_local_proxy(self.store.proxy, wait_seconds=8.0)
            if not ok:
                self.after(
                    0,
                    lambda m=start_msg: self._on_proxy_activation_verified(
                        False, PublicIpInfo(ip=None), None, m, start_msg
                    ),
                )
                return
            clear_public_ip_cache()
            self._verify_proxy_chain_worker(start_msg)

        threading.Thread(target=worker, daemon=True).start()

    def _stop_proxy_in_background(self) -> None:
        self._auto_config_running = True
        self._proxy_verified = False
        self._set_swap_status("Desligando proxy…")

        def worker() -> None:
            ok, msg = stop_local_proxy()
            self._clear_browser_proxies_for_apps()
            self.after(0, lambda: self._finish_global_proxy_off(ok, msg))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_global_proxy_off(self, ok: bool, msg: str) -> None:
        self._auto_config_running = False
        self.store.proxy.enabled = False
        self._proxy_verified = False
        self.store.save()
        clear_public_ip_cache()
        self._proxy_ip_info = None
        self._proxy_ip_updated_at = 0.0
        self._update_ip_display()
        self._sync_global_switch_label()
        self._sync_auto_mode_header()
        self._apps_list_layout_key = None
        self._schedule_refresh_apps_list()
        self._request_scan(min_interval=2.0)
        notify_proxy_down()
        if self._tray:
            self._tray.update(proxy_on=False)
        if not ok:
            messagebox.showwarning("Proxy", msg)

    def _toggle_global_proxy(self) -> None:
        if self.global_proxy_var.get():
            def enable() -> None:
                self._save_proxy_settings(silent=True)
                ok, msg = settings_configured(self.store.proxy)
                if not ok:
                    self.global_proxy_var.set(False)
                    self._sync_global_switch_label()
                    messagebox.showerror("Não foi possível ligar", msg)
                    return
                clear_public_ip_cache()
                self._proxy_ip_info = None
                self._proxy_ip_updated_at = 0.0
                self._start_proxy_in_background(restart=is_running())

            if settings_configured(self.store.proxy)[0]:
                enable()
            else:
                self._ensure_proxy_configured(enable)
            return
        self._stop_proxy_in_background()

    def _apply_settings_from_form(self) -> bool:
        source = self._source_key_from_label(self.source_var.get())
        self.store.proxy.source = source  # type: ignore[assignment]

        if source == "paid":
            try:
                port = int(self.paid_port_entry.get().strip())
            except ValueError:
                messagebox.showerror("Erro", "Porta inválida.")
                return False
            pid = self._paid_provider_id()
            prov = PAID_PROVIDERS[pid]
            self.store.proxy.paid_provider = pid
            self.store.proxy.scheme = prov.scheme
            self.store.proxy.upstream_host = self.paid_host_entry.get().strip() or prov.host
            self.store.proxy.upstream_port = port
            self.store.proxy.username = self.paid_user_entry.get().strip()
            self.store.proxy.password = self.paid_pass_entry.get()
        elif source == "tor":
            try:
                port = int(self.tor_port_entry.get().strip())
            except ValueError:
                messagebox.showerror("Erro", "Porta Tor inválida.")
                return False
            apply_tor(self.store.proxy, port)
        else:
            try:
                port = int(self.port_entry.get().strip())
            except ValueError:
                messagebox.showerror("Erro", "Porta inválida.")
                return False
            self.store.proxy.scheme = self.scheme_var.get()  # type: ignore[assignment]
            self.store.proxy.upstream_host = self.host_entry.get().strip()
            self.store.proxy.upstream_port = port
            self.store.proxy.username = self.user_entry.get().strip()
            self.store.proxy.password = self.pass_entry.get()

        self.store.proxy.auto_proxy_mode = self._auto_mode_key_from_label(  # type: ignore[assignment]
            self.auto_mode_var.get()
        )
        if hasattr(self, "target_country_var"):
            self.store.proxy.target_country = country_code_from_label(self.target_country_var.get())
        self.store.proxy.no_proxy = self.no_proxy_entry.get().strip()
        self.store.proxy.extra_ca_certs = self.ca_entry.get().strip()
        self.store.save()
        return True

    def _run_auto_configure_now(self) -> None:
        if not self._apply_settings_from_form():
            return
        if self._auto_config_running:
            messagebox.showinfo("Aguarde", "Configuração automática em andamento.")
            return

        self._auto_config_running = True
        self.status_label.configure(text="Configurando proxy automaticamente…")

        def worker() -> None:
            ok, msg = auto_configure_proxy(self.store.proxy)
            self.after(0, lambda: self._on_auto_config_done(ok, msg, None))

        threading.Thread(target=worker, daemon=True).start()

    def _reset_presets(self) -> None:
        if messagebox.askyesno("Confirmar", "Restaurar lista padrão de aplicativos?"):
            self.store.reset_presets()
            self._refresh_apps_list()
            self._refresh_all()

    def _ensure_proxy_for_launch(self) -> list[str] | None:
        if not self._apply_settings_from_form():
            return None
        ok, msg = settings_configured(self.store.proxy)
        if not ok:
            if self._auto_config_running:
                messagebox.showinfo(
                    "Aguarde",
                    "Configuração automática em andamento. Tente novamente em alguns segundos.",
                )
            else:
                messagebox.showerror("Proxy externo", msg)
            return None

        if self.store.proxy.source == "direct":
            messagebox.showwarning(
                "Sem proxy externo",
                "O modo atual é Direto — nenhum proxy externo está ativo.\n\n"
                "Aguarde a busca automática encontrar um proxy ou configure\n"
                "um proxy externo na aba Configurações.",
            )
            return None

        if not self._proxy_verified:
            messagebox.showwarning(
                "Proxy não verificado",
                "O proxy ainda está sendo testado.\n\n"
                "Aguarde o indicador ficar verde (● PROXY LIGADO) antes\n"
                "de ligar apps com proxy.",
            )
            return None

        ok, msg = ensure_local_proxy(self.store.proxy)
        if not ok:
            messagebox.showerror("Proxy", msg)
            self.global_proxy_var.set(False)
            self._sync_global_switch_label()
            return None

        self.store.proxy.enabled = True
        self.store.save()
        self.global_proxy_var.set(True)
        self._sync_global_switch_label()
        return []

    def _apply_claude_proxy(self, app: AppRule, *, use_proxy: bool) -> None:
        """Configura ~/.claude/settings.json — método oficial para rede bloqueada."""
        if self._ensure_proxy_for_launch() is None and use_proxy:
            self._sync_app_toggle(app.id, False, persist=True)
            return

        try:
            prepare_claude_proxy(self.store.proxy, use_proxy)
        except OSError as exc:
            self._sync_app_toggle(app.id, False, persist=True)
            messagebox.showerror("Claude Code", f"Não foi possível gravar ~/.claude/settings.json:\n{exc}")
            return

        app.use_proxy = use_proxy
        app.enabled = use_proxy
        self.store.update_app(app)
        if use_proxy:
            self._touch_recent_app(app)
        self._scanner.invalidate()
        self._request_scan(min_interval=0, debounce_ms=0)

        running = find_process_for_app(
            app, self.store.apps, self.store.proxy, self._scanner.cache
        )
        proxy_url = self.store.proxy.local_url

        if use_proxy:
            restart_hint = (
                "Feche a sessão atual (Ctrl+D ou /exit) e abra de novo no terminal:\n  claude"
                if running
                else "Abra um terminal e execute:\n  claude"
            )
            self._set_swap_status(f"Testando {ANTHROPIC_API_HOST}…")

            def _test_and_show(hint: str = restart_hint, url: str = proxy_url) -> None:
                ok, reach_msg = claude_proxy_reachable(timeout=8.0)

                def _show() -> None:
                    if ok:
                        messagebox.showinfo(
                            "Claude Code — pronto",
                            f"Proxy gravado em ~/.claude/settings.json.\n\n"
                            f"Proxy local: {url}\n"
                            f"Teste: ✓ {reach_msg}\n\n"
                            f"{hint}",
                        )
                    else:
                        messagebox.showwarning(
                            "Claude Code — verificar",
                            f"Proxy gravado em ~/.claude/settings.json.\n\n"
                            f"Proxy local: {url}\n"
                            f"Teste: ✗ {reach_msg}\n\n"
                            "Verifique se o proxy externo está funcionando e tente novamente.\n\n"
                            f"{hint}",
                        )
                    self._sync_global_switch_label()

                self.after(0, _show)

            threading.Thread(target=_test_and_show, daemon=True).start()
        else:
            messagebox.showinfo(
                "Claude Code",
                "Proxy removido de ~/.claude/settings.json.\n\n"
                + (
                    "Reinicie o Claude Code no terminal para aplicar."
                    if running
                    else "Inicie o Claude Code normalmente quando quiser."
                ),
            )

    def _start_with_proxy(self, app: AppRule) -> None:
        if not app.command.strip():
            messagebox.showwarning("Aviso", "Nenhum comando configurado para este app.")
            return

        self._touch_recent_app(app)

        if is_claude_app(app):
            self._apply_claude_proxy(app, use_proxy=True)
            return

        running = find_process_for_app(
            app, self.store.apps, self.store.proxy, self._scanner.cache
        )
        if running:
            if running.proxy_active:
                messagebox.showinfo("Proxy ativo", f"{app.name} já está rodando com proxy (PID {running.pid}).")
                return
            if not messagebox.askyesno(
                "Reiniciar",
                f"{app.name} está rodando sem proxy (PID {running.pid}).\n\nReiniciar com proxy?",
            ):
                self._sync_app_toggle(app.id, False, persist=True)
                return
            if self._ensure_proxy_for_launch() is None:
                self._sync_app_toggle(app.id, False, persist=True)
                return
            try:
                result = relaunch_process(
                    running.pid,
                    app=app,
                    proxy=self.store.proxy,
                    use_proxy=True,
                    network_interface=app.network_interface,
                )
                app.use_proxy = True
                app.enabled = True
                self.store.update_app(app)
                self._scanner.invalidate()
                self._request_scan(min_interval=0, debounce_ms=0)
                messagebox.showinfo(
                    "Concluído",
                    f"{app.name} reiniciado com proxy.\nNovo PID: {result.new_pid}\n{self.store.proxy.display_url}",
                )
            except Exception as exc:
                self._sync_app_toggle(app.id, False, persist=True)
                messagebox.showerror("Erro", str(exc))
            return

        self._launch_app(app, use_proxy=True)

    def _stop_proxy(self, app: AppRule, *, revert_toggle: ctk.BooleanVar | None = None) -> None:
        if is_claude_app(app):
            if revert_toggle is not None:
                if not messagebox.askyesno(
                    "Parar proxy",
                    "Remover proxy do Claude Code (~/.claude/settings.json)?",
                ):
                    self._toggle_syncing.add(app.id)
                    revert_toggle.set(True)
                    self._toggle_syncing.discard(app.id)
                    app.enabled = True
                    self.store.update_app(app)
                    return
            self._apply_claude_proxy(app, use_proxy=False)
            return

        running = find_process_for_app(
            app, self.store.apps, self.store.proxy, self._scanner.cache
        )

        if running and running.proxy_active:
            if not messagebox.askyesno(
                "Parar proxy",
                f"{app.name} está com proxy (PID {running.pid}).\n\n"
                "Reiniciar sem proxy? (o app continua, só remove o proxy)",
            ):
                if revert_toggle is not None:
                    self._toggle_syncing.add(app.id)
                    revert_toggle.set(True)
                    self._toggle_syncing.discard(app.id)
                    app.enabled = True
                    self.store.update_app(app)
                return
            try:
                result = relaunch_process(
                    running.pid,
                    app=app,
                    proxy=self.store.proxy,
                    use_proxy=False,
                    network_interface=app.network_interface,
                )
                app.use_proxy = False
                app.enabled = False
                self.store.update_app(app)
                messagebox.showinfo(
                    "Concluído",
                    f"Proxy removido de {app.name}.\nNovo PID: {result.new_pid}",
                )
                self._refresh_apps_list()
                self._refresh_all()
            except Exception as exc:
                messagebox.showerror("Erro", str(exc))
            return

        app.use_proxy = False
        app.enabled = False
        self.store.update_app(app)
        self._request_scan(min_interval=0, debounce_ms=0)
        if running:
            messagebox.showinfo(
                "Proxy desativado",
                f"{app.name} já rodava sem proxy (PID {running.pid}).\nRegra atualizada.",
            )
        else:
            messagebox.showinfo(
                "Proxy desativado",
                f"Proxy desligado para {app.name}.\nInicie o app normalmente quando quiser.",
            )

    def _launch_app(self, app: AppRule, *, use_proxy: bool) -> None:
        warnings: list[str] = []
        if use_proxy:
            warnings = self._ensure_proxy_for_launch()
            if warnings is None:
                self._sync_app_toggle(app.id, False, persist=True)
                return

        try:
            from proxy_manager.launcher import launch_command

            command = app.command.strip()
            if not command:
                messagebox.showwarning("Aviso", "Nenhum comando configurado para este app.")
                return
            proc = launch_command(
                command.split(),
                proxy=self.store.proxy,
                use_proxy=use_proxy,
                network_interface=app.network_interface,
                app=app,
            )
            app.use_proxy = use_proxy
            self.store.update_app(app)
            if use_proxy:
                self._touch_recent_app(app)

            net = resolve_interface_label(app.network_interface)
            saved_warnings = list(warnings)

            def post_launch() -> None:
                time.sleep(0.25)
                try:
                    cmdline = " ".join(read_process_cmdline(proc.pid))
                    special = claude_proxy_active(app, cmdline)
                    if special is None and is_browser_app(app):
                        special = browser_proxy_active(app, cmdline)
                    proxy_active = (
                        special
                        if special is not None
                        else has_active_proxy(read_process_proxy_env(proc.pid))
                    )
                except Exception:
                    proxy_active = use_proxy
                if use_proxy:
                    app.enabled = proxy_active
                    self.store.update_app(app)
                self.after(
                    0,
                    lambda: self._on_launch_done(
                        app, proc.pid, use_proxy, proxy_active, saved_warnings, net
                    ),
                )

            threading.Thread(target=post_launch, daemon=True).start()
            self._scanner.invalidate()
            self.after(400, lambda: self._request_scan(min_interval=1.0))
        except FileNotFoundError:
            if use_proxy:
                self._sync_app_toggle(app.id, False, persist=True)
            messagebox.showerror("Erro", f"Comando não encontrado: {app.command}")
        except RuntimeError as exc:
            if use_proxy:
                self._sync_app_toggle(app.id, False, persist=True)
            messagebox.showerror("Rede", str(exc))
        except OSError as exc:
            if use_proxy:
                self._sync_app_toggle(app.id, False, persist=True)
            messagebox.showerror("Erro", str(exc))

    def _on_launch_done(
        self,
        app: AppRule,
        pid: int,
        use_proxy: bool,
        proxy_active: bool,
        warnings: list[str],
        net: str,
    ) -> None:
        if not self.winfo_exists():
            return
        if use_proxy:
            self._sync_app_toggle(app.id, proxy_active)
            if proxy_active:
                title = "Proxy aplicado"
                body = (
                    f"{app.name} iniciado (PID {pid}).\n"
                    f"Proxy: {self.store.proxy.display_url}\n"
                    f"Rede: {net}"
                )
            else:
                title = "Aviso"
                body = (
                    f"{app.name} iniciado (PID {pid}), mas proxy NÃO detectado no processo.\n"
                    f"Esperado: {self.store.proxy.display_url}"
                )
            if warnings:
                body += "\n\n" + "\n".join(warnings)
            (messagebox.showwarning if not proxy_active else messagebox.showinfo)(title, body)
        else:
            messagebox.showinfo(
                "Iniciado", f"{app.name} iniciado (PID {pid}).\nSem proxy | Rede: {net}"
            )

    def _get_selected_process(self) -> ProcessInfo | None:
        selection = self.proc_tree.selection()
        if not selection:
            messagebox.showinfo("Selecione", "Selecione um processo na tabela.")
            return None
        pid = int(selection[0])
        proc = self._process_cache.get(pid)
        if proc is None:
            messagebox.showerror("Erro", "Processo não encontrado. Atualize a lista.")
        return proc

    def _apply_process_proxy(self, use_proxy: bool) -> None:
        proc = self._get_selected_process()
        if proc is None:
            return

        action = "ativar proxy" if use_proxy else "remover proxy"
        if not messagebox.askyesno(
            "Reiniciar processo",
            f"Para {action} no PID {proc.pid}, o processo será encerrado e reiniciado.\n\n"
            f"App: {proc.matched_app.name if proc.matched_app else proc.name}\n"
            f"Comando: {proc.cmdline}\n\nContinuar?",
        ):
            return

        iface = AUTO_INTERFACE
        if proc.matched_app:
            iface = proc.matched_app.network_interface

        if use_proxy:
            if self._ensure_proxy_for_launch() is None:
                return

        try:
            result = relaunch_process(
                proc.pid,
                app=proc.matched_app,
                proxy=self.store.proxy,
                use_proxy=use_proxy,
                network_interface=iface,
            )
            if proc.matched_app:
                proc.matched_app.use_proxy = use_proxy
                self.store.update_app(proc.matched_app)
            messagebox.showinfo(
                "Concluído",
                f"Processo reiniciado.\nNovo PID: {result.new_pid}\nProxy: {'sim' if use_proxy else 'não'}",
            )
            self._refresh_apps_list()
            self._refresh_all()
        except Exception as exc:
            messagebox.showerror("Erro", str(exc))

    def _change_process_network(self) -> None:
        proc = self._get_selected_process()
        if proc is None:
            return

        dialog = ctk.CTkToplevel(self)
        dialog.title("Trocar rede do processo")
        dialog.geometry("460x220")
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(
            dialog,
            text=f"PID {proc.pid} — {proc.matched_app.name if proc.matched_app else proc.name}",
            font=ctk.CTkFont(size=14, weight="bold"),
        ).pack(pady=(16, 8))

        ctk.CTkLabel(
            dialog,
            text="O processo será reiniciado na rede escolhida.",
            text_color="#94a3b8",
        ).pack(pady=(0, 12))

        iface_values = [label for _, label in self._iface_choices]
        iface_map = {label: value for value, label in self._iface_choices}
        default_iface = proc.matched_app.network_interface if proc.matched_app else AUTO_INTERFACE
        current_label = next(
            (label for value, label in self._iface_choices if value == default_iface),
            iface_values[0],
        )
        iface_var = ctk.StringVar(value=current_label)
        ctk.CTkOptionMenu(dialog, values=iface_values, variable=iface_var, width=360).pack(pady=8)

        def apply() -> None:
            iface = iface_map.get(iface_var.get(), AUTO_INTERFACE)
            use_proxy = proc.matched_app.use_proxy if proc.matched_app else proc.proxy_active
            dialog.destroy()
            try:
                result = relaunch_process(
                    proc.pid,
                    app=proc.matched_app,
                    proxy=self.store.proxy,
                    use_proxy=use_proxy,
                    network_interface=iface,
                )
                if proc.matched_app:
                    proc.matched_app.network_interface = iface
                    self.store.update_app(proc.matched_app)
                messagebox.showinfo(
                    "Concluído",
                    f"Processo reiniciado na rede {resolve_interface_label(iface)}.\nNovo PID: {result.new_pid}",
                )
                self._refresh_apps_list()
                self._refresh_all()
            except Exception as exc:
                messagebox.showerror("Erro", str(exc))

        ctk.CTkButton(dialog, text="Reiniciar com nova rede", command=apply).pack(pady=16)

    def _remove_app(self, app: AppRule) -> None:
        if messagebox.askyesno("Remover", f"Remover {app.name}?"):
            self.store.remove_app(app.id)
            self._refresh_apps_list()

    def _add_app_dialog(self) -> None:
        self._app_dialog("Novo aplicativo", None, self._create_app)

    def _edit_app_dialog(self, app: AppRule) -> None:
        fresh = self.store.get_app(app.id)
        if fresh is None:
            messagebox.showerror("Erro", "Aplicativo não encontrado.")
            return
        self._app_dialog("Editar aplicativo", fresh, self._update_app_from_dialog)

    def _app_dialog(
        self,
        title: str,
        app: AppRule | None,
        on_save: Callable[[AppRule], None],
    ) -> None:
        self._modal_open = True
        dialog = ctk.CTkToplevel(self)
        dialog.title(title)
        dialog.geometry("500x560")
        dialog.minsize(460, 520)
        dialog.transient(self)
        dialog.grid_columnconfigure(1, weight=1)

        def close_dialog() -> None:
            self._modal_open = False
            try:
                dialog.grab_release()
            except Exception:
                pass
            if dialog.winfo_exists():
                dialog.destroy()

        dialog.protocol("WM_DELETE_WINDOW", close_dialog)

        def add_field(label: str, row: int, default: str = "") -> ctk.CTkEntry:
            ctk.CTkLabel(dialog, text=label).grid(row=row, column=0, sticky="w", padx=16, pady=8)
            entry = ctk.CTkEntry(dialog, width=300)
            entry.insert(0, default)
            entry.grid(row=row, column=1, sticky="ew", padx=16, pady=8)
            return entry

        name_e = add_field("Nome:", 0, app.name if app else "")
        patterns_e = add_field(
            "Padrões (vírgula):",
            1,
            ", ".join(app.patterns) if app else "",
        )
        command_e = add_field("Comando:", 2, app.command if app else "")
        notes_e = add_field("Notas:", 3, app.notes if app else "")

        ctk.CTkLabel(dialog, text="Usar proxy:").grid(row=4, column=0, sticky="w", padx=16, pady=8)
        ctk.CTkLabel(
            dialog,
            text="Controlado por Ligar / Parar proxy (status em tempo real).",
            text_color="#94a3b8",
            wraplength=300,
            justify="left",
        ).grid(row=4, column=1, sticky="w", padx=16, pady=8)

        ctk.CTkLabel(dialog, text="Rede:").grid(row=5, column=0, sticky="nw", padx=16, pady=(12, 8))
        default_iface = app.network_interface if app else AUTO_INTERFACE
        iface_tiles_frame, get_iface = self._build_iface_tiles_widget(dialog, default_iface)
        iface_tiles_frame.grid(row=5, column=1, sticky="w", padx=16, pady=8)

        ctk.CTkLabel(
            dialog,
            text="Proxy específico\n(opcional):",
            justify="left",
        ).grid(row=6, column=0, sticky="nw", padx=16, pady=8)
        upstream_e = ctk.CTkEntry(
            dialog,
            width=300,
            placeholder_text="http://user:pass@host:port (vazio = usar global)",
        )
        upstream_e.insert(0, getattr(app, "upstream_proxy", "") if app else "")
        upstream_e.grid(row=6, column=1, sticky="ew", padx=16, pady=8)

        def save() -> None:
            name = name_e.get().strip()
            if not name:
                messagebox.showerror("Erro", "Nome obrigatório.")
                return
            patterns = [p.strip() for p in patterns_e.get().split(",") if p.strip()]
            if not patterns:
                patterns = [name.lower()]

            new_app = AppRule(
                id=app.id if app else _slug_id(name),
                name=name,
                patterns=patterns,
                use_proxy=app.use_proxy if app else False,
                enabled=True if app is None else app.enabled,
                command=command_e.get().strip(),
                category=app.category if app else "custom",
                notes=notes_e.get().strip(),
                network_interface=get_iface(),
                upstream_proxy=upstream_e.get().strip(),
            )
            on_save(new_app)
            close_dialog()
            self._refresh_apps_list()
            self._request_scan(min_interval=0, debounce_ms=0)

        ctk.CTkButton(dialog, text="Salvar", command=save).grid(
            row=7, column=0, columnspan=2, pady=24
        )

        def show_modal() -> None:
            if not dialog.winfo_exists():
                return
            dialog.lift()
            dialog.focus_force()
            dialog.attributes("-topmost", True)
            dialog.after(80, lambda: dialog.attributes("-topmost", False) if dialog.winfo_exists() else None)
            try:
                dialog.wait_visibility()
                dialog.grab_set()
            except Exception:
                pass

        dialog.after(10, show_modal)

    def _create_app(self, app: AppRule) -> None:
        if self.store.get_app(app.id):
            app.id = f"{app.id}-{uuid.uuid4().hex[:6]}"
        self.store.add_app(app)

    def _update_app_from_dialog(self, app: AppRule) -> None:
        self.store.update_app(app)

    def on_closing(self) -> None:
        self._modal_open = False
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
        if self._iface_refresh_job:
            self.after_cancel(self._iface_refresh_job)
        stop_watchdog()
        if self._tray:
            self._tray.stop()
        stop_local_proxy()
        self.destroy()


def _slug_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or f"app-{uuid.uuid4().hex[:6]}"


def run() -> None:
    app = ProxyManagerApp()
    app.protocol("WM_DELETE_WINDOW", app.on_closing)
    app.mainloop()
