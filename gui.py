#!/usr/bin/env python3
"""GCP Identity Diagnostics — tkinter GUI, stdlib only."""

import re
import shutil
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import config_utils
import gcloud_utils
import network_utils
import vpn_utils

# Risorse locali/aziendali, non versionate (vedi .gitignore). I bottoni che le
# usano compaiono solo se il file esiste: così il repo pubblico resta generico.
LOCAL_DIR = Path(__file__).parent / 'local'
NOTE_FILE = LOCAL_DIR / 'note.md'
SETUP_FILE = LOCAL_DIR / 'setup_proxy.py'
# Guida: versione generica versionata nel repo; local/guida.md (privata) la
# sovrascrive se presente. Il bottone c'è sempre.
GUIDA_DEFAULT = Path(__file__).parent / 'guida.md'
GUIDA_LOCAL = LOCAL_DIR / 'guida.md'


def guida_path():
    return GUIDA_LOCAL if GUIDA_LOCAL.exists() else GUIDA_DEFAULT


# ── Tooltip ──────────────────────────────────────────────────────────────────

class Tooltip:
    """Minimal hover tooltip, stdlib only. Usage: Tooltip(widget, 'testo')."""

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self._tip = None
        widget.bind('<Enter>', self._show)
        widget.bind('<Leave>', self._hide)

    def _show(self, _event=None):
        if self._tip or not self.text:
            return
        x = self.widget.winfo_rootx() + 12
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        self._tip = tk.Toplevel(self.widget)
        self._tip.wm_overrideredirect(True)
        self._tip.wm_geometry(f'+{x}+{y}')
        tk.Label(
            self._tip, text=self.text, justify=tk.LEFT, background='#ffffe0',
            relief=tk.SOLID, borderwidth=1, font=('Sans', 9), wraplength=420,
        ).pack(ipadx=5, ipady=3)

    def _hide(self, _event=None):
        if self._tip:
            self._tip.destroy()
            self._tip = None


# ── Setup dialog ───────────────────────────────────────────────────────────────

class SetupDialog(tk.Toplevel):
    """Modal form for configuring two GCP accounts. Blocks caller until saved."""

    def __init__(self, parent, on_save, existing=None):
        super().__init__(parent)
        self.on_save = on_save
        self._existing = existing or {}
        self.title('Impostazioni')
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self._default_radio = tk.StringVar(value='account_a')
        self._fields = {}
        self._build()
        if not existing:
            # First-time setup: prevent closing without saving
            self.protocol('WM_DELETE_WINDOW', lambda: None)
        self.wait_window()

    def _build(self):
        pad = dict(padx=12, pady=6)
        ttk.Label(self, text='Configura i due account GCP', font=('', 11, 'bold')).pack(**pad)

        for key, title in (('account_a', 'Account A'), ('account_b', 'Account B')):
            lf = ttk.LabelFrame(self, text=title, padding=8)
            lf.pack(fill=tk.X, **pad)
            entries = {}
            for field, label_text in (
                ('label',   'Label breve (es. CompanyRossi, CompanyRossiInterna)'),
                ('account', 'Email account GCP'),
                ('project', 'Project ID'),
            ):
                row = ttk.Frame(lf)
                row.pack(fill=tk.X, pady=2)
                ttk.Label(row, text=label_text, width=30, anchor=tk.W).pack(side=tk.LEFT)
                e = ttk.Entry(row, width=44)
                e.pack(side=tk.LEFT)
                entries[field] = e
            ttk.Radiobutton(
                lf,
                text='Questo è il default ADC (rete di sicurezza — application_default_credentials.json)',
                variable=self._default_radio,
                value=key,
            ).pack(anchor=tk.W, pady=(6, 0))
            self._fields[key] = entries

        # Pre-populate if editing
        for key in ('account_a', 'account_b'):
            if key in self._existing:
                info = self._existing[key]
                for field in ('label', 'account', 'project'):
                    self._fields[key][field].delete(0, tk.END)
                    self._fields[key][field].insert(0, info.get(field, ''))
        default_key = config_utils.get_default_adc_key(self._existing)
        if default_key:
            self._default_radio.set(default_key)

        btn_row = ttk.Frame(self)
        btn_row.pack(**pad)
        ttk.Button(btn_row, text='Salva', command=self._save).pack(side=tk.LEFT, padx=(0, 8))
        if self._existing:
            ttk.Button(btn_row, text='Annulla', command=self.destroy).pack(side=tk.LEFT)

    def _save(self):
        data = {}
        for key in ('account_a', 'account_b'):
            label   = self._fields[key]['label'].get().strip()
            account = self._fields[key]['account'].get().strip()
            project = self._fields[key]['project'].get().strip()
            if not label or not account or not project:
                messagebox.showerror('Errore', f'Tutti i campi di {key} sono obbligatori.', parent=self)
                return
            data[key] = {
                'label':        label,
                'account':      account,
                'project':      project,
                'adc_file':     str(config_utils.adc_path_for_label(label)),
                'is_default_adc': self._default_radio.get() == key,
            }
        self.on_save(data)
        self.destroy()


# ── Network setup dialog ───────────────────────────────────────────────────────

class NetworkSetupDialog(tk.Toplevel):
    """Dialog to configure a corporate hotspot SSID + proxy route, with bash preview."""

    def __init__(self, parent, on_save, existing=None, gateway=None, stale_gateways=None):
        super().__init__(parent)
        self.on_save = on_save
        self._iface = network_utils.get_wifi_iface() or 'wlan0'
        self._gateway = gateway or f'<gateway-{self._iface}>'
        self._stale_gateways = stale_gateways or []
        self.title('Configurazione rete aziendale')
        self.resizable(True, False)
        self.transient(parent)
        self.grab_set()
        existing = existing or {}
        pad = dict(padx=12, pady=6)

        ttk.Label(self, text='Hotspot / proxy aziendale', font=('', 11, 'bold')).pack(**pad)

        lf = ttk.LabelFrame(self, text='Parametri', padding=8)
        lf.pack(fill=tk.X, **pad)

        self._vars = {}
        self._fields = {}
        for field, label_text, default in (
            ('hotspot_ssid', 'SSID hotspot (es. iPhone di Mario)', existing.get('hotspot_ssid', '')),
            ('proxy_host',   'IP proxy aziendale',                 existing.get('proxy_host', '')),
            ('proxy_port',   'Porta proxy aziendale',              str(existing.get('proxy_port', 443))),
        ):
            row = ttk.Frame(lf)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=label_text, width=34, anchor=tk.W).pack(side=tk.LEFT)
            var = tk.StringVar(value=default)
            e = ttk.Entry(row, width=36, textvariable=var)
            e.pack(side=tk.LEFT)
            self._vars[field] = var
            self._fields[field] = e

        # Live bash preview
        pf = ttk.LabelFrame(self, text='Comandi bash (esegui una volta sola per connection profile)', padding=6)
        pf.pack(fill=tk.X, **pad)
        self._preview = tk.Text(pf, height=6, font=('Monospace', 9),
                                bg='#1e1e1e', fg='#d4d4d4', state=tk.DISABLED, wrap=tk.NONE)
        sc = ttk.Scrollbar(pf, orient=tk.HORIZONTAL, command=self._preview.xview)
        self._preview.configure(xscrollcommand=sc.set)
        self._preview.pack(fill=tk.X)
        sc.pack(fill=tk.X)

        btn_row = ttk.Frame(self)
        btn_row.pack(**pad)
        ttk.Button(btn_row, text='Salva', command=self._save).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(btn_row, text='Annulla', command=self.destroy).pack(side=tk.LEFT)

        # Update preview whenever SSID or proxy_host changes
        for key in ('hotspot_ssid', 'proxy_host'):
            self._vars[key].trace_add('write', lambda *_: self._update_preview())
        self._update_preview()

        self.wait_window()

    def _update_preview(self):
        ssid = self._vars['hotspot_ssid'].get()
        host = self._vars['proxy_host'].get()
        gw = self._gateway
        lines = []
        if self._stale_gateways:
            lines.append(f'# ⚠ Route stale rilevate nel profilo "{ssid}" — rimuovile prima:')
            for old_gw in self._stale_gateways:
                lines.append(f'sudo nmcli connection modify "{ssid}" -ipv4.routes "{host}/32 {old_gw}"')
            lines.append('')
        iface = self._iface
        lines += [
            f'# Aggiungi route permanente per proxy aziendale:',
            f'sudo nmcli connection modify "{ssid}" +ipv4.routes "{host}/32 {gw}"',
            f'sudo nmcli device reapply {iface}',
            f'',
            f'# Verifica route attive nel profilo:',
            f'nmcli -g ipv4.routes connection show "{ssid}"',
            f'ip route get {host}',
            f'# atteso: {host} via {gw} dev {iface}',
        ]
        self._preview.config(state=tk.NORMAL)
        self._preview.delete('1.0', tk.END)
        self._preview.insert('1.0', '\n'.join(lines))
        self._preview.config(state=tk.DISABLED)

    def _save(self):
        ssid = self._vars['hotspot_ssid'].get().strip()
        host = self._vars['proxy_host'].get().strip()
        port_str = self._vars['proxy_port'].get().strip()
        if not ssid or not host or not port_str:
            messagebox.showerror('Errore', 'Tutti i campi sono obbligatori.', parent=self)
            return
        try:
            port = int(port_str)
        except ValueError:
            messagebox.showerror('Errore', 'Porta deve essere un numero intero.', parent=self)
            return
        self.on_save({'hotspot_ssid': ssid, 'proxy_host': host, 'proxy_port': port})
        self.destroy()


