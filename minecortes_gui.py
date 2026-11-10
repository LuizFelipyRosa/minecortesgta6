#!/usr/bin/env python3
"""MineCortes - interface gráfica. Rode:  python minecortes_gui.py"""
import datetime as dt
import os
import queue
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import colorchooser, filedialog, font as tkfont, messagebox, ttk

import minecortes as mc

BG, PANEL, FG, MUTED, ACCENT = "#17171c", "#22222b", "#ececf1", "#9a9aab", "#ff4fa3"

STATE_TXT = {
    "pendente": "⏳ aguardando", "agendado_youtube": "📅 agendado no YouTube",
    "postado": "✔ postado", "erro": "✖ erro", "perdido": "⚠ perdido",
}
NUMERIC_STYLE = {"size", "words", "outline", "shadow", "box_opacity", "box_pad", "pos_y"}


def fmt_t(sec):
    sec = int(round(sec))
    return f"{sec // 3600}:{sec // 60 % 60:02d}:{sec % 60:02d}"


def parse_t(txt):
    parts = [float(p) for p in txt.strip().replace(",", ".").split(":")]
    total = 0.0
    for p in parts:
        total = total * 60 + p
    return total


def open_path(p):
    try:
        if sys.platform.startswith("win"):
            os.startfile(p)  # noqa
        elif sys.platform == "darwin":
            subprocess.Popen(["open", p])
        else:
            subprocess.Popen(["xdg-open", p])
    except Exception as ex:
        messagebox.showerror("MineCortes", f"Não consegui abrir:\n{ex}")


