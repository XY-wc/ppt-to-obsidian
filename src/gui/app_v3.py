# -*- coding: utf-8 -*-
"""
PPT → Obsidian 智能笔记 GUI v3 — 桌面工作台布局

布局:
  顶栏(紧凑)  ·  左侧知识库(Vault根/课程/本课笔记)  ·  主工作流(课件→设置→生成→修订)  ·  状态栏

保留全部业务方法与数据流，仅重做布局与视觉。
"""
import os
import sys
import json
import threading
import subprocess
from tkinter import ttk, messagebox, filedialog

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.dirname(_HERE)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import tkinter as tk

from core.app_config import AccountManager, AppPaths
from core.knowledge_base import KnowledgeBase
from core.document_loader import is_supported as _doc_supported
from llm.client import KNOWN_PROVIDERS, LLMClient, build_llm
from pipeline import run_conversion
from obsidian import refine as refine_mod
from obsidian.kb_calibration import CalibrationStore

APP_NAME = "PPT → Obsidian 智能笔记"
APP_VERSION = "1.2.0"

# ───────────────────────── 设计 token ─────────────────────────
BG = "#eef0f6"
SIDEBAR_BG = "#f7f8fc"
SURFACE = "#ffffff"
SURFACE_2 = "#f8f9fd"
PRIMARY = "#4f46e5"
PRIMARY_HOVER = "#4338ca"
PRIMARY_LIGHT = "#e0e7ff"
PRIMARY_SOFT = "#eef0ff"
ACCENT = "#7c3aed"
TEXT = "#171a2b"
TEXT_MED = "#5a6178"
TEXT_LIGHT = "#8b93a7"
BORDER = "#e2e6f0"
SUCCESS = "#059669"
SUCCESS_HOVER = "#047857"
SUCCESS_BG = "#d1fae5"
DANGER = "#dc2626"
DANGER_BG = "#fee2e2"
LOG_BG = "#12182a"
SHADOW = "#d8dce8"

FONT = ("Microsoft YaHei UI", 10)
FONT_S = ("Microsoft YaHei UI", 9)
FONT_XS = ("Microsoft YaHei UI", 8)
FONT_APP = ("Microsoft YaHei UI", 12, "bold")
FONT_SECTION = ("Microsoft YaHei UI", 11, "bold")
FONT_B = ("Microsoft YaHei UI", 10, "bold")
FONT_RUN = ("Microsoft YaHei UI", 12, "bold")
FONT_MONO = ("Consolas", 9)
FONT_LOG = ("Microsoft YaHei UI", 9)

BTN_KINDS = {
    "primary": (PRIMARY, PRIMARY_HOVER, "white"),
    "success": (SUCCESS, SUCCESS_HOVER, "white"),
    "danger": (DANGER_BG, "#fecaca", DANGER),
    "ghost": (SURFACE_2, "#e8ebf4", TEXT_MED),
    "outline": (SURFACE, PRIMARY_SOFT, PRIMARY),
}


def mkbtn(parent, text, cmd, kind="ghost", font=FONT_S, padx=12, pady=5, width=None):
    bg, hov, fg = BTN_KINDS[kind]
    b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                  activebackground=hov, activeforeground=fg,
                  relief="flat", bd=0, cursor="hand2", font=font,
                  padx=padx, pady=pady, width=width,
                  highlightthickness=1 if kind == "outline" else 0,
                  highlightbackground=BORDER if kind == "outline" else bg,
                  highlightcolor=BORDER if kind == "outline" else bg)
    b.bind("<Enter>", lambda e: b.config(bg=hov) if str(b["state"]) != "disabled" else None)
    b.bind("<Leave>", lambda e: b.config(bg=bg))
    b._home_bg = bg
    b._home_fg = fg
    b._kind = kind
    return b


def set_btn_enabled(btn, enabled):
    kind = getattr(btn, "_kind", "ghost")
    bg, hov, fg = BTN_KINDS[kind]
    if enabled:
        btn.config(state="normal", bg=bg, fg=fg, activebackground=hov, activeforeground=fg, cursor="hand2")
    else:
        btn.config(state="disabled", bg="#e6e8f0", fg="#b0b6c8",
                   activebackground="#e6e8f0", activeforeground="#b0b6c8", cursor="arrow")


def mid_ellipsis(path, max_chars=42):
    if not path:
        return ""
    p = str(path)
    if len(p) <= max_chars:
        return p
    keep = max_chars - 3
    head = keep * 2 // 3
    tail = keep - head
    return p[:head] + "…" + p[-tail:]


class Card(tk.Frame):
    """轻量卡片：白底 + 细边框。可选标题栏。"""

    def __init__(self, parent, title=None, sub=None, pad=16, **kw):
        super().__init__(parent, bg=SURFACE, highlightthickness=1,
                         highlightbackground=BORDER, **kw)
        self.body = tk.Frame(self, bg=SURFACE)
        self._pad = pad
        self._header = None
        if title:
            hdr = tk.Frame(self, bg=SURFACE)
            hdr.pack(fill="x", padx=pad, pady=(pad, 0))
            row = tk.Frame(hdr, bg=SURFACE)
            row.pack(fill="x")
            tk.Label(row, text=title, font=FONT_SECTION, bg=SURFACE, fg=TEXT,
                     anchor="w").pack(side="left")
            if sub:
                tk.Label(hdr, text=sub, font=FONT_XS, bg=SURFACE, fg=TEXT_LIGHT,
                         anchor="w").pack(fill="x", pady=(2, 0))
            self._header = hdr
        self.body.pack(fill="both", expand=True, padx=pad, pady=(0 if title else pad, pad))


class DashedZone(tk.Frame):
    """虚线拖拽区：Canvas 只画边框，文案用 Label（渲染更稳）。"""

    _STYLES = {
        "normal": {"bg": SURFACE_2, "dash": "#a8b2e8"},
        "hover": {"bg": PRIMARY_SOFT, "dash": PRIMARY},
        "active": {"bg": "#ecfdf5", "dash": SUCCESS},
    }

    def __init__(self, parent, height=120, on_click=None):
        super().__init__(parent, bg=SURFACE, highlightthickness=0, height=height)
        self.pack_propagate(False)
        self._state = "normal"
        self._on_click = on_click
        self._filename = ""
        self._filepath = ""

        self._bg = tk.Frame(self, bg=self._STYLES["normal"]["bg"])
        self._bg.place(x=0, y=0, relwidth=1, relheight=1)
        self._border = tk.Canvas(self._bg, highlightthickness=0, bd=0,
                                 bg=self._STYLES["normal"]["bg"])
        self._border.pack(fill="both", expand=True)

        content = tk.Frame(self, bg=self._STYLES["normal"]["bg"])
        content.place(relx=0.5, rely=0.5, anchor="center")
        self.icon_id = tk.Label(content, text="点击选择 · 或拖入文件",
                                font=FONT_S, bg=self._STYLES["normal"]["bg"], fg=PRIMARY)
        self.icon_id.pack()
        self.title_id = tk.Label(content, text="PPT / PDF 课件",
                                 font=("Microsoft YaHei UI", 12, "bold"),
                                 bg=self._STYLES["normal"]["bg"], fg=TEXT)
        self.title_id.pack(pady=(4, 0))
        self.sub_id = tk.Label(content, text="支持 .pptx / .ppt / .pdf",
                               font=FONT_XS, bg=self._STYLES["normal"]["bg"], fg=TEXT_LIGHT)
        self.sub_id.pack(pady=(4, 0))
        self._content = content

        self.bind("<Configure>", lambda e: self._draw())
        self._bg.bind("<Configure>", lambda e: self._draw())
        self._border.bind("<Configure>", lambda e: self._draw())
        self._bg.bind("<Enter>", lambda e: self._hover(True))
        self._bg.bind("<Leave>", lambda e: self._hover(False))
        self._border.bind("<Enter>", lambda e: self._hover(True))
        self._border.bind("<Leave>", lambda e: self._hover(False))
        for w in (self._bg, self._border, content, self.icon_id, self.title_id, self.sub_id):
            if on_click:
                w.bind("<Button-1>", lambda e: on_click())
            w.bind("<Enter>", lambda e: self._hover(True))
            w.bind("<Leave>", lambda e: self._hover(False))
        self._draw()

    def _hover(self, inside):
        if self._state == "active":
            return
        self._state = "hover" if inside else "normal"
        self._apply_colors()
        self._draw()

    def set_state(self, state):
        self._state = state
        self._apply_colors()
        self._draw()

    def set_file(self, path):
        self._filepath = path or ""
        self._filename = os.path.basename(path) if path else ""
        if self._filename:
            self._state = "active"
            self.icon_id.config(text="已选课件", fg=SUCCESS)
            name = self._filename if len(self._filename) <= 40 else self._filename[:37] + "…"
            self.title_id.config(text=name)
            self.sub_id.config(text=mid_ellipsis(self._filepath, 52), fg=TEXT_MED)
        else:
            self._state = "normal"
            self.icon_id.config(text="点击选择 · 或拖入文件", fg=PRIMARY)
            self.title_id.config(text="PPT / PDF 课件")
            self.sub_id.config(text="支持 .pptx / .ppt / .pdf", fg=TEXT_LIGHT)
        self._apply_colors()
        self._draw()

    def _apply_colors(self):
        st = self._STYLES.get(self._state, self._STYLES["normal"])
        for w in (self, self._bg, self._content, self.icon_id, self.title_id, self.sub_id):
            try:
                w.configure(bg=st["bg"])
            except Exception:
                pass
        try:
            self._border.configure(bg=st["bg"])
        except Exception:
            pass

    def _draw(self):
        c = self._border
        c.delete("all")
        w = max(120, self.winfo_width() or 400)
        h = max(80, self.winfo_height() or 148)
        # 同步画布可视区域，避免边框只画在左上角
        try:
            if c.winfo_width() != w or c.winfo_height() != h:
                c.config(width=w, height=h)
        except Exception:
            pass
        st = self._STYLES.get(self._state, self._STYLES["normal"])
        r = 12
        pts = [r, 2, w - r, 2, w - 2, 2, w - 2, r, w - 2, h - r, w - 2, h - 2,
               w - r, h - 2, r, h - 2, 2, h - 2, 2, h - r, 2, r, 2, 2, r, 2]
        c.create_polygon(pts, fill="", outline=st["dash"], width=2,
                         dash=(6, 4), smooth=True)
        c.tag_raise("all")

    def itemconfig(self, item, **kw):
        target = None
        if item is self.icon_id:
            target = self.icon_id
        elif item is self.title_id:
            target = self.title_id
        elif item is self.sub_id:
            target = self.sub_id
        if target is None:
            return
        if "text" in kw:
            target.config(text=kw["text"])
        if "fill" in kw:
            target.config(fg=kw["fill"])


