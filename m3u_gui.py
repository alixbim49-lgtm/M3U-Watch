#!/usr/bin/env python3
"""
m3u_gui.py - Interface graphique de M3U Watch.

Modifie config.json (que tu peux aussi editer a la main : l'interface le relit
automatiquement) et affiche l'etat de chaque serveur. Les envois automatiques sont
assures par le service d'arriere-plan : python3 m3u_monitor.py --daemon
"""
import os
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import font as tkfont
from tkinter import messagebox, ttk

import m3u_monitor as mm

BG, CARD, HEADER = "#F3F5F9", "#FFFFFF", "#1B2A49"
PRIMARY, PRIMARY_HOVER = "#2F6BFF", "#2457D6"
TEXT, MUTED, LINE = "#1F2937", "#6B7280", "#E5E7EB"
OK, WARN, ERR = "#15803D", "#B45309", "#B91C1C"
STRIPE, SELECT = "#F8FAFC", "#DCE7FF"

DAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]
PRESETS = [("Tous les jours", 1, "days"), ("Tous les 2 jours", 2, "days"), ("Tous les 3 jours", 3, "days"),
           ("Toutes les semaines", 1, "weeks"), ("Personnalisé…", None, None)]

FAMILY = "TkDefaultFont"


# --------------------------------------------------------------- utilitaires --
def fmt_num(n):
    return "{:,}".format(int(n)).replace(",", "\u00a0")


def fmt_totals(d):
    return " - ".join(fmt_num((d or {}).get(k, 0)) for k in mm.KIND_ORDER)


def fmt_dt(s):
    d = mm.parse_dt(s)
    return d.strftime("%d/%m/%Y %H:%M") if d else "—"


def fmt_long(d):
    return "%s %d %s %d à %s" % (DAYS[d.weekday()], d.day, MONTHS[d.month - 1], d.year, d.strftime("%H:%M"))


def open_folder(path):
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        messagebox.showinfo("Dossier", "Dossier : %s\n(%s)" % (path, e))


def run_bg(widget, func, done):
    """Execute func dans un thread, puis done(resultat, erreur) dans l'interface."""
    box = {}

    def work():
        try:
            box["r"] = func()
        except Exception as e:  # noqa
            box["e"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()

    def poll():
        try:
            if not widget.winfo_exists():
                return
            if t.is_alive():
                widget.after(150, poll)
                return
            done(box.get("r"), box.get("e"))
        except tk.TclError:
            pass

    widget.after(150, poll)


def setup_style(root):
    global FAMILY
    style = ttk.Style(root)
    style.theme_use("clam")
    families = set(tkfont.families(root))
    FAMILY = next((f for f in ("Inter", "Segoe UI", "Noto Sans", "Ubuntu", "DejaVu Sans", "Helvetica")
                   if f in families), "TkDefaultFont")
    for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkFixedFont"):
        try:
            tkfont.nametofont(name).configure(family=FAMILY, size=10)
        except tk.TclError:
            pass
    root.configure(bg=BG)

    style.configure(".", background=BG, foreground=TEXT, bordercolor=LINE, focuscolor=BG)
    style.configure("TFrame", background=BG)
    style.configure("Card.TFrame", background=CARD)
    style.configure("Header.TFrame", background=HEADER)
    style.configure("TLabel", background=BG, foreground=TEXT)
    style.configure("Muted.TLabel", foreground=MUTED)
    style.configure("Header.TLabel", background=HEADER, foreground="#FFFFFF")
    style.configure("HeaderSub.TLabel", background=HEADER, foreground="#B8C4E0")
    style.configure("Result.TLabel", foreground=MUTED)

    style.configure("TButton", padding=(14, 8), relief="flat", borderwidth=0, background="#E6EAF3", foreground=TEXT)
    style.map("TButton", background=[("disabled", "#EEF0F5"), ("active", "#D9DFEC")],
              foreground=[("disabled", "#9CA3AF")])
    style.configure("Accent.TButton", background=PRIMARY, foreground="#FFFFFF")
    style.map("Accent.TButton", background=[("disabled", "#9DB7F5"), ("active", PRIMARY_HOVER)],
              foreground=[("disabled", "#FFFFFF")])
    style.configure("Danger.TButton", background="#FBE9E9", foreground=ERR)
    style.map("Danger.TButton", background=[("disabled", "#F6EEEE"), ("active", "#F6D5D5")],
              foreground=[("disabled", "#D6A5A5")])

    style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=TEXT, rowheight=38,
                    borderwidth=0, relief="flat")
    style.configure("Treeview.Heading", background="#EEF2F8", foreground=TEXT, relief="flat", padding=(10, 9),
                    font=(FAMILY, 10, "bold"))
    style.map("Treeview.Heading", background=[("active", "#E3E9F4")])
    style.map("Treeview", background=[("selected", SELECT)], foreground=[("selected", TEXT)])
    style.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])

    style.configure("TEntry", padding=6, fieldbackground="#FFFFFF")
    style.configure("TSpinbox", padding=4, fieldbackground="#FFFFFF", arrowsize=14)
    style.configure("TCombobox", padding=5, fieldbackground="#FFFFFF")
    style.map("TCombobox", fieldbackground=[("readonly", "#FFFFFF"), ("disabled", "#F1F3F7")],
              foreground=[("disabled", MUTED)])
    style.configure("Vertical.TScrollbar", background="#D5DAE5", troughcolor=CARD, bordercolor=CARD,
                    arrowcolor=MUTED, relief="flat", arrowsize=12)
    style.configure("TCheckbutton", background=BG)
    style.configure("TLabelframe", background=BG, bordercolor=LINE)
    style.configure("TLabelframe.Label", background=BG, foreground=TEXT, font=(FAMILY, 10, "bold"))
    style.configure("Horizontal.TProgressbar", background=PRIMARY, troughcolor="#E6EAF3", borderwidth=0)


