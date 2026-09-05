# -*- coding: utf-8 -*-
"""辅助: 自动登录进主界面并截图(用于展示), 非应用组成部分。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import tkinter as tk
import gui.app as ga
from core.app_config import AccountManager, AppPaths

tmp = os.path.join(tempfile.gettempdir(), "ppt2obsidian_preview")
os.makedirs(tmp, exist_ok=True)
app = ga.App()
app.mgr = AccountManager(AppPaths(os.path.join(tmp, "appdata")))
if app.mgr.all_usernames():
    app.account = app.mgr.login(app.mgr.all_usernames()[0], "pass123")
else:
    app.account = app.mgr.register("演示账号", "pass123")
    app.account.add_model("DeepSeek", "https://api.deepseek.com/v1", "sk-xxxxxxxx", "deepseek-chat")
app.switch_to_main()
app._pick_prof("pharmacy")
app.update_idletasks()


def shoot():
    # 把窗口带出到前台再抓取
    app.deiconify()
    app.lift()
    app.attributes("-topmost", True)
    app.update()
    app.after(400, grab)


def grab():
    try:
        from PIL import ImageGrab
        x = app.winfo_rootx(); y = app.winfo_rooty()
        w = app.winfo_width(); h = app.winfo_height()
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "docs", "gui_main.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        img.save(out)
        print("saved:", out)
    except Exception as e:
        print("shot err:", e)
    finally:
        app.destroy()


app.after(600, shoot)
app.mainloop()