def paint_vgradient(canvas, w, h, c1, c2):
    canvas.delete("grad")
    r1, g1, b1 = canvas.winfo_rgb(c1)
    r2, g2, b2 = canvas.winfo_rgb(c2)
    steps = max(1, int(h))
    for i in range(steps):
        t = i / max(1, steps - 1)
        r = int((r1 + (r2 - r1) * t) / 256)
        g = int((g1 + (g2 - g1) * t) / 256)
        b = int((b1 + (b2 - b1) * t) / 256)
        canvas.create_line(0, i, w, i, fill=f"#{r:02x}{g:02x}{b:02x}", tags="grad")
    canvas.tag_lower("grad")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PPT → Obsidian 智能笔记")
        self.geometry("1220x860")
        self.minsize(1040, 720)
        self.configure(bg=BG)
        self._setup_app_icon()
        self._setup_style()

        self.paths = AppPaths()
        self.mgr = AccountManager(self.paths)
        self.kb = KnowledgeBase()
        self.profs = self.kb.load_all()
        self.account = None
        self._ppt_path = ""
        self._vault_dir = ""
        self._last_vault = ""
        self._prof_id = ""
        self._last_notes = []
        self._last_bundle = None
        self._last_fulltext = ""
        self._refine_target = ""
        self._session_corrections = []
        self._session_corr_by_course = {}
        self._vault_root = ""
        self._courses = []
        self._active_course = ""
        self._course_objs = {}
        self._pending_revised = ""
        self._running = False
        self._ready_vars = {}

        self._setup_menu()
        self.container = tk.Frame(self, bg=BG)
        self.container.pack(fill="both", expand=True)
        self._pages = {}
        for name, builder in [("login", self._build_login), ("main", self._build_main)]:
            page = builder(self.container)
            self._pages[name] = page
        self._restore_window_geom()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._show("login")
        self._update_ready_checklist()

    # ───────────────────────── 初始化 ─────────────────────────
    def _setup_app_icon(self):
        if getattr(sys, "frozen", False):
            base = getattr(sys, "_MEIPASS", os.path.dirname(__file__))
        else:
            base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        ico = os.path.join(base, "assets", "icon.ico")
        png = os.path.join(base, "assets", "icon.png")
        try:
            if os.path.isfile(ico):
                self.iconbitmap(ico)
        except Exception:
            pass
        try:
            if os.path.isfile(png):
                from tkinter import PhotoImage
                self._icon_photo = PhotoImage(file=png)
                self.iconphoto(False, self._icon_photo)
        except Exception:
            pass

    def _setup_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", font=FONT, background=BG, foreground=TEXT)
        st.configure("TCombobox", fieldbackground="white", background="white",
                     bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                     arrowcolor=TEXT_MED, padding=4)
        st.map("TCombobox",
               fieldbackground=[("readonly", "white")],
               bordercolor=[("focus", PRIMARY)],
               arrowcolor=[("active", PRIMARY)])
        st.configure("Treeview", background="white", fieldbackground="white",
                     foreground=TEXT, rowheight=26, font=FONT_S,
                     bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        st.configure("Treeview.Heading", background=SURFACE_2, foreground=TEXT_MED,
                     font=FONT_S, relief="flat")
        st.map("Treeview", background=[("selected", PRIMARY_LIGHT)],
               foreground=[("selected", TEXT)])
        st.configure("Vertical.TScrollbar", background="#d0d5e4", troughcolor=SIDEBAR_BG,
                     bordercolor=SIDEBAR_BG, arrowcolor=TEXT_MED, width=10,
                     lightcolor=SIDEBAR_BG, darkcolor=SIDEBAR_BG)
        st.map("Vertical.TScrollbar", background=[("active", "#b4bbcf")])
        st.configure("Accent.Horizontal.TProgressbar", troughcolor=PRIMARY_SOFT,
                     background=PRIMARY, thickness=6, bordercolor=SURFACE,
                     lightcolor=PRIMARY, darkcolor=PRIMARY)

    def _show(self, name):
        for k, p in self._pages.items():
            p.pack_forget()
        self._pages[name].pack(fill="both", expand=True)

    def switch_to_main(self):
        self._show("main")
        self.refresh_main()
        self._update_ready_checklist()
        # 首次进入且未设 Vault 根时，给出引导
        if not getattr(self, "_vault_root", "") and not getattr(self, "_vault_dir", ""):
            self.set_status("先设置知识库根目录，再新建课程")
            if hasattr(self, "run_hint"):
                self.run_hint.config(text="首次使用：左侧「知识库根目录」→「更换」选定 Obsidian 仓库", fg=PRIMARY)

    def _setup_menu(self):
        menubar = tk.Menu(self)
        file_m = tk.Menu(menubar, tearoff=0)
        file_m.add_command(label="打开课件…", accelerator="Ctrl+O", command=self._choose_ppt)
        self._recent_menu = tk.Menu(file_m, tearoff=0, postcommand=self._rebuild_recent_menu)
        file_m.add_cascade(label="最近课件", menu=self._recent_menu)
        file_m.add_command(label="设置知识库根目录…", command=self._choose_vault_root)
        file_m.add_separator()
        file_m.add_command(label="生成笔记", accelerator="Ctrl+Enter", command=self._run)
        file_m.add_command(label="打开输出文件夹", command=self._open_folder)
        file_m.add_separator()
        file_m.add_command(label="退出", accelerator="Alt+F4", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_m)

        help_m = tk.Menu(menubar, tearoff=0)
        help_m.add_command(label="使用说明", command=self._show_usage)
        help_m.add_command(label="快捷键", command=self._show_shortcuts)
        help_m.add_separator()
        help_m.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_m)
        self.config(menu=menubar)
        self.bind_all("<Control-o>", lambda e: self._choose_ppt())
        self.bind_all("<F1>", lambda e: self._show_usage())

    def _rebuild_recent_menu(self):
        m = self._recent_menu
        m.delete(0, "end")
        items = self._get_recent_ppts()
        if not items:
            m.add_command(label="（暂无）", state="disabled")
            return
        for p in items:
            m.add_command(label=mid_ellipsis(p, 48), command=lambda path=p: self._set_ppt(path))
        m.add_separator()
        m.add_command(label="清空记录", command=self._clear_recent_ppts)

    def _clear_recent_ppts(self):
        try:
            if self.account:
                self.account.set_pref("recent_ppts", [])
        except Exception:
            pass
        self.set_status("已清空最近课件记录")

    def _show_usage(self):
        messagebox.showinfo(
            "使用说明",
            "1. 左侧「知识库根目录」选一个 Obsidian 仓库目录\n"
            "2. 新建课程（一门课一个文件夹，记忆互不混杂）\n"
            "3. 拖入或选择 PPT / PDF 课件\n"
            "4. 选专业、确认输出目录，点「生成 Obsidian 笔记」\n"
            "5. 完成后在左侧「本课笔记」双击，用 Obsidian 打开\n\n"
            "提示：未绑定模型也能用离线规则生成；绑定 API 后结构更准。\n"
            "Base URL 必须填接口地址（通常以 /v1 结尾），不是官网文档页。")

    def _show_shortcuts(self):
        messagebox.showinfo(
            "快捷键",
            "Ctrl+O        打开课件\n"
            "Ctrl+Enter    生成笔记\n"
            "F1            使用说明\n"
            "Enter         登录 / 发送修订 / 新建课程\n"
            "双击          打开课程文件夹或笔记")

    def _show_about(self):
        messagebox.showinfo(
            "关于",
            f"{APP_NAME}\n版本 {APP_VERSION}\n\n"
            "把课程 PPT / PDF 转成 Obsidian 结构化笔记。\n"
            "本地优先 · 数据保存在本机 · 支持自备大模型 API。\n\n"
            "许可证：MIT")

    def _on_close(self):
        self._save_window_geom()
        self.destroy()

    def _restore_window_geom(self):
        try:
            cfg_file = os.path.join(self.paths.base, "ui_state.json")
            if os.path.isfile(cfg_file):
                with open(cfg_file, encoding="utf-8") as f:
                    geo = json.load(f).get("geometry") or ""
                if geo and "x" in geo and "×" not in geo:
                    self.geometry(geo)
        except Exception:
            pass

    def _save_window_geom(self):
        try:
            cfg_file = os.path.join(self.paths.base, "ui_state.json")
            data = {}
            if os.path.isfile(cfg_file):
                try:
                    with open(cfg_file, encoding="utf-8") as f:
                        data = json.load(f)
                except Exception:
                    data = {}
            data["geometry"] = self.geometry()
            os.makedirs(self.paths.base, exist_ok=True)
            with open(cfg_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _bind_mousewheel(self, canvas, *widgets):
        def _wheel(e, c=canvas):
            try:
                c.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except Exception:
                pass
        for w in widgets:
            w.bind("<MouseWheel>", _wheel)

    def _make_scroll_area(self, parent, bg=BG):
        wrap = tk.Frame(parent, bg=bg)
        canvas = tk.Canvas(wrap, bg=bg, highlightthickness=0)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        inner = tk.Frame(canvas, bg=bg)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")

        def _on_cfg(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(win_id, width=e.width)

        inner.bind("<Configure>", _on_cfg)
        canvas.bind("<Configure>", _on_cfg)
        self._bind_mousewheel(canvas, canvas, inner)
        return wrap, canvas, inner

    # ───────────────────────── 登录页 ─────────────────────────
    def _build_login(self, parent):
        page = tk.Frame(parent, bg=BG)
        grad = tk.Canvas(page, highlightthickness=0, bd=0)
        grad.place(relx=0, rely=0, relwidth=0.40, relheight=1)

        def _redraw_grad(e=None):
            w = grad.winfo_width() or 400
            h = grad.winfo_height() or 700
            paint_vgradient(grad, w, h, PRIMARY, ACCENT)
            grad.delete("deco")
            grad.create_oval(w * 0.55, h * 0.08, w * 1.15, h * 0.38,
                             fill="#6d64ec", outline="", tags="deco")
            grad.create_oval(-w * 0.25, h * 0.62, w * 0.35, h * 1.05,
                             fill="#5a4fe0", outline="", tags="deco")
            grad.delete("brand")
            cx = w / 2
            grad.create_text(cx, h * 0.34, text="PPT → Obsidian",
                             font=("Microsoft YaHei UI", 18, "bold"),
                             fill="white", tags="brand")
            grad.create_text(cx, h * 0.43, text="课件一键变成结构化笔记",
                             font=FONT, fill="#cdd3ff", tags="brand")
            grad.create_text(cx, h * 0.58, text="专业驱动 · 本地优先 · 可生长的知识库",
                             font=FONT_S, fill="#aab3f8", tags="brand")
            grad.create_text(cx, h * 0.63, text="数据保存在本机 · 绑定自己的大模型 API",
                             font=FONT_XS, fill="#aab3f8", tags="brand")
            grad.tag_raise("brand")
            grad.tag_lower("deco")
            grad.tag_lower("grad")

        grad.bind("<Configure>", _redraw_grad)

        right = tk.Frame(page, bg=BG)
        right.place(relx=0.40, rely=0, relwidth=0.60, relheight=1)
        shell = tk.Frame(right, bg=BG)
        shell.place(relx=0.5, rely=0.5, anchor="center", width=400, height=560)
        card = tk.Frame(shell, bg=SURFACE, highlightthickness=1, highlightbackground=BORDER)
        card.pack(fill="both", expand=True)
        inner = tk.Frame(card, bg=SURFACE)
        inner.pack(fill="both", expand=True, padx=40, pady=36)

        tk.Label(inner, text="欢迎回来", font=("Microsoft YaHei UI", 20, "bold"),
                 bg=SURFACE, fg=TEXT).pack(anchor="w")
        tk.Label(inner, text="登录本地账号后开始转换课件", font=FONT,
                 bg=SURFACE, fg=TEXT_MED).pack(anchor="w", pady=(6, 28))

        tk.Label(inner, text="账号", bg=SURFACE, fg=TEXT_MED, font=FONT_S, anchor="w").pack(fill="x", pady=(0, 4))
        self.username_var = tk.StringVar(value=self.mgr.remember_last_username() or "")
        self._entry(inner, self.username_var)
        tk.Label(inner, text="密码", bg=SURFACE, fg=TEXT_MED, font=FONT_S, anchor="w").pack(fill="x", pady=(14, 4))
        self.pass_var = tk.StringVar()
        e = self._entry(inner, self.pass_var, show="●")
        e.bind("<Return>", lambda _e: self._do_login())

        self.auth_err = tk.Label(inner, text="", bg=SURFACE, fg=DANGER, font=FONT_S, wraplength=300, justify="left")
        self.auth_err.pack(fill="x", pady=(10, 0))

        # 回车提交
        self.pass_var.trace_add("write", lambda *_: self.auth_err.config(text=""))

        mkbtn(inner, "登  录", self._do_login, "primary", font=FONT_B,
              padx=0, pady=10).pack(fill="x", pady=(18, 8))
        mkbtn(inner, "注册新账号", self._do_register, "ghost", font=FONT,
              padx=0, pady=8).pack(fill="x")
        tk.Label(inner, text="首次使用请注册 · 数据保存在本机 · F1 查看使用说明",
                 bg=SURFACE, fg=TEXT_LIGHT, font=FONT_XS).pack(side="bottom", pady=(24, 0))
        return page

    def _entry(self, parent, var, show=None):
        e = tk.Entry(parent, textvariable=var, font=FONT, relief="solid", bd=1,
                     highlightthickness=1, highlightcolor=PRIMARY,
                     highlightbackground=BORDER, bg="white", show=show)
        e.pack(fill="x", ipady=6)
        return e

    def _do_login(self):
        u = self.username_var.get().strip()
        p = self.pass_var.get()
        if not u or not p:
            self.auth_err.config(text="请输入账号和密码")
            return
        try:
            self.account = self.mgr.login(u, p)
            self.auth_err.config(text="")
            self.switch_to_main()
        except Exception as ex:
            self.auth_err.config(text=str(ex))

    def _do_register(self):
        u = self.username_var.get().strip()
        p = self.pass_var.get()
        if not u or not p:
            self.auth_err.config(text="请输入账号和密码后再注册")
            return
        try:
            self.account = self.mgr.register(u, p)
            self.auth_err.config(text="")
            self.switch_to_main()
        except Exception as ex:
            self.auth_err.config(text=str(ex))

    # ───────────────────────── 主界面骨架 ─────────────────────────
    def _build_main(self, parent):
        page = tk.Frame(parent, bg=BG)
        self._build_header(page)
        mid = tk.Frame(page, bg=BG)
        mid.pack(fill="both", expand=True)
        self._build_sidebar(mid)
        self._build_workspace(mid)
        self._build_statusbar(page)
        return page

    def _build_header(self, parent):
        hdr = tk.Frame(parent, bg=SURFACE, height=52,
                       highlightbackground=BORDER, highlightthickness=1)
        hdr.pack(fill="x")
        hdr.pack_propagate(False)

        icon_shown = False
        try:
            if getattr(sys, "frozen", False):
                base = getattr(sys, "_MEIPASS", os.path.dirname(__file__))
            else:
                base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            png32 = os.path.join(base, "assets", "icon_32.png")
            if os.path.isfile(png32):
                self._hdr_icon = tk.PhotoImage(file=png32)
                tk.Label(hdr, image=self._hdr_icon, bg=SURFACE).pack(side="left", padx=(16, 8))
                icon_shown = True
        except Exception:
            pass
        if not icon_shown:
            tk.Label(hdr, text="📒", font=("Segoe UI Emoji", 16), bg=SURFACE).pack(side="left", padx=(16, 8))

        tk.Label(hdr, text="PPT → Obsidian", font=FONT_APP, bg=SURFACE, fg=TEXT).pack(side="left")
        tk.Label(hdr, text="  智能笔记", font=FONT_S, bg=SURFACE, fg=TEXT_LIGHT).pack(side="left", padx=(2, 0), pady=(2, 0))

        mkbtn(hdr, "退出登录", self._logout, "ghost", font=FONT_S, padx=12, pady=4).pack(side="right", padx=(8, 16), pady=10)
        tk.Label(hdr, text="本地优先", font=FONT_XS, bg=PRIMARY_SOFT, fg=PRIMARY,
                 padx=8, pady=2).pack(side="right", padx=(0, 4))

        chip = tk.Frame(hdr, bg=PRIMARY_SOFT, highlightbackground=PRIMARY_LIGHT, highlightthickness=1)
        chip.pack(side="right", pady=10)
        self._avatar = tk.Canvas(chip, width=24, height=24, bg=PRIMARY_SOFT, highlightthickness=0)
        self._avatar.pack(side="left", padx=(8, 6), pady=4)
        self.user_label = tk.Label(chip, text="", bg=PRIMARY_SOFT, fg=PRIMARY, font=FONT_S)
        self.user_label.pack(side="left", padx=(0, 10))

    def _build_sidebar(self, parent):
        side = tk.Frame(parent, bg=SIDEBAR_BG, width=280,
                        highlightbackground=BORDER, highlightthickness=1)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)

        # Vault 根
        vault_card = tk.Frame(side, bg=SIDEBAR_BG)
        vault_card.pack(fill="x", padx=14, pady=(14, 8))
        vhead = tk.Frame(vault_card, bg=SIDEBAR_BG)
        vhead.pack(fill="x")
        tk.Label(vhead, text="知识库根目录", font=FONT_S, bg=SIDEBAR_BG, fg=TEXT_MED).pack(side="left")
        self.set_root_btn = mkbtn(vhead, "更换", self._choose_vault_root, "outline", FONT_XS, padx=8, pady=2)
        self.set_root_btn.pack(side="right")
        self.root_path_lbl = tk.Label(vault_card, text="尚未设置 · 先选一个 Obsidian 仓库",
                                      bg=SIDEBAR_BG, fg=TEXT_LIGHT, font=FONT_XS,
                                      anchor="w", wraplength=250, justify="left")
        self.root_path_lbl.pack(fill="x", pady=(4, 0))

        sep1 = tk.Frame(side, bg=BORDER, height=1)
        sep1.pack(fill="x", padx=14, pady=(8, 10))

        # 课程
        course_box = tk.Frame(side, bg=SIDEBAR_BG)
        course_box.pack(fill="x", padx=14)
        chead = tk.Frame(course_box, bg=SIDEBAR_BG)
        chead.pack(fill="x")
        tk.Label(chead, text="课程", font=FONT_SECTION, bg=SIDEBAR_BG, fg=TEXT).pack(side="left")
        tk.Label(chead, text="一科一夹", font=FONT_XS, bg=SIDEBAR_BG, fg=TEXT_LIGHT).pack(side="right")

        lb_holder = tk.Frame(course_box, bg=SIDEBAR_BG)
        lb_holder.pack(fill="x", pady=(6, 0))
        self.course_lb = tk.Listbox(lb_holder, height=5, font=FONT_S, bg=SURFACE,
                                    relief="flat", bd=0, highlightthickness=1,
                                    highlightbackground=BORDER, highlightcolor=PRIMARY,
                                    selectbackground=PRIMARY_LIGHT, selectforeground=TEXT,
                                    activestyle="none", exportselection=False)
        self.course_lb.pack(side="left", fill="both", expand=True)
        sc = ttk.Scrollbar(lb_holder, orient="vertical", command=self.course_lb.yview)
        self.course_lb.configure(yscrollcommand=sc.set)
        sc.pack(side="right", fill="y")
        self.course_lb.bind("<<ListboxSelect>>", self._on_course_select)
        self.course_lb.bind("<Double-Button-1>", lambda e: self._open_active_folder())

        new_row = tk.Frame(course_box, bg=SIDEBAR_BG)
        new_row.pack(fill="x", pady=(6, 0))
        self.new_course_var = tk.StringVar()
        ent = tk.Entry(new_row, textvariable=self.new_course_var, font=FONT_S, relief="solid", bd=1,
                       highlightbackground=BORDER, highlightcolor=PRIMARY, bg="white")
        ent.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=3)
        ent.bind("<Return>", lambda e: self._new_course())
        ent.bind("<FocusIn>", lambda e: self._clear_course_ph(ent))
        self.new_course_entry = ent
        self._course_name_ph = True
        self._reset_course_ph()
        mkbtn(new_row, "＋ 新建", self._new_course, "primary", FONT_XS, padx=10, pady=3).pack(side="right")

        op_row = tk.Frame(course_box, bg=SIDEBAR_BG)
        op_row.pack(fill="x", pady=(6, 0))
        mkbtn(op_row, "打开文件夹", self._open_active_folder, "ghost", FONT_XS, padx=6, pady=2).pack(side="left")
        mkbtn(op_row, "选课件", self._pick_ppt_in_course, "ghost", FONT_XS, padx=6, pady=2).pack(side="left", padx=(4, 0))
        mkbtn(op_row, "移除", self._remove_course, "danger", FONT_XS, padx=6, pady=2).pack(side="right")

        self.course_hint = tk.Label(course_box, text="新建课程后会在根目录下自动建同名文件夹",
                                    bg=SIDEBAR_BG, fg=TEXT_LIGHT, font=FONT_XS,
                                    anchor="w", wraplength=250, justify="left")
        self.course_hint.pack(fill="x", pady=(6, 0))

        sep2 = tk.Frame(side, bg=BORDER, height=1)
        sep2.pack(fill="x", padx=14, pady=(12, 10))

        # 本课笔记
        files_box = tk.Frame(side, bg=SIDEBAR_BG)
        files_box.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        fhead = tk.Frame(files_box, bg=SIDEBAR_BG)
        fhead.pack(fill="x")
        tk.Label(fhead, text="本课笔记", font=FONT_SECTION, bg=SIDEBAR_BG, fg=TEXT).pack(side="left")
        mkbtn(fhead, "刷新", self._refresh_course_files, "ghost", FONT_XS, padx=6, pady=2).pack(side="right")

        tree_holder = tk.Frame(files_box, bg=SIDEBAR_BG)
        tree_holder.pack(fill="both", expand=True, pady=(6, 0))
        self.course_tree = ttk.Treeview(tree_holder, show="tree", height=8, selectmode="browse")
        self.course_tree.pack(side="left", fill="both", expand=True)
        tsc = ttk.Scrollbar(tree_holder, orient="vertical", command=self.course_tree.yview)
        self.course_tree.configure(yscrollcommand=tsc.set)
        tsc.pack(side="right", fill="y")
        self.course_tree.bind("<Double-Button-1>", self._on_tree_open)
        # 单击文件夹也可切换展开（不依赖前面的小三角）
        self.course_tree.bind("<Button-1>", self._on_tree_click, add="+")
        self.course_tree.tag_configure("dir", foreground=PRIMARY)
        self.course_tree.tag_configure("md", foreground=TEXT)
        self.course_tree.tag_configure("img", foreground=TEXT_LIGHT)

        fop = tk.Frame(files_box, bg=SIDEBAR_BG)
        fop.pack(fill="x", pady=(6, 0))
        mkbtn(fop, "打开选中", self._open_selected_entry, "ghost", FONT_XS, padx=6, pady=2).pack(side="left")
        mkbtn(fop, "重命名", self._rename_selected_entry, "ghost", FONT_XS, padx=6, pady=2).pack(side="left", padx=(4, 0))

        self.course_browse_hint = tk.Label(files_box, text="双击笔记夹展开 · 双击笔记用 Obsidian 打开",
                                           bg=SIDEBAR_BG, fg=TEXT_LIGHT, font=FONT_XS,
                                           anchor="w", wraplength=250, justify="left")
        self.course_browse_hint.pack(fill="x", pady=(6, 0))

    def _build_workspace(self, parent):
        wrap, self._right_canvas, body = self._make_scroll_area(parent, bg=BG)
        wrap.pack(side="left", fill="both", expand=True)
        body.columnconfigure(0, weight=1)

        # ① 课件
        c1 = Card(body, title="①  课件", sub="拖入或选择要转换的课件", pad=14)
        c1.grid(row=0, column=0, sticky="ew", padx=18, pady=(12, 0))
        self._build_ppt_dropzone(c1.body)

        # ② 专业与输出
        c2 = Card(body, title="②  专业与输出", sub="专业知识框架 · 输出目录 · 模型 · 笔记形态", pad=14)
        c2.grid(row=1, column=0, sticky="ew", padx=18, pady=(10, 0))
        self._build_setup_section(c2.body)

        # ③ 生成
        c3 = Card(body, title="③  生成笔记", sub="一键写入 Obsidian 目录", pad=14)
        c3.grid(row=2, column=0, sticky="ew", padx=18, pady=(10, 0))
        self._build_run_section(c3.body)
        self._build_result_section(c3.body)

        # ④ 对话修订
        c4 = Card(body, title="④  对话修订", sub="对笔记提意见，AI 修订并沉淀回知识库", pad=14)
        c4.grid(row=3, column=0, sticky="ew", padx=18, pady=(10, 16))
        self._build_chat_section(c4.body)

    def _build_statusbar(self, parent):
        bar = tk.Frame(parent, bg=SURFACE_2, height=28,
                       highlightbackground=BORDER, highlightthickness=1)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.status_label = tk.Label(bar, text="就绪", font=FONT_XS, bg=SURFACE_2, fg=TEXT_MED, anchor="w")
        self.status_label.pack(side="left", padx=14)
        self.statusbar_model = tk.Label(bar, text="本地规则（离线）", font=FONT_XS, bg=SURFACE_2, fg=TEXT_LIGHT, anchor="e")
        self.statusbar_model.pack(side="right", padx=14)

    # ───────────────────────── 左栏业务：课程 ─────────────────────────
    def _load_courses(self):
        try:
            raw = self.account.get_pref("courses") or {}
        except Exception:
            raw = {}
        self._vault_root = raw.get("vault_root", "") or ""
        self._courses = [dict(c) for c in raw.get("courses", [])]
        self._active_course = raw.get("active", "") or ""
        self._courses = [c for c in self._courses if c.get("name")]

    def _save_courses(self):
        if not self.account:
            return
        payload = {"vault_root": self._vault_root, "active": self._active_course,
                   "courses": self._courses}
        self.account.set_pref("courses", payload)
        self.account.save()

    def _refresh_course_ui(self):
        if self._vault_root:
            self.root_path_lbl.config(text=mid_ellipsis(self._vault_root, 40), fg=TEXT_MED)
        else:
            self.root_path_lbl.config(text="尚未设置 · 先选一个 Obsidian 仓库", fg=TEXT_LIGHT)
        self.course_lb.delete(0, "end")
        for c in self._courses:
            self.course_lb.insert("end", c["name"])
        names = [c["name"] for c in self._courses]
        if self._active_course in names:
            idx = names.index(self._active_course)
            self.course_lb.selection_clear(0, "end")
            self.course_lb.selection_set(idx)
            self.course_lb.see(idx)
        if self._courses:
            act = self._active_course or self._courses[0]["name"]
            mem = self._course_memory_summary(self._course_path(act))
            self.course_hint.config(text=f"当前：{act}\n{mem}", fg=TEXT_LIGHT)
        else:
            self.course_hint.config(
                text="还没有课程。请先「更换」知识库根目录，再点「＋ 新建」",
                fg=PRIMARY)

    def _course_memory_summary(self, course_path):
        if not course_path:
            return "本课记忆：空"
        mdir = os.path.join(course_path, ".ppt2obsidian")
        total = 0
        if os.path.isdir(mdir):
            for fn in os.listdir(mdir):
                if not fn.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(mdir, fn), "r", encoding="utf-8") as f:
                        d = json.load(f)
                    total += (len(d.get("extra_concepts", []))
                              + len(d.get("extra_symbols", []))
                              + len(d.get("extra_misconceptions", [])))
                except Exception:
                    pass
        if total:
            return f"本课记忆：{total} 条沉淀（仅本课可见）"
        return "本课记忆：空 · 越用越准"

    def _course_path(self, name):
        for c in self._courses:
            if c["name"] == name:
                return c.get("path", "")
        return ""

    def _refresh_course_files(self):
        tree = getattr(self, "course_tree", None)
        if tree is None:
            return
        tree.delete(*tree.get_children())
        base = self._vault_dir if (self._vault_dir and os.path.isdir(self._vault_dir)) else ""
        if not base:
            self.course_browse_hint.config(text="选中课程后，这里列出已生成笔记", fg=TEXT_LIGHT)
            return
        try:
            entries = sorted(os.listdir(base))
        except Exception as e:
            self.course_browse_hint.config(text=f"读取失败：{e}", fg=DANGER)
            return
        dirs, files = [], []
        for e in entries:
            if e.startswith("."):
                continue
            full = os.path.join(base, e)
            if os.path.isdir(full):
                dirs.append(e)
            elif os.path.isfile(full) and e.lower().endswith(".md"):
                files.append(e)
        n_notes = 0
        if not dirs and not files:
            self.course_browse_hint.config(text=f"「{self._active_course}」还没有笔记，生成后自动出现", fg=TEXT_LIGHT)
        else:
            for d in dirs:
                did = tree.insert("", "end", text=d, tags=("dir",), open=False)
                try:
                    for sub in sorted(os.listdir(os.path.join(base, d))):
                        if sub.startswith("."):
                            continue
                        sp = os.path.join(base, d, sub)
                        if os.path.isfile(sp) and sub.lower().endswith(".md"):
                            tree.insert(did, "end", text=os.path.splitext(sub)[0],
                                        values=(sp,), tags=("md",))
                            n_notes += 1
                except Exception:
                    pass
            for f in files:
                tree.insert("", "end", text=os.path.splitext(f)[0],
                            values=(os.path.join(base, f),), tags=("md",))
                n_notes += 1
            self.course_browse_hint.config(
                text=f"{len(dirs)} 个笔记夹 · {n_notes} 篇笔记 · 双击打开", fg=TEXT_MED)

    def _selected_tree_path(self):
        tree = getattr(self, "course_tree", None)
        if tree is None:
            return ""
        sel = tree.selection()
        if not sel:
            return ""
        vals = tree.item(sel[0], "values")
        return vals[0] if vals else ""

    def _on_tree_click(self, event):
        """单击笔记夹：直接展开/收起，不用点前面的三角。"""
        tree = getattr(self, "course_tree", None)
        if tree is None:
            return
        row = tree.identify_row(event.y)
        if not row:
            return
        tags = tree.item(row, "tags") or ()
        if "dir" in tags and tree.get_children(row):
            if tree.item(row, "open"):
                tree.item(row, open=False)
            else:
                tree.item(row, open=True)
            return "break"

    def _on_tree_open(self, event=None):
        """双击：笔记夹展开/收起；笔记用 Obsidian 打开。"""
        tree = getattr(self, "course_tree", None)
        if tree is None:
            return
        row = None
        if event is not None:
            row = tree.identify_row(event.y)
            if row:
                tree.selection_set(row)
        if not row:
            sel = tree.selection()
            row = sel[0] if sel else ""
        if not row:
            return
        tags = tree.item(row, "tags") or ()
        vals = tree.item(row, "values")
        path = vals[0] if vals else ""
        # 目录：切换展开，直接看到里面的笔记
        if "dir" in tags or (path and os.path.isdir(path)):
            if tree.get_children(row):
                tree.item(row, open=not bool(tree.item(row, "open")))
            elif path and os.path.isdir(path):
                # 叶子目录（无 md）才退回系统打开
                self._reveal_folder(path)
            return
        # 笔记文件：Obsidian 打开
        if path and os.path.isfile(path) and path.lower().endswith(".md"):
            self._obsidian_open(path)
        elif path:
            self._open_selected_entry()

    def _open_selected_entry(self):
        path = self._selected_tree_path()
        if not path:
            messagebox.showinfo("提示", "请先选中一个笔记或笔记夹")
            return
        if os.path.isdir(path):
            self._reveal_folder(path)
        elif os.path.isfile(path) and path.lower().endswith(".md"):
            self._obsidian_open(path)
        else:
            messagebox.showinfo("提示", "请选中一个笔记(.md)或笔记夹再打开")

    def _obsidian_open(self, md_path):
        uri = "obsidian://open?path=" + md_path.replace(" ", "%20")
        try:
            os.startfile(uri)  # type: ignore
        except Exception:
            try:
                os.startfile(md_path)
            except Exception as e:
                messagebox.showerror("打开失败", f"{e}\n可到系统文件里手动打开该笔记")

    def _rename_selected_entry(self):
        path = self._selected_tree_path()
        if not path:
            messagebox.showinfo("提示", "请先选中要重命名的笔记")
            return
        if not os.path.exists(path):
            messagebox.showwarning("提示", "路径不存在")
            return
        old = os.path.splitext(os.path.basename(path))[0] if os.path.isfile(path) else os.path.basename(path)
        new = self._prompt_rename(old)
        if not new or new == old:
            return
        if os.path.isfile(path):
            self._do_rename_note(path, path, old, new)
        else:
            self._do_rename_dir(path, os.path.join(os.path.dirname(path), new))

    def _prompt_rename(self, old):
        dlg = tk.Toplevel(self)
        dlg.title("重命名")
        dlg.geometry("360x140")
        dlg.configure(bg=BG)
        dlg.transient(self)
        dlg.grab_set()
        tk.Label(dlg, text="新名称", font=FONT_S, bg=BG).pack(anchor="w", padx=16, pady=(16, 4))
        var = tk.StringVar(value=old)
        ent = tk.Entry(dlg, textvariable=var, font=FONT)
        ent.pack(fill="x", padx=16, ipady=4)
        ent.select_range(0, "end")
        ent.focus_set()
        result = {"v": ""}

        def ok():
            result["v"] = var.get().strip()
            dlg.destroy()

        ent.bind("<Return>", lambda e: ok())
        mkbtn(dlg, "确定", ok, "primary", FONT_S, padx=16, pady=4).pack(anchor="e", padx=16, pady=12)
        dlg.wait_window()
        return result["v"]

    def _rewrite_wikilinks_in_dir(self, vault_dir, old_stem, new_stem, exclude_path=""):
        try:
            for root, _dirs, files in os.walk(vault_dir):
                for fn in files:
                    if not fn.lower().endswith(".md"):
                        continue
                    fp = os.path.join(root, fn)
                    if exclude_path and os.path.abspath(fp) == os.path.abspath(exclude_path):
                        continue
                    try:
                        with open(fp, "r", encoding="utf-8") as f:
                            text = f.read()
                    except Exception:
                        continue

                    def _rep(m):
                        return m.group(0).replace(old_stem, new_stem)

                    import re
                    new_text = re.sub(r"\[\[[^\]]+\]\]", _rep, text)
                    if new_text != text:
                        try:
                            with open(fp, "w", encoding="utf-8") as f:
                                f.write(new_text)
                        except Exception:
                            pass
        except Exception:
            pass

    def _update_title_front(self, fp, old_title, new_title):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            return
        lines = text.splitlines(keepends=True)
        for i, ln in enumerate(lines[:12]):
            if ln.startswith("title:"):
                lines[i] = ln.replace(old_title, new_title, 1)
                break
        try:
            with open(fp, "w", encoding="utf-8") as f:
                f.write("".join(lines))
        except Exception:
            pass

    def _do_rename_note(self, path, new_path, old_stem, new_stem):
        target = os.path.join(os.path.dirname(path), new_stem + ".md")
        try:
            os.rename(path, target)
        except Exception as e:
            messagebox.showerror("重命名失败", str(e))
            return
        self._update_title_front(target, old_stem, new_stem)
        vault = self._vault_dir or self._active_vault_root()
        if vault:
            self._rewrite_wikilinks_in_dir(vault, old_stem, new_stem, exclude_path=target)
        self._refresh_course_files()

    def _do_rename_dir(self, path, new_path):
        try:
            os.rename(path, new_path)
        except Exception as e:
            messagebox.showerror("重命名失败", str(e))
            return
        self._refresh_course_files()

    def _active_vault_root(self):
        return self._vault_root or self._vault_dir

    def _choose_vault_root(self):
        v = filedialog.askdirectory(title="选择 Vault 根目录")
        if not v:
            return
        self._vault_root = v
        self._save_courses()
        self._refresh_course_ui()
        self.set_status(f"Vault 根：{mid_ellipsis(v, 36)}")

    def _on_course_select(self, _=None):
        sel = self.course_lb.curselection()
        if not sel:
            return
        name = self.course_lb.get(sel[0])
        self._select_course(name)

    def _select_course(self, name):
        self._active_course = name
        p = self._course_path(name)
        if p:
            try:
                os.makedirs(p, exist_ok=True)
            except Exception:
                pass
            if os.path.isdir(p):
                self._vault_dir = p
                self.vault_entry.delete(0, "end")
                self.vault_entry.insert(0, p)
        self._save_courses()
        self._refresh_course_ui()
        self._refresh_course_files()
        self.set_status(f"已切换课程：{name}")
        self._update_ready_checklist()

    def _new_course(self):
        name = self.new_course_var.get().strip()
        if getattr(self, "_course_name_ph", False) or not name:
            messagebox.showinfo("提示", "请输入课程名称")
            return
        if not self._vault_root:
            messagebox.showwarning("提示", "请先设置知识库根目录")
            return
        if any(c["name"] == name for c in self._courses):
            messagebox.showinfo("提示", "该课程已存在")
            return
        path = os.path.join(self._vault_root, name)
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            messagebox.showerror("创建失败", str(e))
            return
        self._courses.append({"name": name, "path": path})
        self._active_course = name
        self.new_course_var.set("")
        self._reset_course_ph()
        self._save_courses()
        self._select_course(name)

    def _clear_course_ph(self, ent):
        if getattr(self, "_course_name_ph", False):
            ent.delete(0, "end")
            self._course_name_ph = False
            ent.config(fg=TEXT)

    def _reset_course_ph(self):
        self._course_name_ph = True
        self.new_course_var.set("输入课程名，如「药理学」")
        try:
            self.new_course_entry.config(fg=TEXT_LIGHT)
        except Exception:
            pass

    def _remove_course(self):
        name = self._active_course
        if not name:
            messagebox.showinfo("提示", "请先选中一门课程")
            return
        if not messagebox.askyesno("确认", f"移除课程「{name}」？\n（只移除列表项，不会删除磁盘文件夹）"):
            return
        self._courses = [c for c in self._courses if c["name"] != name]
        self._active_course = self._courses[0]["name"] if self._courses else ""
        self._save_courses()
        if self._active_course:
            self._select_course(self._active_course)
        else:
            self._vault_dir = ""
            self.vault_entry.delete(0, "end")
            self._refresh_course_ui()
            self._refresh_course_files()

    def _open_active_folder(self):
        p = self._course_path(self._active_course) or self._vault_dir
        if not p or not os.path.isdir(p):
            messagebox.showinfo("提示", "请先选中一门有效课程")
            return
        self._reveal_folder(p)

    def _pick_ppt_in_course(self):
        p = self._course_path(self._active_course) or self._vault_root
        if not p or not os.path.isdir(p):
            messagebox.showinfo("提示", "请先选中一门课程")
            return
        fp = filedialog.askopenfilename(
            title="在课程目录中选择课件", initialdir=p,
            filetypes=[("课件 (PPT/PDF)", "*.pptx *.ppt *.pdf"),
                       ("PowerPoint", "*.pptx *.ppt"), ("PDF", "*.pdf"), ("所有文件", "*.*")])
        if fp:
            self._set_ppt(fp)

    def _reveal_folder(self, path):
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def _inside_real_vault(self, path):
        p = os.path.abspath(path or "")
        while p and len(p) > 3:
            if os.path.isdir(os.path.join(p, ".obsidian")):
                return True
            parent = os.path.dirname(p)
            if parent == p:
                break
            p = parent
        return False

    # ───────────────────────── 课件输入 ─────────────────────────
    def _build_ppt_dropzone(self, parent):
        dz = DashedZone(parent, height=120, on_click=self._choose_ppt)
        dz.pack(fill="x", pady=(0, 4))
        self.drop_zone = dz
        self._setup_dnd(dz)
        self.ppt_file_label = tk.Label(parent, text="尚未选择课件", bg=SURFACE, fg=TEXT_LIGHT,
                                       font=FONT_XS, anchor="w", wraplength=680, justify="left")
        self.ppt_file_label.pack(fill="x", pady=(2, 0))

    def _choose_ppt(self):
        p = filedialog.askopenfilename(title="选择课件",
                                       filetypes=[("课件 (PPT/PDF)", "*.pptx *.ppt *.pdf"),
                                                  ("PowerPoint", "*.pptx *.ppt"),
                                                  ("PDF", "*.pdf"),
                                                  ("所有文件", "*.*")])
        if p:
            self._set_ppt(p)

    def _set_ppt(self, path):
        if not os.path.exists(path):
            return
        self._ppt_path = path
        self.drop_zone.set_file(path)
        self.ppt_file_label.config(text=f"已选择：{os.path.basename(path)}  ·  {mid_ellipsis(path, 52)}",
                                   fg=SUCCESS)
        self.set_status(f"已选课件：{os.path.basename(path)}")
        self._remember_recent_ppt(path)
        self._update_ready_checklist()

    def _remember_recent_ppt(self, path):
        try:
            recent = list(self.account.get_pref("recent_ppts") or []) if self.account else []
        except Exception:
            recent = []
        path = os.path.abspath(path)
        recent = [p for p in recent if p != path]
        recent.insert(0, path)
        recent = recent[:8]
        try:
            if self.account:
                self.account.set_pref("recent_ppts", recent)
        except Exception:
            pass
        self._recent_ppts = recent

    def _get_recent_ppts(self):
        try:
            if self.account:
                return [p for p in (self.account.get_pref("recent_ppts") or []) if os.path.isfile(p)]
        except Exception:
            pass
        return []

    def _setup_dnd(self, widget):
        try:
            widget.tk.eval("""
                proc tkDndEnter {args} { return copy }
                proc tkDndPosition {args} { return copy }
                proc tkDndDrop {args} { event generate %W <<Drop>> -data [lindex $args 0] }
            """)
            widget.bind("<<Drop>>", self._on_drop)
            widget.tk.call("tkdnd::drop_target", "register", widget._w, "Files")
        except Exception:
            pass

    def _on_drop(self, event):
        data = getattr(event, "data", "")
        if not data:
            return
        files = [f.strip("{}\"") for f in data.split()
                 if _doc_supported(f.strip("{}\""))]
        if files:
            self._set_ppt(files[0])

    # ───────────────────────── 专业 / 输出 / 模型 ─────────────────────────
    def _build_setup_section(self, parent):
        parent.columnconfigure(1, weight=1)

        # 专业
        tk.Label(parent, text="专业", font=FONT_S, bg=SURFACE, fg=TEXT_MED).grid(
            row=0, column=0, sticky="nw", padx=(0, 12), pady=(0, 4))
        prof_row = tk.Frame(parent, bg=SURFACE)
        prof_row.grid(row=0, column=1, columnspan=2, sticky="ew", pady=(0, 2))
        self.prof_buttons = {}
        if not self.profs:
            tk.Label(prof_row, text="未加载专业框架", fg=DANGER, bg=SURFACE, font=FONT_S).pack(side="left")
        else:
            for i, p in enumerate(self.profs):
                b = tk.Button(prof_row, text=p.name, font=FONT_S, relief="flat", cursor="hand2",
                              command=lambda pid=p.id: self._pick_prof(pid), bd=0, padx=10, pady=4)
                b.pack(side="left", padx=(0 if i == 0 else 5))
                self.prof_buttons[p.id] = b
        self.prof_detail = tk.Label(parent, text="选择专业后显示知识框架规模", bg=SURFACE, fg=TEXT_LIGHT,
                                    font=FONT_XS, anchor="w")
        self.prof_detail.grid(row=1, column=1, columnspan=2, sticky="ew", pady=(0, 8))

        # 输出目录
        tk.Label(parent, text="输出到", font=FONT_S, bg=SURFACE, fg=TEXT_MED).grid(
            row=2, column=0, sticky="w", padx=(0, 12), pady=(0, 4))
        vrow = tk.Frame(parent, bg=SURFACE)
        vrow.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(0, 2))
        self.vault_entry = tk.Entry(vrow, font=FONT_S, relief="solid", bd=1,
                                    highlightbackground=BORDER, highlightcolor=PRIMARY, bg="white")
        self.vault_entry.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=3)
        self.vault_entry.bind("<Return>", lambda e: self._set_vault_from_entry())
        mkbtn(vrow, "浏览…", self._choose_vault, "outline", FONT_S, padx=10, pady=3).pack(side="right")
        tk.Label(parent, text="选当前课程文件夹即可；未建 Obsidian 仓库也能生成",
                 bg=SURFACE, fg=TEXT_LIGHT, font=FONT_XS, anchor="w").grid(
            row=3, column=1, columnspan=2, sticky="ew", pady=(0, 8))

        # 模型
        tk.Label(parent, text="模型", font=FONT_S, bg=SURFACE, fg=TEXT_MED).grid(
            row=4, column=0, sticky="w", padx=(0, 12), pady=(0, 4))
        mrow = tk.Frame(parent, bg=SURFACE)
        mrow.grid(row=4, column=1, columnspan=2, sticky="ew")
        self.model_var = tk.StringVar(value="offline")
        tk.Radiobutton(mrow, text="离线规则", variable=self.model_var, value="offline",
                       bg=SURFACE, activebackground=SURFACE, selectcolor="white",
                       highlightthickness=0, font=FONT_S, fg=TEXT_MED, cursor="hand2",
                       command=self._on_model_mode_toggle).pack(side="left")
        tk.Radiobutton(mrow, text="在线模型", variable=self.model_var, value="online",
                       bg=SURFACE, activebackground=SURFACE, selectcolor="white",
                       highlightthickness=0, font=FONT_S, fg=TEXT_MED, cursor="hand2",
                       command=self._on_model_mode_toggle).pack(side="left", padx=(10, 6))
        self.model_dd = ttk.Combobox(mrow, state="readonly", font=FONT_S)
        self.model_dd.pack(side="left", fill="x", expand=True)
        self.model_dd.bind("<<ComboboxSelected>>", self._on_model_select)

        btn_row = tk.Frame(parent, bg=SURFACE)
        btn_row.grid(row=5, column=1, columnspan=2, sticky="ew", pady=(6, 0))
        mkbtn(btn_row, "管理模型…", self._open_model_manage, "outline", FONT_S, padx=10, pady=3).pack(side="left")
        mkbtn(btn_row, "测试连接", self._test_model, "ghost", FONT_S, padx=10, pady=3).pack(side="left", padx=(8, 0))
        self.model_hint = tk.Label(parent, text="离线规则无需 API，可先跑通流程",
                                   bg=SURFACE, fg=TEXT_LIGHT, font=FONT_XS, justify="left",
                                   anchor="w", wraplength=520)
        self.model_hint.grid(row=6, column=1, columnspan=2, sticky="ew", pady=(6, 8))

        # 笔记形态
        tk.Label(parent, text="笔记形态", font=FONT_S, bg=SURFACE, fg=TEXT_MED).grid(
            row=7, column=0, sticky="w", padx=(0, 12), pady=(0, 2))
        mode_row = tk.Frame(parent, bg=SURFACE)
        mode_row.grid(row=7, column=1, columnspan=2, sticky="ew", pady=(0, 2))
        self.mode_var = tk.StringVar(value="lecture")
        self.mode_dd = ttk.Combobox(mode_row, textvariable=self.mode_var, state="readonly", font=FONT_S)
        self.mode_dd.configure(values=["讲义式 · 按小节一文件（推荐）", "概念卡片 · MOC + 原子卡"])
        self.mode_var.set("讲义式 · 按小节一文件（推荐）")
        self._mode_values = {"讲义式 · 按小节一文件（推荐）": "lecture",
                             "概念卡片 · MOC + 原子卡": "cards"}
        self._mode_to_display = {v: k for k, v in self._mode_values.items()}
        self.mode_dd.pack(side="left", fill="x", expand=True)
        self.mode_dd.bind("<<ComboboxSelected>>", self._on_mode_change)

    def _pick_prof(self, pid):
        self._prof_id = pid
        p = self.kb.get(pid)
        if not p:
            return
        for k, b in self.prof_buttons.items():
            sel = (k == pid)
            b.config(bg=PRIMARY if sel else SURFACE_2,
                     fg="white" if sel else TEXT_MED,
                     activebackground=PRIMARY_HOVER if sel else "#e2e6f0",
                     activeforeground="white" if sel else TEXT_MED)
        self.prof_detail.config(
            text=f"已选 {p.name} · 核心概念 {p.concept_count()} · 符号 {len(p.symbol_mapping)} · 易错点 {len(p.common_misconceptions)}")
        self._update_ready_checklist()

    def _choose_vault(self):
        v = filedialog.askdirectory(title="选择 Obsidian Vault 目录")
        if v:
            self._vault_dir = v
            self.vault_entry.delete(0, "end")
            self.vault_entry.insert(0, v)
            self._update_ready_checklist()

    def _set_vault_from_entry(self):
        v = self.vault_entry.get().strip()
        if v:
            self._vault_dir = v
            self._update_ready_checklist()

    # ───────────────────────── 模型 ─────────────────────────
    def _on_model_mode_toggle(self):
        if self.account:
            try:
                self.account.set_pref("use_online", self.model_var.get() == "online")
                self.account.save()
            except Exception:
                pass
        self._update_statusbar_model()
        self._update_ready_checklist()

    def refresh_main(self):
        uname = self.account.username
        self.user_label.config(text=uname)
        try:
            self._avatar.delete("all")
            self._avatar.create_oval(1, 1, 23, 23, fill=PRIMARY, outline="")
            self._avatar.create_text(12, 13, text=(uname[:1] or "U").upper(),
                                     fill="white", font=("Microsoft YaHei UI", 9, "bold"))
        except Exception:
            pass
        models = self.account.get_models()
        names = [f"{m.get('name','?')} · {m.get('model','')}" for m in models]
        if names:
            self.model_dd.config(values=names)
            saved = self.account.get_pref("model_index", 0)
            cur = self.model_dd.current()
            if not (0 <= cur < len(names)):
                idx = saved if 0 <= saved < len(models) else self._default_model_index(models)
                self.model_dd.current(idx)
                cur = idx
            self.model_var.set("online" if self.account.get_pref("use_online") else "offline")
            if 0 <= cur < len(models):
                self.account.set_pref("model_index", cur)
                self._show_model_hint(cur)
        else:
            self.model_dd.config(values=[])
            self.model_dd.set("")
            self.model_var.set("offline")
            self._show_model_hint(-1)

        if self.profs and not self._prof_id:
            self._pick_prof(self.profs[0].id)
        try:
            self._load_courses()
            self._refresh_course_ui()
            if self._active_course and self._course_path(self._active_course):
                p = self._course_path(self._active_course)
                try:
                    os.makedirs(p, exist_ok=True)
                except Exception:
                    pass
                if os.path.isdir(p):
                    self._vault_dir = p
                    self.vault_entry.delete(0, "end")
                    self.vault_entry.insert(0, p)
            self._refresh_course_files()
            try:
                _nm = self.account.get_pref("notes_mode", "lecture") or "lecture"
                if hasattr(self, "mode_var") and hasattr(self, "_mode_to_display"):
                    self.mode_var.set(self._mode_display_of(_nm))
            except Exception:
                pass
        except Exception:
            pass
        self._update_statusbar_model()
        self.set_status("就绪")
        self._update_ready_checklist()
        # 无历史课件时在菜单里保持占位，有则可快速打开
        self._recent_ppts = self._get_recent_ppts()

    @staticmethod
    def _default_model_index(models):
        if not models:
            return 0
        for i, m in enumerate(models):
            if (m.get("api_key") or "").strip() and not (m.get("name") or "").strip().lower().startswith("deepseek"):
                return i
        for i, m in enumerate(models):
            if (m.get("api_key") or "").strip():
                return i
        return 0

    def _show_model_hint(self, idx):
        if idx < 0:
            self.model_hint.config(
                text="当前：离线规则（无需 API，可先跑通流程）。\n点「管理模型…」绑定 DeepSeek / OpenAI / Kimi / Ollama 可让结构更准。")
            self._update_statusbar_model()
            self._update_ready_checklist()
            return
        models = self.account.get_models()
        if 0 <= idx < len(models):
            m = models[idx]
        self.model_hint.config(text=f"将使用：{m.get('name')} · {m.get('model')}\nBase：{m.get('base_url')}")
        self._update_statusbar_model()
        self._update_ready_checklist()

    def _update_statusbar_model(self):
        if not hasattr(self, "statusbar_model"):
            return
        if self.model_var.get() == "online":
            idx = self.model_dd.current()
            models = self.account.get_models() if self.account else []
            if 0 <= idx < len(models):
                m = models[idx]
                self.statusbar_model.config(text=f"{m.get('name')} · {m.get('model')}")
            else:
                self.statusbar_model.config(text="在线模型（未选）")
        else:
            self.statusbar_model.config(text="本地规则（离线）")

    def _on_model_select(self, _=None):
        idx = self.model_dd.current()
        if idx >= 0:
            self.model_var.set("online")
            self.account.set_pref("model_index", idx)
            self._show_model_hint(idx)
            self._update_ready_checklist()

    def _open_model_manage(self):
        ModelDialog(self, self.account)
        self.wait_window(self._mdlg)
        models = self.account.get_models()
        names = [f"{m.get('name','?')} · {m.get('model','')}" for m in models]
        self.model_dd.config(values=names)
        saved = self.account.get_pref("model_index", 0)
        if models:
            if 0 <= saved < len(names):
                self.model_dd.current(saved)
            else:
                self.model_dd.current(min(saved, len(names) - 1))
            self._show_model_hint(self.model_dd.current())
        else:
            self.model_dd.set("")
            self.model_var.set("offline")
            self._show_model_hint(-1)

    def _test_model(self):
        idx = self.model_dd.current()
        if idx < 0:
            messagebox.showinfo("提示", "请先点「管理模型…」添加一个模型")
            return
        m = self.account.get_models()[idx]
        if not (m.get("api_key") or "").strip():
            messagebox.showwarning(
                "该模型没填 API Key",
                f"「{m.get('name')}」还没有 API Key。\n\n请点「管理模型…」→ 选中它 → 填上 API Key → 更新。",
                parent=self)
            return
        try:
            llm = build_llm(m)
            self.log("正在测试连接…", "info")
            self.set_status("测试模型连接…")
            self.update_idletasks()
            out = llm.chat([{"role": "user", "content": "请只回复两个字: 正常"}], max_tokens=64)
            if not out.strip():
                out = "(空回复)"
            self.log(f"连接成功：{out}", "ok")
            self.set_status("模型连接正常")
        except Exception as e:
            self.log(f"连接失败：{e}", "err")
            self.set_status("模型连接失败")
            messagebox.showerror("测试失败", f"{e}\n\n提示：Base URL 必须是 API 接口地址（通常以 /v1 结尾），并填对 API Key。")

    def set_status(self, msg):
        if hasattr(self, "status_label"):
            self.status_label.config(text=msg)

    # ───────────────────────── 生成 ─────────────────────────
    def _build_run_section(self, parent):
        parent.columnconfigure(0, weight=1)
        # 就绪检查：单行四格，保证无需滚动就能看到
        chk = tk.Frame(parent, bg="#f3f5fb", highlightthickness=1, highlightbackground=PRIMARY_LIGHT)
        chk.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        tk.Label(chk, text="就绪检查  ·  全部打勾后点下方按钮", font=FONT_S, bg="#f3f5fb", fg=TEXT_MED, anchor="w"
                 ).pack(fill="x", padx=12, pady=(8, 4))
        grid = tk.Frame(chk, bg="#f3f5fb")
        grid.pack(fill="x", padx=8, pady=(0, 10))
        self._ready_vars = {}
        for i, (key, _label) in enumerate((
            ("ppt", "课件"),
            ("course", "课程/输出"),
            ("prof", "专业"),
            ("model", "模型"),
        )):
            cell = tk.Frame(grid, bg="#f3f5fb", highlightthickness=1, highlightbackground=BORDER)
            cell.grid(row=0, column=i, sticky="ew", padx=3)
            grid.columnconfigure(i, weight=1)
            var = tk.StringVar(value="○ …")
            self._ready_vars[key] = var
            tk.Label(cell, textvariable=var, font=FONT_S, bg="#f3f5fb", fg=TEXT_LIGHT,
                     anchor="w").pack(fill="x", padx=8, pady=6)

        self.run_btn = tk.Button(parent, text="生成 Obsidian 笔记", command=self._run,
                                 bg=PRIMARY, fg="white", activebackground=PRIMARY_HOVER,
                                 activeforeground="white", font=FONT_RUN,
                                 relief="flat", cursor="hand2", bd=0, pady=12)
        self.run_btn.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.run_btn.bind("<Enter>", lambda e: self.run_btn.config(
            bg=PRIMARY_HOVER) if str(self.run_btn["state"]) != "disabled" else None)
        self.run_btn.bind("<Leave>", lambda e: self.run_btn.config(bg=PRIMARY) if str(self.run_btn["state"]) != "disabled" else None)
        self.bind_all("<Control-Return>", lambda e: self._run())

        prog_row = tk.Frame(parent, bg=SURFACE)
        prog_row.grid(row=2, column=0, sticky="ew")
        self.progress = ttk.Progressbar(prog_row, mode="determinate", maximum=100,
                                        style="Accent.Horizontal.TProgressbar")
        self.progress.pack(fill="x")
        self.run_hint = tk.Label(parent, text="就绪 · Ctrl+Enter 可快捷生成", bg=SURFACE,
                                 fg=TEXT_LIGHT, font=FONT_XS, anchor="w", wraplength=640, justify="left")
        self.run_hint.grid(row=3, column=0, sticky="w", pady=(6, 0))
        self._update_ready_checklist()

    def _set_ready(self, key, ok, text_ok, text_no):
        var = self._ready_vars.get(key)
        if not var:
            return
        if ok:
            var.set("✓ " + text_ok)
        else:
            var.set("○ " + text_no)

    def _update_ready_checklist(self):
        if not getattr(self, "_ready_vars", None):
            return
        self._set_ready("ppt", bool(self._ppt_path),
                        os.path.basename(self._ppt_path)[:10] if self._ppt_path else "已选择",
                        "待选课件")
        self._set_ready("course", bool(self._vault_dir),
                        (self._active_course or "已设输出"),
                        "待选课程")
        prof_ok = bool(self._prof_id or self.profs)
        prof_name = ""
        if self._prof_id:
            p = self.kb.get(self._prof_id)
            prof_name = p.name if p else self._prof_id
        elif self.profs:
            prof_name = self.profs[0].name
        self._set_ready("prof", prof_ok, prof_name or "已选择", "待选专业")

        if self.model_var.get() == "online" and self.model_dd.current() >= 0:
            models = self.account.get_models() if self.account else []
            idx = self.model_dd.current()
            if 0 <= idx < len(models) and (models[idx].get("api_key") or "").strip():
                self._set_ready("model", True, "在线·" + (models[idx].get("name") or "")[:6], "")
            else:
                self._set_ready("model", False, "", "缺 API Key")
        else:
            self._set_ready("model", True, "离线规则", "")

        if hasattr(self, "run_hint") and not self._running:
            missing = []
            if not self._ppt_path:
                missing.append("课件")
            if not self._vault_dir:
                missing.append("输出目录")
            if missing:
                self.run_hint.config(text="待完成：" + "、".join(missing), fg=PRIMARY)
            else:
                self.run_hint.config(text="全部就绪 · 可开始生成（Ctrl+Enter）", fg=SUCCESS)

    def _build_result_section(self, parent):
        sep = tk.Frame(parent, bg=BORDER, height=1)
        sep.grid(row=4, column=0, sticky="ew", pady=(14, 12))

        self.log_text = tk.Text(parent, bg=LOG_BG, fg="#dbe2f4", font=FONT_LOG,
                                relief="flat", bd=0, state="disabled", wrap="word",
                                padx=12, pady=8, height=8, highlightthickness=1,
                                highlightbackground=BORDER)
        self.log_text.grid(row=5, column=0, sticky="ew")
        self.log_text.tag_config("ok", foreground="#4ade80")
        self.log_text.tag_config("err", foreground="#fb7185")
        self.log_text.tag_config("info", foreground="#93a5fd")
        self.log_text.tag_config("dim", foreground="#8b93a7")

        res_row = tk.Frame(parent, bg=SURFACE)
        res_row.grid(row=6, column=0, sticky="ew", pady=(10, 0))
        tk.Label(res_row, text="输出", bg=SURFACE, fg=TEXT_MED, font=FONT_S).pack(side="left")
        self.result_path = tk.Label(res_row, text="尚未生成", bg=SURFACE, fg=TEXT_LIGHT, font=FONT_S, anchor="w")
        self.result_path.pack(side="left", fill="x", expand=True, padx=(8, 8))
        self.open_btn = mkbtn(res_row, "打开文件夹", self._open_folder, "primary", FONT_S, padx=12, pady=4)
        set_btn_enabled(self.open_btn, False)
        self.open_btn.pack(side="right")

    def _run(self):
        if self._running:
            return
        missing = []
        if not self._ppt_path:
            missing.append("请先选择 PPT / PDF 课件（或 Ctrl+O）")
        if not self._vault_dir:
            missing.append("请先选中课程或设置输出目录")
        if missing:
            messagebox.showwarning("还差一步", "\n".join(missing))
            self._update_ready_checklist()
            return
        if not os.path.isdir(self._vault_dir):
            try:
                os.makedirs(self._vault_dir, exist_ok=True)
            except Exception:
                messagebox.showwarning("提示", "输出目录无效，请重新选择")
                return
        if not self._inside_real_vault(self._vault_dir):
            if not messagebox.askyesno(
                    "Vault 确认",
                    "所选目录及其父级都找不到 .obsidian，可能还不是 Obsidian 仓库。\n\n"
                    "在 Obsidian 中「打开文件夹作为仓库」后即可正常使用。\n\n仍继续生成吗？"):
                return
        pid = self._prof_id or (self.profs[0].id if self.profs else "")
        mode = self._mode_values.get(self.mode_var.get(), "lecture")
        try:
            if self.account is not None:
                self.account.set_pref("notes_mode", mode)
        except Exception:
            pass
        self._running = True
        set_btn_enabled(self.run_btn, False)
        self.run_btn.config(text="正在生成…", bg=PRIMARY_HOVER)
        self.set_status("正在生成…")
        if hasattr(self, "run_hint"):
            self.run_hint.config(text="正在转换，请稍候…", fg=PRIMARY)
        set_btn_enabled(self.open_btn, False)
        self.progress["value"] = 0
        t = threading.Thread(target=self._run_worker,
                             args=(self._ppt_path, pid, self._vault_dir, mode), daemon=True)
        t.start()

    def _mode_display_of(self, mode):
        if mode in self._mode_to_display:
            return self._mode_to_display[mode]
        return "讲义式 · 按小节一文件（推荐）"

    def _on_mode_change(self, _evt=None):
        if self.account is not None:
            try:
                self.account.set_pref("notes_mode",
                                      self._mode_values.get(self.mode_var.get(), "lecture"))
            except Exception:
                pass

    def _run_worker(self, ppt, pid, vault, mode="lecture"):
        stages = [("准备", 5), ("解析文档", 25), ("调用模型/总结", 55), ("生成笔记", 85), ("写入文件", 95)]
        stage_i = [0]

        def progress(msg):
            self.after(0, lambda mm=msg: self._log_progress(mm, stages, stage_i))

        try:
            use_online = self.model_var.get() == "online" and self.model_dd.current() >= 0
            llm = None
            model_idx = -1 if not use_online else self.model_dd.current()
            if use_online:
                m = self.account.get_models()[model_idx]
                if not (m.get("api_key") or "").strip():
                    self.after(0, lambda: (
                        self.log(f"当前模型「{m.get('name')}」没有 API Key，已中止", "err"),
                        self._run_failed(f"模型「{m.get('name')}」未填 API Key"),
                    ))
                    return
                try:
                    llm = build_llm(m)
                except Exception:
                    llm = None
            result = run_conversion(ppt, pid, vault, account=self.account,
                                    model_index=model_idx, llm=llm, progress=progress,
                                    mode=mode)
            self.after(0, lambda: self._run_done(result))
        except Exception as e:
            self.after(0, lambda: self._run_failed(str(e)))

    def _run_failed(self, err):
        self._running = False
        set_btn_enabled(self.run_btn, True)
        self.run_btn.config(text="生成 Obsidian 笔记", bg=PRIMARY)
        self.set_status("生成失败")
        if hasattr(self, "run_hint"):
            self.run_hint.config(text="生成失败 · 可修改后重试", fg=DANGER)
        self.log(err, "err")
        messagebox.showerror("生成失败", err)

    def _log_progress(self, msg, stages, stage_i):
        self.log(msg, "info")
        self.set_status(msg)
        mapping = {"准备": 8, "解析": 25, "调用模型": 55, "总结": 55, "生成": 85, "写入": 95, "完成": 100}
        for kw, v in mapping.items():
            if kw in msg:
                self.progress["value"] = v
                break

    def _run_done(self, result):
        self._running = False
        set_btn_enabled(self.run_btn, True)
        self.run_btn.config(text="生成 Obsidian 笔记", bg=PRIMARY)
        self.progress["value"] = 100
        if result.ok and result.notes:
            self._last_notes = result.notes
            self._last_bundle = result.bundle
            self._last_fulltext = result.source_fulltext or ""
            self._last_vault = result.vault_dir
            mode = self._mode_values.get(self.mode_var.get(), "lecture")
            n_md = len(result.notes)
            n_extra = max(0, len(result.written_files or []) - n_md)
            if mode == "lecture":
                brief = f"完成 · 讲义 {n_md} 个文件" + (f" + {n_extra} 张截图" if n_extra else "")
            else:
                brief = f"完成 · 生成 {n_md} 个笔记"
            self.log("转换成功", "ok")
            for n in result.notes:
                self.log(f"  · {n.rel_path}", "dim")
            self.result_path.config(text=mid_ellipsis(result.vault_dir, 48), fg=TEXT)
            set_btn_enabled(self.open_btn, True)
            try:
                self._refresh_refine_targets()
            except Exception:
                pass
            self.account.set_pref("use_online", self.model_var.get() == "online")
            self._refresh_course_files()
            if hasattr(self, "run_hint"):
                self.run_hint.config(text="已完成 · 可在左侧「本课笔记」中打开", fg=SUCCESS)
            self._update_ready_checklist()
            _v = getattr(result, "validation", None)
            if _v is not None and getattr(_v, "overall_score", 0) > 0:
                try:
                    _brief = _v.to_brief()
                    brief = f"完成 · {n_md} 个笔记 · {_brief}"
                except Exception:
                    _brief = ""
                self.set_status(brief)
                messagebox.showinfo(
                    "完成",
                    f"已生成 {len(result.notes)} 个笔记到：\n{result.vault_dir}\n\n"
                    f"{_brief}\n详细质量报告见 99_知识质量报告.md")
            else:
                self.set_status(brief)
                messagebox.showinfo("完成", f"已生成 {len(result.notes)} 个笔记到：\n{result.vault_dir}")
        else:
            self._run_failed(result.error or "未知错误")

    def _open_folder(self):
        vault = self._last_vault or self._vault_dir
        if not vault or not os.path.isdir(vault):
            messagebox.showwarning("提示", "请先成功生成一次笔记")
            return
        self._reveal_folder(vault)

    # ───────────────────────── 对话修订 ─────────────────────────
    def _build_chat_section(self, parent):
        parent.columnconfigure(0, weight=1)
        trow = tk.Frame(parent, bg=SURFACE)
        trow.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        tk.Label(trow, text="修改对象", bg=SURFACE, fg=TEXT_MED, font=FONT_S).pack(side="left")
        self.refine_concept_var = tk.StringVar(value="整体 / MOC")
        self.refine_target_dd = ttk.Combobox(trow, textvariable=self.refine_concept_var,
                                             state="readonly", font=FONT_S)
        self.refine_target_dd.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.refine_target_dd.bind("<<ComboboxSelected>>", lambda e: self._sync_refine_target())

        self.chat_text = tk.Text(parent, bg=SURFACE_2, fg=TEXT, font=FONT_S,
                                 relief="flat", bd=0, state="disabled", wrap="word",
                                 height=6, padx=10, pady=8, highlightthickness=1,
                                 highlightbackground=BORDER, highlightcolor=PRIMARY)
        self.chat_text.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        self.chat_text.tag_config("me", foreground=PRIMARY_HOVER)
        self.chat_text.tag_config("ai", foreground="#0d9668")
        self.chat_text.tag_config("sys", foreground="#c2410c")

        irow = tk.Frame(parent, bg=SURFACE)
        irow.grid(row=2, column=0, sticky="ew")
        self.refine_entry = tk.Entry(irow, font=FONT_S, relief="solid", bd=1,
                                     highlightbackground=BORDER, highlightcolor=PRIMARY, bg="white")
        self.refine_entry.pack(side="left", fill="x", expand=True, ipady=5)
        self.refine_entry.bind("<Return>", lambda e: self._send_refine())
        mkbtn(irow, "发送修订", self._send_refine, "primary", FONT_S, padx=12, pady=5).pack(side="left", padx=(8, 0))
        mkbtn(irow, "应用并沉淀", self._apply_refine, "success", FONT_S, padx=12, pady=5).pack(side="left", padx=(6, 0))
        self.refine_hint = tk.Label(parent, text="先生成笔记（概念卡片模式），再对某一概念提修改意见",
                                    bg=SURFACE, fg=TEXT_LIGHT, font=FONT_XS, anchor="w")
        self.refine_hint.grid(row=3, column=0, sticky="w", pady=(8, 0))
        self.refine_send_btn = None

    def _sync_refine_target(self):
        self._refine_target = self.refine_concept_var.get()

    def _refresh_refine_targets(self):
        if self._last_bundle is None:
            names = ["（讲义式输出暂不支持对话修订，请直接编辑 .md）"]
            try:
                self.refine_target_dd.config(values=names)
                self.refine_target_dd.current(0)
            except Exception:
                pass
            self._refine_target = ""
            return
        names = ["整体 / MOC"]
        for n in self._last_notes:
            if n.kind == "concept" and n.title:
                names.append(n.title)
        self.refine_target_dd.config(values=names)
        self.refine_target_dd.current(0)
        self._refine_target = names[0]

    def _current_note_md(self, target) -> str:
        if not target or target.startswith("整体"):
            for n in self._last_notes:
                if n.kind == "moc":
                    return n.content or self._read_note_file(n.abs_path or n.rel_path)
            return ""
        for n in self._last_notes:
            if n.kind == "concept" and (n.title == target or target in n.title):
                return n.content or self._read_note_file(n.abs_path or n.rel_path)
        return ""

    def _read_note_file(self, p):
        try:
            if os.path.isfile(p):
                with open(p, encoding="utf-8") as f:
                    return f.read()
        except Exception:
            pass
        return ""

    def _send_refine(self):
        req = self.refine_entry.get().strip()
        target = self._refine_target or "整体 / MOC"
        if not req:
            messagebox.showinfo("提示", "请先输入修改意见")
            return
        if not self._vault_dir:
            messagebox.showwarning("提示", "请先生成一次笔记")
            return
        m = self._selected_model()
        if m is None:
            messagebox.showwarning("提示", "请先在左侧绑定并选择一个在线模型后再对话修改")
            return
        md = self._current_note_md(target)
        if not md:
            messagebox.showwarning("提示", f"未找到「{target}」的笔记正文。\n（讲义式输出暂不支持对话修订，可直接编辑生成的 .md 文件）")
            return
        try:
            llm = LLMClient(m.get("base_url", ""), m.get("api_key", ""), m.get("model", ""))
        except Exception as e:
            messagebox.showerror("模型错误", str(e))
            return
        prof = self.kb.get(self._prof_id)
        prof_name = prof.name if prof else (self._prof_id or "通用")
        kb_ctx = ""
        extra_ctx = ""
        if prof:
            from obsidian.summarizer import _kb_context
            kb_ctx = _kb_context(prof)
            try:
                store = CalibrationStore(prof.id, scope_dir=self._vault_dir or None)
                extra_ctx = store.to_extra_context()
            except Exception:
                pass
        course_mem = self._course_corr_bucket()
        if course_mem:
            mem = "\n【本会话你已确认的学科修正(仅针对当前课程, 后续修改需保持一致)】\n" + "\n".join(
                f"- {s}" for s in course_mem[-12:])
            extra_ctx = extra_ctx + ("\n" if extra_ctx else "") + mem
        pages_hint = ""
        for n in self._last_notes:
            if n.kind == "concept" and target in n.title:
                pages_hint = n.source_pages or ""
                break
        self.chat("me", f"【改 {target}】{req}")
        self.log("正在让模型修订…", "info")
        self._chat_target_md = md
        self._chat_llm = llm
        self._chat_target = target
        self._chat_kb_ctx = kb_ctx
        self._chat_extra_ctx = extra_ctx
        self._chat_pages = pages_hint
        self._chat_prof_name = prof_name

        t = threading.Thread(target=self._refine_worker, daemon=True)
        t.start()

    def _selected_model(self):
        if self.model_var.get() != "online":
            return None
        idx = self.model_dd.current()
        if idx < 0:
            return None
        models = self.account.get_models() if self.account else []
        return models[idx] if 0 <= idx < len(models) else None

    def _refine_worker(self):
        try:
            md = getattr(self, "_chat_target_md", "") or ""
            res = refine_mod.revise_note_text(
                llm=self._chat_llm,
                concept_or_target=self._chat_target,
                current_md=md,
                user_request=self.refine_entry.get().strip(),
                professional_name=self._chat_prof_name,
                ppt_text=getattr(self, "_last_fulltext", ""),
                pages_hint=self._chat_pages,
                kb_context=self._chat_kb_ctx,
                extra_context=self._chat_extra_ctx,
                progress=lambda msg: self.after(0, lambda: self.log(msg, "info")),
            )
            if res.error:
                self.after(0, lambda: (self.chat("ai", f"修订失败：{res.error}"),
                                       self.log(res.error, "err")))
                return
            self._pending_revised = res.revised_md
            self.after(0, lambda: self.chat(
                "ai",
                "已生成修订稿（点「应用并沉淀」写回并记住）：\n"
                + res.revised_md[:400] + ("…" if len(res.revised_md) > 400 else "")))
            self.log("修订稿已生成", "ok")
        except Exception as e:
            self.after(0, lambda: self.chat("ai", f"出错：{e}"))

    def _apply_refine(self):
        revised = self._pending_revised
        target = self._refine_target or "整体 / MOC"
        if not revised:
            messagebox.showinfo("提示", "还没有可应用的修订稿，请先「发送修订」")
            return
        if not self._vault_dir:
            messagebox.showwarning("提示", "未设置输出目录")
            return
        path = self._resolve_target_file(target)
        if not path:
            messagebox.showwarning("提示", f"未定位到「{target}」对应的笔记文件")
            return
        if not messagebox.askyesno("确认应用",
                                   f"将用修订稿覆写：\n{path}\n\n并把本次修正沉淀到知识库，继续？"):
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(revised)
        except Exception as e:
            messagebox.showerror("写回失败", str(e))
            return
        self._persist_correction(target, revised)
        self._pending_revised = ""
        messagebox.showinfo("完成", "已应用修订并沉淀到知识库（下次转换同专业会更准）")

    def _resolve_target_file(self, target) -> str:
        if not self._vault_dir:
            return ""
        for n in self._last_notes:
            is_moc = target.startswith("整体") and n.kind == "moc"
            is_concept = (n.kind == "concept" and (n.title == target or target in n.title))
            if is_moc or is_concept:
                p = n.abs_path or os.path.join(self._vault_dir, n.rel_path.replace("/", os.sep))
                if os.path.isfile(p):
                    return p
        try:
            from obsidian.vault_aware import index_vault
            vx = index_vault(self._vault_dir)
            note = vx.find_by_concept(target)
            if note:
                return note.abs_path
        except Exception:
            pass
        return ""

    def _course_corr_bucket(self):
        key = self._vault_dir or self._active_course or "default"
        return self._session_corr_by_course.setdefault(key, [])

    def _persist_correction(self, target, revised):
        try:
            if not self._chat_llm:
                return
            orig = self._read_original_for(target)
            delta = refine_mod.extract_delta(llm=self._chat_llm, target=target,
                                             original_md=orig, revised_md=revised,
                                             professional_name=self._chat_prof_name,
                                             progress=lambda m: self.log(m, "info"))
            if not delta:
                self.log("未提炼出可沉淀的修正项（可能仅为措辞调整）", "dim")
                return
            store = CalibrationStore(self._prof_id, scope_dir=self._vault_dir or None)
            n = refine_mod.apply_user_correction(store, delta)
            bucket = self._course_corr_bucket()
            for it in (delta.get("corrected_definitions") or []):
                nm = (it or {}).get("name", "")
                if nm:
                    bucket.append(f"「{nm}」定义: {(it or {}).get('definition','')}")
            for it in (delta.get("new_symbols") or []):
                bucket.append(f"符号 {(it or {}).get('symbol','')} = {(it or {}).get('meaning','')}")
            for it in (delta.get("new_misconceptions") or []):
                bucket.append(f"易错: {(it or {}).get('misconception','')} → {(it or {}).get('clarification','')}")
            if n:
                self.log(f"已沉淀 {n} 条用户认可的修正到「{self._chat_prof_name}」知识库", "ok")
                self.chat("sys", f"已记住 {n} 条学科修正（下次转换/修改会更准）")
        except Exception as e:
            self.log(f"沉淀失败：{e}", "err")

    def _read_original_for(self, target):
        return self._current_note_md(target)

    def chat(self, who, msg):
        self.chat_text.config(state="normal")
        tag = "me" if who == "me" else ("ai" if who == "ai" else "sys")
        prefix = "你：" if who == "me" else ("AI：" if who == "ai" else "")
        self.chat_text.insert("end", prefix + msg + "\n\n", tag)
        self.chat_text.see("end")
        self.chat_text.config(state="disabled")

    def _logout(self):
        self.account = None
        self.pass_var.set("")
        self._show("login")

    def log(self, msg, tag="dim"):
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n", tag)
        self.log_text.see("end")
        self.log_text.config(state="disabled")