def center_on(win, parent):
    win.update_idletasks()
    w, h = win.winfo_width(), win.winfo_height()
    x = parent.winfo_rootx() + max(0, (parent.winfo_width() - w) // 2)
    y = parent.winfo_rooty() + max(0, (parent.winfo_height() - h) // 3)
    win.geometry("+%d+%d" % (x, y))


# --------------------------------------------------------- dialogue serveur --
class ServerDialog(tk.Toplevel):
    def __init__(self, app, server=None):
        super().__init__(app)
        self.app, self.server, self.result = app, server or {}, None
        self.title("Modifier le serveur" if server else "Ajouter un serveur")
        self.configure(bg=BG)
        self.transient(app)
        self.resizable(False, False)

        self.name_var = tk.StringVar(value=self.server.get("name", ""))
        self.url_var = tk.StringVar(value=self.server.get("url", ""))
        self.token_var = tk.StringVar(value=self.server.get("telegram_token", ""))
        self.chat_var = tk.StringVar(value=self.server.get("telegram_chat_id", ""))
        self.enabled_var = tk.BooleanVar(value=self.server.get("enabled", True))
        self.show_var = tk.BooleanVar(value=False)

        body = ttk.Frame(self, padding=(22, 18, 22, 18))
        body.pack(fill="both", expand=True)

        # --- serveur
        box = ttk.Labelframe(body, text="Serveur IPTV", padding=14)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text="Nom").grid(row=0, column=0, sticky="w", pady=4)
        self.name_entry = ttk.Entry(box, textvariable=self.name_var, width=52)
        self.name_entry.grid(row=0, column=1, columnspan=2, sticky="ew", padx=(12, 0), pady=4)
        ttk.Label(box, text="Lien du serveur").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(box, textvariable=self.url_var, width=52).grid(row=1, column=1, columnspan=2, sticky="ew",
                                                                  padx=(12, 0), pady=4)
        ttk.Label(box, text="Lien M3U ou lien Xtream (get.php?username=…)", style="Muted.TLabel").grid(
            row=2, column=1, columnspan=2, sticky="w", padx=(12, 0))
        self.btn_srv = ttk.Button(box, text="Tester le serveur", command=self.test_server)
        self.btn_srv.grid(row=3, column=1, sticky="w", padx=(12, 0), pady=(10, 0))
        self.srv_res = ttk.Label(box, text="", style="Result.TLabel", wraplength=470, justify="left")
        self.srv_res.grid(row=4, column=1, columnspan=2, sticky="w", padx=(12, 0), pady=(6, 0))

        # --- telegram
        box2 = ttk.Labelframe(body, text="Bot Telegram", padding=14)
        box2.pack(fill="x", pady=(14, 0))
        box2.columnconfigure(1, weight=1)
        ttk.Label(box2, text="Token du bot").grid(row=0, column=0, sticky="w", pady=4)
        self.token_entry = ttk.Entry(box2, textvariable=self.token_var, width=44, show="•")
        self.token_entry.grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=4)
        ttk.Checkbutton(box2, text="Afficher", variable=self.show_var, command=self.toggle_token).grid(
            row=0, column=2, padx=(8, 0))
        ttk.Label(box2, text="Chaîne ou chat").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Entry(box2, textvariable=self.chat_var, width=44).grid(row=1, column=1, columnspan=2, sticky="ew",
                                                                    padx=(12, 0), pady=4)
        ttk.Label(box2, text="@nom_de_la_chaine, ou -100… si elle est privée. Le bot doit en être administrateur.",
                  style="Muted.TLabel", wraplength=470).grid(row=2, column=1, columnspan=2, sticky="w", padx=(12, 0))
        self.btn_tg = ttk.Button(box2, text="Tester Telegram", command=self.test_telegram)
        self.btn_tg.grid(row=3, column=1, sticky="w", padx=(12, 0), pady=(10, 0))
        self.tg_res = ttk.Label(box2, text="", style="Result.TLabel", wraplength=470, justify="left")
        self.tg_res.grid(row=4, column=1, columnspan=2, sticky="w", padx=(12, 0), pady=(6, 0))

        ttk.Checkbutton(body, text="Surveillance active pour ce serveur", variable=self.enabled_var).pack(
            anchor="w", pady=(14, 0))

        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(18, 0))
        ttk.Button(bar, text="Enregistrer", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(bar, text="Annuler", command=self.destroy).pack(side="right", padx=(0, 10))

        self.bind("<Escape>", lambda e: self.destroy())
        center_on(self, app)
        self.grab_set()
        self.name_entry.focus_set()

    def toggle_token(self):
        self.token_entry.config(show="" if self.show_var.get() else "•")

    def _show(self, label, ok, text):
        label.config(text=("✓ " if ok else "✗ ") + text, foreground=OK if ok else ERR)

    def test_server(self):
        url = self.url_var.get().strip()
        self.srv_res.config(text="Test en cours… (cela peut durer jusqu'à une minute)", foreground=MUTED)
        self.btn_srv.state(["disabled"])

        def done(r, e):
            self.btn_srv.state(["!disabled"])
            if e:
                self._show(self.srv_res, False, str(e))
            else:
                self._show(self.srv_res, r["ok"], r["message"])

        run_bg(self, lambda: mm.test_server(url, self.app.cfg["settings"]), done)

    def test_telegram(self):
        token, chat = self.token_var.get(), self.chat_var.get()
        self.tg_res.config(text="Envoi du message de test…", foreground=MUTED)
        self.btn_tg.state(["disabled"])

        def done(r, e):
            self.btn_tg.state(["!disabled"])
            if e:
                self._show(self.tg_res, False, str(e))
            else:
                self._show(self.tg_res, r[0], r[1])

        run_bg(self, lambda: mm.tg_test(token, chat), done)

    def save(self):
        name, url = self.name_var.get().strip(), self.url_var.get().strip()
        if not name:
            messagebox.showwarning("Serveur", "Donne un nom à ce serveur.", parent=self)
            return
        if not url.startswith(("http://", "https://")):
            messagebox.showwarning("Serveur", "Le lien doit commencer par http:// ou https://", parent=self)
            return
        token, chat = self.token_var.get().strip(), self.chat_var.get().strip()
        if not token or not chat:
            if not messagebox.askyesno("Telegram", "Le bot Telegram n'est pas complet : aucun message ne sera "
                                       "envoyé pour ce serveur.\nEnregistrer quand même ?", parent=self):
                return
        srv = dict(self.server)
        srv.update(id=srv.get("id") or mm.new_server_id(), name=name, url=url, telegram_token=token,
                   telegram_chat_id=chat, enabled=bool(self.enabled_var.get()))
        self.result = srv
        self.destroy()


# ------------------------------------------------------- dialogue paramètres --
class SettingsDialog(tk.Toplevel):
    def __init__(self, app):
        super().__init__(app)
        self.app, self.saved = app, False
        s = app.cfg["settings"]
        sched = s["schedule"]
        start = mm.parse_dt(sched.get("start")) or datetime.now().replace(hour=8, minute=0, second=0, microsecond=0)
        self.title("Paramètres")
        self.configure(bg=BG)
        self.transient(app)
        self.resizable(False, False)

        self.vars = {k: tk.StringVar(value=v) for k, v in
                     dict(day="%02d" % start.day, month="%02d" % start.month, year=str(start.year),
                          hour="%02d" % start.hour, minute="%02d" % start.minute).items()}
        n, unit = int(sched.get("every") or 1), sched.get("unit", "days")
        label = next((p[0] for p in PRESETS if p[1] == n and p[2] == unit), PRESETS[-1][0])
        self.preset_var = tk.StringVar(value=label)
        self.every_var = tk.StringVar(value=str(n))
        self.unit_var = tk.StringVar(value="semaine(s)" if unit == "weeks" else "jour(s)")
        self.max_items = tk.StringVar(value=str(s.get("max_items", 10)))
        self.photos = tk.BooleanVar(value=bool(s.get("send_photos", True)))
        self.delay = tk.StringVar(value=str(s.get("photo_delay", 3)))
        self.ua = tk.StringVar(value=s.get("user_agent", ""))
        self.timeout = tk.StringVar(value=str(s.get("timeout", 120)))
        self.series_checks = tk.StringVar(value=str(s.get("max_series_checks", 30)))

        body = ttk.Frame(self, padding=(22, 18, 22, 18))
        body.pack(fill="both", expand=True)

        # --- planification
        box = ttk.Labelframe(body, text="Planification des envois", padding=14)
        box.pack(fill="x")
        ttk.Label(box, text="Premier envoi").grid(row=0, column=0, sticky="w", pady=4)
        row = ttk.Frame(box)
        row.grid(row=0, column=1, sticky="w", padx=(12, 0))
        for key, lo, hi, w in (("day", 1, 31, 3), ("month", 1, 12, 3), ("year", 2024, 2100, 5)):
            ttk.Spinbox(row, from_=lo, to=hi, width=w, textvariable=self.vars[key], wrap=True).pack(side="left")
            if key != "year":
                ttk.Label(row, text="/").pack(side="left", padx=3)
        ttk.Label(row, text="   à   ").pack(side="left")
        ttk.Spinbox(row, from_=0, to=23, width=3, textvariable=self.vars["hour"], format="%02.0f", wrap=True).pack(side="left")
        ttk.Label(row, text=":").pack(side="left", padx=3)
        ttk.Spinbox(row, from_=0, to=59, width=3, textvariable=self.vars["minute"], format="%02.0f", wrap=True).pack(side="left")

        ttk.Label(box, text="Récurrence").grid(row=1, column=0, sticky="w", pady=8)
        row2 = ttk.Frame(box)
        row2.grid(row=1, column=1, sticky="w", padx=(12, 0))
        self.preset_box = ttk.Combobox(row2, textvariable=self.preset_var, state="readonly", width=20,
                                       values=[p[0] for p in PRESETS])
        self.preset_box.pack(side="left")
        self.preset_box.bind("<<ComboboxSelected>>", lambda e: self.on_preset())
        ttk.Label(row2, text="   tous les ").pack(side="left")
        self.every_spin = ttk.Spinbox(row2, from_=1, to=365, width=4, textvariable=self.every_var)
        self.every_spin.pack(side="left")
        self.unit_box = ttk.Combobox(row2, textvariable=self.unit_var, state="readonly", width=10,
                                     values=["jour(s)", "semaine(s)"])
        self.unit_box.pack(side="left", padx=(6, 0))

        self.preview = ttk.Label(box, text="", style="Muted.TLabel", wraplength=540, justify="left")
        self.preview.grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ttk.Label(box, text="Les envois partent même si la fenêtre est fermée, tant que le service tourne.",
                  style="Muted.TLabel", wraplength=540).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 0))

        # --- messages
        box2 = ttk.Labelframe(body, text="Contenu des messages", padding=14)
        box2.pack(fill="x", pady=(14, 0))
        ttk.Label(box2, text="Éléments détaillés par envoi").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Spinbox(box2, from_=1, to=100, width=5, textvariable=self.max_items).grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Checkbutton(box2, text="Envoyer les jaquettes (une photo par ajout)", variable=self.photos).grid(
            row=1, column=0, columnspan=2, sticky="w", pady=4)
        ttk.Label(box2, text="Pause entre deux photos (s)").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Spinbox(box2, from_=0, to=30, width=5, textvariable=self.delay).grid(row=2, column=1, sticky="w", padx=(12, 0))

        # --- avance
        box3 = ttk.Labelframe(body, text="Avancé", padding=14)
        box3.pack(fill="x", pady=(14, 0))
        box3.columnconfigure(1, weight=1)
        ttk.Label(box3, text="Identifiant (User-Agent)").grid(row=0, column=0, sticky="w", pady=4)
        ttk.Entry(box3, textvariable=self.ua).grid(row=0, column=1, sticky="ew", padx=(12, 0), pady=4)
        ttk.Label(box3, text="Délai de téléchargement (s)").grid(row=1, column=0, sticky="w", pady=4)
        ttk.Spinbox(box3, from_=10, to=900, width=6, textvariable=self.timeout).grid(row=1, column=1, sticky="w", padx=(12, 0))
        ttk.Label(box3, text="Séries examinées par passage").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Spinbox(box3, from_=1, to=300, width=6, textvariable=self.series_checks).grid(row=2, column=1, sticky="w", padx=(12, 0))

        foot = ttk.Frame(body)
        foot.pack(fill="x", pady=(14, 0))
        ttk.Label(foot, text="Fichier : %s" % mm.CONFIG_PATH, style="Muted.TLabel", wraplength=330).pack(side="left")
        ttk.Button(foot, text="Ouvrir le dossier", command=lambda: open_folder(mm.HOME)).pack(side="right")

        bar = ttk.Frame(body)
        bar.pack(fill="x", pady=(16, 0))
        ttk.Button(bar, text="Enregistrer", style="Accent.TButton", command=self.save).pack(side="right")
        ttk.Button(bar, text="Annuler", command=self.destroy).pack(side="right", padx=(0, 10))

        for v in list(self.vars.values()) + [self.every_var, self.unit_var]:
            v.trace_add("write", lambda *a: self.update_preview())
        self.on_preset(initial=True)
        self.bind("<Escape>", lambda e: self.destroy())
        center_on(self, app)
        self.grab_set()

    def on_preset(self, initial=False):
        p = next(p for p in PRESETS if p[0] == self.preset_var.get())
        custom = p[1] is None
        if not custom:
            self.every_var.set(str(p[1]))
            self.unit_var.set("semaine(s)" if p[2] == "weeks" else "jour(s)")
        state = "normal" if custom else "disabled"
        self.every_spin.config(state=state)
        self.unit_box.config(state="readonly" if custom else "disabled")
        self.update_preview()

    def schedule(self):
        """Construit la planification depuis les champs (ValueError si invalide)."""
        v = self.vars
        start = datetime(int(v["year"].get()), int(v["month"].get()), int(v["day"].get()),
                         int(v["hour"].get()), int(v["minute"].get()))
        every = int(self.every_var.get())
        if every < 1:
            raise ValueError("récurrence")
        return {"start": mm.iso(start), "every": every, "unit": "weeks" if self.unit_var.get().startswith("sem") else "days"}

    def update_preview(self):
        try:
            sched = self.schedule()
        except (ValueError, tk.TclError):
            self.preview.config(text="Date ou heure invalide.", foreground=ERR)
            return
        now, slots = datetime.now(), []
        slot = mm.next_slot(sched, now)
        for _ in range(3):
            slots.append(slot.strftime("%a %d/%m %H:%M").replace("Mon", "lun.").replace("Tue", "mar.").replace(
                "Wed", "mer.").replace("Thu", "jeu.").replace("Fri", "ven.").replace("Sat", "sam.").replace("Sun", "dim."))
            slot = slot + mm.interval(sched)
        self.preview.config(text="Prochains envois : " + "  ·  ".join(slots), foreground=MUTED)

    def save(self):
        try:
            sched = self.schedule()
            max_items, delay = int(self.max_items.get()), float(self.delay.get())
            timeout, checks = int(self.timeout.get()), int(self.series_checks.get())
        except (ValueError, tk.TclError):
            messagebox.showwarning("Paramètres", "Une valeur est invalide (date, heure ou nombre).", parent=self)
            return
        cfg = mm.load_config()
        cfg["settings"].update(schedule=sched, max_items=max(1, max_items), send_photos=bool(self.photos.get()),
                               photo_delay=max(0, delay), user_agent=self.ua.get().strip() or mm.default_settings()["user_agent"],
                               timeout=max(10, timeout), max_series_checks=max(1, checks))
        mm.save_config(cfg)
        self.saved = True
        self.destroy()


