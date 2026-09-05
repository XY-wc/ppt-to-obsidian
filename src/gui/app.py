# -*- coding: utf-8 -*-
"""
图形界面 (tkinter, 本地优先, 无需联网鉴权服务器)。

窗口流程:
  1. 登录/注册页  —— 本地账号 + 密码
  2. 主工作页
      ├─ 选择专业(卡片式)
      ├─ 选择 PPT
      ├─ 模型设置(绑定的大模型, 或离线模式)
      ├─ 输出 vault 目录
      └─ 运行转换 + 日志 + 打开 vault

配色: 简洁浅色(light), 与系统浅色主题一致; 用 ttk 现代样式。
"""
import os
import sys
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
from llm.client import KNOWN_PROVIDERS, LLMClient
from pipeline import run_conversion

# 主题色
BG = "#f5f6f8"
CARD = "#ffffff"
PRIMARY = "#3b6ef5"
PRIMARY_DARK = "#2f57c4"
TEXT = "#1f2329"
SUB = "#6b7280"
ACCENT_BG = "#eaf0ff"

FONT = ("Microsoft YaHei UI", 10)
FONT_S = ("Microsoft YaHei UI", 9)
FONT_TITLE = ("Microsoft YaHei UI", 16, "bold")
FONT_B = ("Microsoft YaHei UI", 11, "bold")


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PPT → Obsidian 智能笔记 (专业驱动)")
        self.geometry("1020x700")
        self.minsize(920, 620)
        self.configure(bg=BG)
        self._setup_style()

        self.paths = AppPaths()
        self.mgr = AccountManager(self.paths)
        self.kb = KnowledgeBase()
        self.profs = self.kb.load_all()
        self.account = None

        self.container = tk.Frame(self, bg=BG)
        self.container.pack(fill="both", expand=True)

        self._pages = {}
        for name, builder in [("login", self._build_login), ("main", self._build_main)]:
            page = builder(self.container)
            self._pages[name] = page

        self._show("login")

    # ---------------- 样式 ----------------
    def _setup_style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure(".", font=FONT, background=BG, foreground=TEXT)
        st.configure("TButton", padding=(14, 7))
        st.map("TButton",
               background=[("active", PRIMARY_DARK), ("!disabled", PRIMARY)],
               foreground=[("!disabled", "white")])
        st.configure("Primary.TButton", background=PRIMARY, foreground="white")
        st.map("Primary.TButton",
               background=[("active", PRIMARY_DARK), ("!disabled", PRIMARY)],
               foreground=[("!disabled", "white")])
        st.configure("Ghost.TButton", background="#eef1f5", foreground=TEXT)
        st.map("Ghost.TButton",
               background=[("active", "#dfe4ea")], foreground=[("!disabled", TEXT)])
        st.configure("Card.TFrame", background=CARD)
        st.configure("TEntry", fieldbackground="white")
        st.configure("Card.TLabelframe", background=CARD, bordercolor="#e3e6ea")
        st.configure("Card.TLabelframe.Label", background=CARD, foreground=TEXT)

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
        # 卡片
        card = tk.Frame(page, bg=CARD, highlightbackground="#e3e6ea",
                        highlightthickness=1)
        card.place(relx=0.5, rely=0.5, anchor="center", width=420, height=520)
        # Logo区
        tk.Label(card, text="🧠", font=("Segoe UI Emoji", 34), bg=CARD).pack(pady=(36, 0))
        tk.Label(card, text="PPT → Obsidian 智能笔记", font=FONT_TITLE, bg=CARD,
                 fg=TEXT).pack(pady=(6, 2))
        tk.Label(card, text="专业驱动 · 本地优先 · 生成可生长的知识图谱",
                 font=FONT_S, bg=CARD, fg=SUB).pack()

        self._auth_mode = tk.StringVar(value="login")
        tk.Label(card, text="账号", bg=CARD, fg=SUB, font=FONT_S, anchor="w").pack(
            fill="x", padx=44, pady=(26, 2))
        self.username_var = tk.StringVar(value=self.mgr.remember_last_username() or "")
        e_user = tk.Entry(card, textvariable=self.username_var, font=FONT,
                          relief="solid", bd=1, highlightthickness=1)
        e_user.pack(fill="x", padx=44)

        tk.Label(card, text="密码", bg=CARD, fg=SUB, font=FONT_S, anchor="w").pack(
            fill="x", padx=44, pady=(14, 2))
        self.pass_var = tk.StringVar()
        e_pass = tk.Entry(card, textvariable=self.pass_var, font=FONT, show="●",
                          relief="solid", bd=1, highlightthickness=1)
        e_pass.pack(fill="x", padx=44)

        self.auth_err = tk.Label(card, text="", bg=CARD, fg="#d93025", font=FONT_S)
        self.auth_err.pack(pady=(10, 0))

        # 按钮区
        btns = tk.Frame(card, bg=CARD)
        btns.pack(pady=(14, 6))
        tk.Button(btns, text="登  录", command=self._do_login, bg=PRIMARY,
                  fg="white", activebackground=PRIMARY_DARK, relief="flat",
                  font=FONT_B, width=16, cursor="hand2").pack(side="left", padx=6)
        tk.Button(btns, text="注册新账号", command=self._do_register,
                  bg="#eef1f5", fg=TEXT, activebackground="#dfe4ea", relief="flat",
                  font=FONT, width=14, cursor="hand2").pack(side="left", padx=6)

        tk.Label(card,
                 text="· 所有数据保存在本机，不上传\n· 首次使用请先注册一个本地账号\n· 绑定你自己的大模型 API 后即可生成结构化笔记",
                 justify="left", bg=CARD, fg=SUB, font=("Microsoft YaHei UI", 8)).pack(
            side="bottom", pady=18)
        page._card = card
        return page

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
        header = tk.Frame(page, bg=CARD, highlightbackground="#e6e9ee", highlightthickness=1)
        header.pack(fill="x")
        tk.Label(header, text="📚 PPT → Obsidian 智能笔记", font=FONT_TITLE, bg=CARD).pack(side="left", padx=18, pady=14)
        self.user_label = tk.Label(header, text="", bg=CARD, fg=SUB, font=FONT_S)
        self.user_label.pack(side="right", padx=18)
        tk.Button(header, text="退出", command=self._logout, bg="#eef1f5", fg=TEXT,
                  relief="flat", cursor="hand2", font=FONT_S).pack(side="right", padx=(0, 6), pady=12)

        body = tk.Frame(page, bg=BG)
        body.pack(fill="both", expand=True, padx=16, pady=12)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        # 左: 步骤区
        left = tk.Frame(body, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        self._build_step1(left)
        self._build_step2(left)
        self._build_run(left)

        # 右: 模型设置 + 日志
        right = tk.Frame(body, bg=BG)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))
        self._build_models(right)
        self._build_log(right)

        return page

    def _card_frame(self, parent, title):
        c = tk.Frame(parent, bg=CARD, highlightbackground="#e6e9ee", highlightthickness=1)
        c.pack(fill="x", pady=(0, 10))
        tk.Label(c, text=title, font=FONT_B, bg=CARD).pack(anchor="w", padx=14, pady=(10, 2))
        inner = tk.Frame(c, bg=CARD)
        inner.pack(fill="x", padx=14, pady=(0, 12))
        return c, inner

    # -- 步骤1: 专业 --
    def _build_step1(self, parent):
        c, inner = self._card_frame(parent, "① 选择专业 (决定总结准确度)")
        self.prof_var = tk.StringVar()
        prof_row = tk.Frame(inner, bg=CARD)
        prof_row.pack(fill="x")
        self.prof_buttons = {}
        if not self.profs:
            tk.Label(inner, text="未加载到专业框架!", fg="#d93025", bg=CARD).pack(anchor="w")
            return
        for i, p in enumerate(self.profs):
            b = tk.Button(prof_row, text=p.name, font=FONT_B,
                          command=lambda pid=p.id: self._pick_prof(pid),
                          relief="flat", cursor="hand2")
            b.pack(side="left", padx=(0 if i == 0 else 8), ipadx=16, ipady=6)
            self.prof_buttons[p.id] = b
            if i == 0:
                self.prof_var.set(p.id)
        self.prof_desc = tk.Label(inner, text="", font=FONT_S, bg=CARD, fg=SUB,
                                  justify="left", anchor="w", wraplength=520)
        self.prof_desc.pack(fill="x", pady=(8, 0))
        self._pick_prof(self.profs[0].id)

    def _pick_prof(self, pid):
        self.prof_var.set(pid)
        for k, b in self.prof_buttons.items():
            sel = (k == pid)
            b.configure(bg=PRIMARY if sel else "#eef1f5",
                        fg="white" if sel else TEXT,
                        activebackground=PRIMARY_DARK if sel else "#dfe4ea")
        p = self.kb.get(pid)
        if p:
            n = p.concept_count()
            self.prof_desc.config(
                text=f"{p.desc}\n核心概念 {n} 个 · 符号 {len(p.symbol_mapping)} 个 · 易错点 {len(p.common_misconceptions)} 个")
        # 也刷新预览标签里的专业名(预览标题会用它)

    # -- 步骤2: PPT + vault --
    def _build_step2(self, parent):
        c, inner = self._card_frame(parent, "② 选择 PPT 与输出位置")
        row = tk.Frame(inner, bg=CARD)
        row.pack(fill="x")
        self.ppt_label = tk.Label(row, text="未选择 PPT", bg=CARD, fg=SUB, anchor="w")
        self.ppt_label.pack(side="left", fill="x", expand=True)
        tk.Button(row, text="选择 PPTX", command=self._choose_ppt, bg="#eef1f5", fg=TEXT,
                  relief="flat", cursor="hand2").pack(side="right")

        row2 = tk.Frame(inner, bg=CARD)
        row2.pack(fill="x", pady=(8, 0))
        self.vault_label = tk.Label(row2, text="未选择 Obsidian Vault", bg=CARD, fg=SUB, anchor="w")
        self.vault_label.pack(side="left", fill="x", expand=True)
        tk.Button(row2, text="选择Vault", command=self._choose_vault, bg="#eef1f5", fg=TEXT,
                  relief="flat", cursor="hand2").pack(side="right")

    # -- 运行按钮 --
    def _build_run(self, parent):
        c, inner = self._card_frame(parent, "③ 开始转换")
        self.model_choice = tk.StringVar(value="offline")
        # 离线/在线 radio 动态在模型卡里? 这里放进度与按钮
        self.run_btn = tk.Button(inner, text="🚀 生成 Obsidian 笔记", command=self._run,
                                 bg=PRIMARY, fg="white", activebackground=PRIMARY_DARK,
                                 font=FONT_B, relief="flat", cursor="hand2", height=2)
        self.run_btn.pack(fill="x")
        self.progress_bar = ttk.Progressbar(inner, mode="indeterminate", length=300)
        self.progress_bar.pack(fill="x", pady=(8, 0))
        # 打开vault按钮(转换后可用)
        self.open_btn = tk.Button(inner, text="📂 在 Obsidian 中打开笔记", command=self._open_vault,
                                  state="disabled", bg="#eef1f5", fg=TEXT, relief="flat", cursor="hand2")
        self.open_btn.pack(fill="x", pady=(8, 0))

    # -- 模型管理卡 --
    def _build_models(self, parent):
        c, inner = self._card_frame(parent, "大模型绑定 (用于专业驱动生成)")
        self.model_var = tk.StringVar(value="offline")
        radio_row = tk.Frame(inner, bg=CARD)
        radio_row.pack(fill="x")
        tk.Radiobutton(radio_row, text="本地规则模式(离线, 免费)", variable=self.model_var,
                       value="offline", bg=CARD, font=FONT_S, cursor="hand2").pack(anchor="w")
        tk.Radiobutton(radio_row, text="使用已绑定的大模型", variable=self.model_var,
                       value="online", bg=CARD, font=FONT_S, cursor="hand2").pack(anchor="w")
        self.model_dd = ttk.Combobox(inner, state="readonly", font=FONT_S)
        self.model_dd.pack(fill="x", pady=(4, 0))
        self.model_dd.bind("<<ComboboxSelected>>", self._on_model_select)

        btnrow = tk.Frame(inner, bg=CARD)
        btnrow.pack(fill="x", pady=(6, 0))
        tk.Button(btnrow, text="⚙️ 管理模型", command=self._open_model_manage,
                  bg="#eef1f5", fg=TEXT, relief="flat", cursor="hand2").pack(side="left", padx=(0, 6))
        tk.Button(btnrow, text="测试连接", command=self._test_model,
                  bg="#eef1f5", fg=TEXT, relief="flat", cursor="hand2").pack(side="left")
        self.model_hint = tk.Label(inner, text="", bg=CARD, fg=SUB, font=FONT_S,
                                   justify="left", anchor="w", wraplength=340)
        self.model_hint.pack(fill="x", pady=(8, 0))

    def _build_log(self, parent):
        c, inner = self._card_frame(parent, "运行日志")
        self.log_text = tk.Text(inner, height=12, bg="#fafbfc", fg=TEXT, font=("Consolas", 9),
                                relief="solid", bd=1, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True)
        self.log_text.tag_config("ok", foreground="#1a7f37")
        self.log_text.tag_config("err", foreground="#d93025")
        self.log_text.tag_config("info", foreground="#3b6ef5")
        self.log_text.tag_config("dim", foreground="#6b7280")

    # ---------------- 模型选择下拉刷新 ----------------
    def refresh_main(self):
        self.user_label.config(text=f"👤 {self.account.username}")
        models = self.account.get_models()
        names = [f"{m.get('name','?')} · {m.get('model','')}" for m in models]
        if names:
            self.model_dd.config(values=names)
            self.model_dd.current(0)
            self.model_var.set("online" if self.account.get_pref("use_online") else ("offline" if not names else "online"))
            self._show_model_hint(0)
        else:
            self.model_dd.config(values=[])
            self.model_dd.set("")
            self.model_var.set("offline")
            self._show_model_hint(-1)

    def _show_model_hint(self, idx):
        if idx < 0:
            self.model_hint.config(text="当前未绑定任何模型。\n点击「管理模型」添加 (支持 DeepSeek / OpenAI / Kimi / 智谱 / Ollama 等 OpenAI 兼容接口)。\n\n离线模式仍会生成基础笔记, 但绑定模型后可获得更准确的结构化笔记。")
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
            llm = LLMClient(m.get("base_url"), m.get("api_key"), m.get("model"))
            self.log("正在测试连接…", "info")
            self.update_idletasks()
            out = llm.chat([{"role": "user", "content": "请只回复两个字: 正常"}], max_tokens=10)
            self.log(f"✅ 连接成功: {out}", "ok")
        except Exception as e:
            self.log(f"❌ 连接失败: {e}", "err")
            messagebox.showerror("测试失败", str(e))

    # ---------------- 文件选择 ----------------
    def _choose_ppt(self):
        p = filedialog.askopenfilename(
            title="选择 PPT 课件", filetypes=[("PowerPoint", "*.pptx *.ppt"), ("所有文件", "*.*")])
        if p:
            self._ppt_path = p
            self.ppt_label.config(text="📄 " + os.path.basename(p), fg=TEXT)

    def _choose_vault(self):
        # 允许用户选到包含 .obsidian 的目录, 或新建一个目录作为 vault
        v = filedialog.askdirectory(title="选择 Obsidian Vault 目录(若没有 .obsidian 将提示创建)")
        if v:
            self._vault_dir = v
            self.vault_label.config(text="🗂 " + v, fg=TEXT)

    # ---------------- 运行 ----------------
    def _run(self):
        ppt = getattr(self, "_ppt_path", None)
        vault = getattr(self, "_vault_dir", None)
        if not ppt:
            messagebox.showwarning("提示", "请先选择一个 PPTX 文件")
            return
        if not vault:
            messagebox.showwarning("提示", "请先选择输出用的 Obsidian Vault 目录")
            return
        # 校验 vault (非 .obsidian 提示创建)
        if not os.path.isdir(os.path.join(vault, ".obsidian")):
            if not messagebox.askyesno("Vault 确认",
                                       f"所选目录还不是 Obsidian vault(无 .obsidian 文件夹)。\n\n在 Obsidian 中「打开文件夹作为仓库」选择此目录即可正常使用。\n\n仍继续生成笔记吗?"):
                return
        pid = self.prof_var.get()
        self.run_btn.config(state="disabled")
        self.progress_bar.start(10)
        self.open_btn.config(state="disabled")
        t = threading.Thread(target=self._run_worker, args=(ppt, pid, vault), daemon=True)
        t.start()

    def _run_worker(self, ppt, pid, vault):
        try:
            use_online = self.model_var.get() == "online" and self.model_dd.current() >= 0
            llm = None
            model_idx = 0
            if use_online:
                model_idx = self.model_dd.current()
                m = self.account.get_models()[model_idx]
                llm = LLMClient(m.get("base_url", ""), m.get("api_key", ""), m.get("model", ""))
            result = run_conversion(ppt, pid, vault, account=self.account,
                                    model_index=model_idx, llm=llm,
                                    progress=lambda m: self.after(0, lambda mm=m: self.log(mm, "info")))
            self.after(0, lambda: self._run_done(result))
        except Exception as e:
            self.after(0, lambda: self.log(f"❌ {e}", "err"))

    def _run_done(self, result):
        self.progress_bar.stop()
        self.run_btn.config(state="normal")
        if result.ok and result.notes:
            self.log(f"✅ 转换成功! 共生成 {len(result.notes)} 个笔记文件:", "ok")
            for n in result.notes:
                self.log(f"   · {n.rel_path}", "dim")
            self.open_btn.config(state="normal")
            self._last_vault = result.vault_dir
            # 保存偏好
            self.account.set_pref("use_online", self.model_var.get() == "online")
            # 提示重复概念
            messagebox.showinfo("完成", f"已生成 {len(result.notes)} 个笔记到:\n{result.vault_dir}\n\n点击「在 Obsidian 中打开笔记」可预览。")
        else:
            self.log(f"❌ 转换失败: {result.error}", "err")
            messagebox.showerror("转换失败", result.error)

    def _open_vault(self):
        vault = getattr(self, "_last_vault", None)
        if not vault:
            vault = getattr(self, "_vault_dir", None)
        if not vault or not os.path.isdir(vault):
            messagebox.showwarning("提示", "请先运行一次转换")
            return
        # 用系统默认打开 vault 根; 并直接打开 MOC
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

    # ---------------- 日志 ----------------
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
        self.geometry("560x460")
        self.configure(bg=BG)
        self.transient(master)
        self.grab_set()

        tk.Label(self, text="绑定你的大模型 API (OpenAI 兼容接口)", font=FONT_B, bg=BG).pack(
            anchor="w", padx=16, pady=(14, 4))

        # 列表
        listf = tk.Frame(self, bg=BG)
        listf.pack(fill="both", expand=True, padx=16)
        self.tree = ttk.Treeview(listf, columns=("name", "model", "url"), show="headings", height=5)
        self.tree.heading("name", text="名称")
        self.tree.heading("model", text="模型")
        self.tree.heading("url", text="Base URL")
        self.tree.column("name", width=110)
        self.tree.column("model", width=150)
        self.tree.column("url", width=230)
        self.tree.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(listf, orient="vertical", command=self.tree.yview)
        sb.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=sb.set)
        self.refresh()

        # 表单
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

        # 动作
        act = tk.Frame(self, bg=BG)
        act.pack(fill="x", padx=16, pady=10)
        tk.Button(act, text="添加", command=self._add, bg=PRIMARY, fg="white", relief="flat", cursor="hand2").pack(side="left", padx=3)
        tk.Button(act, text="更新选中", command=self._update, bg="#eef1f5", fg=TEXT, relief="flat", cursor="hand2").pack(side="left", padx=3)
        tk.Button(act, text="删除选中", command=self._delete, bg="#eef1f5", fg="#d93025", relief="flat", cursor="hand2").pack(side="left", padx=3)
        self._note = tk.Label(self, text="", bg=BG, fg="#1a7f37", font=FONT_S)
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
        # 通知主界面刷新
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