class App:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("MineCortes")
        self.root.geometry("1240x820")
        self.root.minsize(1100, 720)
        self.cfg = mc.load_config()
        self.uiq = queue.Queue()
        self.analysis = None
        self.clips = []
        self.busy = False
        self.queue_active = True
        self.stop_all = threading.Event()
        self.stop_auto = threading.Event()
        self.auto_thread = None
        self.tmpdir = tempfile.mkdtemp(prefix="minecortes_")
        self._prev_job = None
        self._prev_img = None
        self._prev_n = 0
        self._loading = False
        self.action_buttons = []

        self.setup_style()
        self.make_vars()
        self.build_ui()
        self.refresh_queue_view()
        self.root.after(80, self.pump)
        self.root.after(5000, self.tick_view)
        threading.Thread(target=self.scheduler_loop, daemon=True).start()
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.update_video_info()
        self.schedule_preview()

    # ------------------------------------------------------------------ infraestrutura
    def ui(self, fn):
        self.uiq.put(fn)

    def pump(self):
        try:
            while True:
                self.uiq.get_nowait()()
        except queue.Empty:
            pass
        self.root.after(80, self.pump)

    def log(self, msg):
        def _do():
            self.logbox.configure(state="normal")
            self.logbox.insert("end", f"{dt.datetime.now():%H:%M:%S}  {msg}\n")
            self.logbox.see("end")
            self.logbox.configure(state="disabled")
            self.status.set(msg[:140])
        self.ui(_do)

    def progress(self, frac, msg=None):
        def _do():
            self.pbar["value"] = max(0, min(1, frac)) * 100
            if msg:
                self.status.set(msg)
        self.ui(_do)

    def cb(self, frac, msg):
        self.progress(frac, msg)
        if "%" not in msg:  # não enche o log com cada % da transcrição
            self.log(msg)

    def set_busy(self, flag):
        self.busy = flag
        for b in self.action_buttons:
            b.configure(state="disabled" if flag else "normal")
        if not flag:
            self.pbar["value"] = 0

    def error(self, ex):
        self.set_busy(False)
        self.log(f"ERRO: {ex}")
        messagebox.showerror("MineCortes", str(ex))

    def setup_style(self):
        s = ttk.Style(self.root)
        s.theme_use("clam")
        s.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=PANEL,
                    lightcolor=PANEL, darkcolor=PANEL, troughcolor=PANEL, focuscolor=ACCENT,
                    font=("Segoe UI", 10))
        s.configure("TNotebook", background=BG, borderwidth=0)
        s.configure("TNotebook.Tab", padding=(18, 9), background=PANEL, foreground=MUTED)
        s.map("TNotebook.Tab", background=[("selected", BG)], foreground=[("selected", ACCENT)])
        s.configure("TButton", padding=7, background=PANEL, foreground=FG)
        s.map("TButton", background=[("active", "#2e2e3a")])
        s.configure("Accent.TButton", background=ACCENT, foreground="#ffffff", padding=10,
                    font=("Segoe UI", 10, "bold"))
        s.map("Accent.TButton", background=[("active", "#ff72b6"), ("disabled", "#5a3347")])
        s.configure("Treeview", background=PANEL, fieldbackground=PANEL, foreground=FG, rowheight=28,
                    borderwidth=0)
        s.map("Treeview", background=[("selected", "#4a2b45")])
        s.configure("Treeview.Heading", background="#2e2e3a", foreground=FG, padding=6)
        for w in ("TCheckbutton", "TRadiobutton", "TLabelframe", "TLabel"):
            s.configure(w, background=BG)
        s.map("TCheckbutton", background=[("active", BG)])
        s.map("TRadiobutton", background=[("active", BG)])
        s.configure("TLabelframe.Label", background=BG, foreground=ACCENT, font=("Segoe UI", 10, "bold"))
        s.configure("Muted.TLabel", foreground=MUTED)
        s.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL)
        s.configure("Horizontal.TScale", background=BG)
        self.root.configure(bg=BG)
        self.root.option_add("*TCombobox*Listbox.background", PANEL)
        self.root.option_add("*TCombobox*Listbox.foreground", FG)

    # ------------------------------------------------------------------ variáveis
    @staticmethod
    def _mk(val):
        if isinstance(val, bool):
            return tk.BooleanVar(value=val)
        if isinstance(val, (int, float)):
            return tk.DoubleVar(value=val)
        return tk.StringVar(value=str(val))

    def make_vars(self):
        s = self.cfg["settings"]
        if not s.get("when_date"):
            s["when_date"] = (dt.date.today() + dt.timedelta(days=1)).isoformat()
        keys = [k for k in mc.DEFAULT_SETTINGS if k not in ("platforms", "style", "style_name")]
        self.v = {k: self._mk(s[k]) for k in keys}
        self.plat = {p: tk.BooleanVar(value=p in s["platforms"]) for p in ("youtube", "tiktok", "instagram")}
        self.style_name = tk.StringVar(value=s.get("style_name", "Clássico amarelo"))
        self.new_style_name = tk.StringVar()
        st = mc.full_style(s["style"])
        self.sty = {k: self._mk(val) for k, val in st.items()}
        self.prev_time = tk.DoubleVar(value=10)
        self.acc = {k: tk.StringVar(value=val) for k, val in self.cfg["accounts"].items()}
        for var in list(self.sty.values()) + [self.v["layout"], self.v["focus"], self.prev_time]:
            var.trace_add("write", self.schedule_preview)
        self.v["video"].trace_add("write", lambda *_: self.root.after(300, self.update_video_info))

    def get_style(self):
        out = {}
        for k, var in self.sty.items():
            val = var.get()
            out[k] = int(round(float(val))) if k in NUMERIC_STYLE else val
        return out

    def get_settings(self):
        s = {}
        for k, var in self.v.items():
            val = var.get()
            default = mc.DEFAULT_SETTINGS[k]
            if isinstance(default, bool):
                s[k] = bool(val)
            elif isinstance(default, int):
                s[k] = int(round(float(val)))
            elif isinstance(default, float):
                s[k] = float(val)
            else:
                s[k] = str(val)
        s["platforms"] = [p for p, v in self.plat.items() if v.get()]
        s["style"] = self.get_style()
        s["style_name"] = self.style_name.get()
        return s

    def save_all(self):
        self.cfg["settings"] = self.get_settings()
        for k, var in self.acc.items():
            self.cfg["accounts"][k] = var.get().strip()
        mc.save_config(self.cfg)

    # ------------------------------------------------------------------ construção da janela
    def build_ui(self):
        head = tk.Frame(self.root, bg=BG)
        head.pack(fill="x", padx=16, pady=(12, 0))
        tk.Label(head, text="🌴 MineCortes", bg=BG, fg=ACCENT, font=("Segoe UI", 18, "bold")).pack(side="left")
        tk.Label(head, text="cortes de live com legenda, zoom e postagem agendada", bg=BG, fg=MUTED,
                 font=("Segoe UI", 10)).pack(side="left", padx=12, pady=(8, 0))

        self.nb = ttk.Notebook(self.root)
        self.nb.pack(fill="both", expand=True, padx=12, pady=10)
        tabs = [("1  Vídeo", self.tab_video), ("2  Cortes", self.tab_clips),
                ("3  Legendas", self.tab_captions), ("4  Publicar e agendar", self.tab_publish),
                ("Automático", self.tab_auto), ("Contas", self.tab_accounts)]
        for title, builder in tabs:
            f = ttk.Frame(self.nb, padding=14)
            self.nb.add(f, text=title)
            builder(f)

        bottom = tk.Frame(self.root, bg=BG)
        bottom.pack(fill="x", padx=14, pady=(0, 12))
        self.pbar = ttk.Progressbar(bottom, maximum=100)
        self.pbar.pack(fill="x")
        self.status = tk.StringVar(value="Pronto.")
        ttk.Label(bottom, textvariable=self.status, style="Muted.TLabel").pack(anchor="w", pady=(4, 2))
        self.logbox = tk.Text(bottom, height=4, bg=PANEL, fg=FG, relief="flat", state="disabled",
                              font=("Consolas", 9), insertbackground=FG)
        self.logbox.pack(fill="x")

    # --- helpers de widgets
    def entry_row(self, parent, r, text, var, width=None, browse=None, show=None):
        ttk.Label(parent, text=text).grid(row=r, column=0, sticky="w", padx=6, pady=5)
        e = ttk.Entry(parent, textvariable=var, width=width or 40, show=show)
        e.grid(row=r, column=1, sticky="ew", padx=6)
        if browse:
            ttk.Button(parent, text="Escolher…", command=browse).grid(row=r, column=2, padx=6)
        return e

    def combo_row(self, parent, r, text, var, values, width=18):
        ttk.Label(parent, text=text).grid(row=r, column=0, sticky="w", padx=6, pady=5)
        c = ttk.Combobox(parent, textvariable=var, values=values, width=width, state="readonly")
        c.grid(row=r, column=1, sticky="w", padx=6)
        return c

    def spin_row(self, parent, r, text, var, lo, hi, inc=1, width=8):
        ttk.Label(parent, text=text).grid(row=r, column=0, sticky="w", padx=6, pady=5)
        s = ttk.Spinbox(parent, textvariable=var, from_=lo, to=hi, increment=inc, width=width)
        s.grid(row=r, column=1, sticky="w", padx=6)
        return s

    def slider_row(self, parent, r, text, var, lo, hi, fmt="{:.0f}"):
        ttk.Label(parent, text=text).grid(row=r, column=0, sticky="w", padx=6, pady=3)
        ttk.Scale(parent, from_=lo, to=hi, variable=var, orient="horizontal", length=190).grid(
            row=r, column=1, sticky="w", padx=6)
        lab = ttk.Label(parent, width=6, style="Muted.TLabel")
        lab.grid(row=r, column=2, sticky="w")
        upd = lambda *_: lab.configure(text=fmt.format(var.get()))
        var.trace_add("write", upd)
        upd()

    def color_row(self, parent, r, text, key):
        ttk.Label(parent, text=text).grid(row=r, column=0, sticky="w", padx=6, pady=3)
        btn = tk.Button(parent, width=10, relief="flat", bd=0, cursor="hand2")
        btn.grid(row=r, column=1, sticky="w", padx=6)

        def refresh(*_):
            c = self.sty[key].get()
            btn.configure(bg=c, activebackground=c, text=c.upper(),
                          fg="#000000" if sum(mc._rgb(c)) > 380 else "#ffffff")

        def pick():
            res = colorchooser.askcolor(color=self.sty[key].get(), title=text)
            if res and res[1]:
                self.sty[key].set(res[1])

        btn.configure(command=pick)
        self.sty[key].trace_add("write", refresh)
        refresh()

    # ------------------------------------------------------------------ aba 1: vídeo
    def tab_video(self, f):
        f.columnconfigure(0, weight=1)
        box = ttk.LabelFrame(f, text="Sua live", padding=12)
        box.grid(row=0, column=0, sticky="ew")
        box.columnconfigure(1, weight=1)
        self.entry_row(box, 0, "Arquivo de vídeo", self.v["video"], browse=self.pick_video)
        self.info_lbl = ttk.Label(box, text="", style="Muted.TLabel")
        self.info_lbl.grid(row=1, column=1, sticky="w", padx=6)

        opt = ttk.LabelFrame(f, text="Como cortar", padding=12)
        opt.grid(row=1, column=0, sticky="ew", pady=10)
        opt.columnconfigure(3, weight=1)
        left = ttk.Frame(opt)
        left.grid(row=0, column=0, sticky="nw")
        self.combo_row(left, 0, "Idioma da fala", self.v["lang"], ["pt", "en", "es", "fr", "de", "it", "ja"], 8)
        self.combo_row(left, 1, "Transcrição", self.v["model"], ["tiny", "base", "small", "medium"], 10)
        ttk.Label(left, text="tiny = rápido · medium = mais preciso", style="Muted.TLabel").grid(
            row=2, column=1, sticky="w", padx=6)
        self.spin_row(left, 3, "Quantos cortes", self.v["n"], 1, 60)
        self.spin_row(left, 4, "Duração de cada corte (s)", self.v["len"], 10, 180, 5)

        right = ttk.Frame(opt)
        right.grid(row=0, column=1, sticky="nw", padx=40)
        ttk.Label(right, text="Enquadramento vertical (9:16)").grid(row=0, column=0, columnspan=3, sticky="w", pady=3)
        ttk.Radiobutton(right, text="Vídeo inteiro com fundo desfocado", variable=self.v["layout"],
                        value="blur").grid(row=1, column=0, columnspan=3, sticky="w")
        ttk.Radiobutton(right, text="Preencher a tela (corta as laterais)", variable=self.v["layout"],
                        value="preencher").grid(row=2, column=0, columnspan=3, sticky="w")
        self.slider_row(right, 3, "   Foco (esq → dir)", self.v["focus"], 0, 1, "{:.2f}")
        ttk.Checkbutton(right, text="Zoom automático nos picos de emoção", variable=self.v["zoom"]).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(8, 0))
        self.slider_row(right, 5, "   Força do zoom", self.v["zoom_strength"], 0.05, 0.4, "{:.2f}")

        out = ttk.Frame(f)
        out.grid(row=2, column=0, sticky="ew")
        out.columnconfigure(1, weight=1)
        self.entry_row(out, 0, "Pasta de saída", self.v["out"], browse=self.pick_out)

        row = ttk.Frame(f)
        row.grid(row=3, column=0, sticky="w", pady=16)
        b = ttk.Button(row, text="🔍  Analisar live e achar os melhores momentos", style="Accent.TButton",
                       command=self.start_analysis)
        b.pack(side="left")
        self.action_buttons.append(b)
        ttk.Label(f, text="Dica: a 1ª análise de uma live longa demora (a transcrição fica salva e "
                          "as próximas são rápidas).", style="Muted.TLabel").grid(row=4, column=0, sticky="w")

    def pick_video(self):
        p = filedialog.askopenfilename(title="Escolha a live", filetypes=[
            ("Vídeos", "*.mp4 *.mkv *.mov *.flv *.ts *.webm"), ("Todos", "*.*")])
        if p:
            self.v["video"].set(p)

    def pick_out(self):
        p = filedialog.askdirectory(title="Pasta de saída")
        if p:
            self.v["out"].set(p)

    def update_video_info(self):
        p = self.v["video"].get().strip()
        if not p or not os.path.exists(p):
            self.info_lbl.configure(text="")
            return
        try:
            d = mc.video_duration(p)
            self.info_lbl.configure(text=f"Duração: {fmt_t(d)}")
            self.vid_dur = d
            self.prev_scale.configure(to=max(5, d - 1))
            self.schedule_preview()
        except Exception:
            self.info_lbl.configure(text="(não consegui ler o vídeo)")

    def start_analysis(self):
        if self.busy:
            return
        s = self.get_settings()
        if not os.path.exists(s["video"]):
            messagebox.showwarning("MineCortes", "Escolha o arquivo de vídeo primeiro.")
            return
        self.set_busy(True)
        self.log("Analisando a live...")

        def work():
            try:
                a = mc.analyze(s["video"], s["lang"], s["model"], self.cb)
                clips = mc.find_clips(a, s["n"], s["len"])
                self.ui(lambda: self.on_analyzed(a, clips))
            except Exception as ex:
                self.ui(lambda ex=ex: self.error(ex))

        threading.Thread(target=work, daemon=True).start()

    def on_analyzed(self, a, clips):
        self.analysis, self.clips = a, clips
        self.set_busy(False)
        self.fill_clip_tree()
        self.log(f"{len(clips)} melhores momentos encontrados.")
        if clips:
            self.prev_time.set(clips[0]["start"] + 2)
        self.nb.select(1)
        self.schedule_preview()

    # ------------------------------------------------------------------ aba 2: cortes
    def tab_clips(self, f):
        f.columnconfigure(0, weight=1)
        f.rowconfigure(0, weight=1)
        cols = ("sel", "n", "ini", "fim", "dur", "nota", "status", "titulo")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", selectmode="browse")
        for c, t, w in [("sel", "✔", 40), ("n", "#", 40), ("ini", "Início", 80), ("fim", "Fim", 80),
                        ("dur", "Dur.", 60), ("nota", "Nota", 60), ("status", "Estado", 110),
                        ("titulo", "Título (vira o título do post)", 560)]:
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w" if c == "titulo" else "center", stretch=(c == "titulo"))
        sb = ttk.Scrollbar(f, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<Button-1>", self.on_tree_click)
        self.tree.bind("<<TreeviewSelect>>", self.on_clip_select)

        ed = ttk.LabelFrame(f, text="Editar o corte selecionado", padding=10)
        ed.grid(row=1, column=0, columnspan=2, sticky="ew", pady=8)
        ed.columnconfigure(5, weight=1)
        self.e_start, self.e_end, self.e_title = tk.StringVar(), tk.StringVar(), tk.StringVar()
        ttk.Label(ed, text="Início").grid(row=0, column=0, padx=4)
        ttk.Entry(ed, textvariable=self.e_start, width=10).grid(row=0, column=1, padx=4)
        ttk.Label(ed, text="Fim").grid(row=0, column=2, padx=4)
        ttk.Entry(ed, textvariable=self.e_end, width=10).grid(row=0, column=3, padx=4)
        ttk.Label(ed, text="Título").grid(row=0, column=4, padx=4)
        ttk.Entry(ed, textvariable=self.e_title).grid(row=0, column=5, sticky="ew", padx=4)
        ttk.Button(ed, text="Aplicar", command=self.apply_edit).grid(row=0, column=6, padx=4)
        ttk.Label(ed, text="Tempo em mm:ss ou h:mm:ss. Ao editar, o corte precisa ser gerado de novo.",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=7, sticky="w", pady=(6, 0))

        bar = ttk.Frame(f)
        bar.grid(row=2, column=0, columnspan=2, sticky="ew")
        ttk.Button(bar, text="Marcar todos", command=lambda: self.mark_all(True)).pack(side="left", padx=3)
        ttk.Button(bar, text="Desmarcar", command=lambda: self.mark_all(False)).pack(side="left", padx=3)
        ttk.Button(bar, text="+ Corte manual", command=self.add_manual).pack(side="left", padx=3)
        ttk.Button(bar, text="▶ Abrir corte pronto", command=self.open_clip).pack(side="left", padx=3)
        ttk.Button(bar, text="📁 Abrir pasta", command=self.open_out_dir).pack(side="left", padx=3)
        b = ttk.Button(bar, text="🎬  Gerar cortes marcados", style="Accent.TButton", command=self.render_selected)
        b.pack(side="right", padx=3)
        self.action_buttons.append(b)

        lg = ttk.LabelFrame(f, text="Vídeo longo com os melhores momentos", padding=10)
        lg.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(lg, text="Duração final (min)").pack(side="left", padx=4)
        ttk.Spinbox(lg, textvariable=self.v["minutes"], from_=1, to=180, width=6).pack(side="left", padx=4)
        ttk.Label(lg, text="Trecho de cada momento (s)").pack(side="left", padx=(16, 4))
        ttk.Spinbox(lg, textvariable=self.v["seg_len"], from_=10, to=120, increment=5, width=6).pack(side="left", padx=4)
        b2 = ttk.Button(lg, text="🎞  Gerar vídeo longo", style="Accent.TButton", command=self.make_long)
        b2.pack(side="right", padx=4)
        self.action_buttons.append(b2)

    def fill_clip_tree(self):
        self.tree.delete(*self.tree.get_children())
        for c in self.clips:
            self.tree.insert("", "end", iid=str(c["id"]), values=self.clip_row(c))

    def clip_row(self, c, status=None):
        st = status or ("pronto ✔" if c.get("file") else "—")
        return ("☑" if c["selected"] else "☐", c["id"], fmt_t(c["start"]), fmt_t(c["end"]),
                f"{int(c['end'] - c['start'])}s", f"{c['score']:.1f}", st, c["title"])

    def clip_by_iid(self, iid):
        return next((c for c in self.clips if str(c["id"]) == str(iid)), None)

    def set_clip_status(self, c, status):
        if self.tree.exists(str(c["id"])):
            self.tree.item(str(c["id"]), values=self.clip_row(c, status))

    def on_tree_click(self, e):
        if self.tree.identify_region(e.x, e.y) == "cell" and self.tree.identify_column(e.x) == "#1":
            c = self.clip_by_iid(self.tree.identify_row(e.y))
            if c:
                c["selected"] = not c["selected"]
                self.set_clip_status(c, None)

    def on_clip_select(self, _e=None):
        sel = self.tree.selection()
        c = self.clip_by_iid(sel[0]) if sel else None
        if c:
            self.e_start.set(fmt_t(c["start"]))
            self.e_end.set(fmt_t(c["end"]))
            self.e_title.set(c["title"])
            self.prev_time.set(c["start"] + 2)

    def apply_edit(self):
        sel = self.tree.selection()
        c = self.clip_by_iid(sel[0]) if sel else None
        if not c:
            return
        try:
            a, b = parse_t(self.e_start.get()), parse_t(self.e_end.get())
            if b - a < 3:
                raise ValueError("O corte precisa ter pelo menos 3 segundos.")
        except ValueError as ex:
            messagebox.showwarning("MineCortes", f"Tempo inválido: {ex}")
            return
        c.update(start=a, end=b, title=self.e_title.get().strip() or c["title"], file=None)
        self.set_clip_status(c, None)

    def mark_all(self, flag):
        for c in self.clips:
            c["selected"] = flag
            self.set_clip_status(c, None)

    def add_manual(self):
        if not self.analysis:
            messagebox.showinfo("MineCortes", "Analise uma live primeiro (aba 1).")
            return
        n = max([c["id"] for c in self.clips] + [0]) + 1
        s = self.get_settings()
        c = {"id": n, "start": 0.0, "end": float(s["len"]), "score": 0.0,
             "title": "Corte manual", "selected": True, "file": None}
        self.clips.append(c)
        self.tree.insert("", "end", iid=str(n), values=self.clip_row(c))
        self.tree.selection_set(str(n))

    def open_clip(self):
        sel = self.tree.selection()
        c = self.clip_by_iid(sel[0]) if sel else None
        if c and c.get("file") and os.path.exists(c["file"]):
            open_path(c["file"])
        else:
            messagebox.showinfo("MineCortes", "Esse corte ainda não foi gerado.")

    def open_out_dir(self):
        if self.analysis:
            open_path(str(mc.out_dir_for(self.v["out"].get(), self.analysis["video"])))

    def out_path(self, c):
        d = mc.out_dir_for(self.v["out"].get(), self.analysis["video"])
        return d / f"corte_{c['id']:02d}_{int(c['start'])}s.mp4"

    def render_selected(self, then=None):
        if self.busy:
            return
        if not self.analysis:
            messagebox.showinfo("MineCortes", "Analise uma live primeiro (aba 1).")
            return
        todo = [c for c in self.clips if c["selected"]]
        if not todo:
            messagebox.showinfo("MineCortes", "Marque pelo menos um corte.")
            return
        self.set_busy(True)
        self.render_worker(todo, then)

    def render_worker(self, todo, then=None):
        s = self.get_settings()
        self.save_all()

        def work():
            try:
                for i, c in enumerate(todo, 1):
                    if c.get("file") and os.path.exists(c["file"]):
                        continue
                    self.ui(lambda c=c: self.set_clip_status(c, "gerando…"))
                    path = self.out_path(c)
                    mc.render_clip(self.analysis["video"], c, self.analysis, s, path)
                    c["file"] = str(path)
                    self.progress(i / len(todo), f"Corte {i}/{len(todo)} pronto")
                    self.ui(lambda c=c: self.set_clip_status(c, None))
                self.ui(lambda: self.progress(1.0, f"{len(todo)} corte(s) gerado(s)."))
                if then:
                    then(todo)
                else:
                    self.ui(lambda: self.set_busy(False))
            except Exception as ex:
                self.ui(lambda ex=ex: self.error(ex))

        threading.Thread(target=work, daemon=True).start()

    def make_long(self):
        if self.busy:
            return
        if not self.analysis:
            messagebox.showinfo("MineCortes", "Analise uma live primeiro (aba 1).")
            return
        s = self.get_settings()
        self.set_busy(True)

        def work():
            try:
                d = mc.out_dir_for(s["out"], self.analysis["video"])
                out = mc.compile_long(self.analysis["video"], self.analysis, s["minutes"], s["seg_len"], d, self.cb)
                self.ui(lambda: (self.set_busy(False), self.log(f"Vídeo longo salvo em {out}"),
                                 open_path(str(d))))
            except Exception as ex:
                self.ui(lambda ex=ex: self.error(ex))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ aba 3: legendas
    def tab_captions(self, f):
        f.rowconfigure(0, weight=1)
        # coluna 1: texto e posição
        c1 = ttk.LabelFrame(f, text="Texto", padding=10)
        c1.grid(row=0, column=0, sticky="nw")
        r = 0
        ttk.Checkbutton(c1, text="Legendas ativadas", variable=self.sty["enabled"]).grid(
            row=r, column=0, columnspan=3, sticky="w", pady=4)
        r += 1
        ttk.Label(c1, text="Estilo pronto").grid(row=r, column=0, sticky="w", padx=6, pady=4)
        self.preset_cb = ttk.Combobox(c1, textvariable=self.style_name, state="readonly", width=22,
                                      values=self.preset_names())
        self.preset_cb.grid(row=r, column=1, columnspan=2, sticky="w", padx=6)
        self.preset_cb.bind("<<ComboboxSelected>>", lambda _e: self.apply_preset(self.style_name.get()))
        r += 1
        ttk.Label(c1, text="Salvar meu estilo").grid(row=r, column=0, sticky="w", padx=6, pady=4)
        ttk.Entry(c1, textvariable=self.new_style_name, width=14).grid(row=r, column=1, sticky="w", padx=6)
        ttk.Button(c1, text="Salvar", command=self.save_custom_style).grid(row=r, column=2, padx=4)
        r += 1
        ttk.Label(c1, text="Fonte").grid(row=r, column=0, sticky="w", padx=6, pady=4)
        fams = sorted(set(tkfont.families(self.root)))
        ttk.Combobox(c1, textvariable=self.sty["font"], values=fams, width=22).grid(
            row=r, column=1, columnspan=2, sticky="w", padx=6)
        r += 1
        self.slider_row(c1, r, "Tamanho", self.sty["size"], 40, 180); r += 1
        ttk.Checkbutton(c1, text="Negrito", variable=self.sty["bold"]).grid(row=r, column=0, sticky="w", padx=6)
        ttk.Checkbutton(c1, text="MAIÚSCULAS", variable=self.sty["uppercase"]).grid(row=r, column=1, sticky="w", padx=6)
        r += 1
        ttk.Label(c1, text="Modo").grid(row=r, column=0, sticky="nw", padx=6, pady=4)
        mf = ttk.Frame(c1)
        mf.grid(row=r, column=1, columnspan=2, sticky="w", padx=6)
        for txt, val in [("Destaca a palavra", "destaque"), ("Uma palavra", "palavra"), ("Frase inteira", "linha")]:
            ttk.Radiobutton(mf, text=txt, variable=self.sty["mode"], value=val).pack(anchor="w")
        r += 1
        self.slider_row(c1, r, "Palavras por bloco", self.sty["words"], 1, 6); r += 1
        self.slider_row(c1, r, "Altura (% do topo)", self.sty["pos_y"], 10, 92)

        # coluna 2: cores e efeitos
        c2 = ttk.LabelFrame(f, text="Cores e efeitos", padding=10)
        c2.grid(row=0, column=1, sticky="nw", padx=12)
        r = 0
        self.color_row(c2, r, "Cor do texto", "color"); r += 1
        self.color_row(c2, r, "Cor do destaque", "highlight"); r += 1
        self.color_row(c2, r, "Cor do contorno", "outline_color"); r += 1
        self.slider_row(c2, r, "Contorno", self.sty["outline"], 0, 16); r += 1
        self.slider_row(c2, r, "Sombra", self.sty["shadow"], 0, 10); r += 1
        ttk.Checkbutton(c2, text="Fundo em caixa atrás do texto", variable=self.sty["box"]).grid(
            row=r, column=0, columnspan=3, sticky="w", padx=6, pady=(10, 0)); r += 1
        self.color_row(c2, r, "   Cor da caixa", "box_color"); r += 1
        self.slider_row(c2, r, "   Opacidade (%)", self.sty["box_opacity"], 10, 100); r += 1
        ttk.Checkbutton(c2, text="Animação de entrada (pulinho)", variable=self.sty["pop"]).grid(
            row=r, column=0, columnspan=3, sticky="w", padx=6, pady=(10, 0))

        # coluna 3: prévia
        right = ttk.LabelFrame(f, text="Prévia ao vivo", padding=10)
        right.grid(row=0, column=2, sticky="n")
        self.prev_lbl = tk.Label(right, bg=PANEL, width=34, height=22, fg=MUTED, text="gerando prévia…")
        self.prev_lbl.pack()
        ttk.Label(right, text="Momento do vídeo", style="Muted.TLabel").pack(anchor="w", pady=(8, 0))
        self.prev_scale = ttk.Scale(right, from_=0, to=600, variable=self.prev_time, orient="horizontal",
                                    length=270)
        self.prev_scale.pack(anchor="w")
        ttk.Label(right, text="Usa o vídeo da aba 1\n(ou fundo liso se não escolheu).",
                  style="Muted.TLabel").pack(anchor="w", pady=(4, 0))

    def preset_names(self):
        return list(mc.PRESETS) + [f"★ {n}" for n in self.cfg["custom_styles"]]

    def apply_preset(self, name):
        if name.startswith("★ "):
            data = self.cfg["custom_styles"].get(name[2:], {})
        else:
            data = mc.PRESETS.get(name, {})
        st = mc.full_style(data)
        for k, val in st.items():
            self.sty[k].set(val)
        self.schedule_preview()

    def save_custom_style(self):
        name = self.new_style_name.get().strip()
        if not name:
            messagebox.showinfo("MineCortes", "Digite um nome para o seu estilo.")
            return
        self.cfg["custom_styles"][name] = self.get_style()
        mc.save_config(self.cfg)
        self.preset_cb.configure(values=self.preset_names())
        self.style_name.set(f"★ {name}")
        self.log(f"Estilo '{name}' salvo.")

    def schedule_preview(self, *_):
        if self._prev_job:
            try:
                self.root.after_cancel(self._prev_job)
            except Exception:
                pass
        self._prev_job = self.root.after(450, self.refresh_preview)

    def refresh_preview(self):
        self._prev_job = None
        st, s = self.get_style(), self.get_settings()
        video = s["video"] if os.path.exists(s["video"]) else None
        t = float(self.prev_time.get())
        sample = None
        if self.analysis:
            ws = [w[2] for w in self.analysis["words"] if w[0] >= t][:6]
            sample = ws or None
        self._prev_n += 1
        n = self._prev_n
        png = os.path.join(self.tmpdir, f"prev_{n}.png")

        def work():
            try:
                mc.make_preview(video, t, st, s, sample, png, 270)
                self.ui(lambda: self.show_preview(png, n))
            except Exception as ex:
                self.ui(lambda ex=ex: self.prev_lbl.configure(image="", text=f"prévia indisponível\n{str(ex)[:120]}"))

        threading.Thread(target=work, daemon=True).start()

    def show_preview(self, png, n):
        if n != self._prev_n:
            return
        img = tk.PhotoImage(file=png)
        self._prev_img = img
        self.prev_lbl.configure(image=img, text="", width=img.width(), height=img.height())

    # ------------------------------------------------------------------ aba 4: publicar e agendar
    def tab_publish(self, f):
        f.columnconfigure(0, weight=1)
        f.rowconfigure(1, weight=1)
        top = ttk.Frame(f)
        top.grid(row=0, column=0, sticky="ew")
        a = ttk.LabelFrame(top, text="Onde postar", padding=10)
        a.grid(row=0, column=0, sticky="nw")
        ttk.Checkbutton(a, text="YouTube (Shorts)", variable=self.plat["youtube"]).grid(row=0, column=0, sticky="w")
        ttk.Checkbutton(a, text="TikTok", variable=self.plat["tiktok"]).grid(row=1, column=0, sticky="w")
        ttk.Checkbutton(a, text="Instagram (Reels)", variable=self.plat["instagram"]).grid(row=2, column=0, sticky="w")
        self.combo_row(a, 3, "Visib. YouTube", self.v["yt_privacy"], ["private", "unlisted", "public"], 10)
        self.combo_row(a, 4, "Visib. TikTok", self.v["tt_privacy"],
                       ["SELF_ONLY", "MUTUAL_FOLLOW_FRIENDS", "PUBLIC_TO_EVERYONE"], 22)

        b = ttk.LabelFrame(top, text="Texto do post", padding=10)
        b.grid(row=0, column=1, sticky="nw", padx=12)
        b.columnconfigure(1, weight=1)
        self.entry_row(b, 0, "Hashtags", self.v["hashtags"], width=34)
        self.entry_row(b, 1, "Descrição extra", self.v["description"], width=34)
        ttk.Label(b, text="O título de cada post é o título do corte (aba 2).", style="Muted.TLabel").grid(
            row=2, column=0, columnspan=2, sticky="w", padx=6)

        c = ttk.LabelFrame(top, text="Quando postar", padding=10)
        c.grid(row=0, column=2, sticky="nw")
        ttk.Radiobutton(c, text="Assim que ficar pronto", variable=self.v["when_mode"], value="now").grid(
            row=0, column=0, columnspan=4, sticky="w")
        ttk.Radiobutton(c, text="Agendar", variable=self.v["when_mode"], value="schedule").grid(
            row=1, column=0, sticky="w")
        ttk.Entry(c, textvariable=self.v["when_date"], width=11).grid(row=1, column=1, padx=3)
        ttk.Entry(c, textvariable=self.v["when_time"], width=6).grid(row=1, column=2, padx=3)
        ttk.Label(c, text="(AAAA-MM-DD  HH:MM)", style="Muted.TLabel").grid(row=1, column=3, padx=3)
        ttk.Label(c, text="Intervalo entre cortes (h)").grid(row=2, column=0, sticky="w", pady=4)
        ttk.Spinbox(c, textvariable=self.v["step_hours"], from_=0, to=72, width=5).grid(row=2, column=1, sticky="w", padx=3)
        ttk.Label(c, text="0 = todos no mesmo horário", style="Muted.TLabel").grid(row=2, column=2, columnspan=2, sticky="w")
        btn = ttk.Button(c, text="➕  Adicionar cortes marcados à fila", style="Accent.TButton",
                         command=self.enqueue_checked)
        btn.grid(row=3, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.action_buttons.append(btn)

        qf = ttk.LabelFrame(f, text="Fila de postagens", padding=8)
        qf.grid(row=1, column=0, sticky="nsew", pady=10)
        qf.columnconfigure(0, weight=1)
        qf.rowconfigure(0, weight=1)
        cols = ("titulo", "quando", "youtube", "tiktok", "instagram")
        self.qtree = ttk.Treeview(qf, columns=cols, show="headings", selectmode="extended")
        for col, t, w in [("titulo", "Corte", 330), ("quando", "Quando", 120), ("youtube", "YouTube", 190),
                          ("tiktok", "TikTok", 130), ("instagram", "Instagram", 130)]:
            self.qtree.heading(col, text=t)
            self.qtree.column(col, width=w, anchor="w")
        sb = ttk.Scrollbar(qf, command=self.qtree.yview)
        self.qtree.configure(yscrollcommand=sb.set)
        self.qtree.grid(row=0, column=0, sticky="nsew")
        sb.grid(row=0, column=1, sticky="ns")
        self.qtree.bind("<Double-1>", lambda _e: self.show_details())

        bar = ttk.Frame(qf)
        bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        self.active_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(bar, text="Fila ativa (posta sozinha na hora marcada)", variable=self.active_var,
                        command=lambda: setattr(self, "queue_active", self.active_var.get())).pack(side="left")
        ttk.Button(bar, text="Detalhes", command=self.show_details).pack(side="right", padx=3)
        ttk.Button(bar, text="Remover", command=self.remove_selected).pack(side="right", padx=3)
        ttk.Button(bar, text="Postar agora", command=self.post_now).pack(side="right", padx=3)
        ttk.Label(f, text="📅 YouTube: o horário é gravado no próprio YouTube, então publica mesmo com o PC desligado.\n"
                          "⏰ TikTok e Instagram não aceitam agendamento por API: o MineCortes precisa estar ABERTO "
                          "(e o PC ligado) na hora marcada.",
                  style="Muted.TLabel", justify="left").grid(row=2, column=0, sticky="w")

    def when_list(self, n):
        s = self.get_settings()
        if s["when_mode"] == "now":
            return [None] * n
        try:
            first = dt.datetime.strptime(f"{s['when_date']} {s['when_time']}", "%Y-%m-%d %H:%M")
        except ValueError:
            raise ValueError("Data/hora inválida. Use AAAA-MM-DD e HH:MM (ex.: 2026-11-12 18:00).")
        if first < dt.datetime.now():
            raise ValueError("Essa data/hora já passou.")
        return mc.spread_times(first, float(s["step_hours"]), n)

    def enqueue_checked(self):
        if self.busy:
            return
        todo = [c for c in self.clips if c["selected"]]
        s = self.get_settings()
        if not self.analysis or not todo:
            messagebox.showinfo("MineCortes", "Analise a live e marque os cortes (abas 1 e 2).")
            return
        if not s["platforms"]:
            messagebox.showinfo("MineCortes", "Marque pelo menos uma plataforma.")
            return
        try:
            times = self.when_list(len(todo))
        except ValueError as ex:
            messagebox.showwarning("MineCortes", str(ex))
            return
        self.set_busy(True)

        def after_render(done):
            try:
                for c, when in zip(done, times):
                    cap = mc.build_caption(c["title"], s["hashtags"], s["description"])
                    mc.add_queue_item(c["file"], c["title"], cap, s["platforms"], when,
                                      {"youtube": s["yt_privacy"], "tiktok": s["tt_privacy"]})
                self.log(f"{len(done)} corte(s) adicionados à fila.")
                self.ui(self.refresh_queue_view)
                self.ui(lambda: self.set_busy(False))
                self.run_queue_async()
            except Exception as ex:
                self.ui(lambda ex=ex: self.error(ex))

        self.render_worker(todo, after_render)

    def run_queue_async(self, force_ids=None):
        def work():
            try:
                mc.process_queue(self.cfg, self.log, force_ids)
            except Exception as ex:
                self.log(f"Fila: erro {ex}")
            self.ui(self.refresh_queue_view)
        threading.Thread(target=work, daemon=True).start()

    def refresh_queue_view(self):
        sel = self.qtree.selection()
        self.qtree.delete(*self.qtree.get_children())
        for it in mc.load_queue():
            row = [it["title"], it["when"].replace("T", " ") if it["when"] else "assim que possível"]
            for p in ("youtube", "tiktok", "instagram"):
                st = it["status"].get(p)
                row.append(STATE_TXT.get(st["state"], st["state"]) if st else "—")
            self.qtree.insert("", "end", iid=it["id"], values=row)
        for i in sel:
            if self.qtree.exists(i):
                self.qtree.selection_add(i)

    def tick_view(self):
        self.refresh_queue_view()
        self.root.after(5000, self.tick_view)

    def selected_items(self):
        return list(self.qtree.selection())

    def post_now(self):
        ids = self.selected_items()
        if not ids:
            messagebox.showinfo("MineCortes", "Selecione itens na fila.")
            return
        if messagebox.askyesno("MineCortes", f"Postar {len(ids)} item(ns) AGORA, ignorando o horário?"):
            self.run_queue_async(ids)

    def remove_selected(self):
        ids = self.selected_items()
        if ids and messagebox.askyesno(
                "MineCortes", "Remover da fila?\n(Vídeos já agendados no YouTube continuam agendados lá.)"):
            mc.remove_queue_items(ids)
            self.refresh_queue_view()

    def show_details(self):
        ids = self.selected_items()
        if not ids:
            return
        it = next((i for i in mc.load_queue() if i["id"] == ids[0]), None)
        if it:
            lines = [it["title"], f"Arquivo: {it['file']}", ""]
            for p, st in it["status"].items():
                lines.append(f"{p}: {STATE_TXT.get(st['state'], st['state'])}\n   {st.get('detail', '')}")
            messagebox.showinfo("Detalhes", "\n".join(lines))

    def scheduler_loop(self):
        while not self.stop_all.wait(20):
            if self.queue_active:
                try:
                    if mc.process_queue(self.cfg, self.log):
                        self.ui(self.refresh_queue_view)
                except Exception as ex:
                    self.log(f"Fila: erro {ex}")

    # ------------------------------------------------------------------ aba automático
    def tab_auto(self, f):
        f.columnconfigure(1, weight=1)
        ttk.Label(f, text="Deixe o MineCortes vigiando uma pasta: cada live nova que aparecer é analisada, "
                          "cortada, legendada e vai para a fila de postagem, espaçada no tempo.",
                  wraplength=900, justify="left").grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 10))
        self.entry_row(f, 1, "Pasta das lives", self.v["auto_folder"], browse=self.pick_auto)
        self.spin_row(f, 2, "Primeiro post daqui a (min)", self.v["auto_delay"], 0, 1440, 5)
        self.spin_row(f, 3, "Intervalo entre posts (h)", self.v["auto_every"], 0, 72, 1)
        ttk.Label(f, text="Usa as opções das outras abas: quantidade e duração dos cortes, legendas, "
                          "plataformas e visibilidade.", style="Muted.TLabel").grid(
            row=4, column=0, columnspan=3, sticky="w", padx=6, pady=6)
        self.auto_btn = ttk.Button(f, text="▶  Ligar modo automático", style="Accent.TButton", command=self.toggle_auto)
        self.auto_btn.grid(row=5, column=0, columnspan=2, sticky="w", padx=6, pady=14)
        self.auto_lbl = ttk.Label(f, text="Desligado.", style="Muted.TLabel")
        self.auto_lbl.grid(row=6, column=0, columnspan=3, sticky="w", padx=6)

    def pick_auto(self):
        p = filedialog.askdirectory(title="Pasta das lives")
        if p:
            self.v["auto_folder"].set(p)

    def toggle_auto(self):
        if self.auto_thread and self.auto_thread.is_alive():
            self.stop_auto.set()
            self.auto_btn.configure(text="▶  Ligar modo automático")
            self.auto_lbl.configure(text="Desligando…")
            return
        folder = self.v["auto_folder"].get()
        if not os.path.isdir(folder):
            messagebox.showwarning("MineCortes", "Escolha a pasta das lives.")
            return
        if not self.get_settings()["platforms"] and not messagebox.askyesno(
                "MineCortes", "Nenhuma plataforma marcada (aba 4). Vai só gerar os cortes, sem postar. Continuar?"):
            return
        self.save_all()
        self.stop_auto = threading.Event()
        self.auto_thread = threading.Thread(
            target=mc.watch_folder, args=(folder, self.cfg, self.log, self.cb, self.stop_auto), daemon=True)
        self.auto_thread.start()
        self.auto_btn.configure(text="■  Desligar modo automático")
        self.auto_lbl.configure(text=f"Ligado, vigiando {folder}")

    # ------------------------------------------------------------------ aba contas
    def tab_accounts(self, f):
        f.columnconfigure(1, weight=1)
        yt = ttk.LabelFrame(f, text="YouTube", padding=10)
        yt.grid(row=0, column=0, sticky="ew", pady=4)
        yt.columnconfigure(1, weight=1)
        self.entry_row(yt, 0, "client_secret.json", self.acc["yt_client_secret"], browse=self.pick_secret)
        ttk.Button(yt, text="Conectar minha conta do YouTube", command=self.connect_youtube).grid(
            row=1, column=1, sticky="w", padx=6, pady=6)
        tt = ttk.LabelFrame(f, text="TikTok", padding=10)
        tt.grid(row=1, column=0, sticky="ew", pady=4)
        tt.columnconfigure(1, weight=1)
        self.entry_row(tt, 0, "Access token", self.acc["tiktok_token"], show="•")
        ig = ttk.LabelFrame(f, text="Instagram (conta profissional)", padding=10)
        ig.grid(row=2, column=0, sticky="ew", pady=4)
        ig.columnconfigure(1, weight=1)
        self.entry_row(ig, 0, "ID da conta (IG user id)", self.acc["ig_user_id"])
        self.entry_row(ig, 1, "Access token", self.acc["ig_token"], show="•")
        self.entry_row(ig, 2, "Versão da API", self.acc["ig_version"], width=10)
        ttk.Button(f, text="💾  Salvar contas", style="Accent.TButton", command=self.save_accounts).grid(
            row=3, column=0, sticky="w", pady=10)
        ttk.Label(f, text="Como obter cada chave está no README. Os dados ficam só neste computador, "
                          "no arquivo minecortes_config.json. Não compartilhe essa pasta.",
                  style="Muted.TLabel", wraplength=900, justify="left").grid(row=4, column=0, sticky="w")

    def pick_secret(self):
        p = filedialog.askopenfilename(title="client_secret.json", filetypes=[("JSON", "*.json")])
        if p:
            self.acc["yt_client_secret"].set(p)

    def save_accounts(self):
        self.save_all()
        self.log("Contas salvas.")

    def connect_youtube(self):
        self.save_all()

        def work():
            try:
                mc.youtube_service(self.cfg)
                self.log("YouTube conectado!")
            except Exception as ex:
                self.ui(lambda ex=ex: messagebox.showerror("MineCortes", str(ex)))

        threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ encerramento
    def on_close(self):
        try:
            self.save_all()
        except Exception:
            pass  # campo inválido: fecha mesmo assim
        self.stop_all.set()
        self.stop_auto.set()
        self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    App().run()