# ── Guida dialog ──────────────────────────────────────────────────────────────

class GuidaDialog(tk.Toplevel):
    def __init__(self, parent, text):
        super().__init__(parent)
        self.title('Guida — ordine operazioni')
        self.geometry('620x560')
        self.resizable(True, True)
        self.transient(parent)

        txt = tk.Text(self, wrap=tk.WORD, font=('Monospace', 9),
                      bg='#f8f8f8', padx=10, pady=8)
        sc = ttk.Scrollbar(self, command=txt.yview)
        txt.configure(yscrollcommand=sc.set)
        sc.pack(side=tk.RIGHT, fill=tk.Y)
        txt.pack(fill=tk.BOTH, expand=True)
        txt.insert('1.0', text)
        txt.config(state=tk.DISABLED)
        ttk.Button(self, text='Chiudi', command=self.destroy).pack(pady=8)


# ── Main application ───────────────────────────────────────────────────────────

class App:
    def __init__(self, root: tk.Tk):
        self.root = root
        self._accounts = None
        self._buttons = {}
        self._busy_count = 0
        self._adc_login_times: dict = {}
        self._vpn_proc = None
        self._vpn_pw = None
        self._vpn_pw_sent = False
        root.title('GCP Identity Diagnostics')
        root.resizable(True, True)
        self._build_static_ui()
        self._load_or_setup()

    # ── Static UI construction ─────────────────────────────────────────────

    def _build_static_ui(self):
        self._setup_styles()
        self.root.configure(bg=self.BG)
        self.root.protocol('WM_DELETE_WINDOW', self._on_close)

        main = ttk.Frame(self.root)
        main.pack(fill=tk.BOTH, expand=True)

        # Header bar (enterprise)
        header = ttk.Frame(main, style='HeaderBar.TFrame', padding=(14, 10))
        header.pack(fill=tk.X)
        htext = ttk.Frame(header, style='HeaderBar.TFrame')
        htext.pack(side=tk.LEFT)
        ttk.Label(htext, text='GCP Identity Diagnostics', style='Header.TLabel').pack(anchor=tk.W)
        ttk.Label(htext, text='Gestione identità GCP, rete aziendale e VPN',
                  style='Sub.TLabel').pack(anchor=tk.W)
        badge = ttk.Frame(header, style='HeaderBar.TFrame')
        badge.pack(side=tk.RIGHT, anchor=tk.E)
        self._vpn_dot = ttk.Label(badge, text='●', style='Badge.TLabel',
                                  foreground=self.DOT_OFF)
        self._vpn_dot.pack(side=tk.LEFT, padx=(0, 5))
        self._vpn_badge_var = tk.StringVar(value='VPN disconnessa')
        ttk.Label(badge, textvariable=self._vpn_badge_var,
                  style='Badge.TLabel').pack(side=tk.LEFT)

        # Toolbar — i bottoni locali compaiono solo se la risorsa esiste
        toolbar = ttk.Frame(main, padding=(12, 8))
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text='Guida',
                   command=self._open_guida).pack(side=tk.LEFT, padx=(0, 4))
        if NOTE_FILE.exists():
            ttk.Button(toolbar, text='Note',
                       command=self._open_note).pack(side=tk.LEFT, padx=(0, 4))
        if SETUP_FILE.exists():
            ttk.Button(toolbar, text='Setup proxy',
                       command=self._run_setup_local).pack(side=tk.LEFT)
        ttk.Label(toolbar,
                  text='🟠 bottone arancione = azione consigliata dalla diagnosi',
                  style='Hint.TLabel').pack(side=tk.RIGHT)

        # Tabs
        nb = ttk.Notebook(main)
        nb.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 6))
        diag_tab = ttk.Frame(nb, padding=8)
        vpn_tab = ttk.Frame(nb, padding=8)
        nb.add(diag_tab, text='Diagnostica')
        nb.add(vpn_tab, text='FortiVPN')
        self._build_diag_tab(diag_tab)
        self._build_vpn_tab(vpn_tab)

        # Status bar
        self._status_var = tk.StringVar()
        ttk.Label(main, textvariable=self._status_var, style='Status.TLabel',
                  padding=(12, 4)).pack(fill=tk.X, side=tk.BOTTOM)

    def _setup_styles(self):
        self.BG, self.CARD, self.FG, self.MUTED = '#eef1f4', '#ffffff', '#1a2733', '#5a6b7b'
        self.DOT_OFF, self.DOT_ON, self.DOT_WARN = '#9e9e9e', '#4caf50', '#ffb300'
        PRIMARY, PRIMARY_D = '#1565c0', '#0d47a1'
        DANGER, DANGER_A = '#c62828', '#d32f2f'
        SUCCESS, SUCCESS_A = '#2e7d32', '#388e3c'
        style = ttk.Style(self.root)
        try:
            style.theme_use('clam')  # 'clam' rispetta i colori custom sui bottoni ttk
        except tk.TclError:
            pass
        style.configure('.', background=self.BG, foreground=self.FG)
        style.configure('TFrame', background=self.BG)
        style.configure('TLabel', background=self.BG, foreground=self.FG)
        style.configure('TLabelframe', background=self.BG, borderwidth=1, relief='solid')
        style.configure('TLabelframe.Label', background=self.BG, foreground=PRIMARY,
                        font=('Sans', 10, 'bold'))
        style.configure('TButton', font=('Sans', 9), padding=(8, 4))
        style.configure('TNotebook', background=self.BG, borderwidth=0, tabmargins=[2, 4, 2, 0])
        style.configure('TNotebook.Tab', font=('Sans', 10), padding=(22, 8),
                        background='#c9d2da', foreground=self.MUTED,
                        borderwidth=1, bordercolor=self.BG,
                        lightcolor=self.BG, darkcolor=self.BG)
        style.map('TNotebook.Tab',
                  background=[('selected', self.CARD)],
                  foreground=[('selected', PRIMARY)],
                  lightcolor=[('selected', self.CARD)],
                  darkcolor=[('selected', self.CARD)],
                  padding=[('selected', (22, 8))],
                  expand=[('selected', [0, 0, 0, 0])])
        for name, bg, act in (('Accent', PRIMARY, PRIMARY_D),
                              ('Danger', DANGER, DANGER_A),
                              ('Success', SUCCESS, SUCCESS_A)):
            style.configure(f'{name}.TButton', background=bg, foreground='white',
                            font=('Sans', 9, 'bold'))
            style.map(f'{name}.TButton',
                      background=[('active', act), ('disabled', '#b7c2cc')],
                      foreground=[('disabled', '#eef1f4')])
        style.configure('Highlight.TButton', background='#ff9800', foreground='black',
                        font=('Sans', 9, 'bold'))
        style.map('Highlight.TButton', background=[('active', '#ffb74d')])
        style.configure('HeaderBar.TFrame', background=PRIMARY_D)
        style.configure('Header.TLabel', background=PRIMARY_D, foreground='white',
                        font=('Sans', 15, 'bold'))
        style.configure('Sub.TLabel', background=PRIMARY_D, foreground='#bcd4f0',
                        font=('Sans', 9))
        style.configure('Badge.TLabel', background=PRIMARY_D, foreground='white',
                        font=('Sans', 10, 'bold'))
        style.configure('Hint.TLabel', foreground='#b05000', font=('Sans', 9, 'italic'))
        style.configure('Status.TLabel', background='#dfe5ea', foreground=self.MUTED,
                        font=('Sans', 9))

    def _build_diag_tab(self, parent):
        pad = dict(padx=8, pady=4)
        # Horizontal split: log on left, panels on right
        paned = tk.PanedWindow(parent, orient=tk.HORIZONTAL, sashwidth=6,
                               sashrelief=tk.FLAT, bg=self.BG)
        paned.pack(fill=tk.BOTH, expand=True)

        # Left: log comandi
        log_lf = ttk.LabelFrame(paned, text='Log comandi', padding=6)
        self._log_box = tk.Text(log_lf, wrap=tk.WORD, font=('Monospace', 9),
                                bg='#1e1e1e', fg='#d4d4d4', insertbackground='white',
                                relief='flat', state=tk.DISABLED)
        sc_log = ttk.Scrollbar(log_lf, command=self._log_box.yview)
        self._log_box.configure(yscrollcommand=sc_log.set)
        self._log_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc_log.pack(side=tk.RIGHT, fill=tk.Y)
        paned.add(log_lf, width=380, stretch='never')

        # Right: status panels
        right = ttk.Frame(paned)
        paned.add(right, stretch='always')

        # Prerequisiti
        pf = ttk.LabelFrame(right, text='Prerequisiti', padding=6)
        pf.pack(fill=tk.X, **pad)
        self._prereq_box = self._status_text(pf, height=5)
        ttk.Button(pf, text='Aggiorna prerequisiti', command=self.refresh_prereq).pack(anchor=tk.W, pady=(4, 0))

        # Stato rete
        nf = ttk.LabelFrame(right, text='Stato rete', padding=6)
        nf.pack(fill=tk.X, **pad)
        self._net_box = self._status_text(nf, height=6)
        net_btn_row = ttk.Frame(nf)
        net_btn_row.pack(anchor=tk.W, pady=(4, 0))
        ttk.Button(net_btn_row, text='Aggiorna rete', command=self.refresh_network).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(net_btn_row, text='Configura rete aziendale', command=self._show_network_setup).pack(side=tk.LEFT)

        # Stato identità
        idf = ttk.LabelFrame(right, text='Stato identità', padding=6)
        idf.pack(fill=tk.X, **pad)
        self._id_box = self._status_text(idf, height=12)
        ttk.Button(idf, text='Aggiorna identità', command=self.refresh_identity).pack(anchor=tk.W, pady=(4, 0))

        # Azioni (dynamic inner frame)
        af = ttk.LabelFrame(right, text='Azioni', padding=6)
        af.pack(fill=tk.X, **pad)
        self._actions_inner = ttk.Frame(af)
        self._actions_inner.pack(fill=tk.X)

    def _build_vpn_tab(self, parent):
        self._vpn_otp_var = tk.StringVar()
        self._vpn_pw_var = tk.StringVar()
        self._vpn_dir_var = tk.StringVar()
        self._vpn_configs = []

        form = ttk.LabelFrame(parent, text='Parametri connessione', padding=8)
        form.pack(fill=tk.X, pady=(0, 8))
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text='Cartella:').grid(row=0, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        ttk.Label(form, textvariable=self._vpn_dir_var, foreground=self.MUTED).grid(
            row=0, column=1, sticky=tk.W, pady=3)
        fbtn = ttk.Frame(form)
        fbtn.grid(row=0, column=2, padx=(6, 0))
        ttk.Button(fbtn, text='Cambia…', command=self._vpn_change_folder).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(fbtn, text='↻', width=2, command=self._vpn_refresh_list).pack(side=tk.LEFT)

        ttk.Label(form, text='Config:').grid(row=1, column=0, sticky=tk.NW, pady=3, padx=(0, 6))
        lw = ttk.Frame(form)
        lw.grid(row=1, column=1, columnspan=2, sticky=tk.EW, pady=3)
        self._vpn_list = tk.Listbox(lw, height=5, font=('Monospace', 9),
                                    activestyle='dotbox', exportselection=False)
        lsc = ttk.Scrollbar(lw, command=self._vpn_list.yview)
        self._vpn_list.configure(yscrollcommand=lsc.set)
        self._vpn_list.pack(side=tk.LEFT, fill=tk.X, expand=True)
        lsc.pack(side=tk.RIGHT, fill=tk.Y)
        self._vpn_list.bind('<Double-Button-1>', lambda _e: self._connect_vpn())

        ttk.Label(form, text='OTP:').grid(row=2, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        ttk.Entry(form, textvariable=self._vpn_otp_var, width=20).grid(row=2, column=1, sticky=tk.W, pady=3)

        ttk.Label(form, text='Password sudo:').grid(row=3, column=0, sticky=tk.W, pady=3, padx=(0, 6))
        ttk.Entry(form, textvariable=self._vpn_pw_var, show='•', width=20).grid(row=3, column=1, sticky=tk.W, pady=3)

        self._vpn_refresh_list()

        bar = ttk.Frame(parent)
        bar.pack(fill=tk.X, pady=(0, 8))
        self._vpn_connect_btn = ttk.Button(
            bar, text='Connetti a FortiVPN', style='Success.TButton',
            command=self._connect_vpn)
        self._vpn_connect_btn.pack(side=tk.LEFT, padx=(0, 6))
        self._vpn_disconnect_btn = ttk.Button(
            bar, text='Disconnetti', style='Danger.TButton',
            state=tk.DISABLED, command=self._disconnect_vpn)
        self._vpn_disconnect_btn.pack(side=tk.LEFT)
        self._vpn_status_var = tk.StringVar(value='Disconnesso')
        ttk.Label(bar, textvariable=self._vpn_status_var,
                  font=('Sans', 10, 'bold')).pack(side=tk.RIGHT)
        self._vpn_status_dot = ttk.Label(bar, text='●', foreground=self.DOT_OFF,
                                         font=('Sans', 12))
        self._vpn_status_dot.pack(side=tk.RIGHT, padx=(0, 5))

        term_lf = ttk.LabelFrame(parent, text='Terminale FortiVPN', padding=6)
        term_lf.pack(fill=tk.BOTH, expand=True)
        self._vpn_term = tk.Text(term_lf, wrap=tk.CHAR, font=('Monospace', 9),
                                 bg='#101417', fg='#d4d4d4', insertbackground='white',
                                 relief='flat', state=tk.DISABLED)
        sc = ttk.Scrollbar(term_lf, command=self._vpn_term.yview)
        self._vpn_term.configure(yscrollcommand=sc.set)
        self._vpn_term.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sc.pack(side=tk.RIGHT, fill=tk.Y)

    def _status_text(self, parent, height=8):
        frame = ttk.Frame(parent)
        frame.pack(fill=tk.X)
        box = tk.Text(frame, wrap=tk.WORD, height=height, font=('Monospace', 9),
                      state=tk.DISABLED, bg='#f5f5f5')
        sc = ttk.Scrollbar(frame, command=box.yview)
        box.configure(yscrollcommand=sc.set)
        box.pack(side=tk.LEFT, fill=tk.X, expand=True)
        sc.pack(side=tk.RIGHT, fill=tk.Y)
        return box

    def _set_highlights(self, targets: set):
        """targets: set of keys like ('fix','account_a'), ('login_adc','account_b'), ('sync',)."""
        def _apply():
            for key, btn in self._buttons.items():
                btn.configure(style='Highlight.TButton' if key in targets else 'TButton')
        self.root.after(0, _apply)

    def _update_action_buttons(self):
        for w in self._actions_inner.winfo_children():
            w.destroy()
        self._buttons = {}

        if not self._accounts:
            ttk.Label(self._actions_inner, text='Nessuna configurazione — usa "Modifica configurazione".').pack(anchor=tk.W)
        else:
            for key in ('account_a', 'account_b'):
                info = self._accounts.get(key, {})
                label = info.get('label', key)
                is_default = info.get('is_default_adc')
                tag = f'[{label}] default ADC' if is_default else f'[{label}] kubectl/Lens'
                row = ttk.Frame(self._actions_inner)
                row.pack(anchor=tk.W, pady=2)

                tag_lbl = ttk.Label(row, text=tag, width=26, anchor=tk.W)
                tag_lbl.pack(side=tk.LEFT)
                Tooltip(tag_lbl,
                        "'default ADC' = questo account è quello che i tool "
                        "SDK-based (librerie client / tool esterni) usano di default, e a cui "
                        "viene tenuto sincronizzato application_default_credentials.json "
                        "come rete di sicurezza.\n"
                        "'kubectl/Lens' = questo account è quello usato per "
                        "autenticarsi su Kubernetes/GKE (via gke-gcloud-auth-plugin)."
                        if not is_default else
                        "'default ADC' = questo account è quello che i tool "
                        "SDK-based (librerie client / tool esterni) usano di default, e a cui "
                        "viene tenuto sincronizzato application_default_credentials.json "
                        "come rete di sicurezza.\n"
                        "'kubectl/Lens' = l'altro account è quello usato per "
                        "autenticarsi su Kubernetes/GKE.")

                fix_btn = ttk.Button(row, text=f'Fix {label}',
                           command=lambda k=key: self._run_heavy(lambda: self._do_fix(k)))
                fix_btn.pack(side=tk.LEFT, padx=(0, 4))
                self._buttons[('fix', key)] = fix_btn
                if is_default:
                    Tooltip(fix_btn,
                            f"Equivale a: gcloud config configurations activate {label}\n"
                            f"+ verifica che il token ADC (adc-{label}.json) sia valido.\n\n"
                            "ADC (Application Default Credentials) è il file che "
                            "SDK e librerie leggono in automatico — diverso "
                            "dall'account 'loggato' nel CLI con gcloud auth login. "
                            "SDK e tool basati su ADC usano SEMPRE questo meccanismo.")
                else:
                    Tooltip(fix_btn,
                            f"Equivale a: gcloud config configurations activate {label}\n"
                            f"+ gcloud config set account/project\n"
                            "+ cancella ~/.kube/gke_gcloud_auth_plugin_cache "
                            "(può restare bloccata su un'identità sbagliata fino a "
                            "un'ora anche dopo aver cambiato account)\n"
                            "+ rigenera il launcher di Lens con le credenziali giuste.")

                cli_btn = ttk.Button(row, text='Login CLI',
                           command=lambda k=key: self._run_heavy(lambda: self._do_login_cli(k)))
                cli_btn.pack(side=tk.LEFT, padx=(0, 4))
                self._buttons[('login_cli', key)] = cli_btn
                Tooltip(cli_btn,
                        "Esegue: gcloud auth login <email>\n\n"
                        "Autentica l'account 'attivo' nel CLI gcloud — usato da "
                        "gcloud config config-helper e dal fallback legacy di "
                        "kubectl/Lens. NON è lo stesso delle credenziali ADC: "
                        "questo login non tocca application_default_credentials.json.\n\n"
                        "QUANDO SERVE: se nel log vedi un errore tipo "
                        "'Reauthentication failed: cannot prompt during "
                        "non-interactive execution', oppure se Lens continua a "
                        "fallire dopo un 'Fix' e questo bottone diventa arancione.")

                adc_btn = ttk.Button(row, text='Login ADC',
                           command=lambda k=key: self._run_heavy(lambda: self._do_login_adc(k)))
                adc_btn.pack(side=tk.LEFT)
                self._buttons[('login_adc', key)] = adc_btn
                Tooltip(adc_btn,
                        "Esegue: gcloud auth login --update-adc\n\n"
                        "Rinnova le Application Default Credentials (ADC): il file "
                        "che SDK/librerie (client Python/Node, ecc.) leggono "
                        "di default. ATTENZIONE: scrive SEMPRE in "
                        "application_default_credentials.json, ignora "
                        "GOOGLE_APPLICATION_CREDENTIALS anche se impostata. "
                        "Dopo il login usa 'Verifica e sincronizza ADC' per "
                        "smistarlo al file dell'account giusto.\n\n"
                        "QUANDO SERVE: se SDK/tool basati su ADC danno errore "
                        "'invalid_grant'/'invalid_rapt', o se qui sopra il file "
                        f"adc-{label}.json mostra ERRORE, o se questo bottone "
                        "diventa arancione dopo 'Aggiorna identità'.")

        ttk.Separator(self._actions_inner, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=6)
        bot = ttk.Frame(self._actions_inner)
        bot.pack(anchor=tk.W)

        kubectl_btn = ttk.Button(bot, text='Test kubectl',
                   command=lambda: self._run_heavy(self._do_test_kubectl))
        kubectl_btn.pack(side=tk.LEFT, padx=(0, 4))
        self._buttons[('test_kubectl',)] = kubectl_btn
        Tooltip(kubectl_btn,
                "Esegue: kubectl config current-context + kubectl get nodes\n\n"
                "Mostra anche quale identità Google sta usando kubectl in questo "
                "momento (via gke-gcloud-auth-plugin) — utile per capire se "
                "Lens/kubectl stanno autenticando con l'account sbagliato prima "
                "ancora di vedere l'errore Forbidden.")

        sync_btn = ttk.Button(bot, text='Verifica e sincronizza ADC',
                   command=lambda: self._run_heavy(self._do_verify_sync_adc))
        sync_btn.pack(side=tk.LEFT, padx=(0, 4))
        self._buttons[('sync',)] = sync_btn
        Tooltip(sync_btn,
                "Legge l'identità reale di application_default_credentials.json "
                "(il file 'fisico' di default) e la copia nel file ADC dedicato "
                "dell'account corrispondente. Se non è l'account marcato come "
                "'default ADC', ripristina subito dopo quel file col contenuto "
                "giusto — per non lasciare l'ADC di sistema sull'identità sbagliata. "
                "Usalo sempre dopo 'Login ADC'.")

        renew_btn = ttk.Button(bot, text='Rinnova token ADC',
                   command=lambda: self._run_heavy(self._do_silent_refresh_all))
        renew_btn.pack(side=tk.LEFT, padx=(0, 4))
        self._buttons[('renew_adc',)] = renew_btn
        Tooltip(renew_btn,
                "Tenta il rinnovo silenzioso dei token ADC usando il refresh_token salvato.\n"
                "Non apre il browser. Se il refresh_token è scaduto (invalid_grant),\n"
                "il bottone diventa arancione e serve 'Login ADC'.")

        edit_btn = ttk.Button(bot, text='Modifica configurazione',
                   command=self._show_setup_edit)
        edit_btn.pack(side=tk.LEFT, padx=(4, 0))
        Tooltip(edit_btn,
                "Riapre il form per correggere a mano label/email/project dei "
                "due account, se l'auto-detect ha sbagliato qualcosa o gli "
                "account sono cambiati.")

    # ── Config loading / setup ─────────────────────────────────────────────

    def _load_or_setup(self):
        cfg = config_utils.load_config()
        if not cfg:
            cfg = config_utils.auto_detect_config()
            if cfg:
                config_utils.save_config(cfg)
        if cfg:
            self._accounts = cfg
            self._update_action_buttons()
            self.refresh_all()
            self.root.after(5000, self._auto_refresh)
        else:
            self._show_setup()

    def _show_setup(self):
        def on_save(cfg):
            config_utils.save_config(cfg)
            self._accounts = cfg
            self._update_action_buttons()
            self.refresh_all()
        SetupDialog(self.root, on_save=on_save)

    def _show_setup_edit(self):
        def on_save(cfg):
            config_utils.save_config(cfg)
            self._accounts = cfg
            self._update_action_buttons()
            self.refresh_identity()
        SetupDialog(self.root, on_save=on_save, existing=self._accounts)

    def _show_network_setup(self):
        existing = config_utils.load_network_config()
        if not existing['hotspot_ssid']:
            existing['hotspot_ssid'] = network_utils.get_wifi_ssid() or ''
        gateway = network_utils.get_wifi_gateway()
        # Detect stale routes already in the connection profile
        stale_gateways = []
        if existing['hotspot_ssid'] and existing['proxy_host']:
            stale_gateways = network_utils.get_connection_routes(
                existing['hotspot_ssid'], existing['proxy_host']
            )
        def on_save(net_cfg):
            config_utils.save_network_config(net_cfg)
            self.refresh_network()
        NetworkSetupDialog(self.root, on_save=on_save, existing=existing,
                           gateway=gateway, stale_gateways=stale_gateways)

    # ── Thread helpers ─────────────────────────────────────────────────────

    def _run(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def _run_heavy(self, fn):
        """Like _run but disables action buttons and shows busy label."""
        self._set_busy(True)
        def wrapper():
            try:
                fn()
            finally:
                self._set_busy(False)
        threading.Thread(target=wrapper, daemon=True).start()

    def _set_busy(self, busy: bool):
        def _apply():
            self._busy_count += 1 if busy else -1
            self._busy_count = max(0, self._busy_count)
            is_busy = self._busy_count > 0
            for btn in self._buttons.values():
                try:
                    btn.configure(state=tk.DISABLED if is_busy else tk.NORMAL)
                except tk.TclError:
                    pass
            self._status_var.set('⏳ Operazione in corso — attendi completamento...' if is_busy else '')
        self.root.after(0, _apply)

    def _log(self, msg: str):
        def _write():
            self._log_box.config(state=tk.NORMAL)
            self._log_box.insert(tk.END, msg + '\n')
            self._log_box.see(tk.END)
            self._log_box.config(state=tk.DISABLED)
        self.root.after(0, _write)

    def _set_status(self, box: tk.Text, text: str):
        def _update():
            box.config(state=tk.NORMAL)
            box.delete('1.0', tk.END)
            box.insert('1.0', text)
            box.config(state=tk.DISABLED)
        self.root.after(0, _update)

    def _confirm(self, msg: str) -> bool:
        """Call from worker thread — blocks until user answers the dialog."""
        result = [False]
        event = threading.Event()
        def ask():
            result[0] = messagebox.askyesno('Conferma', msg, parent=self.root)
            event.set()
        self.root.after(0, ask)
        event.wait()
        return result[0]

    def _run_cmd(self, args: list) -> subprocess.CompletedProcess:
        self._log(f'$ {" ".join(str(a) for a in args)}')
        r = subprocess.run(args, capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        if out:
            self._log(out)
        return r

    # ── Prerequisiti ───────────────────────────────────────────────────────

    def refresh_prereq(self):
        self._run(self._do_refresh_prereq)

    def _do_refresh_prereq(self):
        lines = ['Strumenti richiesti:']
        for tool, hint in (
            ('gcloud',               'https://cloud.google.com/sdk/docs/install'),
            ('kubectl',              'https://kubernetes.io/docs/tasks/tools/'),
            ('gke-gcloud-auth-plugin', 'gcloud components install gke-gcloud-auth-plugin'),
            ('python3',              'già presente su Ubuntu'),
        ):
            found = shutil.which(tool)
            mark = '✓' if found else '✗'
            detail = found or f'non trovato — {hint}'
            lines.append(f'  {mark} {tool}: {detail}')

        lines.append('')
        ca = config_utils.CA_CERT_PATH
        if ca.exists():
            lines.append(f'✓ CA cert: {ca}')
        else:
            lines.append(f'✗ CA cert assente: {ca}')
            lines.append('  (copia il file se necessario per connessioni via proxy aziendale)')

        # Warn if both ADC files are byte-identical
        if self._accounts:
            paths = [Path(info['adc_file']) for info in self._accounts.values()
                     if info.get('adc_file')]
            if (len(paths) == 2
                    and all(p.exists() for p in paths)
                    and paths[0].read_bytes() == paths[1].read_bytes()):
                lines.append('')
                lines.append('⚠ ATTENZIONE: entrambi i file ADC sono byte-identici!')
                lines.append('  Probabile errore di copia — un account ha le credenziali sbagliate.')

        self._set_status(self._prereq_box, '\n'.join(lines))

    # ── Stato rete ─────────────────────────────────────────────────────────

    def refresh_network(self):
        self._run(self._do_refresh_network)

    def _do_refresh_network(self):
        ifaces = network_utils.get_interfaces()
        routes = network_utils.get_default_routes()
        net_cfg = config_utils.load_network_config()
        proxy_host = net_cfg['proxy_host']
        proxy_port = net_cfg['proxy_port']

        lines = ['Interfacce attive (UP):']
        if ifaces:
            for name, addrs in ifaces:
                lines.append(f'  {name}: {", ".join(addrs) if addrs else "(no addr)"}')
        else:
            lines.append('  nessuna')

        lines.append('\nDefault route(s):')
        if routes:
            for dev, gw in routes:
                lines.append(f'  {dev}  via {gw}')
        else:
            lines.append('  nessuna')

        if len(routes) > 1:
            lines.append('\n⚠  PIÙ DEFAULT ROUTE ATTIVE — possibile conflitto di routing!')

        # WiFi / hotspot
        ssid = network_utils.get_wifi_ssid()
        configured_ssid = net_cfg['hotspot_ssid']
        if ssid:
            if configured_ssid and ssid == configured_ssid:
                lines.append(f'\nWiFi SSID: {ssid}  ✓ hotspot aziendale attivo')
            elif configured_ssid:
                lines.append(f'\nWiFi SSID: {ssid}  ✗ non è hotspot aziendale ({configured_ssid!r})')
            else:
                lines.append(f'\nWiFi SSID: {ssid}  (hotspot non configurato)')
        else:
            if configured_ssid:
                lines.append(f'\nWiFi: non connesso  ✗ hotspot aziendale non attivo ({configured_ssid!r})')
            else:
                lines.append('\nWiFi: non connesso o SSID non rilevabile')

        if proxy_host:
            reachable = network_utils.test_connectivity(host=proxy_host, port=proxy_port)
            status = '✓ raggiungibile' if reachable else '✗ non raggiungibile (timeout 3s)'
            lines.append(f'\n{proxy_host}:{proxy_port} — {status}')
        self._set_status(self._net_box, '\n'.join(lines))

    # ── Stato identità ─────────────────────────────────────────────────────

    def refresh_identity(self):
        self._run(self._do_refresh_identity)

    def _do_refresh_identity(self):
        if not self._accounts:
            self._set_status(self._id_box, 'Nessuna configurazione. Usa "Modifica configurazione".')
            return

        lines = []
        problems = set()

        lines.append('── gcloud configurations ──')
        for key in ('account_a', 'account_b'):
            info = self._accounts.get(key, {})
            label = info.get('label', key)
            account, project = gcloud_utils.get_config_info(label)
            ok = account == info.get('account') and project == info.get('project')
            lines.append(f'  [{"✓" if ok else "⚠"} {label}]  account={account}  project={project}')
            if not ok:
                problems.add(('fix', key))

        lines.append('')
        lines.append('── ADC identity (via tokeninfo) ──')
        for key in ('account_a', 'account_b'):
            info = self._accounts.get(key, {})
            label = info.get('label', key)
            adc_file = info.get('adc_file', '')
            if not adc_file:
                lines.append(f'  [{label}]: adc_file non configurato')
                problems.add(('login_adc', key))
                continue
            adc = Path(adc_file)
            if not adc.exists():
                lines.append(f'  {adc.name}: FILE MANCANTE')
                problems.add(('login_adc', key))
                continue
            identity, err = gcloud_utils.get_adc_identity(adc)
            if err:
                lines.append(f'  {adc.name}: ERRORE — {err}')
                problems.add(('login_adc', key))
            else:
                ok = identity == info.get('account')
                lines.append(f'  {adc.name}: {"✓" if ok else "⚠"} {identity}')
                if not ok:
                    problems.add(('login_adc', key))

        lines.append('')
        lines.append('── Login ADC timer ──')
        now = time.time()
        for key in ('account_a', 'account_b'):
            info = self._accounts.get(key, {})
            label = info.get('label', key)
            t = self._adc_login_times.get(key)
            if t is None:
                lines.append(f'  [{label}]: nessun login ADC in questa sessione')
            else:
                elapsed = int(now - t)
                m, s = divmod(elapsed, 60)
                h, m = divmod(m, 60)
                elapsed_str = f'{h}h {m:02d}m {s:02d}s' if h else f'{m}m {s:02d}s'
                warn = '  ⚠ potrebbe essere scaduto' if elapsed > 1800 else ''
                lines.append(f'  [{label}]: {elapsed_str} fa{warn}')

        lines.append('')

        # Default ADC sync check
        default_key = config_utils.get_default_adc_key(self._accounts)
        if default_key:
            default_info = self._accounts[default_key]
            default_adc = Path(default_info['adc_file'])
            match = gcloud_utils.default_adc_matches(default_adc)
            lines.append(f'DEFAULT_ADC == {default_adc.name}: {"✓ sì" if match else "✗ no"}')
            if not match:
                # Fix sull'account default rifà anche la sync; è il click piu' diretto.
                problems.add(('fix', default_key))

        # Plugin cache
        exists, ts = gcloud_utils.get_plugin_cache_info()
        if exists:
            lines.append(f'Plugin cache ({gcloud_utils.PLUGIN_CACHE.name}): esiste  mtime={ts}')
        else:
            lines.append(f'Plugin cache ({gcloud_utils.PLUGIN_CACHE.name}): assente')

        # Lens desktop + identità reale usata da kubectl (kubectl/Lens account)
        kubectl_key = config_utils.get_kubectl_account_key(self._accounts)
        if kubectl_key:
            kubectl_info = self._accounts[kubectl_key]
            adc_path = kubectl_info.get('adc_file', '')
            if adc_path:
                expected = gcloud_utils.get_lens_desktop_content(adc_path)
                lens_ok = (gcloud_utils.LENS_DESKTOP.exists() and
                           gcloud_utils.LENS_DESKTOP.read_text() == expected)
                lines.append(f'Lens .desktop: {"✓ corretto" if lens_ok else "⚠ mancante o diverso"}')
                if not lens_ok:
                    problems.add(('fix', kubectl_key))

            if shutil.which('gke-gcloud-auth-plugin'):
                kubectl_identity, kerr = gcloud_utils.get_kubectl_identity()
                if kerr:
                    lines.append(f'Identità kubectl attuale: ERRORE — {kerr}')
                    # Reauth/token scaduto su config-helper → serve 'gcloud auth login'.
                    # ponytail: non parsiamo l'account dall'errore, accendiamo Login CLI
                    # sull'account kubectl (quello che il plugin usa dopo un Fix).
                    if any(s in kerr for s in ('Reauthentication', 'cannot prompt',
                                               'auth login', 'invalid_grant')):
                        problems.add(('login_cli', kubectl_key))
                    else:
                        problems.add(('fix', kubectl_key))
                else:
                    kok = kubectl_identity == kubectl_info.get('account')
                    lines.append(f'Identità kubectl attuale: {"✓" if kok else "⚠"} {kubectl_identity}')
                    if not kok:
                        problems.add(('fix', kubectl_key))

        self._set_status(self._id_box, '\n'.join(lines))
        self._set_highlights(problems)

    # ── Fix (per account) ─────────────────────────────────────────────────

    def _do_fix(self, account_key: str):
        if not self._accounts:
            return
        info = self._accounts[account_key]
        label = info['label']
        is_default = info.get('is_default_adc', False)
        self._log(f'\n=== Fix {label} ===')

        self._run_cmd(['gcloud', 'config', 'configurations', 'activate', label])

        if is_default:
            if gcloud_utils.DEFAULT_ADC.exists():
                self._log('Verifica identità application_default_credentials.json...')
                ok, msg = gcloud_utils.sync_default_adc_to_account(
                    info['account'], Path(info['adc_file'])
                )
                self._log(msg)

            self._log(f'Verifica validità token {Path(info["adc_file"]).name}...')
            valid = gcloud_utils.check_adc_token_valid(Path(info['adc_file']))
            if not valid:
                self._log('Token non valido o file assente.')
                self._log('Lancia "Login ADC" per rinnovarlo, poi clicca di nuovo "Fix".')
                return
            self._log('Token valido.')

        else:
            self._run_cmd(['gcloud', 'config', 'set', 'account', info['account']])
            self._run_cmd(['gcloud', 'config', 'set', 'project', info['project']])

            cache = gcloud_utils.PLUGIN_CACHE
            if cache.exists():
                self._log(f'$ rm {cache}')
                cache.unlink()
                self._log('Plugin cache eliminata.')
            else:
                self._log('Plugin cache assente, skip.')

            self._log('Verifica file .desktop Lens...')
            changed, msg = gcloud_utils.ensure_lens_desktop(info['adc_file'])
            self._log(msg)

            self._log('Ricerca processo Lens...')
            r = subprocess.run(['pgrep', '-f', 'lens-desktop'], capture_output=True)
            if r.returncode == 0:
                if self._confirm('Lens è in esecuzione. Terminarlo con pkill?\n(Necessario per applicare il nuovo .desktop)'):
                    self._run_cmd(['pkill', '-f', 'lens-desktop'])
                    self._log('Lens terminato. Riavvialo dal menu applicazioni.')
                else:
                    self._log('pkill annullato.')
            else:
                self._log('Lens non in esecuzione.')

        self._log(f'Fix {label} completato.')
        self.root.after(0, self.refresh_identity)

    # ── Login CLI ─────────────────────────────────────────────────────────

    def _do_login_cli(self, account_key: str):
        if not self._accounts:
            return
        info = self._accounts[account_key]
        label, email = info['label'], info['account']
        self._log(f'\n=== Login CLI [{label}] ===')
        self._open_terminal(
            f'gcloud auth login {email}',
            f'Login CLI completato per {label}. Chiudi questa finestra.',
        )

    # ── Login ADC ─────────────────────────────────────────────────────────

    def _do_login_adc(self, account_key: str):
        if not self._accounts:
            return
        info = self._accounts[account_key]
        label = info['label']
        self._log(f'\n=== Login ADC [{label}] ===')
        self._log('Nota: gcloud auth login --update-adc scrive SEMPRE in application_default_credentials.json')
        self._log('indipendentemente da GOOGLE_APPLICATION_CREDENTIALS.')
        self._log('GOOGLE_APPLICATION_CREDENTIALS rimossa per questa sessione (niente prompt Y/n).')
        self._log('Al termine: fix automatico per entrambi gli account.')
        # env -u evita il prompt GOOGLE_APPLICATION_CREDENTIALS senza toccare l'env del processo padre
        proc = self._open_terminal(
            'env -u GOOGLE_APPLICATION_CREDENTIALS gcloud auth login --update-adc',
            f'ADC [{label}] aggiornato. Chiudi questa finestra per avviare fix automatico.',
        )
        if proc is None:
            return
        proc.wait()
        self._log('\nTerminale chiuso — sincronizzazione ADC...')
        # Sync prima dei fix: copia application_default_credentials.json → adc-<label>.json
        # e ripristina application_default_credentials.json sull'account default ADC.
        # Necessario per creare adc-<label>.json su primo setup (es. adc-work.json).
        self._do_verify_sync_adc()
        self._adc_login_times[account_key] = time.time()
        self._log('Avvio fix automatico...')
        other_key = 'account_b' if account_key == 'account_a' else 'account_a'
        self._do_fix(account_key)
        if other_key in self._accounts:
            self._do_fix(other_key)

    # ── Verifica e sincronizza ADC ────────────────────────────────────────

    def _do_verify_sync_adc(self):
        self._log('\n=== Verifica e sincronizza ADC ===')
        accounts = self._accounts
        if not accounts:
            self._log('Nessuna configurazione.')
            return
        if not gcloud_utils.DEFAULT_ADC.exists():
            self._log(f'{gcloud_utils.DEFAULT_ADC} non esiste.')
            return

        self._log('Lettura identità application_default_credentials.json...')
        identity, err = gcloud_utils.get_adc_identity(gcloud_utils.DEFAULT_ADC)
        if err:
            self._log(f'ERRORE: {err}')
            return
        self._log(f'Identità rilevata: {identity}')

        matched_key = next(
            (k for k, v in accounts.items() if v.get('account') == identity),
            None,
        )
        if not matched_key:
            self._log(f'⚠ Identità {identity!r} non corrisponde a nessun account configurato.')
            return

        matched = accounts[matched_key]
        dest = Path(matched['adc_file'])
        self._log(f'$ cp {gcloud_utils.DEFAULT_ADC.name} → {dest.name}')
        shutil.copy2(str(gcloud_utils.DEFAULT_ADC), str(dest))
        self._log(f'Copiato → {dest.name}')

        default_key = config_utils.get_default_adc_key(accounts)
        if matched_key != default_key and default_key:
            restore_info = accounts[default_key]
            restore_from = Path(restore_info['adc_file'])
            if restore_from.exists():
                self._log(f'[{matched["label"]}] non è il default ADC.')
                self._log(f'$ cp {restore_from.name} → {gcloud_utils.DEFAULT_ADC.name}')
                shutil.copy2(str(restore_from), str(gcloud_utils.DEFAULT_ADC))
                self._log(f'DEFAULT_ADC ripristinato con identità [{restore_info["label"]}].')
            else:
                self._log(f'⚠ {restore_from.name} non esiste — impossibile ripristinare DEFAULT_ADC.')

        self._log('Sincronizzazione completata.')
        self.root.after(0, self.refresh_identity)

    # ── Rinnovo silenzioso ADC ─────────────────────────────────────────────

    def _do_silent_refresh_all(self):
        self._log('\n=== Rinnova token ADC (silenzioso) ===')
        if not self._accounts:
            self._log('Nessuna configurazione.')
            return
        any_failed = False
        for key in ('account_a', 'account_b'):
            info = self._accounts.get(key, {})
            label = info.get('label', key)
            adc_path = info.get('adc_file', '')
            if not adc_path or not Path(adc_path).exists():
                self._log(f'[{label}] file ADC mancante, skip.')
                any_failed = True
                continue
            self._log(f'[{label}] tentativo rinnovo...')
            ok, msg = gcloud_utils.silent_refresh_adc(Path(adc_path))
            self._log(f'[{label}] {msg}')
            if not ok:
                any_failed = True
        if any_failed:
            self._log('⚠ Uno o più token non rinnovabili — usa "Login ADC" per quell\'account.')
        else:
            self._log('✓ Tutti i token validi.')
        self.root.after(0, self.refresh_identity)

    # ── Test kubectl ──────────────────────────────────────────────────────

    def _do_test_kubectl(self):
        self._log('\n=== Test kubectl ===')

        self._run_cmd(['kubectl', 'config', 'current-context'])

        if shutil.which('gke-gcloud-auth-plugin'):
            self._log('Identità kubectl (via gke-gcloud-auth-plugin + tokeninfo)...')
            identity, err = gcloud_utils.get_kubectl_identity()
            if err:
                self._log(f'  ⚠ Errore identità: {err}')
            else:
                self._log(f'  Identità in uso: {identity}')
                kubectl_key = config_utils.get_kubectl_account_key(self._accounts or {})
                if kubectl_key and self._accounts:
                    expected = self._accounts[kubectl_key]['account']
                    if identity != expected:
                        self._log(f'  ⚠ Attesa {expected!r} — identità errata!')
        else:
            self._log('  gke-gcloud-auth-plugin non trovato, skip identity check.')

        self._log('$ kubectl get nodes --request-timeout=10s')
        r = subprocess.run(['kubectl', 'get', 'nodes', '--request-timeout=10s'],
                           capture_output=True, text=True)
        out = (r.stdout + r.stderr).strip()
        if out:
            self._log(out)

        if r.returncode != 0:
            low = (r.stdout + r.stderr).lower()
            if any(w in low for w in ('forbidden', 'unauthorized', 'denied')):
                kubectl_key = config_utils.get_kubectl_account_key(self._accounts or {})
                fix_label = (self._accounts[kubectl_key]['label']
                             if kubectl_key and self._accounts else 'kubectl')
                self._log(f'⚠ Accesso negato. Suggerimento: cancella plugin cache e clicca "Fix {fix_label}".')
        else:
            self._log('✓ kubectl get nodes OK.')

        self.root.after(0, self.refresh_identity)  # ricalcola highlight bottoni in base al risultato

    # ── Terminal helper ───────────────────────────────────────────────────

    # ── FortiVPN (terminale integrato, rete normale) ─────────────────

    def _connect_vpn(self):
        """openfortivpn in un pty integrato. File config custom (es. ~/Desktop/vpn),
        sudo tramite -S (password via dialog), OTP opzionale via --otp."""
        if self._vpn_proc and self._vpn_proc.is_running():
            messagebox.showinfo('FortiVPN', 'Connessione già attiva. Disconnetti prima.',
                                parent=self.root)
            return
        sel = self._vpn_list.curselection()
        if not sel or not self._vpn_configs:
            messagebox.showinfo('FortiVPN', 'Seleziona un file config dalla lista.',
                                parent=self.root)
            return
        path = str(self._vpn_configs[sel[0]])
        otp = self._vpn_otp_var.get().strip()
        pw = self._vpn_pw_var.get()

        # -c <file>: il file è un config; l'arg posizionale sarebbe l'host.
        # -S: sudo legge la password da stdin; -p marca il prompt per rilevarlo.
        argv = ['sudo', '-S', '-p', 'SUDO_PW_PROMPT:', 'openfortivpn', '-c', path]
        if otp:
            argv += ['--otp', otp]
        self._vpn_pw = pw
        self._vpn_pw_var.set('')  # non lasciare la password in chiaro nel campo
        self._vpn_pw_sent = False
        self._vpn_clear()
        self._vpn_append(f'$ sudo openfortivpn -c {path}'
                         + (' --otp ***' if otp else '') + '\n')
        self._vpn_proc = vpn_utils.PtyProcess(
            argv, on_output=self._vpn_on_output, on_exit=self._vpn_on_exit)
        self._vpn_set_state('connecting')
        self._vpn_proc.start()

    def _vpn_refresh_list(self):
        net_cfg = config_utils.load_network_config()
        vpn_dir = net_cfg['vpn_dir']
        self._vpn_dir_var.set(vpn_dir)
        self._vpn_configs = vpn_utils.list_configs(vpn_dir)
        self._vpn_list.delete(0, tk.END)
        for p in self._vpn_configs:
            self._vpn_list.insert(tk.END, p.name)
        if self._vpn_configs:
            self._vpn_list.selection_set(0)
        else:
            self._vpn_list.insert(tk.END, '(nessun file config trovato)')

    def _vpn_change_folder(self):
        net_cfg = config_utils.load_network_config()
        cur = Path(net_cfg['vpn_dir'])
        folder = filedialog.askdirectory(
            parent=self.root, title='Cartella config openfortivpn',
            initialdir=str(cur) if cur.is_dir() else str(Path.home()))
        if not folder or folder == net_cfg['vpn_dir']:
            return
        net_cfg['vpn_dir'] = folder
        config_utils.save_network_config(net_cfg)
        self._vpn_refresh_list()

    def _disconnect_vpn(self):
        if self._vpn_proc and self._vpn_proc.is_running():
            self._vpn_append('\n[Ctrl-C inviato — disconnessione in corso...]\n')
            self._vpn_proc.send_sigint()

    def _vpn_on_output(self, chunk: str):
        # Invia la password sudo appena compare il prompt, una sola volta.
        if not self._vpn_pw_sent and 'SUDO_PW_PROMPT:' in chunk:
            self._vpn_pw_sent = True
            self._vpn_proc.write((self._vpn_pw or '') + '\n')
            self._vpn_pw = None  # non tenerla in memoria
            chunk = chunk.replace('SUDO_PW_PROMPT:', '')
        if 'Tunnel is up and running' in chunk:
            self.root.after(0, lambda: self._vpn_set_state('connected'))
        self.root.after(0, lambda: self._vpn_append(chunk))

    def _vpn_on_exit(self, rc: int):
        def done():
            self._vpn_append(f'\n[openfortivpn terminato — codice {rc}]\n')
            self._vpn_set_state('disconnected')
        self.root.after(0, done)

    def _vpn_set_state(self, state: str):
        running = state in ('connecting', 'connected')
        self._vpn_connect_btn.configure(state=tk.DISABLED if running else tk.NORMAL)
        self._vpn_disconnect_btn.configure(state=tk.NORMAL if running else tk.DISABLED)
        label, badge, dot = {
            'connecting':   ('Connessione...', 'VPN connessione...', self.DOT_WARN),
            'connected':    ('Connesso',       'VPN connessa',       self.DOT_ON),
            'disconnected': ('Disconnesso',    'VPN disconnessa',    self.DOT_OFF),
        }[state]
        self._vpn_status_var.set(label)
        self._vpn_badge_var.set(badge)
        self._vpn_dot.configure(foreground=dot)
        self._vpn_status_dot.configure(foreground=dot)

    def _vpn_clear(self):
        self._vpn_term.config(state=tk.NORMAL)
        self._vpn_term.delete('1.0', tk.END)
        self._vpn_term.config(state=tk.DISABLED)

    # ANSI escape + C0 control (tranne \n \t) → vanno via, altrimenti "caratteri strani".
    _TERM_CLEAN = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]|[\x00-\x08\x0b-\x1f]')

    def _vpn_append(self, text: str):
        text = self._TERM_CLEAN.sub('', text.replace('\r\n', '\n').replace('\r', '\n'))
        self._vpn_term.config(state=tk.NORMAL)
        self._vpn_term.insert(tk.END, text)
        self._vpn_term.see(tk.END)
        self._vpn_term.config(state=tk.DISABLED)

    def _on_close(self):
        if self._vpn_proc and self._vpn_proc.is_running():
            if not messagebox.askyesno(
                    'FortiVPN', 'VPN ancora attiva. Disconnettere e uscire?',
                    parent=self.root):
                return
            self._vpn_proc.send_sigint()
            time.sleep(0.3)
        self.root.destroy()

    def _open_terminal(self, cmd: str, done_msg: str):
        bash_cmd = f'{cmd}; echo; echo "--- {done_msg} ---"; echo "Premi INVIO per chiudere."; read'
        term = None
        for t in ['x-terminal-emulator', 'konsole', 'gnome-terminal', 'xterm']:
            if shutil.which(t):
                term = t
                break

        if not term:
            self._log('ERRORE: nessun terminale trovato (x-terminal-emulator/konsole/gnome-terminal/xterm).')
            self._log(f'Lancia manualmente: {cmd}')
            return

        if term == 'gnome-terminal':
            args = [term, '--', 'bash', '-c', bash_cmd]
        elif term in ('konsole', 'xterm'):
            args = [term, '-e', 'bash', '-c', bash_cmd]
        else:  # x-terminal-emulator
            args = [term, '-e', f"bash -c '{bash_cmd}'"]

        self._log(f'Terminale: {term}')
        self._log(f'$ {cmd}')
        proc = subprocess.Popen(args)
        self._log('Terminale aperto. Completa l\'operazione nel browser.')
        return proc

    # ── Risorse locali (guida / note / setup) ───────────────────────────────

    def _open_guida(self):
        path = guida_path()
        if not path.exists():
            messagebox.showwarning('File non trovato', str(path))
            return
        GuidaDialog(self.root, path.read_text(encoding='utf-8'))

    def _open_note(self):
        if not NOTE_FILE.exists():
            messagebox.showwarning('File non trovato', str(NOTE_FILE))
            return
        win = tk.Toplevel(self.root)
        win.title('Note')
        win.geometry('820x620')
        win.resizable(True, True)
        txt = tk.Text(win, wrap=tk.WORD, font=('Monospace', 9), bg='#f8f8f8', padx=10, pady=8)
        sc = ttk.Scrollbar(win, command=txt.yview)
        txt.configure(yscrollcommand=sc.set)
        sc.pack(side=tk.RIGHT, fill=tk.Y)
        txt.pack(fill=tk.BOTH, expand=True)
        txt.insert('1.0', NOTE_FILE.read_text(encoding='utf-8'))
        txt.config(state=tk.DISABLED)
        ttk.Button(win, text='Chiudi', command=win.destroy).pack(pady=8)

    def _run_setup_local(self):
        if not SETUP_FILE.exists():
            messagebox.showwarning('File non trovato', str(SETUP_FILE))
            return
        self._open_terminal(f'python3 "{SETUP_FILE}"', 'Setup completato')

    # ── Global refresh ─────────────────────────────────────────────────────

    def refresh_all(self):
        self.refresh_prereq()
        self.refresh_network()
        self.refresh_identity()

    def _auto_refresh(self):
        self.refresh_all()
        self.root.after(5000, self._auto_refresh)


def main():
    root = tk.Tk()
    App(root)
    root.update_idletasks()
    w = max(root.winfo_reqwidth(), 800)
    h = max(root.winfo_reqheight(), 700)
    root.geometry(f'{w}x{h}')
    root.mainloop()


if __name__ == '__main__':
    main()