# ───────────────────────── 模型管理弹窗 ─────────────────────────
class ModelDialog(tk.Toplevel):
    def __init__(self, master, account):
        super().__init__(master)
        self.account = account
        master._mdlg = self
        self.title("管理大模型")
        self.geometry("560x520")
        self.minsize(520, 480)
        self.configure(bg=BG)
        self.transient(master)
        self.grab_set()

        root = tk.Frame(self, bg=BG)
        root.pack(fill="both", expand=True, padx=16, pady=14)

        tk.Label(root, text="绑定 OpenAI 兼容大模型 API", font=FONT_SECTION, bg=BG, fg=TEXT).pack(anchor="w")
        tk.Label(root, text="选快捷厂商可自动填好 Base URL / 模型名，只需粘贴 API Key",
                 font=FONT_XS, bg=BG, fg=TEXT_LIGHT).pack(anchor="w", pady=(2, 10))

        listf = tk.Frame(root, bg=BG)
        listf.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(listf, columns=("name", "model", "url"), show="headings", height=5)
        for c in ("name", "model", "url"):
            self.tree.heading(c, text={"name": "名称", "model": "模型", "url": "Base URL"}[c])
        self.tree.column("name", width=90)
        self.tree.column("model", width=130)
        self.tree.column("url", width=220)
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(listf, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.refresh()

        form = tk.Frame(root, bg=BG)
        form.pack(fill="x", pady=(12, 4))
        self.name_v = tk.StringVar()
        self.url_v = tk.StringVar()
        self.key_v = tk.StringVar()
        self.model_v = tk.StringVar()
        self.provider_v = tk.StringVar()
        self.auth_h = ""
        self.auth_s = ""
        self.disable_thinking = False
        self._fill(name="DeepSeek", url=KNOWN_PROVIDERS[0]["base_url"], model=KNOWN_PROVIDERS[0]["model"])
        r0 = tk.Frame(form, bg=BG)
        r0.pack(fill="x", pady=2)
        tk.Label(r0, text="快捷厂商", bg=BG, font=FONT_S, width=9, anchor="w").pack(side="left")
        prov = ttk.Combobox(r0, textvariable=self.provider_v,
                            values=[p["label"] for p in KNOWN_PROVIDERS],
                            state="readonly")
        prov.pack(side="left", fill="x", expand=True, padx=(4, 0))
        prov.bind("<<ComboboxSelected>>", self._on_provider)
        self._row(form, "名称", self.name_v)
        self._row(form, "Base URL", self.url_v)
        self._row(form, "API Key", self.key_v, show="●")
        self._row(form, "模型名", self.model_v)

        act = tk.Frame(self, bg=BG)
        act.pack(fill="x", padx=16, pady=(8, 4))
        mkbtn(act, "＋ 添加", self._add, "primary", FONT_S, padx=14, pady=5).pack(side="left", padx=(0, 6))
        mkbtn(act, "更新选中", self._update, "outline", FONT_S, padx=12, pady=5).pack(side="left", padx=6)
        mkbtn(act, "删除选中", self._delete, "danger", FONT_S, padx=12, pady=5).pack(side="left", padx=6)
        mkbtn(act, "关闭", self.destroy, "ghost", FONT_S, padx=12, pady=5).pack(side="right")

        self._note = tk.Label(self, text="", bg=BG, fg=SUCCESS, font=FONT_S, wraplength=520, justify="left")
        self._note.pack(anchor="w", padx=16, pady=(0, 10))

    def _row(self, parent, label, var, show=None):
        r = tk.Frame(parent, bg=BG)
        r.pack(fill="x", pady=2)
        tk.Label(r, text=label, bg=BG, font=FONT_S, width=9, anchor="w").pack(side="left")
        tk.Entry(r, textvariable=var, font=FONT_S, relief="solid", bd=1,
                 show=show, highlightthickness=1, highlightbackground=BORDER,
                 highlightcolor=PRIMARY).pack(side="left", fill="x", expand=True, ipady=3)

    def _on_provider(self, _=None):
        self.auth_h = ""
        self.auth_s = ""
        self.disable_thinking = False
        for p in KNOWN_PROVIDERS:
            if p["label"] == self.provider_v.get():
                self.name_v.set(p["label"])
                self.url_v.set(p["base_url"])
                self.model_v.set(p["model"])
                self.auth_h = p.get("auth_header", "") or ""
                self.auth_s = p.get("auth_scheme", "") or ""
                self.disable_thinking = bool(p.get("disable_thinking"))
                if p.get("auth_header") or p.get("disable_thinking"):
                    tips = []
                    if p.get("auth_header"):
                        tips.append(f"自定义请求头 {p['auth_header']} 认证")
                    if p.get("disable_thinking"):
                        tips.append("深度思考已关闭（更快）")
                    self._note.config(text="提示：" + "；".join(tips), fg="#b45309")
                else:
                    self._note.config(text="", fg=SUCCESS)
                break

    @staticmethod
    def _looks_like_doc_page(url: str) -> bool:
        low = (url or "").lower()
        bad = ("/platform/docs", "/platform", "/docs", "/documentation",
               "/doc/", "/guide", "/introduction", "/overview", "/readme",
               "/getting-started", "docs.", "/manual", "/help", "/wiki")
        return any(b in low for b in bad)

    def _fill(self, name="", url="", model="", key=""):
        self.name_v.set(name)
        self.url_v.set(url)
        self.model_v.set(model)
        self.key_v.set(key)

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for i, m in enumerate(self.account.get_models()):
            self.tree.insert("", "end", iid=str(i),
                             values=(m.get("name"), m.get("model"), m.get("base_url")))
        try:
            self.tree.unbind("<<TreeviewSelect>>")
        except Exception:
            pass
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)

    def _on_tree_select(self, _=None):
        sel = self.tree.selection()
        if not sel:
            return
        try:
            m = self.account.get_models()[int(sel[0])]
        except Exception:
            return
        self._fill(name=m.get("name", ""), url=m.get("base_url", ""),
                   model=m.get("model", ""), key=m.get("api_key", ""))
        self.auth_h = m.get("auth_header", "") or ""
        self.auth_s = m.get("auth_scheme", "") or ""
        self.disable_thinking = bool(m.get("disable_thinking"))

    def _warn_doc_url(self, url: str) -> bool:
        if self._looks_like_doc_page(url):
            messagebox.showwarning(
                "Base URL 疑似填错",
                "你填的 Base URL 像『文档/官网网页』，不是 API 接口。\n\n"
                "请改成形如 https://api.xxx.com/v1 的接口地址。\n"
                "DeepSeek：https://api.deepseek.com/v1",
                parent=self)
            return True
        return False

    def _add(self):
        name = self.name_v.get().strip()
        url = self.url_v.get().strip()
        key = self.key_v.get().strip()
        model = self.model_v.get().strip()
        if not name or not url or not model:
            messagebox.showwarning("提示", "名称 / Base URL / 模型名 不能为空", parent=self)
            return
        if self._warn_doc_url(url):
            return
        self.account.add_model(name, url, key, model,
                               auth_header=self.auth_h, auth_scheme=self.auth_s,
                               disable_thinking=self.disable_thinking)
        self.account.set_pref("model_index", len(self.account.get_models()) - 1)
        self.refresh()
        self._note.config(text="已添加，可在主界面选中使用", fg=SUCCESS)

    def _update(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在列表中选中一项", parent=self)
            return
        if self._warn_doc_url(self.url_v.get()):
            return
        self.account.update_model(int(sel[0]), name=self.name_v.get(), base_url=self.url_v.get(),
                                  api_key=self.key_v.get(), model=self.model_v.get(),
                                  auth_header=self.auth_h, auth_scheme=self.auth_s,
                                  disable_thinking=self.disable_thinking)
        self.refresh()
        self._note.config(text="已更新", fg=SUCCESS)

    def _delete(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在列表中选中一项", parent=self)
            return
        if messagebox.askyesno("确认", "删除该模型？", parent=self):
            self.account.remove_model(int(sel[0]))
            self.refresh()
            self._note.config(text="已删除", fg=TEXT_MED)


if __name__ == "__main__":
    app = App()
    app.mainloop()