# ---------------------------------------------------------- fenêtre principale --
class App(tk.Tk):
    COLS = (("name", "Serveur", 190, "w"),
            ("last_send", "Dernier envoi", 140, "center"),
            ("totals", "Total  (chaînes - films - séries)", 270, "center"),
            ("added", "Ajoutés  (chaînes - films - séries)", 285, "center"),
            ("last_check", "Dernière vérif.", 140, "center"),
            ("status", "Statut", 200, "w"))

    def __init__(self):
        super().__init__()
        self.title("M3U Watch")
        self.geometry("1300x680")
        self.minsize(1000, 520)
        setup_style(self)

        self.cfg, self.stats = mm.load_config(), mm.load_stats()
        self.busy = False
        self._mtimes = {}

        # en-tete
        head = ttk.Frame(self, style="Header.TFrame", padding=(26, 20, 26, 20))
        head.pack(fill="x")
        ttk.Label(head, text="M3U Watch", style="Header.TLabel", font=(FAMILY, 20, "bold")).pack(anchor="w")
        self.sub_var = tk.StringVar()
        ttk.Label(head, textvariable=self.sub_var, style="HeaderSub.TLabel", font=(FAMILY, 11)).pack(anchor="w", pady=(4, 0))

        # barre d'outils
        tool = ttk.Frame(self, padding=(26, 18, 26, 10))
        tool.pack(fill="x")
        self.btn_add = ttk.Button(tool, text="Ajouter un serveur", style="Accent.TButton", command=self.on_add)
        self.btn_add.pack(side="left")
        self.btn_edit = ttk.Button(tool, text="Modifier", command=self.on_edit)
        self.btn_edit.pack(side="left", padx=(10, 0))
        self.btn_del = ttk.Button(tool, text="Supprimer", style="Danger.TButton", command=self.on_delete)
        self.btn_del.pack(side="left", padx=(10, 0))
        self.btn_run = ttk.Button(tool, text="Vérifier maintenant", command=self.on_run)
        self.btn_run.pack(side="left", padx=(24, 0))
        self.btn_set = ttk.Button(tool, text="Paramètres", command=self.on_settings)
        self.btn_set.pack(side="right")

        # pied de page et detail (empiles en bas d'abord, pour rester visibles)
        foot = ttk.Frame(self, padding=(26, 8, 26, 14))
        foot.pack(side="bottom", fill="x")
        self.detail_var = tk.StringVar()
        ttk.Label(self, textvariable=self.detail_var, style="Muted.TLabel", wraplength=1100).pack(
            side="bottom", fill="x", padx=28, pady=(10, 0), anchor="w")

        # tableau
        wrap = ttk.Frame(self, padding=(26, 0, 26, 0))
        wrap.pack(fill="both", expand=True)
        card = tk.Frame(wrap, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        card.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(card, columns=[c[0] for c in self.COLS], show="headings", selectmode="browse",
                                 height=6)
        for cid, title, width, anchor in self.COLS:
            self.tree.heading(cid, text=title, anchor="w" if anchor == "w" else "center")
            self.tree.column(cid, width=width, anchor=anchor, stretch=True)
        sb = ttk.Scrollbar(card, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        for tag, fg in (("err", ERR), ("wait", WARN), ("off", MUTED)):
            self.tree.tag_configure(tag, foreground=fg)
        self.tree.tag_configure("odd", background=STRIPE)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self.update_detail())
        self.tree.bind("<Double-1>", lambda e: self.on_edit())
        self.tree.bind("<Delete>", lambda e: self.on_delete())
        self.empty = tk.Label(card, bg=CARD, fg=MUTED, font=(FAMILY, 12), justify="center",
                              text="Aucun serveur pour l'instant.\nClique sur « Ajouter un serveur » pour commencer.")

        self.dot = tk.Canvas(foot, width=12, height=12, bg=BG, highlightthickness=0)
        self.dot.pack(side="left")
        self.dot_id = self.dot.create_oval(1, 1, 11, 11, fill=MUTED, outline="")
        self.svc_var = tk.StringVar()
        ttk.Label(foot, textvariable=self.svc_var, style="Muted.TLabel").pack(side="left", padx=(8, 0))
        self.pb = ttk.Progressbar(foot, mode="indeterminate", length=140)
        self.msg_var = tk.StringVar()
        self.msg_label = ttk.Label(foot, textvariable=self.msg_var, style="Muted.TLabel")

        self.refresh()
        self.after(2000, self.poll)

    # ---- donnees
    def server_id_selected(self):
        sel = self.tree.selection()
        return sel[0] if sel else None

    def find(self, sid):
        return next((s for s in self.cfg["servers"] if mm.server_id(s) == sid), None)

    def status_of(self, srv, st):
        if not srv.get("enabled", True):
            return "○  Désactivé", "off"
        if not st or not st.get("last_check"):
            return "●  À vérifier", "wait"
        s = st.get("status")
        if s == "ok":
            return "●  OK", "ok"
        if s == "initialized":
            return "●  Initialisé", "ok"
        if s == "telegram_error":
            return "●  Erreur Telegram", "err"
        return "●  Erreur de lecture", "err"

    def refresh(self):
        keep = self.server_id_selected()
        self.tree.delete(*self.tree.get_children())
        servers = self.cfg["servers"]
        for i, srv in enumerate(servers):
            sid = mm.server_id(srv)
            st = self.stats["servers"].get(sid, {})
            ls = st.get("last_send")
            text, tag = self.status_of(srv, st)
            values = (srv.get("name", "?"), fmt_dt(ls["at"]) if ls else "—",
                      fmt_totals(st["totals"]) if st.get("totals") else "—",
                      fmt_totals(ls["added"]) if ls else "—",
                      fmt_dt(st.get("last_check")), text)
            self.tree.insert("", "end", iid=sid, values=values, tags=(tag, "odd") if i % 2 else (tag,))
        if keep and self.tree.exists(keep):
            self.tree.selection_set(keep)
        if servers:
            self.empty.place_forget()
        else:
            self.empty.place(relx=0.5, rely=0.5, anchor="center")
            self.empty.lift()
        self.update_header()
        self.update_service()
        self.update_detail()

    def update_header(self):
        sched = self.cfg["settings"]["schedule"]
        if self.cfg["servers"]:
            self.sub_var.set("Prochain envoi : %s  ·  %s" % (fmt_long(mm.next_slot(sched)), mm.describe_interval(sched)))
        else:
            self.sub_var.set("Aucun serveur configuré")

    def update_service(self):
        age = mm.heartbeat_age()
        active = age is not None and age < 120
        self.dot.itemconfig(self.dot_id, fill=OK if active else ERR)
        self.svc_var.set("Service d'arrière-plan actif" if active else
                         "Service arrêté : les envois automatiques ne partiront pas (voir README, étape « service »)")

    def update_detail(self):
        sid = self.server_id_selected()
        srv = self.find(sid) if sid else None
        st = self.stats["servers"].get(sid, {}) if sid else {}
        if srv and st.get("error"):
            self.detail_var.set("Détail : %s" % st["error"])
        elif srv and st.get("last_send"):
            n = st["last_send"].get("series_updates", 0)
            self.detail_var.set("Dernier envoi : %s%s" % (fmt_dt(st["last_send"]["at"]),
                                                          ("  ·  %d série(s) avec nouveaux épisodes" % n) if n else ""))
        else:
            self.detail_var.set("")

    def poll(self):
        """Relit config.json et les statistiques s'ils ont change (modif manuelle, service)."""
        try:
            changed = False
            for key, path in (("cfg", mm.CONFIG_PATH), ("stats", mm.STATS_PATH)):
                m = path.stat().st_mtime if path.exists() else 0
                if self._mtimes.get(key) != m:
                    self._mtimes[key] = m
                    changed = True
            if changed and not self._has_dialog():
                self.cfg, self.stats = mm.load_config(), mm.load_stats()
                self.refresh()
            else:
                self.update_service()
        except Exception:
            pass
        self.after(2000, self.poll)

    def _has_dialog(self):
        return any(isinstance(w, tk.Toplevel) for w in self.winfo_children())

    # ---- actions
    def set_busy(self, busy, text=""):
        self.busy = busy
        for b in (self.btn_add, self.btn_edit, self.btn_del, self.btn_run, self.btn_set):
            b.state(["disabled"] if busy else ["!disabled"])
        if busy:
            self.pb.pack(side="right")
            self.pb.start(12)
            self.msg_label.pack(side="right", padx=(0, 12))
        else:
            self.pb.stop()
            self.pb.pack_forget()
        self.msg_var.set(text)

    def on_add(self):
        dlg = ServerDialog(self)
        self.wait_window(dlg)
        if not dlg.result:
            return
        cfg = mm.load_config()
        cfg["servers"].append(dlg.result)
        mm.save_config(cfg)
        self.cfg = mm.load_config()
        self.refresh()
        if messagebox.askyesno("Première vérification", "Vérifier ce serveur maintenant ?\n"
                               "Le premier passage enregistre simplement le catalogue actuel (aucune alerte).", parent=self):
            self.run_ids({mm.server_id(dlg.result)})

    def on_edit(self):
        sid = self.server_id_selected()
        srv = self.find(sid) if sid else None
        if not srv:
            messagebox.showinfo("Modifier", "Sélectionne d'abord un serveur dans le tableau.")
            return
        dlg = ServerDialog(self, srv)
        self.wait_window(dlg)
        if not dlg.result:
            return
        cfg = mm.load_config()
        cfg["servers"] = [dlg.result if mm.server_id(s) == sid else s for s in cfg["servers"]]
        mm.save_config(cfg)
        if dlg.result["url"] != srv.get("url"):
            mm.forget_server(sid, srv.get("url"))
        self.cfg, self.stats = mm.load_config(), mm.load_stats()
        self.refresh()

    def on_delete(self):
        sid = self.server_id_selected()
        srv = self.find(sid) if sid else None
        if not srv:
            return
        if not messagebox.askyesno("Supprimer", "Supprimer le serveur « %s » et son historique ?" % srv.get("name", "?")):
            return
        cfg = mm.load_config()
        cfg["servers"] = [s for s in cfg["servers"] if mm.server_id(s) != sid]
        mm.save_config(cfg)
        mm.forget_server(sid, srv.get("url"))
        self.cfg, self.stats = mm.load_config(), mm.load_stats()
        self.refresh()

    def on_settings(self):
        dlg = SettingsDialog(self)
        self.wait_window(dlg)
        if dlg.saved:
            self.cfg = mm.load_config()
            self.refresh()

    def on_run(self):
        sid = self.server_id_selected()
        targets = [s for s in self.cfg["servers"] if s.get("enabled", True) and (sid is None or mm.server_id(s) == sid)]
        if not targets:
            messagebox.showinfo("Vérifier", "Aucun serveur actif à vérifier.")
            return
        what = "« %s »" % targets[0].get("name", "?") if len(targets) == 1 else "les %d serveurs" % len(targets)
        if not messagebox.askyesno("Vérifier maintenant", "Vérifier %s maintenant et envoyer les changements "
                                   "sur Telegram ?" % what):
            return
        self.run_ids({mm.server_id(s) for s in targets})

    def run_ids(self, ids):
        self.set_busy(True, "Vérification en cours…")

        def done(results, err):
            self.set_busy(False)
            self.cfg, self.stats = mm.load_config(), mm.load_stats()
            self.refresh()
            if isinstance(err, mm.RunLocked):
                messagebox.showinfo("Vérifier", "Une vérification est déjà en cours (service d'arrière-plan).\n"
                                    "Réessaie dans un instant.")
            elif err:
                messagebox.showerror("Vérifier", str(err))
            else:
                bad = [r for r in results if r["status"] in ("error", "telegram_error")]
                self.msg_var.set("Terminé : %d serveur(s) vérifié(s)%s" % (len(results), (", %d en erreur" % len(bad)) if bad else ""))
                self.msg_label.pack(side="right")

        run_bg(self, lambda: mm.run_servers(self.cfg, ids), done)


def main():
    App().mainloop()


if __name__ == "__main__":
    main()
