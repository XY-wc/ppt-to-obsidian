# -*- coding: utf-8 -*-
"""
PPT → Obsidian 智能笔记 GUI v3 —— 聚焦"单页工作流"

布局(用户指定):
  左侧(工作区):
    ├─ 大 PPT 拖拽区(醒目)
    └─ 大模型 API 管理(直接内嵌在页面)
  右侧(结果区):
    ├─ 专业选择 + Vault 输出目录
    ├─ 运行按钮 + 实时进度
    └─ 笔记生成地址 + 「打开文件夹」按钮

其余(登录页、模型弹窗管理)复用 v2 的实现与视觉。
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

# ---------- 配色(现代靛蓝体系) ----------
BG = "#f3f4f9"            # 窗口底: 淡紫灰
SURFACE = "#ffffff"       # 卡片
SURFACE_2 = "#f8f9fd"     # 次级面
PRIMARY = "#4f46e5"       # 主色: 靛蓝
PRIMARY_HOVER = "#4338ca"
PRIMARY_LIGHT = "#e0e7ff"
PRIMARY_SOFT = "#eef0ff"  # 芯片/浅底
ACCENT = "#7c3aed"        # 渐变尾色(紫)
TEXT = "#1a1d2e"
TEXT_MED = "#5b6178"
TEXT_LIGHT = "#9aa1b5"
BORDER = "#e6e8f0"
SUCCESS = "#10b981"
SUCCESS_HOVER = "#0da271"
SUCCESS_BG = "#d9f7ec"
DANGER = "#ef4444"
DANGER_BG = "#fee2e2"
SHADOW = "#e2e5f1"        # 卡片投影

FONT = ("Microsoft YaHei UI", 10)
FONT_S = ("Microsoft YaHei UI", 9)
FONT_XS = ("Microsoft YaHei UI", 8)
FONT_TITLE = ("Microsoft YaHei UI", 22, "bold")
FONT_H1 = ("Microsoft YaHei UI", 15, "bold")
FONT_H2 = ("Microsoft YaHei UI", 12, "bold")
FONT_B = ("Microsoft YaHei UI", 11, "bold")

# 按钮种类 -> (默认底, hover底, 前景)
BTN_KINDS = {
    "primary": (PRIMARY, PRIMARY_HOVER, "white"),
    "success": (SUCCESS, SUCCESS_HOVER, "white"),
    "danger": ("#fef2f2", "#fee2e2", DANGER),
    "ghost": ("#eef0f7", "#e2e6f0", TEXT_MED),
}


def mkbtn(parent, text, cmd, kind="ghost", font=FONT_S, padx=12, pady=5, width=None):
    """统一的扁平按钮: 圆角感靠配色, hover 变色, 手型光标。"""
    bg, hov, fg = BTN_KINDS[kind]
    b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                  activebackground=hov, activeforeground=fg,
                  relief="flat", bd=0, cursor="hand2", font=font,
                  padx=padx, pady=pady, width=width)
    b.bind("<Enter>", lambda e: b.config(bg=hov) if str(b["state"]) != "disabled" else None)
    b.bind("<Leave>", lambda e: b.config(bg=bg))
    b._home_bg = bg
    return b


class RoundedFrame(tk.Canvas):
    """圆角卡片: 柔和投影 + 1px 细边。内容放在 .inner() 里, 高度随内容自适应。"""

    def __init__(self, parent, radius=14, bg=SURFACE, border=BORDER, border_w=1,
                 shadow=True, **kw):
        self._r = radius
        self._bg = bg
        self._border = border
        self._border_w = border_w
        self._shadow = shadow
        self._fit = kw.pop("fit_height", True)
        super().__init__(parent, bg=parent.cget("bg"), highlightthickness=0, **kw)
        self._children = tk.Frame(self, bg=bg)
        self._children.bind("<Configure>", lambda e: self._draw())
        self.bind("<Configure>", lambda e: self._draw())
        self._draw()

    @staticmethod
    def _rpts(w, h, r, dx=0, dy=0):
        return [r + dx, dy, w - r + dx, dy, w + dx, dy, w + dx, r + dy,
                w + dx, h - r + dy, w + dx, h + dy, w - r + dx, h + dy,
                r + dx, h + dy, dx, h + dy, dx, h - r + dy, dx, r + dy, dx, dy, r + dx, dy]

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() or 200
        # 内容自适应高度(仅 fit_height 时)
        if self._fit:
            try:
                self._children.update_idletasks()
                req = self._children.winfo_reqheight() + self._border_w * 2 + (4 if self._shadow else 0)
                if req > 4 and abs(int(float(self.cget("height") or 0)) - req) > 2:
                    self.config(height=req)
                    return  # config 会再触发 Configure -> 重绘
            except Exception:
                pass
        h = self.winfo_height() or 100
        r = self._r
        if self._shadow:
            self.create_polygon(self._rpts(w - 2, h - 4, r, dx=1, dy=3),
                                fill=SHADOW, outline="", smooth=True)
        self.create_polygon(self._rpts(w - 2, h - 4, r),
                            fill=self._bg, outline=self._border,
                            width=self._border_w, smooth=True)
        self._children.place(x=self._border_w, y=self._border_w,
                             width=max(1, w - self._border_w * 2 - 2),
                             height=max(1, h - self._border_w * 2 - 4))

    def inner(self):
        return self._children


class StepBadge(tk.Canvas):
    """步骤序号徽章: 浅靛蓝圆 + 主色数字。"""

    def __init__(self, parent, num, size=26):
        super().__init__(parent, width=size, height=size,
                         bg=parent.cget("bg"), highlightthickness=0)
        self.create_oval(1, 1, size - 1, size - 1, fill=PRIMARY_LIGHT, outline="")
        self.create_text(size / 2, size / 2 + 1, text=str(num),
                         fill=PRIMARY, font=("Microsoft YaHei UI", 11, "bold"))


class DashedZone(tk.Canvas):
    """虚线圆角拖拽区: normal/hover/active 三态。"""

    _STYLES = {
        "normal": {"bg": "#f8f9ff", "dash": "#a5b0f3"},
        "hover": {"bg": "#eef1ff", "dash": PRIMARY},
        "active": {"bg": "#ecfdf5", "dash": SUCCESS},
    }

    def __init__(self, parent, height=150, on_click=None):
        super().__init__(parent, bg=SURFACE, highlightthickness=0,
                         height=height, cursor="hand2")
        self._state = "normal"
        self._on_click = on_click
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Enter>", lambda e: self._hover(True))
        self.bind("<Leave>", lambda e: self._hover(False))
        if on_click:
            self.bind("<Button-1>", lambda e: on_click())
        self._draw()

    def _hover(self, inside):
        if self._state == "active":
            return
        self._state = "hover" if inside else "normal"
        self._draw()

    def set_state(self, state):
        self._state = state
        self._draw()

    def _draw(self):
        self.delete("all")
        w = self.winfo_width() or 400
        h = self.winfo_height() or 150
        st = self._STYLES.get(self._state, self._STYLES["normal"])
        r = 14
        pts = [r, 2, w - r, 2, w - 2, 2, w - 2, r, w - 2, h - r, w - 2, h - 2,
               w - r, h - 2, r, h - 2, 2, h - 2, 2, h - r, 2, r, 2, 2, r, 2]
        self.create_polygon(pts, fill=st["bg"], outline="", smooth=True)
        self.create_polygon(pts, fill="", outline=st["dash"], width=2,
                            dash=(6, 4), smooth=True)
        cx = w / 2
        self.icon_id = self.create_text(cx, h * 0.26, text="📥",
                                        font=("Segoe UI Emoji", 28), fill=PRIMARY)
        self.title_id = self.create_text(cx, h * 0.60, text="拖拽 PPT / PDF 文件到此处",
                                         font=FONT_B, fill=TEXT)
        self.sub_id = self.create_text(cx, h * 0.80, text="或点击选择文件 (.pptx / .ppt / .pdf)",
                                       font=FONT_XS, fill=TEXT_LIGHT)


def paint_vgradient(canvas, w, h, c1, c2):
    """在 canvas 上画垂直渐变(供登录页品牌面板)。"""
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
        self.geometry("1160x860")
        self.minsize(980, 700)
        self.configure(bg=BG)
        self._setup_app_icon()           # 窗口/任务栏图标(ico + png)
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
        self._last_notes = []      # 最近一次生成的笔记清单
        self._last_bundle = None
        self._last_fulltext = ""
        self._refine_target = ""   # 当前对话/修改目标(概念名或 "整体 / MOC")
        self._session_corrections = []   # 本会话内当前课程已认可的修正(回喂给模型, 保持连贯)
        self._session_corr_by_course = {}  # course_dir -> list[str], 切换课程即隔离记忆
        # 课程/文件夹管理器
        self._vault_root = ""           # Vault 根目录(课程都放其下)
        self._courses = []              # [{name, path}, ...]
        self._active_course = ""        # 当前选中的课程名
        self._course_objs = {}          # 课程名 -> 组件句柄(选中高亮用)

        self.container = tk.Frame(self, bg=BG)
        self.container.pack(fill="both", expand=True)
        self._pages = {}
        for name, builder in [("login", self._build_login), ("main", self._build_main)]:
            page = builder(self.container)
            self._pages[name] = page
        self._show("login")

    def _setup_app_icon(self):
        """设置窗口/任务栏图标。开发期与 PyInstaller 打包后均生效。
        优先 iconbitmap(.ico) 用于 Windows 原生窗口标题/任务栏,
        再用 iconphoto(PNG) 保证带 Alpha 通道的现代外观。"""
        # 资源定位: 打包后位于 sys._MEIPASS/assets, 开发期位于项目根/assets
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
        st.configure("TButton", padding=(12, 6))
        st.configure("Primary.TButton", background=PRIMARY, foreground="white")
        st.map("Primary.TButton", background=[("active", PRIMARY_HOVER)], foreground=[("!disabled", "white")])
        st.configure("Ghost.TButton", background="#eef0f7", foreground=TEXT_MED)
        st.map("Ghost.TButton", background=[("active", "#e2e6f0")], foreground=[("!disabled", TEXT_MED)])
        # 下拉框
        st.configure("TCombobox", fieldbackground="white", background="white",
                     bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER,
                     arrowcolor=TEXT_MED, padding=4)
        st.map("TCombobox",
               fieldbackground=[("readonly", "white")],
               bordercolor=[("focus", PRIMARY)],
               arrowcolor=[("active", PRIMARY)])
        # 树形列表
        st.configure("Treeview", background="white", fieldbackground="white",
                     foreground=TEXT, rowheight=25, font=FONT_S,
                     bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER)
        st.configure("Treeview.Heading", background=SURFACE_2, foreground=TEXT_MED,
                     font=("Microsoft YaHei UI", 9, "bold"), relief="flat")
        st.map("Treeview", background=[("selected", PRIMARY_LIGHT)],
               foreground=[("selected", TEXT)])
        # 滚动条(细款)
        st.configure("Vertical.TScrollbar", background="#d6dae6", troughcolor=BG,
                     bordercolor=BG, arrowcolor=TEXT_MED, width=10,
                     lightcolor=BG, darkcolor=BG)
        st.map("Vertical.TScrollbar", background=[("active", "#b9c0d4")])
        # 进度条(主色, 加粗)
        st.configure("Accent.Horizontal.TProgressbar", troughcolor=PRIMARY_SOFT,
                     background=PRIMARY, thickness=8, bordercolor=BG,
                     lightcolor=PRIMARY, darkcolor=PRIMARY)

    def _show(self, name):
        for k, p in self._pages.items():
            p.pack_forget()
        self._pages[name].pack(fill="both", expand=True)

    def switch_to_main(self):
        self._show("main")
        self.refresh_main()

    # =================================================================
    #  登录页 (同 v2)
    # =================================================================
    def _build_login(self, parent):
        page = tk.Frame(parent, bg=BG)
        # 左侧: 渐变品牌面板(靛蓝 -> 紫)
        grad = tk.Canvas(page, highlightthickness=0, bd=0)
        grad.place(relx=0, rely=0, relwidth=0.42, relheight=1)
        def _redraw_grad(e=None):
            w = grad.winfo_width() or 400
            h = grad.winfo_height() or 700
            paint_vgradient(grad, w, h, PRIMARY, ACCENT)
            # 装饰圆(同族浅色叠加)
            grad.create_oval(w * 0.55, h * 0.08, w * 1.15, h * 0.38,
                             fill="#6d64ec", outline="", tags="deco")
            grad.create_oval(-w * 0.25, h * 0.62, w * 0.35, h * 1.05,
                             fill="#5a4fe0", outline="", tags="deco")
            # 文案直接画在画布上, 跟随渐变背景
            grad.delete("brand")
            cx = w / 2
            grad.create_text(cx, h * 0.32, text="🧠", font=("Segoe UI Emoji", 52),
                             fill="white", tags="brand")
            grad.create_text(cx, h * 0.46, text="PPT → Obsidian",
                             font=("Microsoft YaHei UI", 19, "bold"),
                             fill="white", tags="brand")
            grad.create_text(cx, h * 0.54, text="专业驱动 · 本地优先 · 可生长的知识图谱",
                             font=FONT, fill="#cdd3ff", tags="brand")
            grad.create_text(cx, h * 0.72, text="数据保存在本机 · 绑定自己的大模型 API",
                             font=FONT_XS, fill="#aab3f8", tags="brand")
            grad.tag_raise("brand")
            grad.tag_lower("deco")
            grad.tag_lower("grad")
        grad.bind("<Configure>", _redraw_grad)

        # 右侧: 居中圆角登录卡(带投影)
        right = tk.Frame(page, bg=BG)
        right.place(relx=0.42, rely=0, relwidth=0.58, relheight=1)
        shell = tk.Frame(right, bg=BG)
        shell.place(relx=0.5, rely=0.5, anchor="center", width=400, height=720)
        card = RoundedFrame(shell, radius=18, bg=SURFACE, border=BORDER, border_w=1,
                            fit_height=False)
        card.pack(fill="both", expand=True)
        inner = card.inner()
        tk.Label(inner, text="欢迎使用", font=FONT_TITLE, bg=SURFACE, fg=TEXT).pack(pady=(30, 2))
        tk.Label(inner, text="登录你的本地账号", font=FONT, bg=SURFACE, fg=TEXT_MED).pack()

        tk.Label(inner, text="账号", bg=SURFACE, fg=TEXT_MED, font=FONT_S, anchor="w").pack(fill="x", padx=40, pady=(28, 2))
        self.username_var = tk.StringVar(value=self.mgr.remember_last_username() or "")
        self._entry(inner, self.username_var)
        tk.Label(inner, text="密码", bg=SURFACE, fg=TEXT_MED, font=FONT_S, anchor="w").pack(fill="x", padx=40, pady=(16, 2))
        self.pass_var = tk.StringVar()
        self._entry(inner, self.pass_var, show="●")
        self.auth_err = tk.Label(inner, text="", bg=SURFACE, fg=DANGER, font=FONT_S)
        self.auth_err.pack(pady=(12, 0))
        mkbtn(inner, "登  录", self._do_login, "primary", font=FONT_B,
              padx=0, pady=9).pack(fill="x", padx=40, pady=(18, 6))
        mkbtn(inner, "注册新账号", self._do_register, "ghost", font=FONT,
              padx=0, pady=8).pack(fill="x", padx=40)
        tk.Label(inner, text="首次使用请注册 · 未绑定模型也能离线生成",
                 justify="center", bg=SURFACE, fg=TEXT_LIGHT, font=FONT_XS,
                 wraplength=340).pack(side="bottom", padx=30, pady=14)
        return page

    def _entry(self, parent, var, show=None):
        e = tk.Entry(parent, textvariable=var, font=FONT, relief="solid", bd=1,
                     highlightthickness=1, highlightcolor=PRIMARY,
                     highlightbackground=BORDER, bg="white", show=show)
        e.pack(fill="x", padx=40, ipady=5)
        return e

    def _do_login(self):
        u = self.username_var.get().strip(); p = self.pass_var.get()
        if not u or not p:
            self.auth_err.config(text="请输入账号和密码"); return
        try:
            acc = self.mgr.login(u, p)
        except ValueError as e:
            self.auth_err.config(text=str(e)); return
        self.account = acc
        self.switch_to_main()

    def _do_register(self):
        u = self.username_var.get().strip(); p = self.pass_var.get()
        if not u or not p:
            self.auth_err.config(text="请输入账号和密码"); return
        try:
            acc = self.mgr.register(u, p)
        except ValueError as e:
            self.auth_err.config(text=str(e)); return
        self.account = acc
        self.switch_to_main()

    # =================================================================
    #  主页面 —— 单页工作流
    #  ============ 左右两栏 ============
    #  左: [PPT拖拽] [大模型API设置]
    #  右: [专业/输出] [运行/进度] [结果地址+打开]
    # =================================================================
    def _build_main(self, parent):
        page = tk.Frame(parent, bg=BG)
        self._build_header(page)
        body = tk.Frame(page, bg=BG)
        body.pack(fill="both", expand=True, padx=18, pady=14)
        self._build_left(body)
        self._build_right(body)
        return page

    def _build_header(self, parent):
        hdr = tk.Frame(parent, bg=SURFACE, highlightbackground=BORDER, highlightthickness=1)
        hdr.pack(fill="x")
        # 应用图标(优先 assets/icon_32.png 预缩放版, 避免 subsample 锯齿; 失败退回 emoji)
        icon_shown = False
        try:
            if getattr(sys, "frozen", False):
                base = getattr(sys, "_MEIPASS", os.path.dirname(__file__))
            else:
                base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            png32 = os.path.join(base, "assets", "icon_32.png")
            if os.path.isfile(png32):
                self._hdr_icon = tk.PhotoImage(file=png32)
                tk.Label(hdr, image=self._hdr_icon, bg=SURFACE).pack(side="left", padx=(18, 10), pady=10)
                icon_shown = True
        except Exception:
            pass
        if not icon_shown:
            tk.Label(hdr, text="📚", font=("Segoe UI Emoji", 20),
                     bg=SURFACE).pack(side="left", padx=(18, 10), pady=8)
        tcol = tk.Frame(hdr, bg=SURFACE)
        tcol.pack(side="left", pady=8)
        tk.Label(tcol, text="PPT → Obsidian 智能笔记", font=FONT_H1,
                 bg=SURFACE, fg=TEXT).pack(anchor="w")
        tk.Label(tcol, text="课件一键变身结构化笔记", font=FONT_XS,
                 bg=SURFACE, fg=TEXT_LIGHT).pack(anchor="w")
        mkbtn(hdr, "退出登录", self._logout, "ghost",
              font=FONT_S, padx=14, pady=5).pack(side="right", padx=(6, 18), pady=12)
        # 用户徽章: 头像圆(首字) + 用户名
        chip = tk.Frame(hdr, bg=PRIMARY_SOFT,
                        highlightbackground=PRIMARY_LIGHT, highlightthickness=1)
        chip.pack(side="right", pady=12)
        self._avatar = tk.Canvas(chip, width=26, height=26, bg=PRIMARY_SOFT,
                                 highlightthickness=0)
        self._avatar.pack(side="left", padx=(8, 6), pady=4)
        self.user_label = tk.Label(chip, text="", bg=PRIMARY_SOFT, fg=PRIMARY, font=FONT_S)
        self.user_label.pack(side="left", padx=(0, 10))

    # ---------------- 左栏 ----------------
    def _build_left(self, parent):
        # 使用 place 让左侧严格占 46% 宽度; 内容较长, 用可滚动画布承载(与右侧一致)
        left = tk.Frame(parent, bg=BG)
        left.place(relx=0, rely=0, relwidth=0.46, relheight=1)
        canvas = tk.Canvas(left, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(left, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        inner = tk.Frame(canvas, bg=BG)
        win_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        def _on_cfg(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(win_id, width=e.width)
        inner.bind("<Configure>", _on_cfg)
        canvas.bind("<Configure>", _on_cfg)
        def _wheel(e, c=canvas):
            try:
                c.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except Exception:
                pass
        canvas.bind("<MouseWheel>", _wheel)
        inner.bind("<MouseWheel>", _wheel)
        self._left_canvas = canvas

        # 顶部: 课程/文件夹管理器(大学课程多, 一科一夹)
        self._build_course_section(inner)
        # PPT 拖拽区
        self._build_ppt_dropzone(inner)
        # 大模型 API 管理
        self._build_model_section(inner)

    # -- 通用: 步骤标题(徽章 + 标题 + 副标题) --
    def _step_head(self, parent, num, title, sub=None):
        row = tk.Frame(parent, bg=SURFACE)
        StepBadge(row, num).pack(side="left", padx=(0, 10))
        col = tk.Frame(row, bg=SURFACE)
        col.pack(side="left")
        tk.Label(col, text=title, font=FONT_H2, bg=SURFACE, fg=TEXT).pack(anchor="w")
        if sub:
            tk.Label(col, text=sub, font=FONT_XS, bg=SURFACE, fg=TEXT_LIGHT).pack(anchor="w")
        return row

    # -- STEP 0: 我的课程 / 文件夹管理器 --
    def _build_course_section(self, parent):
        card = RoundedFrame(parent, radius=16)
        card.pack(fill="x", pady=(0, 14))
        inner = card.inner()
        inner.columnconfigure(0, weight=1)

        # 标题行 + Vault 根按钮(先 pack 右侧按钮, 保证它拿满所需宽度不被挤压)
        title_row = tk.Frame(inner, bg=SURFACE)
        title_row.pack(fill="x", padx=18, pady=(16, 2))
        self.set_root_btn = mkbtn(title_row, "设置 Vault 根", self._choose_vault_root,
                                  "ghost", FONT_S, padx=12, pady=4)
        self.set_root_btn.pack(side="right")
        self._step_head(title_row, 0, "我的课程", "一科一夹，互不混杂"
                        ).pack(side="left", fill="x", expand=True)
        # Vault 根当前路径
        self.root_path_lbl = tk.Label(inner, text="尚未设置 → 请先选一个 Vault 根(如 Obsidian 仓库目录)",
                                      bg=SURFACE, fg=TEXT_LIGHT, font=FONT_XS, anchor="w", wraplength=380, justify="left")
        self.root_path_lbl.pack(fill="x", padx=20, pady=(4, 8))

        # 课程列表(可滚动)
        list_holder = tk.Frame(inner, bg=SURFACE)
        list_holder.pack(fill="x", padx=18)
        self.course_lb = tk.Listbox(list_holder, height=4, font=FONT_S, bg=SURFACE_2,
                                    relief="flat", bd=0, highlightthickness=1,
                                    highlightbackground=BORDER, highlightcolor=PRIMARY,
                                    selectbackground=PRIMARY_LIGHT, selectforeground=TEXT,
                                    activestyle="none", exportselection=False)
        self.course_lb.pack(side="left", fill="x", expand=True)
        sc = ttk.Scrollbar(list_holder, orient="vertical", command=self.course_lb.yview)
        self.course_lb.configure(yscrollcommand=sc.set)
        sc.pack(side="right", fill="y")
        self.course_lb.bind("<<ListboxSelect>>", self._on_course_select)
        self.course_lb.bind("<Double-Button-1>", lambda e: self._open_active_folder())

        # 新建课程行
        new_row = tk.Frame(inner, bg=SURFACE)
        new_row.pack(fill="x", padx=18, pady=(8, 2))
        self.new_course_var = tk.StringVar()
        ent = tk.Entry(new_row, textvariable=self.new_course_var, font=FONT_S, relief="solid", bd=1,
                       highlightbackground=BORDER, highlightcolor=PRIMARY, bg="white")
        ent.pack(side="left", fill="x", expand=True, padx=(0, 6), ipady=3)
        ent.bind("<Return>", lambda e: self._new_course())
        ent.bind("<FocusIn>", lambda e: self._clear_course_ph(ent))
        self.new_course_entry = ent
        self._course_name_ph = True
        self._reset_course_ph()
        mkbtn(new_row, "＋ 新建课程", self._new_course, "primary",
              FONT_S, padx=12, pady=4).pack(side="right")

        # 操作按钮行
        op_row = tk.Frame(inner, bg=SURFACE)
        op_row.pack(fill="x", padx=18, pady=(6, 12))
        mkbtn(op_row, "📂 打开课程文件夹", self._open_active_folder,
              "ghost", FONT_S, padx=10, pady=4).pack(side="left")
        mkbtn(op_row, "🎯 在此课程选课件", self._pick_ppt_in_course,
              "ghost", FONT_S, padx=10, pady=4).pack(side="left", padx=(8, 0))
        mkbtn(op_row, "✂ 移除", self._remove_course,
              "danger", FONT_S, padx=10, pady=4).pack(side="right")

        self.course_hint = tk.Label(inner, text="尚未创建任何课程。新建后会自动在 Vault 根下建同名文件夹",
                                    bg=SURFACE, fg=TEXT_LIGHT, font=FONT_XS, anchor="w", wraplength=380, justify="left")
        self.course_hint.pack(fill="x", padx=20, pady=(0, 12))

        # ---- 课程夹内容浏览器 ----
        browse_title = tk.Frame(inner, bg=SURFACE)
        browse_title.pack(fill="x", padx=18, pady=(0, 4))
        tk.Label(browse_title, text="📂 已生成的笔记（双击用 Obsidian 打开）",
                 bg=SURFACE, fg=TEXT_MED, font=FONT_S, anchor="w").pack(side="left")
        mkbtn(browse_title, "↻ 刷新", self._refresh_course_files,
              "ghost", FONT_XS, padx=8, pady=2).pack(side="right")
        tree_holder = tk.Frame(inner, bg=SURFACE)
        tree_holder.pack(fill="x", padx=18, pady=(0, 6))
        self.course_tree = ttk.Treeview(tree_holder, show="tree", height=6, selectmode="browse")
        self.course_tree.pack(side="left", fill="both", expand=True)
        tsc = ttk.Scrollbar(tree_holder, orient="vertical", command=self.course_tree.yview)
        self.course_tree.configure(yscrollcommand=tsc.set)
        tsc.pack(side="right", fill="y")
        self.course_tree.bind("<Double-Button-1>", self._on_tree_open)
        self.course_tree.tag_configure("dir", foreground=PRIMARY)
        self.course_tree.tag_configure("md", foreground=TEXT)
        self.course_tree.tag_configure("img", foreground=TEXT_LIGHT)
        # 该浏览器操作按钮
        browse_op = tk.Frame(inner, bg=SURFACE)
        browse_op.pack(fill="x", padx=18, pady=(2, 12))
        mkbtn(browse_op, "📖 打开选中", self._open_selected_entry,
              "ghost", FONT_S, padx=10, pady=4).pack(side="left")
        mkbtn(browse_op, "✏️ 重命名", self._rename_selected_entry,
              "ghost", FONT_S, padx=10, pady=4).pack(side="left", padx=(8, 0))
        self.course_browse_hint = tk.Label(inner, text="选中一门课程后，这里会列出它里面已生成的内容",
                                           bg=SURFACE, fg=TEXT_LIGHT, font=FONT_XS, anchor="w", wraplength=380, justify="left")
        self.course_browse_hint.pack(fill="x", padx=20, pady=(0, 8))

    # -- 课程数据层(存于账号 prefs, key=courses) --
    def _load_courses(self):
        try:
            raw = self.account.get_pref("courses") or {}
        except Exception:
            raw = {}
        self._vault_root = raw.get("vault_root", "") or ""
        self._courses = [dict(c) for c in raw.get("courses", [])]
        self._active_course = raw.get("active", "") or ""
        # 过滤掉路径已被删/失效的课程项
        self._courses = [c for c in self._courses if c.get("name")]

    def _save_courses(self):
        if not self.account:
            return
        payload = {"vault_root": self._vault_root, "active": self._active_course,
                   "courses": self._courses}
        self.account.set_pref("courses", payload)
        self.account.save()

    def _refresh_course_ui(self):
        # 根路径显示
        if self._vault_root:
            self.root_path_lbl.config(text=f"Vault 根: {self._vault_root}", fg=TEXT_MED)
        else:
            self.root_path_lbl.config(text="尚未设置 → 请先选一个 Vault 根(如 Obsidian 仓库目录)", fg=TEXT_LIGHT)
        # 课程列表重建(保留选中高亮)
        self.course_lb.delete(0, "end")
        for c in self._courses:
            self.course_lb.insert("end", c["name"])
        # 定位并标蓝当前课程
        names = [c["name"] for c in self._courses]
        if self._active_course in names:
            idx = names.index(self._active_course)
            self.course_lb.selection_clear(0, "end")
            self.course_lb.selection_set(idx)
            self.course_lb.see(idx)
        # 提示(含该课程自己的长期记忆摘要, 让"一科一记忆"可见)
        if self._courses:
            act = self._active_course or self._courses[0]["name"]
            mem = self._course_memory_summary(self._course_path(act))
            self.course_hint.config(
                text=f"共 {len(self._courses)} 门课 · 当前: {act}\n{mem}",
                fg=TEXT_LIGHT)
        else:
            self.course_hint.config(text="尚未创建任何课程。新建后会自动在 Vault 根下建同名文件夹", fg=TEXT_LIGHT)

    def _course_memory_summary(self, course_path):
        """统计该课程文件夹里 .ppt2obsidian/*.json 的沉淀规模, 空则返回提示。"""
        if not course_path:
            return "本课记忆: 空(切换此课只读本课沉淀)"
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
            return f"本课记忆: {total} 条沉淀(仅本课可见, 切换课程即隔离)"
        return "本课记忆: 空 · 越用越准(此记忆只跟随这门课)"

    def _course_path(self, name):
        for c in self._courses:
            if c["name"] == name:
                return c.get("path", "")
        return ""

    # ---- 课程夹内容浏览器 ----
    def _refresh_course_files(self):
        """把当前课程夹里的内容(笔记子夹 + 笔记 .md)列到 course_tree。"""
        tree = getattr(self, "course_tree", None)
        if tree is None:
            return
        tree.delete(*tree.get_children())
        base = self._vault_dir if (self._vault_dir and os.path.isdir(self._vault_dir)) else ""
        if not base:
            self.course_browse_hint.config(text="先选中一门课程，即可在这里查看它已生成的内容", fg=TEXT_LIGHT)
            return
        try:
            entries = sorted(os.listdir(base))
        except Exception as e:
            self.course_browse_hint.config(text=f"读取失败: {e}", fg=DANGER)
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
            self.course_browse_hint.config(text=f"课程「{self._active_course}」内还没有笔记，生成后会自动出现在这里", fg=TEXT_LIGHT)
        else:
            # 目录(每份 PPT 一夹) 优先
            for d in dirs:
                did = tree.insert("", "end", text="📁 " + d, tags=("dir",), open=False)
                try:
                    for sub in sorted(os.listdir(os.path.join(base, d))):
                        if sub.startswith("."):
                            continue
                        sp = os.path.join(base, d, sub)
                        if os.path.isfile(sp) and sub.lower().endswith(".md"):
                            tree.insert(did, "end", text="📄 " + os.path.splitext(sub)[0], values=(sp,),
                                        tags=("md",))
                            n_notes += 1
                        elif os.path.isdir(sp):
                            pass
                except Exception:
                    pass
            # 课程夹根目录直接放 .md(未按 PPT 分夹的情况)
            for f in files:
                tree.insert("", "end", text="📄 " + os.path.splitext(f)[0],
                            values=(os.path.join(base, f),), tags=("md",))
                n_notes += 1
            self.course_browse_hint.config(
                text=f"课程「{self._active_course}」已生成 {len(dirs)} 个笔记夹 / {n_notes} 篇笔记 · 双击用 Obsidian 打开",
                fg=TEXT_MED)

    def _selected_tree_path(self):
        """返回 course_tree 当前选中项对应的磁盘路径(仅叶子有 values)。"""
        tree = getattr(self, "course_tree", None)
        if tree is None:
            return ""
        sel = tree.selection()
        if not sel:
            return ""
        vals = tree.item(sel[0], "values")
        return vals[0] if vals else ""

    def _on_tree_open(self, _=None):
        """双击: 若是 .md 笔记则用 Obsidian 打开; 若是笔记夹则打开系统文件夹。"""
        self._open_selected_entry()

    def _open_selected_entry(self):
        path = self._selected_tree_path()
        if not path:
            return
        if os.path.isdir(path):
            self._reveal_folder(path)
        elif os.path.isfile(path) and path.lower().endswith(".md"):
            self._obsidian_open(path)
        else:
            messagebox.showinfo("提示", "请选中一个笔记(.md)或笔记夹再打开")

    def _obsidian_open(self, md_path):
        """用 Obsidian URI 打开指定 .md; 若打不开则退回系统默认 .md 编辑器。"""
        # obsidian://open?path= 支持用绝对路径定位文件所在仓库并打开
        uri = "obsidian://open?path=" + md_path.replace(" ", "%20")
        try:
            os.startfile(uri)  # type: ignore
        except Exception:
            try:
                os.startfile(md_path)  # 退回系统默认打开(markdown 编辑器)
            except Exception as e:
                messagebox.showerror("打开失败", f"{e}\n可到系统文件里手动打开该笔记")

    def _rename_selected_entry(self):
        """重命名选中的笔记(.md)或笔记夹, 并同步更新 vault 内指向它的 [[双链]] 与自身 title。"""
        path = self._selected_tree_path()
        if not path:
            messagebox.showinfo("提示", "先在下面选中要改名的笔记/笔记夹")
            return
        base_name = os.path.basename(path)
        if os.path.isdir(path):
            if not messagebox.askyesno("改名笔记夹",
                                       f"重命名笔记夹\n{base_name}\n\n会同步更新 vault 内指向其中笔记的双链。继续?"):
                return
            new_name = self._prompt_rename(base_name)
            if not new_name or new_name == base_name:
                return
            new_path = os.path.join(os.path.dirname(path), new_name)
            return self._do_rename_dir(path, new_path)
        if path.lower().endswith(".md"):
            new_name = self._prompt_rename(base_name)
            if not new_name or new_name == base_name:
                return
            stem = os.path.splitext(base_name)[0]
            new_stem = os.path.splitext(new_name)[0]
            new_path = os.path.join(os.path.dirname(path), new_stem + ".md")
            self._do_rename_note(path, new_path, stem, new_stem)
        else:
            messagebox.showinfo("提示", "仅支持改名 .md 笔记或笔记夹")

    def _prompt_rename(self, old):
        import tkinter.simpledialog as sd
        return sd.askstring("重命名", "新名称(不含 .md):", initialvalue=old, parent=self)

    def _rewrite_wikilinks_in_dir(self, vault_dir, old_stem, new_stem, exclude_path=""):
        """扫描 vault_dir 下所有 .md, 把正文里 [[old_stem|...]] / [[old_stem#...]] 双链改为 new_stem。
        返回改写处数。exclude_path 为被改名文件自身, 跳过避免误伤其内部 heading 已同步改名。"""
        from obsidian.vault_aware import _norm as vnorm
        import re
        pat = re.compile(r"(\[\[)([^\]\|#]+)([\]\|#])")
        cnt = 0
        if not vault_dir or not os.path.isdir(vault_dir):
            return 0
        for root, _, files in os.walk(vault_dir):
            if ".obsidian" in root or ".ppt2obsidian" in root or ".git" in root:
                continue
            for fn in files:
                if not fn.lower().endswith(".md"):
                    continue
                fp = os.path.join(root, fn)
                if exclude_path and os.path.abspath(fp) == os.path.abspath(exclude_path):
                    continue
                try:
                    with open(fp, "r", encoding="utf-8") as f:
                        txt = f.read()
                except Exception:
                    continue
                def _rep(m):
                    tgt = m.group(2).strip()
                    if vnorm(tgt) == vnorm(old_stem):
                        return m.group(1) + new_stem + m.group(3)
                    return m.group(0)
                new_txt, n = pat.subn(_rep, txt)
                if n:
                    try:
                        with open(fp, "w", encoding="utf-8") as f:
                            f.write(new_txt)
                        cnt += n
                    except Exception:
                        pass
        return cnt

    def _update_title_front(self, fp, old_title, new_title):
        """把笔记正文里的 YAML title 与顶层 # 标题改掉。"""
        import re
        try:
            with open(fp, "r", encoding="utf-8") as f:
                txt = f.read()
        except Exception:
            return 0
        # frontmatter title
        t1 = re.sub(r"(?m)^title:\s*\".*?\"", f'title: "{new_title}"', txt, count=1)
        # 顶层 # 标题(第一处非代码块的行首 # xxx)
        if old_title and new_title:
            lines = t1.splitlines(keepends=True)
            in_code = False
            for i, ln in enumerate(lines):
                s = ln.strip()
                if s.startswith("```"):
                    in_code = not in_code
                    continue
                if not in_code and re.match(r"^#\s+", s) and old_title in s:
                    lines[i] = re.sub(r"(?<=#\s).*", new_title, ln)
                    break
            t1 = "".join(lines)
        if t1 != txt:
            try:
                with open(fp, "w", encoding="utf-8") as f:
                    f.write(t1)
                return 1
            except Exception:
                return 0
        return 0

    def _do_rename_note(self, path, new_path, old_stem, new_stem):
        vault_dir = self._active_vault_root()
        # 1) 先在本文件(旧路径)更新 frontmatter title 与顶层标题
        try:
            self._update_title_front(path, old_stem, new_stem)
        except Exception:
            pass
        # 2) 改文件系统名
        try:
            os.rename(path, new_path)
        except Exception as e:
            messagebox.showerror("改名失败", f"{e}")
            return
        # 3) 全 vault 扫描, 把指向旧名的 [[双链]] 改成新名(跳过刚改名的自身)
        n = 0
        if vault_dir:
            n = self._rewrite_wikilinks_in_dir(vault_dir, old_stem, new_stem, exclude_path=new_path)
        self._refresh_course_files()
        messagebox.showinfo("完成",
                            f"已改名为\n{os.path.basename(new_path)}\n"
                            + (f"并同步更新了 {n} 处指向它的双链。" if n else "(vault 内无其它指向它的双链。)"))

    def _do_rename_dir(self, path, new_path):
        # 目录名与其中 .md 文件名无关, 仅改名目录 + 刷新; vault 内双链基于文件名, 不受影响
        try:
            os.rename(path, new_path)
        except Exception as e:
            messagebox.showerror("改名失败", f"{e}")
            return
        self._refresh_course_files()
        messagebox.showinfo("完成", "已改名笔记夹。")

    def _active_vault_root(self):
        """向上找到当前课程夹所属的 Obsidian vault 根(含 .obsidian), 找不到则用课程夹本身。"""
        base = self._vault_dir or self._vault_root
        try:
            cur = os.path.abspath(base or "")
            while True:
                if os.path.isdir(os.path.join(cur, ".obsidian")):
                    return cur
                parent = os.path.dirname(cur)
                if parent == cur:
                    break
                cur = parent
        except Exception:
            pass
        return (self._vault_dir or "")

    def _choose_vault_root(self):
        v = filedialog.askdirectory(title="选择 Vault 根目录(Obsidian 仓库)")
        if not v:
            return
        self._vault_root = v
        # 迁移: 若某课程 path 落在旧根下, 一并刷新相对; 简单起见仅存新根
        self._save_courses()
        self._refresh_course_ui()

    def _on_course_select(self, _=None):
        sel = self.course_lb.curselection()
        if not sel:
            return
        name = self.course_lb.get(sel[0])
        self._select_course(name)

    def _select_course(self, name):
        """选中课程 -> 把输出目录指向该课程夹, 并同步右侧 vault_entry。"""
        path = self._course_path(name)
        if not path:
            return
        if not os.path.isdir(path):
            try:
                os.makedirs(path, exist_ok=True)
            except Exception as e:
                messagebox.showwarning("提示", f"无法创建课程目录:\n{path}\n{e}")
                return
        self._active_course = name
        self._vault_dir = path
        # 切换课程 -> 同步会话修正记忆为"这门课"的桶(语文/数学互不可见)
        self._session_corrections = self._course_corr_bucket()
        # 同步右侧输出目录输入框
        try:
            self.vault_entry.delete(0, "end")
            self.vault_entry.insert(0, path)
        except Exception:
            pass
        self._save_courses()
        self._refresh_course_ui()
        self._refresh_course_files()

    def _new_course(self):
        ent = self.new_course_var.get().strip()
        placeholder = "输入课程名, 如「药理学」「高数」"
        # 只要不是占位符, 就当作真实输入(即使 ph 标志未清除)
        if not ent or ent == placeholder:
            messagebox.showwarning("提示", "请输入课程名")
            return
        if any(c["name"] == ent for c in self._courses):
            messagebox.showwarning("提示", "已有同名课程")
            return
        # 默认路径: Vault根/课程名; 若无根则弹出选择目录
        path = ""
        if self._vault_root and os.path.isdir(self._vault_root):
            path = os.path.join(self._vault_root, ent)
        else:
            path = filedialog.askdirectory(title=f"选择「{ent}」课程的存放文件夹")
            if not path:
                return
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as e:
            messagebox.showwarning("提示", f"无法创建文件夹:\n{path}\n{e}")
            return
        self._courses.append({"name": ent, "path": path})
        self._reset_course_ph()
        self._select_course(ent)

    def _clear_course_ph(self, ent):
        if self._course_name_ph:
            ent.delete(0, "end")
            ent.config(fg=TEXT)
            self._course_name_ph = False

    def _reset_course_ph(self):
        ent = self.new_course_entry
        self.new_course_var.set("输入课程名, 如「药理学」「高数」")
        ent.config(fg=TEXT_LIGHT)
        self._course_name_ph = True

    def _remove_course(self):
        if not self._active_course:
            messagebox.showwarning("提示", "请先在列表里点选一门课程")
            return
        if not messagebox.askyesno("移除课程", f"仅从列表移除「{self._active_course}」，不会删除磁盘上的文件夹/笔记。\n继续吗?"):
            return
        self._courses = [c for c in self._courses if c["name"] != self._active_course]
        self._active_course = self._courses[0]["name"] if self._courses else ""
        if self._active_course:
            self._select_course(self._active_course)
        else:
            self._vault_dir = ""
            try:
                self.vault_entry.delete(0, "end")
                self.vault_entry.insert(0, "")
            except Exception:
                pass
        self._save_courses()
        self._refresh_course_ui()
        self._refresh_course_files()

    def _open_active_folder(self):
        path = self._course_path(self._active_course) if self._active_course else ""
        if not path or not os.path.isdir(path):
            messagebox.showwarning("提示", "请先选中一门有效课程")
            return
        self._reveal_folder(path)

    def _pick_ppt_in_course(self):
        base = self._course_path(self._active_course) if self._active_course else ""
        base = base if (base and os.path.isdir(base)) else self._vault_root or self._vault_dir or "."
        p = filedialog.askopenfilename(title="在该课程文件夹中选择课件", initialdir=base,
                                       filetypes=[("课件 (PPT/PDF)", "*.pptx *.ppt *.pdf"),
                                                  ("PowerPoint", "*.pptx *.ppt"),
                                                  ("PDF", "*.pdf"),
                                                  ("所有文件", "*.*")])
        if p:
            self._set_ppt(p)

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
        """判断 path 自身或任一父级是否为 Obsidian vault(含 .obsidian)。"""
        try:
            cur = os.path.abspath(path)
            while True:
                if os.path.isdir(os.path.join(cur, ".obsidian")):
                    return True
                parent = os.path.dirname(cur)
                if parent == cur:
                    return False
                cur = parent
        except Exception:
            return False

    # -- PPT 大拖拽区 --
    def _build_ppt_dropzone(self, parent):
        card = RoundedFrame(parent, radius=16)
        card.pack(fill="x", pady=(0, 14))
        inner = card.inner()
        inner.columnconfigure(0, weight=1)
        head = self._step_head(inner, 1, "选择课件", "支持 .pptx / .ppt / .pdf")
        head.pack(fill="x", padx=18, pady=(16, 10), anchor="w")

        # 虚线拖拽框(三态: normal/hover/active)
        dz = DashedZone(inner, height=150, on_click=self._choose_ppt)
        dz.pack(fill="x", padx=18, pady=(0, 6))
        self.drop_zone = dz
        self._setup_dnd(dz)
        # PPT 文件名提示
        self.ppt_file_label = tk.Label(inner, text="", bg=SURFACE, fg=SUCCESS, font=FONT_S, anchor="w")
        self.ppt_file_label.pack(fill="x", padx=22, pady=(2, 14))

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
        dz = self.drop_zone
        dz.set_state("active")
        dz.itemconfig(dz.icon_id, text="✅", fill=SUCCESS)
        dz.itemconfig(dz.title_id, text=os.path.basename(path), fill=SUCCESS)
        dz.itemconfig(dz.sub_id, text=os.path.dirname(path), fill=TEXT_LIGHT)
        self.ppt_file_label.config(text=f"已选择: {os.path.basename(path)}")

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

    # -- 大模型 API 管理(内嵌) --
    def _build_model_section(self, parent):
        card = RoundedFrame(parent, radius=16)
        card.pack(fill="x", pady=(0, 4))
        inner = card.inner()
        inner.columnconfigure(0, weight=1)
        head = self._step_head(inner, 2, "大模型 API", "绑定在线模型获得更准笔记，也可离线生成")
        head.grid(row=0, column=0, sticky="w", padx=18, pady=(16, 8))

        # 当前模型下拉 + 使用方式
        mode_row = tk.Frame(inner, bg=SURFACE)
        mode_row.grid(row=1, column=0, sticky="ew", padx=18)
        self.model_var = tk.StringVar(value="offline")
        tk.Radiobutton(mode_row, text="本地规则(离线)", variable=self.model_var, value="offline",
                       bg=SURFACE, activebackground=SURFACE, selectcolor="white",
                       highlightthickness=0, font=FONT_S, fg=TEXT_MED, cursor="hand2").pack(side="left")
        tk.Radiobutton(mode_row, text="使用模型", variable=self.model_var, value="online",
                       bg=SURFACE, activebackground=SURFACE, selectcolor="white",
                       highlightthickness=0, font=FONT_S, fg=TEXT_MED, cursor="hand2").pack(side="left", padx=(12, 6))
        self.model_dd = ttk.Combobox(mode_row, state="readonly", font=FONT_S)
        self.model_dd.pack(side="left", fill="x", expand=True)
        self.model_dd.bind("<<ComboboxSelected>>", self._on_model_select)

        # 测试连接 / 管理按钮
        btn_row = tk.Frame(inner, bg=SURFACE)
        btn_row.grid(row=2, column=0, sticky="ew", padx=18, pady=(10, 0))
        mkbtn(btn_row, "⚙ 添加/管理模型", self._open_model_manage,
              "ghost", FONT_S, padx=10, pady=4).pack(side="left")
        mkbtn(btn_row, "⚡ 测试连接", self._test_model,
              "ghost", FONT_S, padx=10, pady=4).pack(side="left", padx=(8, 0))

        # 提示文案
        self.model_hint = tk.Label(inner, text="未绑定模型 → 使用本地规则模式(离线)生成基础笔记",
                                   bg=SURFACE, fg=TEXT_MED, font=FONT_XS, justify="left",
                                   anchor="w", wraplength=360)
        self.model_hint.grid(row=3, column=0, sticky="ew", padx=20, pady=(10, 16))

    # ---------------- 右栏 ----------------
    def _build_right(self, parent):
        # 使用 place 让右侧严格占剩余宽度
        right = tk.Frame(parent, bg=BG)
        right.place(relx=0.46, rely=0, relwidth=0.54, relheight=1)
        # 右侧内容较长, 用可滚动画布承载, 任意分辨率都不被裁切
        canvas = tk.Canvas(right, bg=BG, highlightthickness=0)
        vsb = ttk.Scrollbar(right, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vsb.set)
        canvas.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        body_inner = tk.Frame(canvas, bg=BG)
        win_id = canvas.create_window((0, 0), window=body_inner, anchor="nw")
        def _on_canvas(e):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(win_id, width=e.width)
        body_inner.bind("<Configure>", _on_canvas)
        canvas.bind("<Configure>", _on_canvas)
        # 鼠标滚轮
        def _wheel_yscroll(e, canvas=canvas):
            try:
                canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
            except Exception:
                pass
        canvas.bind("<MouseWheel>", _wheel_yscroll)
        body_inner.bind("<MouseWheel>", _wheel_yscroll)
        self._right_canvas = canvas
        for _w in (body_inner, canvas, vsb):
            pass  # 各子卡自带滚动无需重复绑定

        # 专业 + 输出目录
        self._build_setup_section(body_inner)
        # 运行按钮 + 进度
        self._build_run_section(body_inner)
        # 生成结果(地址 + 日志)
        self._build_result_section(body_inner)
        # 对话修改(AI 强化笔记 & 知识库)
        self._build_chat_section(body_inner)

    def _build_setup_section(self, parent):
        card = RoundedFrame(parent, radius=16)
        card.pack(fill="x", pady=(0, 14))
        inner = card.inner()
        inner.columnconfigure(0, weight=1)
        head = self._step_head(inner, 3, "专业与输出目录", "专业知识框架让笔记更贴合学科")
        head.grid(row=0, column=0, sticky="w", padx=18, pady=(16, 8))

        # 专业按钮(胶囊切换)
        self.prof_buttons = {}
        prof_row = tk.Frame(inner, bg=SURFACE)
        prof_row.grid(row=1, column=0, sticky="ew", padx=18)
        if not self.profs:
            tk.Label(prof_row, text="未加载专业", fg=DANGER, bg=SURFACE).pack(side="left")
        else:
            for i, p in enumerate(self.profs):
                b = tk.Button(prof_row, text=p.name, font=FONT_S, relief="flat", cursor="hand2",
                              command=lambda pid=p.id: self._pick_prof(pid), bd=0,
                              padx=12, pady=5)
                b.pack(side="left", padx=(0 if i == 0 else 6))
                self.prof_buttons[p.id] = b
        self.prof_detail = tk.Label(inner, text="", bg=SURFACE, fg=TEXT_LIGHT, font=FONT_XS, anchor="w")
        self.prof_detail.grid(row=2, column=0, sticky="w", padx=18, pady=(6, 4))

        # Vault 输出目录
        vault_row = tk.Frame(inner, bg=SURFACE)
        vault_row.grid(row=3, column=0, sticky="ew", padx=18, pady=(4, 6))
        tk.Label(vault_row, text="输出目录", bg=SURFACE, fg=TEXT_MED, font=FONT_S).pack(side="left")
        self.vault_entry = tk.Entry(vault_row, font=FONT_S, relief="solid", bd=1,
                                    highlightbackground=BORDER, highlightcolor=PRIMARY, bg="white")
        self.vault_entry.pack(side="left", fill="x", expand=True, padx=(6, 6), ipady=3)
        self.vault_entry.bind("<Return>", lambda e: self._set_vault_from_entry())
        mkbtn(vault_row, "选择…", self._choose_vault, "ghost",
              FONT_S, padx=10, pady=3).pack(side="right")
        tk.Label(inner, text="提示: 选择已建好的 Obsidian Vault 文件夹，若无则新建空文件夹即可",
                 bg=SURFACE, fg=TEXT_LIGHT, font=FONT_XS, anchor="w").grid(
            row=4, column=0, sticky="w", padx=18, pady=(0, 14))

    def _build_run_section(self, parent):
        card = RoundedFrame(parent, radius=16)
        card.pack(fill="x", pady=(0, 14))
        inner = card.inner()
        inner.columnconfigure(0, weight=1)
        self.run_btn = tk.Button(inner, text="🚀  生成 Obsidian 笔记", command=self._run,
                                 bg=PRIMARY, fg="white", activebackground=PRIMARY_HOVER,
                                 activeforeground="white", font=("Microsoft YaHei UI", 13, "bold"),
                                 relief="flat", cursor="hand2", bd=0, pady=12)
        self.run_btn.grid(row=0, column=0, sticky="ew", padx=18, pady=(16, 8))
        self.run_btn.bind("<Enter>", lambda e: self.run_btn.config(
            bg=PRIMARY_HOVER) if str(self.run_btn["state"]) != "disabled" else None)
        self.run_btn.bind("<Leave>", lambda e: self.run_btn.config(bg=PRIMARY))
        # 输出形态: 讲义式(默认) / 概念卡片
        mode_row = tk.Frame(inner, bg=SURFACE)
        mode_row.grid(row=1, column=0, sticky="ew", padx=18, pady=(0, 8))
        tk.Label(mode_row, text="笔记形态", bg=SURFACE, fg=TEXT_MED, font=FONT_S).pack(side="left")
        self.mode_var = tk.StringVar(value="lecture")
        self.mode_dd = ttk.Combobox(
            mode_row, textvariable=self.mode_var, state="readonly", font=FONT_S,
            values=["lecture", "cards"])
        self.mode_dd.set("lecture")
        self.mode_dd.pack(side="left", fill="x", expand=True, padx=(6, 6))
        # 显示名(combobox 显示友好文案, 实际值用 lecture/cards)
        self.mode_dd.configure(values=["讲义式 · 按小节一文件(推荐)", "概念卡片 · MOC+原子卡"])
        self.mode_var.set("讲义式 · 按小节一文件(推荐)")
        self._mode_values = {"讲义式 · 按小节一文件(推荐)": "lecture",
                             "概念卡片 · MOC+原子卡": "cards"}
        self._mode_to_display = {v: k for k, v in self._mode_values.items()}
        self.mode_dd.bind("<<ComboboxSelected>>", self._on_mode_change)
        # 进度条
        self.progress = ttk.Progressbar(inner, mode="determinate", maximum=100,
                                        style="Accent.Horizontal.TProgressbar")
        self.progress.grid(row=2, column=0, sticky="ew", padx=18)
        self.status_label = tk.Label(inner, text="就绪", font=FONT_S, bg=SURFACE, fg=TEXT_MED, anchor="w")
        self.status_label.grid(row=3, column=0, sticky="w", padx=20, pady=(6, 14))

    def _build_result_section(self, parent):
        card = RoundedFrame(parent, radius=16)
        card.pack(fill="x", pady=(0, 12))
        inner = card.inner()
        inner.columnconfigure(0, weight=1)
        tk.Label(inner, text="📁  生成结果", font=FONT_H2, bg=SURFACE, fg=TEXT).grid(
            row=0, column=0, sticky="w", padx=18, pady=(14, 8))

        # 地址 + 打开按钮
        addr_row = tk.Frame(inner, bg=SURFACE)
        addr_row.grid(row=1, column=0, sticky="ew", padx=18)
        tk.Label(addr_row, text="输出地址", bg=SURFACE, fg=TEXT_MED, font=FONT_S).pack(side="left")
        self.result_path = tk.Label(addr_row, text="尚未生成", bg=SURFACE, fg=TEXT_LIGHT, font=FONT_S,
                                    anchor="w")
        self.result_path.pack(side="left", fill="x", expand=True, padx=(6, 6))
        self.open_btn = mkbtn(addr_row, "打开文件夹", self._open_folder,
                              "primary", FONT_S, padx=12, pady=4)
        self.open_btn.config(state="disabled", disabledforeground="#c7c9d8")
        self.open_btn.pack(side="right")
        # 日志区(深色控制台)
        self.log_text = tk.Text(inner, bg="#151b2e", fg="#dbe2f4", font=("Consolas", 9),
                                relief="flat", bd=0, state="disabled", wrap="word",
                                padx=12, pady=8, height=6, highlightthickness=1,
                                highlightbackground=BORDER)
        self.log_text.grid(row=2, column=0, sticky="ew", padx=18, pady=(10, 16))
        self.log_text.tag_config("ok", foreground="#4ade80")
        self.log_text.tag_config("err", foreground="#fb7185")
        self.log_text.tag_config("info", foreground="#93a5fd")
        self.log_text.tag_config("dim", foreground="#8b93a7")

    # ---------------- 专业选择 ----------------
    def _pick_prof(self, pid):
        self._prof_id = pid
        p = self.kb.get(pid)
        if not p:
            return
        for k, b in self.prof_buttons.items():
            sel = (k == pid)
            b.config(bg=PRIMARY if sel else "#eef0f7",
                     fg="white" if sel else TEXT_MED,
                     activebackground=PRIMARY_HOVER if sel else "#e2e6f0",
                     activeforeground="white" if sel else TEXT_MED)
        self.prof_detail.config(text=f"已选: {p.name}  |  核心概念 {p.concept_count()} 个 · 符号 {len(p.symbol_mapping)} 个 · 易错点 {len(p.common_misconceptions)} 个")

    # ---------------- Vault ----------------
    def _choose_vault(self):
        v = filedialog.askdirectory(title="选择 Obsidian Vault 目录")
        if v:
            self._vault_dir = v
            self.vault_entry.delete(0, "end")
            self.vault_entry.insert(0, v)

    def _set_vault_from_entry(self):
        v = self.vault_entry.get().strip()
        if v:
            self._vault_dir = v

    # ---------------- 模型 ----------------
    def refresh_main(self):
        uname = self.account.username
        self.user_label.config(text=uname)
        # 头像圆: 主色底 + 用户名首字
        try:
            self._avatar.delete("all")
            self._avatar.create_oval(1, 1, 25, 25, fill=PRIMARY, outline="")
            self._avatar.create_text(13, 14, text=(uname[:1] or "U").upper(),
                                     fill="white", font=("Microsoft YaHei UI", 10, "bold"))
        except Exception:
            pass
        models = self.account.get_models()
        names = [f"{m.get('name','?')} · {m.get('model','')}" for m in models]
        if names:
            self.model_dd.config(values=names)
            # 记住并恢复用户上次选中的模型, 而不是每次都跳到第 0 个(默认 DeepSeek)。
            # 用户之前反馈"创建的模型总变成 deepseek", 正是因为这里无条件 current(0)。
            saved = self.account.get_pref("model_index", 0)
            cur = self.model_dd.current()
            if not (0 <= cur < len(names)):
                # 优先: 恢复上次记住的; 其次: 找到非空 key 且名含 dots 的; 最后第 0 个
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

        # ---- 每次进入/刷新主界面: 恢复专业、课程列表与上次课程目录(直接联动右侧输出) ----
        if self.profs and not self._prof_id:
            self._pick_prof(self.profs[0].id)
        try:
            self._load_courses()
            self._refresh_course_ui()
            if self._active_course and self._course_path(self._active_course):
                # 不弹目录, 直接设输出(目录不存在则建)
                p = self._course_path(self._active_course)
                try:
                    os.makedirs(p, exist_ok=True)
                except Exception:
                    pass
                if os.path.isdir(p):
                    self._vault_dir = p
                    self.vault_entry.delete(0, "end")
                    self.vault_entry.insert(0, p)
            self._refresh_course_files()   # 打开/选中课程后直接看到夹里已生成内容
            # 恢复上次的笔记形态选择(讲义式/概念卡片)
            try:
                _nm = self.account.get_pref("notes_mode", "lecture") or "lecture"
                if hasattr(self, "mode_var") and hasattr(self, "_mode_to_display"):
                    self.mode_var.set(self._mode_display_of(_nm))
            except Exception:
                pass
        except Exception:
            pass

    @staticmethod
    def _default_model_index(models):
        """列表里挑一个"看起来真能连"的模型作为默认选中, 避免停在空 key 的占位项。"""
        if not models:
            return 0
        # 优先非空 key 的(真实配好 key 的模型), 且名字不叫 DeepSeek 占位
        for i, m in enumerate(models):
            if (m.get("api_key") or "").strip() and not (m.get("name") or "").strip().lower().startswith("deepseek"):
                return i
        # 其次任意非空 key
        for i, m in enumerate(models):
            if (m.get("api_key") or "").strip():
                return i
        return 0

    def _show_model_hint(self, idx):
        if idx < 0:
            self.model_hint.config(text="未绑定模型 → 使用本地规则模式(离线)生成基础笔记。\n点击「添加/管理模型」绑定 DeepSeek / OpenAI / Kimi / 智谱 / Ollama 等可获更准结构化笔记。")
            return
        models = self.account.get_models()
        if 0 <= idx < len(models):
            m = models[idx]
            self.model_hint.config(text=f"将使用: {m.get('name')} · {m.get('model')}\nBase: {m.get('base_url')}")

    def _on_model_select(self, _=None):
        idx = self.model_dd.current()
        if idx >= 0:
            self.model_var.set("online")
            self.account.set_pref("model_index", idx)  # 记住本次选择
            self._show_model_hint(idx)

    def _open_model_manage(self):
        # 用 wait_window 等用户在弹窗里操作完再刷新:
        # 1) 新增的模型能立即出现在下拉;
        # 2) 期间不会再被 refresh_main 重置成 DeepSeek。
        ModelDialog(self, self.account)
        self.wait_window(self._mdlg)
        # 若新增/删除了模型, 重设下拉 values 并尽量保持原选择
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
            messagebox.showinfo("提示", "请先点击「添加/管理模型」添加一个模型")
            return
        m = self.account.get_models()[idx]
        if not (m.get("api_key") or "").strip():
            messagebox.showwarning(
                "该模型没填 API Key",
                f"当前选中的「{m.get('name')}」没有填写 API Key，无法连接。\n\n"
                f"请点「添加/管理模型」→ 选中它(会回填到表单) → 填上 API Key → 点「更新选中」。\n"
                f"或直接用快捷厂商选一个、只填 Key 后「添加」。", parent=self)
            return
        try:
            llm = build_llm(m)
            self.log("正在测试连接…", "info")
            self.update_idletasks()
            out = llm.chat([{"role": "user", "content": "请只回复两个字: 正常"}], max_tokens=64)
            if not out.strip():
                out = "(空回复)"
            self.log(f"✅ 连接成功: {out}", "ok")
        except Exception as e:
            self.log(f"❌ 连接失败: {e}", "err")
            messagebox.showerror("测试失败", f"{e}\n\n提示: 请确认 Base URL 填的是官方 API 地址(不要填文档网页), 且已填对 API Key。")

    # ---------------- 运行 ----------------
    def _run(self):
        if not self._ppt_path:
            messagebox.showwarning("提示", "请先在左侧拖入一个 PPT / PDF 文件")
            return
        if not self._vault_dir:
            messagebox.showwarning("提示", "请先在右侧设置输出目录(Vault)")
            return
        if not os.path.isdir(self._vault_dir):
            try:
                os.makedirs(self._vault_dir, exist_ok=True)
            except Exception:
                messagebox.showwarning("提示", "输出目录无效, 请重新选择")
                return
        # 只要输出目录位于某个含 .obsidian 的仓库内(含自身或向上追溯)即视为有效 vault,
        # 这样"整 Vault 根 + 各课程子夹"不会每次都弹"还不是 Obsidian"提示。
        if not self._inside_real_vault(self._vault_dir):
            if not messagebox.askyesno("Vault 确认",
                                       f"所选目录及其父级都不是 Obsidian vault(找不到 .obsidian 文件夹)。\n\n在 Obsidian 中「打开文件夹作为仓库」选择它即可正常使用。\n\n仍继续生成笔记吗?"):
                return
        pid = self._prof_id or self.profs[0].id
        mode = self._mode_values.get(self.mode_var.get(), "lecture")
        try:
            if self.account is not None:
                self.account.set_pref("notes_mode", mode)
        except Exception:
            pass
        self.run_btn.config(state="disabled")
        self.status_label.config(text="正在生成…", fg=PRIMARY)
        self.open_btn.config(state="disabled")
        self.progress["value"] = 0
        t = threading.Thread(target=self._run_worker,
                             args=(self._ppt_path, pid, self._vault_dir, mode), daemon=True)
        t.start()

    def _mode_display_of(self, mode):
        if mode in self._mode_to_display:
            return self._mode_to_display[mode]
        return "讲义式 · 按小节一文件(推荐)"

    def _on_mode_change(self, _evt=None):
        if self.account is not None:
            try:
                self.account.set_pref("notes_mode",
                                      self._mode_values.get(self.mode_var.get(), "lecture"))
            except Exception:
                pass

    def _run_worker(self, ppt, pid, vault, mode="lecture"):
        # 阶段进度
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
                        self.log(f"❌ 当前选中的「{m.get('name')}」没有填写 API Key, 已中止", "err"),
                        messagebox.showwarning("该模型没填 API Key",
                                               f"当前选中的「{m.get('name')}」没有填写 API Key。\n\n"
                                               f"请点「添加/管理模型」→ 选中它 → 填上 API Key → 「更新选中」。")) )
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
            self.after(0, lambda: self.log(f"❌ {e}", "err"))

    def _log_progress(self, msg, stages, stage_i):
        # 根据消息关键词推进进度
        self.log(msg, "info")
        map = {"解析": 25, "调用模型": 55, "生成": 85, "完成": 100}
        for kw, v in map.items():
            if kw in msg:
                self.progress["value"] = v
                break

    def _run_done(self, result):
        self.run_btn.config(state="normal")
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
                self.status_label.config(
                    text=f"✅ 完成, 讲义 {n_md} 个文件" + (f" + {n_extra} 张截图" if n_extra else ""),
                    fg=SUCCESS)
            else:
                self.status_label.config(text=f"✅ 完成, 生成 {n_md} 个笔记", fg=SUCCESS)
            self.log("✅ 转换成功!", "ok")
            for n in result.notes:
                self.log(f"   · {n.rel_path}", "dim")
            # 显示输出地址 + 打开按钮
            self.result_path.config(text=result.vault_dir, fg=TEXT)
            self.open_btn.config(state="normal")
            # 刷新对话修改目标
            try:
                self._refresh_refine_targets()
            except Exception:
                pass
            self.account.set_pref("use_online", self.model_var.get() == "online")
            self._refresh_course_files()   # 让新生成的笔记夹立即出现在 STEP0 内容浏览器里
            _v = getattr(result, "validation", None)
            if _v is not None and getattr(_v, "overall_score", 0) > 0:
                try:
                    _brief = _v.to_brief()
                    self.status_label.config(
                        text=f"✅ 完成, 生成 {len(result.notes)} 个笔记 · {_brief}", fg=SUCCESS)
                except Exception:
                    _brief = ""
                messagebox.showinfo(
                    "完成",
                    f"已生成 {len(result.notes)} 个笔记到:\n{result.vault_dir}\n\n"
                    f"{_brief}\n详细质量报告已写入该课的 99_知识质量报告.md")
            else:
                messagebox.showinfo("完成", f"已生成 {len(result.notes)} 个笔记到:\n{result.vault_dir}")
        else:
            self.status_label.config(text="❌ 生成失败", fg=DANGER)
            self.log(f"❌ 失败: {result.error}", "err")
            messagebox.showerror("生成失败", result.error)

    def _open_folder(self):
        vault = self._last_vault or self._vault_dir
        if not vault or not os.path.isdir(vault):
            messagebox.showwarning("提示", "请先成功生成一次笔记")
            return
        try:
            if sys.platform.startswith("win"):
                os.startfile(vault)  # type: ignore
            elif sys.platform == "darwin":
                subprocess.Popen(["open", vault])
            else:
                subprocess.Popen(["xdg-open", vault])
        except Exception as e:
            messagebox.showerror("打开失败", str(e))

    # =================================================================
    #  右侧底部: 对话修改(AI 强化笔记 & 知识库)
    # =================================================================
    def _build_chat_section(self, parent):
        card = RoundedFrame(parent, radius=16)
        card.pack(fill="x", pady=(0, 4))
        inner = card.inner()
        inner.columnconfigure(0, weight=1)
        head = self._step_head(inner, 4, "对话修改（越用越准）",
                               "对笔记提修改意见，AI 就地修订并沉淀回知识库")
        head.grid(row=0, column=0, sticky="w", padx=18, pady=(14, 6))

        # 目标选择
        trow = tk.Frame(inner, bg=SURFACE)
        trow.grid(row=2, column=0, sticky="ew", padx=18, pady=(4, 6))
        tk.Label(trow, text="修改对象", bg=SURFACE, fg=TEXT_MED, font=FONT_S).pack(side="left")
        self.refine_concept_var = tk.StringVar(value="整体 / MOC")
        self.refine_target_dd = ttk.Combobox(trow, textvariable=self.refine_concept_var,
                                             state="readonly", font=FONT_S)
        self.refine_target_dd.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self.refine_target_dd.bind("<<ComboboxSelected>>", lambda e: self._sync_refine_target())

        # 对话历史(只读)
        self.chat_text = tk.Text(inner, bg=SURFACE_2, fg=TEXT, font=FONT_S,
                                 relief="flat", bd=0, state="disabled", wrap="word",
                                 height=5, padx=10, pady=8, highlightthickness=1,
                                 highlightbackground=BORDER, highlightcolor=PRIMARY)
        self.chat_text.grid(row=3, column=0, sticky="ew", padx=18, pady=(2, 8))
        self.chat_text.tag_config("me", foreground=PRIMARY_HOVER)
        self.chat_text.tag_config("ai", foreground="#0d9668")
        self.chat_text.tag_config("sys", foreground="#c2410c")

        # 输入 + 按钮
        irow = tk.Frame(inner, bg=SURFACE)
        irow.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 10))
        self.refine_entry = tk.Entry(irow, font=FONT_S, relief="solid", bd=1,
                                     highlightbackground=BORDER, highlightcolor=PRIMARY, bg="white")
        self.refine_entry.pack(side="left", fill="x", expand=True, ipady=4)
        self.refine_entry.bind("<Return>", lambda e: self._send_refine())
        mkbtn(irow, "发送修订", self._send_refine, "primary",
              FONT_S, padx=12, pady=4).pack(side="left", padx=(6, 0))
        mkbtn(irow, "应用到笔记 + 沉淀", self._apply_refine, "success",
              FONT_S, padx=12, pady=4).pack(side="left", padx=(6, 0))
        self.refine_hint = tk.Label(inner, text="提示: 先生成一次笔记, 即可针对某概念让 AI 修改并让知识库记住",
                                    bg=SURFACE, fg=TEXT_LIGHT, font=FONT_XS, anchor="w")
        self.refine_hint.grid(row=5, column=0, sticky="w", padx=20, pady=(0, 14))
        self.refine_send_btn = None
        # 记录最近修订内容(供"应用")
        self._pending_revised = ""

    def _sync_refine_target(self):
        self._refine_target = self.refine_concept_var.get()

    def _refresh_refine_targets(self):
        """根据最近一次生成结果填充对象下拉。"""
        if self._last_bundle is None:
            # 讲义式: 无概念卡/MOC, 对话修订不适用
            names = ["（讲义式输出，对话修订仅支持概念卡模式）"]
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
        """返回目标笔记当前的 markdown(优先用最近生成的在内存版本; 兜底读盘)。"""
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
        # 取模型
        m = self._selected_model()
        if m is None:
            messagebox.showwarning("提示", "请先在左侧绑定并选择一个在线模型后再对话修改")
            return
        md = self._current_note_md(target)
        if not md:
            messagebox.showwarning("提示", f"未找到「{target}」的笔记正文。\n(讲义式输出暂不支持对话修订，可直接编辑生成的 .md 文件)")
            return
        try:
            llm = LLMClient(m.get("base_url", ""), m.get("api_key", ""), m.get("model", ""))
        except Exception as e:
            messagebox.showerror("模型错误", str(e))
            return
        # 组装专业上下文 + 自学沉淀
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
        # 本会话内"当前课程"已确认的修正记忆, 一并回喂, 让模型"记得"对这门课之前的修订
        course_mem = self._course_corr_bucket()
        if course_mem:
            mem = "\n【本会话你已确认的学科修正(仅针对当前课程, 后续修改需保持一致)】\n" + "\n".join(
                f"- {s}" for s in course_mem[-12:])
            extra_ctx = extra_ctx + ("\n" if extra_ctx else "") + mem
        pages_hint = ""
        # 目标概念 source_pages
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
                self.after(0, lambda: (self.chat("ai", f"修订失败: {res.error}"),
                                       self.log(f"❌ {res.error}", "err")))
                return
            self._pending_revised = res.revised_md
            # 展示修订稿开头
            self.after(0, lambda: self.chat("ai",
                "已生成修订稿（可点「应用到笔记 + 沉淀」写回 vault 并记住这次修正）：\n"
                + res.revised_md[:400] + ("…" if len(res.revised_md) > 400 else "")))
            self.log("✅ 修订稿已生成", "ok")
        except Exception as e:
            self.after(0, lambda: self.chat("ai", f"出错: {e}"))

    def _apply_refine(self):
        revised = self._pending_revised
        target = self._refine_target or "整体 / MOC"
        if not revised:
            messagebox.showinfo("提示", "还没有可应用的修订稿, 请先「发送修订」")
            return
        if not self._vault_dir:
            messagebox.showwarning("提示", "未设置输出目录")
            return
        # 定位要覆写的文件
        path = self._resolve_target_file(target)
        if not path:
            messagebox.showwarning("提示", f"未定位到「{target}」对应的笔记文件, 无法应用")
            return
        if not messagebox.askyesno("确认应用",
                                   f"将用修订稿覆写:\n{path}\n\n并把本次修正沉淀到该专业自学知识库, 继续?"):
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(revised)
        except Exception as e:
            messagebox.showerror("写回失败", str(e))
            return
        # 沉淀知识 delta
        self._persist_correction(target, revised)
        self._pending_revised = ""
        messagebox.showinfo("完成", "已应用修订并沉淀到知识库(下次转换同专业会更准)")

    def _resolve_target_file(self, target) -> str:
        """在 vault 里定位目标笔记文件的绝对路径。"""
        if not self._vault_dir:
            return ""
        for n in self._last_notes:
            is_moc = target.startswith("整体") and n.kind == "moc"
            is_concept = (n.kind == "concept" and (n.title == target or target in n.title))
            if is_moc or is_concept:
                # n.abs_path 已在落盘时未回填; 用 vault_dir + rel_path 构造
                p = n.abs_path or os.path.join(self._vault_dir, n.rel_path.replace("/", os.sep))
                if os.path.isfile(p):
                    return p
        # 概念可能因 merge 未生成新文件: 用 vault 索引找
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
        """返回当前课程在会话里的修正记忆列表(按课程目录隔离, 切课即换桶)。"""
        key = self._vault_dir or self._active_course or "default"
        return self._session_corr_by_course.setdefault(key, [])

    def _persist_correction(self, target, revised):
        """对本次修订做结构化提炼并沉淀进该专业自学库。"""
        try:
            if not self._chat_llm:
                return
            # 找原始 md
            orig = self._read_original_for(target)
            delta = refine_mod.extract_delta(llm=self._chat_llm, target=target,
                                             original_md=orig, revised_md=revised,
                                             professional_name=self._chat_prof_name,
                                             progress=lambda m: self.log(m, "info"))
            if not delta:
                self.log("未提炼出可沉淀的修正项(可能仅为措辞调整)", "dim")
                return
            store = CalibrationStore(self._prof_id, scope_dir=self._vault_dir or None)
            n = refine_mod.apply_user_correction(store, delta)
            # 记录进"该课程"会话记忆, 让后续对这门课的修改与本次保持一致
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
                self.log(f"🧠 已沉淀 {n} 条用户认可的修正到「{self._chat_prof_name}」知识库", "ok")
                self.chat("sys", f"✅ 已记住 {n} 条学科修正(下次转换/修改会更准)")
        except Exception as e:
            self.log(f"沉淀失败: {e}", "err")

    def _read_original_for(self, target):
        return self._current_note_md(target)

    def chat(self, who, msg):
        self.chat_text.config(state="normal")
        tag = "me" if who == "me" else ("ai" if who == "ai" else "sys")
        prefix = "你: " if who == "me" else ("AI: " if who == "ai" else "")
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


# =================================================================
#  模型管理弹窗
# =================================================================
class ModelDialog(tk.Toplevel):
    def __init__(self, master, account):
        super().__init__(master)
        self.account = account
        master._mdlg = self  # 让主窗口能 wait_window 等本弹窗
        self.title("管理大模型")
        self.geometry("520x460")
        self.configure(bg=BG)
        self.transient(master)
        self.grab_set()
        tk.Label(self, text="绑定你的大模型 API (OpenAI 兼容)", font=FONT_H2, bg=BG).pack(anchor="w", padx=16, pady=(14, 4))
        listf = tk.Frame(self, bg=BG)
        listf.pack(fill="both", expand=True, padx=16)
        self.tree = ttk.Treeview(listf, columns=("name", "model", "url"), show="headings", height=5)
        for c in ("name", "model", "url"):
            self.tree.heading(c, text={"name": "名称", "model": "模型", "url": "Base URL"}[c])
        self.tree.column("name", width=90)
        self.tree.column("model", width=130)
        self.tree.column("url", width=200)
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(listf, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.refresh()

        form = tk.Frame(self, bg=BG)
        form.pack(fill="x", padx=16, pady=8)
        self.name_v = tk.StringVar(); self.url_v = tk.StringVar()
        self.key_v = tk.StringVar(); self.model_v = tk.StringVar(); self.provider_v = tk.StringVar()
        self.auth_h = ""; self.auth_s = ""; self.disable_thinking = False
        self._fill(name="DeepSeek", url=KNOWN_PROVIDERS[0]["base_url"], model=KNOWN_PROVIDERS[0]["model"])
        r0 = tk.Frame(form, bg=BG); r0.pack(fill="x", pady=2)
        tk.Label(r0, text="快捷厂商", bg=BG, font=FONT_S).pack(side="left")
        prov = ttk.Combobox(r0, textvariable=self.provider_v, values=[p["label"] for p in KNOWN_PROVIDERS],
                            state="readonly", width=16)
        prov.pack(side="left", padx=8)
        prov.bind("<<ComboboxSelected>>", self._on_provider)
        self._row(form, "名称", self.name_v)
        self._row(form, "Base URL", self.url_v)
        self._row(form, "API Key", self.key_v, show="●")
        self._row(form, "模型名", self.model_v)
        act = tk.Frame(self, bg=BG)
        act.pack(fill="x", padx=16, pady=10)
        mkbtn(act, "＋ 添加", self._add, "primary", FONT_S, padx=14, pady=4).pack(side="left", padx=3)
        mkbtn(act, "更新选中", self._update, "ghost", FONT_S, padx=12, pady=4).pack(side="left", padx=3)
        mkbtn(act, "删除选中", self._delete, "danger", FONT_S, padx=12, pady=4).pack(side="left", padx=3)
        self._note = tk.Label(self, text="", bg=BG, fg=SUCCESS, font=FONT_S)
        self._note.pack(anchor="w", padx=16)

    def _row(self, parent, label, var, show=None):
        r = tk.Frame(parent, bg=BG); r.pack(fill="x", pady=2)
        tk.Label(r, text=label, bg=BG, font=FONT_S, width=9, anchor="w").pack(side="left")
        tk.Entry(r, textvariable=var, font=FONT_S, relief="solid", bd=1, show=show).pack(side="left", fill="x", expand=True)

    def _on_provider(self, _=None):
        self.auth_h = ""
        self.auth_s = ""
        self.disable_thinking = False
        for p in KNOWN_PROVIDERS:
            if p["label"] == self.provider_v.get():
                self.name_v.set(p["label"]); self.url_v.set(p["base_url"]); self.model_v.set(p["model"])
                # 携带厂商自定义认证头(如 Dots 的 api-key)与思考开关
                self.auth_h = p.get("auth_header", "") or ""
                self.auth_s = p.get("auth_scheme", "") or ""
                self.disable_thinking = bool(p.get("disable_thinking"))
                if p.get("auth_header") or p.get("disable_thinking"):
                    tips = []
                    if p.get("auth_header"):
                        tips.append(f"自定义请求头 {p['auth_header']} 认证(非 Bearer)")
                    if p.get("disable_thinking"):
                        tips.append("深度思考已自动关闭(更快更省)")
                    self._note.config(text="⚠ " + "；".join(tips) + "。", fg="#b45309")
                else:
                    self._note.config(text="", fg=SUCCESS)
                break

    @staticmethod
    def _looks_like_doc_page(url: str) -> bool:
        """判断用户是否把"文档/官网网页地址"误填成了 Base URL。"""
        low = (url or "").lower()
        bad = ("/platform/docs", "/platform", "/docs", "/documentation",
               "/doc/", "/guide", "/introduction", "/overview", "/readme",
               "/getting-started", "docs.", "/manual", "/help", "/wiki")
        return any(b in low for b in bad)

    def _fill(self, name="", url="", model="", key=""):
        self.name_v.set(name); self.url_v.set(url); self.model_v.set(model); self.key_v.set(key)

    def refresh(self):
        self.tree.delete(*self.tree.get_children())
        for i, m in enumerate(self.account.get_models()):
            self.tree.insert("", "end", iid=str(i), values=(m.get("name"), m.get("model"), m.get("base_url")))
        # 点选已有模型行 → 把该行回填到表单, 便于直接"更新"(避免被表单残留的默认 DeepSeek 覆盖)
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
        # 回填表单(名称/URL/模型名/key 明文回填到输入框; key 用 ● 显示, 仍可修改)
        self._fill(name=m.get("name", ""), url=m.get("base_url", ""),
                   model=m.get("model", ""), key=m.get("api_key", ""))
        self.auth_h = m.get("auth_header", "") or ""
        self.auth_s = m.get("auth_scheme", "") or ""
        self.disable_thinking = bool(m.get("disable_thinking"))

    def _warn_doc_url(self, url: str) -> bool:
        """Base URL 看着像文档网页时弹警告, 返回 True 表示应中止。"""
        if self._looks_like_doc_page(url):
            messagebox.showwarning(
                "Base URL 疑似填错",
                "你填的 Base URL 看起来像『文档/官网网页』地址(例如 /docs 结尾),\n"
                "而不是 API 接口地址。\n\n"
                "请改成厂商文档里标注的真实 API Base URL,\n"
                "一般是形如  https://api.xxx.com/v1  这样的接口地址。\n"
                "例如 dots.ai 用: https://note3-prev-api.askdiandian.com/v1\n"
                "DeepSeek 用: https://api.deepseek.com/v1",
                parent=self)
            return True
        return False

    def _add(self):
        name = self.name_v.get().strip(); url = self.url_v.get().strip()
        key = self.key_v.get().strip(); model = self.model_v.get().strip()
        if not name or not url or not model:
            messagebox.showwarning("提示", "名称 / Base URL / 模型名 不能为空", parent=self); return
        if self._warn_doc_url(url):
            return
        self.account.add_model(name, url, key, model,
                               auth_header=self.auth_h, auth_scheme=self.auth_s,
                               disable_thinking=self.disable_thinking)
        # 刚添加的模型设为"当前选中", 让主界面 wait_window 后自动选中它(而非跳回 DeepSeek)
        self.account.set_pref("model_index", len(self.account.get_models()) - 1)
        self.refresh(); self._note.config(text="✅ 已添加")

    def _update(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先选中一项", parent=self); return
        if self._warn_doc_url(self.url_v.get()):
            return
        self.account.update_model(int(sel[0]), name=self.name_v.get(), base_url=self.url_v.get(),
                                  api_key=self.key_v.get(), model=self.model_v.get(),
                                  auth_header=self.auth_h, auth_scheme=self.auth_s,
                                  disable_thinking=self.disable_thinking)
        self.refresh(); self._note.config(text="✅ 已更新")

    def _delete(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "先选中一项", parent=self); return
        if messagebox.askyesno("确认", "删除该模型?", parent=self):
            self.account.remove_model(int(sel[0]))
            self.refresh(); self._note.config(text="已删除")


if __name__ == "__main__":
    app = App()
    app.mainloop()
