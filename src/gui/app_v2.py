# -*- coding: utf-8 -*-
"""
PPT → Obsidian 智能笔记 GUI v2

改进点:
  - 专业卡片网格 + 悬停动效
  - PPT 文件拖拽支持
  - 右侧实时预览面板(可折叠)
  - 进度动画 + 状态指示器
  - 更精致的浅色主题排版
  - 响应式布局(窗口缩放自适应)
"""
import os
import sys
import threading
import subprocess
import tempfile
from tkinter import ttk, messagebox, filedialog

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.dirname(_HERE)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import tkinter as tk

from core.app_config import AccountManager, AppPaths
from core.knowledge_base import KnowledgeBase
from llm.client import KNOWN_PROVIDERS, LLMClient
from pipeline import run_conversion

# ---------- 配色 ----------
BG = "#f0f2f5"
SURFACE = "#ffffff"
PRIMARY = "#2563eb"
PRIMARY_HOVER = "#1d4ed8"
PRIMARY_LIGHT = "#dbeafe"
TEXT = "#0f172a"
TEXT_MED = "#475569"
TEXT_LIGHT = "#94a3b8"
BORDER = "#e2e8f0"
BORDER_HOVER = "#cbd5e1"
SUCCESS = "#059669"
SUCCESS_BG = "#d1fae5"
WARN = "#d97706"
WARN_BG = "#fef3c7"
DANGER = "#dc2626"
DANGER_BG = "#fee2e2"
ACCENT = "#7c3aed"

FONT = ("Microsoft YaHei UI", 10)
FONT_S = ("Microsoft YaHei UI", 9)
FONT_XS = ("Microsoft YaHei UI", 8)
FONT_TITLE = ("Microsoft YaHei UI", 20, "bold")
FONT_H1 = ("Microsoft YaHei UI", 14, "bold")
FONT_H2 = ("Microsoft YaHei UI", 12, "bold")
FONT_B = ("Microsoft YaHei UI", 11, "bold")

# ---------- 工具函数 ----------

def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def _rgb_to_hex(r, g, b):
    return f"#{r:02x}{g:02x}{b:02x}"

def _blend(c1, c2, alpha):
    r1, g1, b1 = _hex_to_rgb(c1)
    r2, g2, b2 = _hex_to_rgb(c2)
    return _rgb_to_hex(
        int(r1 + (r2 - r1) * alpha),
        int(g1 + (g2 - g1) * alpha),
        int(b1 + (b2 - b1) * alpha),
    )


# ---------- 圆角卡片组件 ----------

class RoundedFrame(tk.Canvas):
    """圆角矩形卡片, 可放子控件。"""
    def __init__(self, parent, radius=12, bg=SURFACE, border=BORDER, border_w=1, **kw):
        self._r = radius
        self._bg = bg
        self._border = border
        self._border_w = border_w
        self._children = tk.Frame(parent, bg=bg)
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, **kw)
        self._children.bind("<Configure>", lambda e: self._draw())
        self.bind("<Configure>", lambda e: self._draw())
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() or 200
        h = self.winfo_height() or 100
        r = self._r
        # 圆角多边形
        pts = [
            r, 0, w - r, 0, w, 0, w, r, w, h - r, w, h, w - r, h, r, h, 0, h, 0, h - r, 0, r, 0, 0, r, 0
        ]
        self.create_polygon(pts, fill=self._bg, outline=self._border, width=self._border_w, smooth=True)
        # 把子 frame 放到正确位置
        self._children.place(x=self._border_w, y=self._border_w,
                             width=max(1, w - self._border_w * 2),
                             height=max(1, h - self._border_w * 2))

    def inner(self):
        return self._children


# ---------- 主应用 ----------

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PPT → Obsidian 智能笔记")
        self.geometry("1180x760")
        self.minsize(960, 640)
        self.configure(bg=BG)
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

        # 主容器
        self.container = tk.Frame(self, bg=BG)
        self.container.pack(fill="both", expand=True)

        self._pages = {}
        for name, builder in [("login", self._build_login), ("main", self._build_main)]:
            page = builder(self.container)
            self._pages[name] = page

        self._show("login")

    def _setup_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", font=FONT, background=BG, foreground=TEXT)
        st.configure("TButton", padding=(12, 6))
        st.configure("Primary.TButton", background=PRIMARY, foreground="white")
        st.map("Primary.TButton",
               background=[("active", PRIMARY_HOVER), ("!disabled", PRIMARY)],
               foreground=[("!disabled", "white")])
        st.configure("Ghost.TButton", background="#f1f5f9", foreground=TEXT)
        st.map("Ghost.TButton",
               background=[("active", "#e2e8f0")], foreground=[("!disabled", TEXT)])
        st.configure("TEntry", fieldbackground="white")

    def _clear_page(self, frame):
        for w in frame.winfo_children():
            w.destroy()

    def _show(self, name):
        for k, p in self._pages.items():
            p.pack_forget()
        self._pages[name].pack(fill="both", expand=True)

    def switch_to_main(self):
        self._show("main")
        self.refresh_main()

    # =================================================================
    #  登录页
    # =================================================================
    def _build_login(self, parent):
        page = tk.Frame(parent, bg=BG)
        # 左侧品牌区
        left = tk.Frame(page, bg=PRIMARY)
        left.place(relx=0, rely=0, relwidth=0.42, relheight=1)
        tk.Label(left, text="🧠", font=("Segoe UI Emoji", 48), bg=PRIMARY, fg="white").place(
            relx=0.5, rely=0.35, anchor="center")
        tk.Label(left, text="PPT → Obsidian", font=("Microsoft YaHei UI", 22, "bold"),
                 bg=PRIMARY, fg="white").place(relx=0.5, rely=0.48, anchor="center")
        tk.Label(left, text="专业驱动 · 本地优先 · 可生长的知识图谱",
                 font=FONT_S, bg=PRIMARY, fg="#bfdbfe").place(relx=0.5, rely=0.55, anchor="center")
        tk.Label(left, text="所有数据保存在本机\n不上传、不依赖中心服务器",
                 font=FONT_XS, bg=PRIMARY, fg="#93c5fd", justify="center").place(
            relx=0.5, rely=0.72, anchor="center")

        # 右侧登录卡片
        right = tk.Frame(page, bg=BG)
        right.place(relx=0.42, rely=0, relwidth=0.58, relheight=1)
        card = tk.Frame(right, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        card.place(relx=0.5, rely=0.5, anchor="center", width=420, height=520)

        tk.Label(card, text="欢迎回来", font=FONT_TITLE, bg=SURFACE, fg=TEXT).pack(pady=(40, 4))
        tk.Label(card, text="登录你的本地账号", font=FONT_S, bg=SURFACE, fg=TEXT_MED).pack()

        self._build_login_form(card)
        return page

    def _build_login_form(self, card):
        pad = {"fill": "x", "padx": 44}
        tk.Label(card, text="账号", bg=SURFACE, fg=TEXT_MED, font=FONT_S, anchor="w").pack(pad, pady=(30, 2))
        self.username_var = tk.StringVar(value=self.mgr.remember_last_username() or "")
        self._entry(card, self.username_var)

        tk.Label(card, text="密码", bg=SURFACE, fg=TEXT_MED, font=FONT_S, anchor="w").pack(pad, pady=(16, 2))
        self.pass_var = tk.StringVar()
        self._entry(card, self.pass_var, show="●")

        self.auth_err = tk.Label(card, text="", bg=SURFACE, fg=DANGER, font=FONT_S)
        self.auth_err.pack(pady=(10, 0))

        btns = tk.Frame(card, bg=SURFACE)
        btns.pack(pady=(18, 6))
        tk.Button(btns, text="登  录", command=self._do_login, bg=PRIMARY,
                  fg="white", activebackground=PRIMARY_HOVER, relief="flat",
                  font=FONT_B, width=16, cursor="hand2", bd=0).pack(side="left", padx=6)
        tk.Button(btns, text="注册新账号", command=self._do_register,
                  bg="#f1f5f9", fg=TEXT, activebackground="#e2e8f0", relief="flat",
                  font=FONT, width=14, cursor="hand2", bd=0).pack(side="left", padx=6)

        tk.Label(card,
                 text="· 首次使用请先注册一个本地账号\n· 绑定你自己的大模型 API 后可获得更准确的结构化笔记\n· 未绑定模型也能用本地规则模式离线出基础笔记",
                 justify="left", bg=SURFACE, fg=TEXT_LIGHT, font=FONT_XS).pack(side="bottom", pady=18)

    def _entry(self, parent, var, show=None):
        e = tk.Entry(parent, textvariable=var, font=FONT, relief="solid", bd=1,
                     highlightthickness=1, highlightcolor=PRIMARY, highlightbackground=BORDER,
                     bg="white", show=show)
        e.pack(fill="x", padx=44)
        return e

    def _do_login(self):
        u = self.username_var.get().strip()
        p = self.pass_var.get()
        if not u or not p:
            self.auth_err.config(text="请输入账号和密码")
            return
        try:
            acc = self.mgr.login(u, p)
        except ValueError as e:
            self.auth_err.config(text=str(e))
            return
        self.account = acc
        self.switch_to_main()

    def _do_register(self):
        u = self.username_var.get().strip()
        p = self.pass_var.get()
        if not u or not p:
            self.auth_err.config(text="请输入账号和密码")
            return
        try:
            acc = self.mgr.register(u, p)
        except ValueError as e:
            self.auth_err.config(text=str(e))
            return
        self.account = acc
        self.switch_to_main()

    # =================================================================
    #  主页面
    # =================================================================
    def _build_main(self, parent):
        page = tk.Frame(parent, bg=BG)
        # 顶部导航栏
        self._build_header(page)
        # 主体内容区(三列: 左侧步骤 | 中间预览 | 右侧模型+日志)
        body = tk.Frame(page, bg=BG)
        body.pack(fill="both", expand=True, padx=20, pady=(0, 16))
        body.columnconfigure(0, weight=2, minsize=380)
        body.columnconfigure(1, weight=3, minsize=420)
        body.columnconfigure(2, weight=2, minsize=280)
        body.rowconfigure(0, weight=1)

        self._build_left_panel(body)
        self._build_center_panel(body)
        self._build_right_panel(body)
        # 初始化默认选中第一个专业(此时 preview_text 已创建)
        if self.profs:
            self._pick_prof(self.profs[0].id)
        return page

    def _build_header(self, parent):
        hdr = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        hdr.pack(fill="x")
        tk.Label(hdr, text="📚  PPT → Obsidian 智能笔记", font=FONT_H1, bg=SURFACE).pack(side="left", padx=20, pady=12)
        self.user_label = tk.Label(hdr, text="", bg=SURFACE, fg=TEXT_MED, font=FONT_S)
        self.user_label.pack(side="right", padx=20)
        tk.Button(hdr, text="退出", command=self._logout, bg="#f1f5f9", fg=TEXT,
                  relief="flat", cursor="hand2", font=FONT_S, bd=0).pack(side="right", padx=(0, 6), pady=10)

    def _build_left_panel(self, parent):
        left = tk.Frame(parent, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        self._build_profession_section(left)
        self._build_file_section(left)
        self._build_action_section(left)

    def _build_center_panel(self, parent):
        center = tk.Frame(parent, bg=BG)
        center.grid(row=0, column=1, sticky="nsew", padx=(10, 10))
        self._build_preview_section(center)

    def _build_right_panel(self, parent):
        right = tk.Frame(parent, bg=BG)
        right.grid(row=0, column=2, sticky="nsew", padx=(10, 0))
        self._build_model_section(right)
        self._build_log_section(right)

    # ---------- 专业选择(卡片网格) ----------
    def _build_profession_section(self, parent):
        card = RoundedFrame(parent, radius=12, bg=SURFACE, border=BORDER)
        card.pack(fill="x", pady=(0, 12))
        inner = card.inner()
        tk.Label(inner, text="① 选择专业", font=FONT_H2, bg=SURFACE).pack(anchor="w", padx=16, pady=(14, 2))
        tk.Label(inner, text="选择与 PPT 内容最匹配的专业，准确度会显著提升", font=FONT_S,
                 bg=SURFACE, fg=TEXT_MED).pack(anchor="w", padx=16)

        grid = tk.Frame(inner, bg=SURFACE)
        grid.pack(fill="x", padx=16, pady=(10, 14))
        self.prof_buttons = {}
        if not self.profs:
            tk.Label(grid, text="未加载到专业框架", fg=DANGER, bg=SURFACE).pack(anchor="w")
            return
        for i, p in enumerate(self.profs):
            b = tk.Frame(grid, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1, cursor="hand2")
            b.grid(row=0, column=i, padx=(0 if i == 0 else 8), pady=2, sticky="nsew")
            b.bind("<Enter>", lambda e, f=b: f.config(highlightbackground=PRIMARY))
            b.bind("<Leave>", lambda e, f=b, pid=p.id: f.config(
                highlightbackground=PRIMARY if self._prof_id == pid else BORDER))
            b.bind("<Button-1>", lambda e, pid=p.id: self._pick_prof(pid))
            lbl = tk.Label(b, text=p.name, font=FONT_B, bg=SURFACE, fg=TEXT)
            lbl.pack(padx=18, pady=(10, 2))
            lbl.bind("<Button-1>", lambda e, pid=p.id: self._pick_prof(pid))
            desc = tk.Label(b, text=f"{p.concept_count()} 概念 · {len(p.symbol_mapping)} 符号", font=FONT_XS,
                            bg=SURFACE, fg=TEXT_LIGHT)
            desc.pack(padx=18, pady=(0, 10))
            desc.bind("<Button-1>", lambda e, pid=p.id: self._pick_prof(pid))
            self.prof_buttons[p.id] = b

    def _pick_prof(self, pid):
        self._prof_id = pid
        for k, b in self.prof_buttons.items():
            sel = (k == pid)
            b.config(highlightbackground=PRIMARY if sel else BORDER, highlightthickness=2 if sel else 1)
            for child in b.winfo_children():
                child.config(fg=PRIMARY if sel else TEXT if child.cget("text") == b.winfo_children()[0].cget("text") else TEXT_LIGHT)
        p = self.kb.get(pid)
        if p and hasattr(self, "prof_detail_label"):
            self.prof_detail_label.config(text=f"已选: {p.name}  |  {p.desc}")
        self._update_preview()

    # ---------- 文件选择 + 拖拽 ----------
    def _build_file_section(self, parent):
        card = RoundedFrame(parent, radius=12, bg=SURFACE, border=BORDER)
        card.pack(fill="x", pady=(0, 12))
        inner = card.inner()
        tk.Label(inner, text="② 选择 PPT 与输出位置", font=FONT_H2, bg=SURFACE).pack(anchor="w", padx=16, pady=(14, 2))

        # PPT 拖拽区
        self.drop_zone = tk.Frame(inner, bg="#f8fafc", highlightbackground=BORDER, highlightthickness=2,
                                  height=80, cursor="hand2")
        self.drop_zone.pack(fill="x", padx=16, pady=(8, 6))
        self.drop_zone.pack_propagate(False)
        self.drop_label = tk.Label(self.drop_zone, text="📎 拖拽 PPTX 文件到此处，或点击选择",
                                   font=FONT_S, bg="#f8fafc", fg=TEXT_MED)
        self.drop_label.pack(expand=True)
        self.drop_zone.bind("<Button-1>", lambda e: self._choose_ppt())
        self._setup_dnd(self.drop_zone)
        for c in self.drop_zone.winfo_children():
            c.bind("<Button-1>", lambda e: self._choose_ppt())

        # Vault 选择
        row = tk.Frame(inner, bg=SURFACE)
        row.pack(fill="x", padx=16, pady=(4, 14))
        self.vault_label = tk.Label(row, text="未选择 Obsidian Vault", bg=SURFACE, fg=TEXT_LIGHT, anchor="w")
        self.vault_label.pack(side="left", fill="x", expand=True)
        tk.Button(row, text="选择 Vault", command=self._choose_vault, bg="#f1f5f9", fg=TEXT,
                  relief="flat", cursor="hand2", font=FONT_S, bd=0).pack(side="right")

    def _setup_dnd(self, widget):
        """注册 tkinter 拖拽事件 (Windows)。"""
        try:
            widget.tk.eval("""
                proc tkDndEnter {args} { return copy }
                proc tkDndPosition {args} { return copy }
                proc tkDndDrop {args} { event generate %W <<Drop>> -data [lindex $args 0] }
            """)
            widget.bind("<<Drop>>", self._on_drop)
            widget.tk.call("tkdnd::drop_target", "register", widget._w, "Files")
        except Exception:
            # tkdnd 未安装则静默跳过, 仍可用按钮选择
            pass

    def _on_drop(self, event):
        data = getattr(event, "data", "")
        if not data:
            return
        # Windows 拖拽数据格式: {C:/path/to/file.pptx} 或空格分隔
        files = [f.strip("{}\"") for f in data.split() if f.strip("{}\"").lower().endswith(".pptx")]
        if files:
            self._set_ppt(files[0])

    def _set_ppt(self, path):
        self._ppt_path = path
        self.drop_label.config(text=f"📄 {os.path.basename(path)}", fg=TEXT)
        self.drop_zone.config(highlightbackground=SUCCESS, bg=SUCCESS_BG)
        self._update_preview()

    def _choose_ppt(self):
        p = filedialog.askopenfilename(title="选择 PPT 课件",
                                       filetypes=[("PowerPoint", "*.pptx *.ppt"), ("所有文件", "*.*")])
        if p:
            self._set_ppt(p)

    def _choose_vault(self):
        v = filedialog.askdirectory(title="选择 Obsidian Vault 目录")
        if v:
            self._vault_dir = v
            self.vault_label.config(text=f"🗂 {v}", fg=TEXT)
            self._update_preview()

    # ---------- 预览面板 ----------
    def _build_preview_section(self, parent):
        card = RoundedFrame(parent, radius=12, bg=SURFACE, border=BORDER)
        card.pack(fill="both", expand=True)
        inner = card.inner()
        tk.Label(inner, text="📋 预览", font=FONT_H2, bg=SURFACE).pack(anchor="w", padx=16, pady=(14, 2))
        self.prof_detail_label = tk.Label(inner, text="请先选择专业与 PPT", font=FONT_S,
                                          bg=SURFACE, fg=TEXT_MED, anchor="w")
        self.prof_detail_label.pack(fill="x", padx=16)

        self.preview_text = tk.Text(inner, height=10, bg="#f8fafc", fg=TEXT, font=FONT_S,
                                    relief="solid", bd=1, state="disabled", wrap="word",
                                    padx=10, pady=10)
        self.preview_text.pack(fill="both", expand=True, padx=16, pady=(8, 14))
        self.preview_text.tag_config("title", font=FONT_B, foreground=PRIMARY)
        self.preview_text.tag_config("dim", foreground=TEXT_LIGHT)
        self.preview_text.tag_config("ok", foreground=SUCCESS)
        self.preview_text.tag_config("warn", foreground=WARN)

    def _update_preview(self):
        self.preview_text.config(state="normal")
        self.preview_text.delete("1.0", "end")
        if not self._prof_id:
            self.preview_text.insert("end", "请选择专业…", "dim")
            self.preview_text.config(state="disabled")
            return
        p = self.kb.get(self._prof_id)
        if not p:
            self.preview_text.config(state="disabled")
            return
        self.preview_text.insert("end", f"专业: {p.name}\n", "title")
        self.preview_text.insert("end", f"核心概念 {p.concept_count()} 个 · 符号 {len(p.symbol_mapping)} 个 · 易错点 {len(p.common_misconceptions)} 个\n\n", "dim")
        if self._ppt_path:
            self.preview_text.insert("end", f"PPT: {os.path.basename(self._ppt_path)}\n", "ok")
        else:
            self.preview_text.insert("end", "尚未选择 PPT\n", "warn")
        if self._vault_dir:
            self.preview_text.insert("end", f"Vault: {self._vault_dir}\n", "ok")
        else:
            self.preview_text.insert("end", "尚未选择 Vault\n", "warn")
        self.preview_text.config(state="disabled")

    # ---------- 运行按钮 ----------
    def _build_action_section(self, parent):
        card = RoundedFrame(parent, radius=12, bg=SURFACE, border=BORDER)
        card.pack(fill="x", pady=(0, 12))
        inner = card.inner()
        self.run_btn = tk.Button(inner, text="🚀 生成 Obsidian 笔记", command=self._run,
                                 bg=PRIMARY, fg="white", activebackground=PRIMARY_HOVER,
                                 font=FONT_B, relief="flat", cursor="hand2", bd=0, height=2)
        self.run_btn.pack(fill="x", padx=16, pady=(12, 6))
        self.status_label = tk.Label(inner, text="就绪", font=FONT_S, bg=SURFACE, fg=TEXT_MED)
        self.status_label.pack(anchor="w", padx=16, pady=(0, 12))
        self.open_btn = tk.Button(inner, text="📂 在 Obsidian 中打开笔记", command=self._open_vault,
                                  state="disabled", bg="#f1f5f9", fg=TEXT, relief="flat",
                                  cursor="hand2", font=FONT_S, bd=0)
        self.open_btn.pack(fill="x", padx=16, pady=(0, 12))

    # ---------- 模型管理 ----------
    def _build_model_section(self, parent):
        card = RoundedFrame(parent, radius=12, bg=SURFACE, border=BORDER)
        card.pack(fill="x", pady=(0, 12))
        inner = card.inner()
        tk.Label(inner, text="🤖 大模型", font=FONT_H2, bg=SURFACE).pack(anchor="w", padx=16, pady=(14, 2))
        self.model_var = tk.StringVar(value="offline")
        tk.Radiobutton(inner, text="本地规则模式(离线, 免费)", variable=self.model_var,
                       value="offline", bg=SURFACE, font=FONT_S, cursor="hand2").pack(anchor="w", padx=16)
        tk.Radiobutton(inner, text="使用已绑定的大模型", variable=self.model_var,
                       value="online", bg=SURFACE, font=FONT_S, cursor="hand2").pack(anchor="w", padx=16)
        self.model_dd = ttk.Combobox(inner, state="readonly", font=FONT_S)
        self.model_dd.pack(fill="x", padx=16, pady=(4, 0))
        self.model_dd.bind("<<ComboboxSelected>>", self._on_model_select)

        btnrow = tk.Frame(inner, bg=SURFACE)
        btnrow.pack(fill="x", padx=16, pady=(8, 0))
        tk.Button(btnrow, text="⚙️ 管理模型", command=self._open_model_manage,
                  bg="#f1f5f9", fg=TEXT, relief="flat", cursor="hand2", font=FONT_S, bd=0).pack(side="left", padx=(0, 6))
        tk.Button(btnrow, text="测试连接", command=self._test_model,
                  bg="#f1f5f9", fg=TEXT, relief="flat", cursor="hand2", font=FONT_S, bd=0).pack(side="left")
        self.model_hint = tk.Label(inner, text="", bg=SURFACE, fg=TEXT_MED, font=FONT_XS,
                                   justify="left", anchor="w", wraplength=260)
        self.model_hint.pack(fill="x", padx=16, pady=(8, 14))

    def _build_log_section(self, parent):
        card = RoundedFrame(parent, radius=12, bg=SURFACE, border=BORDER)
        card.pack(fill="both", expand=True)
        inner = card.inner()
        tk.Label(inner, text="📜 日志", font=FONT_H2, bg=SURFACE).pack(anchor="w", padx=16, pady=(14, 2))
        self.log_text = tk.Text(inner, height=8, bg="#f8fafc", fg=TEXT, font=("Consolas", 9),
                                relief="solid", bd=1, state="disabled", wrap="word", padx=8, pady=8)
        self.log_text.pack(fill="both", expand=True, padx=16, pady=(0, 14))
        self.log_text.tag_config("ok", foreground=SUCCESS)
        self.log_text.tag_config("err", foreground=DANGER)
        self.log_text.tag_config("info", foreground=PRIMARY)
        self.log_text.tag_config("dim", foreground=TEXT_LIGHT)

    # ---------------- 模型选择下拉刷新 ----------------
    def refresh_main(self):
        self.user_label.config(text=f"👤 {self.account.username}")
        models = self.account.get_models()
        names = [f"{m.get('name','?')} · {m.get('model','')}" for m in models]
        if names:
            self.model_dd.config(values=names)
            self.model_dd.current(0)
            self.model_var.set("online" if self.account.get_pref("use_online") else "offline")
            self._show_model_hint(0)
        else:
            self.model_dd.config(values=[])
            self.model_dd.set("")
            self.model_var.set("offline")
            self._show_model_hint(-1)
        self._update_preview()

    def _show_model_hint(self, idx):
        if idx < 0:
            self.model_hint.config(text="当前未绑定任何模型。\n点击「管理模型」添加 (支持 DeepSeek / OpenAI / Kimi / 智谱 / Ollama 等)。\n离线模式仍会生成基础笔记。")
            return
        models = self.account.get_models()
        if 0 <= idx < len(models):
            m = models[idx]
            self.model_hint.config(text=f"将使用: {m.get('name')}\nBase: {m.get('base_url')}\nModel: {m.get('model')}")

    def _on_model_select(self, _=None):
        idx = self.model_dd.current()
        if idx >= 0:
            self.model_var.set("online")
            self._show_model_hint(idx)

    def _open_model_manage(self):
        ModelDialog(self, self.account)

    def _test_model(self):
        idx = self.model_dd.current()
        if idx < 0:
            messagebox.showinfo("提示", "请先在弹窗中添加一个模型")
            return
        m = self.account.get_models()[idx]
        try:
            llm = LLMClient(m.get("base_url", ""), m.get("api_key", ""), m.get("model", ""))
            self.log("正在测试连接…", "info")
            self.update_idletasks()
            out = llm.chat([{"role": "user", "content": "请只回复两个字: 正常"}], max_tokens=10)
            self.log(f"✅ 连接成功: {out}", "ok")
        except Exception as e:
            self.log(f"❌ 连接失败: {e}", "err")
            messagebox.showerror("测试失败", str(e))

    # ---------------- 运行 ----------------
    def _run(self):
        if not self._ppt_path:
            messagebox.showwarning("提示", "请先选择一个 PPTX 文件")
            return
        if not self._vault_dir:
            messagebox.showwarning("提示", "请先选择输出用的 Obsidian Vault 目录")
            return
        if not os.path.isdir(os.path.join(self._vault_dir, ".obsidian")):
            if not messagebox.askyesno("Vault 确认",
                                       f"所选目录还不是 Obsidian vault(无 .obsidian 文件夹)。\n\n在 Obsidian 中「打开文件夹作为仓库」选择此目录即可正常使用。\n\n仍继续生成笔记吗?"):
                return
        pid = self._prof_id
        self.run_btn.config(state="disabled")
        self.status_label.config(text="正在转换…", fg=PRIMARY)
        self.open_btn.config(state="disabled")
        t = threading.Thread(target=self._run_worker, args=(self._ppt_path, pid, self._vault_dir), daemon=True)
        t.start()

    def _run_worker(self, ppt, pid, vault):
        try:
            use_online = self.model_var.get() == "online" and self.model_dd.current() >= 0
            llm = None
            model_idx = 0
            if use_online:
                model_idx = self.model_dd.current()
                m = self.account.get_models()[model_idx]
                try:
                    llm = LLMClient(m.get("base_url", ""), m.get("api_key", ""), m.get("model", ""))
                except Exception:
                    llm = None
            result = run_conversion(ppt, pid, vault, account=self.account,
                                    model_index=model_idx, llm=llm,
                                    progress=lambda m: self.after(0, lambda mm=m: self.log(mm, "info")))
            self.after(0, lambda: self._run_done(result))
        except Exception as e:
            self.after(0, lambda: self.log(f"❌ {e}", "err"))

    def _run_done(self, result):
        self.run_btn.config(state="normal")
        if result.ok and result.notes:
            self.status_label.config(text=f"✅ 完成, 生成 {len(result.notes)} 个笔记", fg=SUCCESS)
            self.log(f"✅ 转换成功! 共生成 {len(result.notes)} 个笔记文件:", "ok")
            for n in result.notes:
                self.log(f"   · {n.rel_path}", "dim")
            self.open_btn.config(state="normal")
            self._last_vault = result.vault_dir
            self.account.set_pref("use_online", self.model_var.get() == "online")
            messagebox.showinfo("完成", f"已生成 {len(result.notes)} 个笔记到:\n{result.vault_dir}\n\n点击「在 Obsidian 中打开笔记」可预览。")
        else:
            self.status_label.config(text="❌ 转换失败", fg=DANGER)
            self.log(f"❌ 转换失败: {result.error}", "err")
            messagebox.showerror("转换失败", result.error)

    def _open_vault(self):
        vault = getattr(self, "_last_vault", None) or getattr(self, "_vault_dir", None)
        if not vault or not os.path.isdir(vault):
            messagebox.showwarning("提示", "请先运行一次转换")
            return
        moc = self._find_moc(vault)
        target = moc or vault
        try:
            if sys.platform.startswith("win"):
                os.startfile(target)  # type: ignore
            elif sys.platform == "darwin":
                subprocess.Popen(["open", target])
            else:
                subprocess.Popen(["xdg-open", target])
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    def _find_moc(self, vault):
        for root, _, files in os.walk(vault):
            for f in files:
                if f == "00_MOC.md":
                    return os.path.join(root, f)
        return None

    def _logout(self):
        self.account = None
        self.pass_var.set("")
        self._show("login")

    def log(self, msg, tag="dim"):
        self.log_text.config(state="normal")
        self.log_text.insert("end", msg + "\n", tag)
        self.log_text.see("end")
        self.log_text.config(state="disabled")


# =================================================================
#  模型管理弹窗
# =================================================================
class ModelDialog(tk.Toplevel):
    def __init__(self, master, account):
        super().__init__(master)
        self.account = account
        self.title("管理模型绑定")
        self.geometry("540x480")
        self.configure(bg=BG)
        self.transient(master)
        self.grab_set()

        tk.Label(self, text="绑定你的大模型 API (OpenAI 兼容接口)", font=FONT_H2, bg=BG).pack(
            anchor="w", padx=16, pady=(14, 4))

        listf = tk.Frame(self, bg=BG)
        listf.pack(fill="both", expand=True, padx=16)
        self.tree = ttk.Treeview(listf, columns=("name", "model", "url"), show="headings", height=5)
        self.tree.heading("name", text="名称")
        self.tree.heading("model", text="模型")
        self.tree.heading("url", text="Base URL")
        self.tree.column("name", width=100)
        self.tree.column("model", width=140)
        self.tree.column("url", width=220)
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(listf, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.refresh()

        form = tk.Frame(self, bg=BG)
        form.pack(fill="x", padx=16, pady=8)
        self.name_v = tk.StringVar(); self.url_v = tk.StringVar()
        self.key_v = tk.StringVar(); self.model_v = tk.StringVar()
        self.provider_v = tk.StringVar()
        self._fill(name="DeepSeek", url=KNOWN_PROVIDERS[0]["base_url"], model=KNOWN_PROVIDERS[0]["model"])

        r0 = tk.Frame(form, bg=BG); r0.pack(fill="x", pady=2)
        tk.Label(r0, text="快捷厂商", bg=BG, font=FONT_S).pack(side="left")
        prov = ttk.Combobox(r0, textvariable=self.provider_v, values=[p["label"] for p in KNOWN_PROVIDERS],
                            state="readonly", width=14)
        prov.pack(side="left", padx=8)
        prov.bind("<<ComboboxSelected>>", self._on_provider)

        self._row(form, "名称", self.name_v)
        self._row(form, "Base URL", self.url_v)
        self._row(form, "API Key", self.key_v, show="●")
        self._row(form, "模型名", self.model_v)

        act = tk.Frame(self, bg=BG)
        act.pack(fill="x", padx=16, pady=10)
        tk.Button(act, text="添加", command=self._add, bg=PRIMARY, fg="white", relief="flat", cursor="hand2", bd=0).pack(side="left", padx=3)
        tk.Button(act, text="更新选中", command=self._update, bg="#f1f5f9", fg=TEXT, relief="flat", cursor="hand2", bd=0).pack(side="left", padx=3)
        tk.Button(act, text="删除选中", command=self._delete, bg="#f1f5f9", fg=DANGER, relief="flat", cursor="hand2", bd=0).pack(side="left", padx=3)
        self._note = tk.Label(self, text="", bg=BG, fg=SUCCESS, font=FONT_S)
        self._note.pack(anchor="w", padx=16)

    def _row(self, parent, label, var, show=None):
        r = tk.Frame(parent, bg=BG); r.pack(fill="x", pady=2)
        tk.Label(r, text=label, bg=BG, font=FONT_S, width=9, anchor="w").pack(side="left")
        e = tk.Entry(r, textvariable=var, font=FONT_S, relief="solid", bd=1, show=show)
        e.pack(side="left", fill="x", expand=True)

    def _on_provider(self, _=None):
        lbl = self.provider_v.get()
        for p in KNOWN_PROVIDERS:
            if p["label"] == lbl:
                self.name_v.set(lbl)
                self.url_v.set(p["base_url"])
                self.model_v.set(p["model"])
                break

    def _fill(self, name="", url="", model="", key=""):
        self.name_v.set(name); self.url_v.set(url); self.model_v.set(model); self.key_v.set(key)

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for i, m in enumerate(self.account.get_models()):
            self.tree.insert("", "end", iid=str(i), values=(m.get("name"), m.get("model"), m.get("base_url")))

    def _add(self):
        name = self.name_v.get().strip(); url = self.url_v.get().strip()
        key = self.key_v.get().strip(); model = self.model_v.get().strip()
        if not name or not url or not model:
            messagebox.showwarning("提示", "名称 / Base URL / 模型名 不能为空", parent=self)
            return
        self.account.add_model(name, url, key, model)
        self.refresh(); self._note.config(text="✅ 已添加")
        if hasattr(self.master, "refresh_main"):
            self.master.refresh_main()

    def _update(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在列表选中一项", parent=self); return
        idx = int(sel[0])
        self.account.update_model(idx, name=self.name_v.get(), base_url=self.url_v.get(),
                                  api_key=self.key_v.get(), model=self.model_v.get())
        self.refresh(); self._note.config(text="✅ 已更新")
        if hasattr(self.master, "refresh_main"):
            self.master.refresh_main()

    def _delete(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先在列表选中一项", parent=self); return
        idx = int(sel[0])
        if messagebox.askyesno("确认", "删除该模型绑定?", parent=self):
            self.account.remove_model(idx)
            self.refresh(); self._note.config(text="已删除")
            if hasattr(self.master, "refresh_main"):
                self.master.refresh_main()


if __name__ == "__main__":
    app = App()
    app.mainloop()
