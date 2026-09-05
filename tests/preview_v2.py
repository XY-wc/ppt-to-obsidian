# -*- coding: utf-8 -*-
"""辅助: 自动登录进主界面并截图 v2 GUI"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import gui.app_v2 as ga
from core.app_config import AccountManager, AppPaths

tmp = os.path.join(tempfile.gettempdir(), "ppt2obsidian_preview2")
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
app._ppt_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs", "sample_pharmacy.pptx")
app._vault_dir = os.path.join(tmp, "vault")
os.makedirs(app._vault_dir, exist_ok=True)
app.update_idletasks()

def shoot():
    app.deiconify(); app.lift(); app.attributes("-topmost", True); app.update()
    app.after(500, grab)

def grab():
    try:
        from PIL import ImageGrab
        x, y = app.winfo_rootx(), app.winfo_rooty()
        w, h = app.winfo_width(), app.winfo_height()
        img = ImageGrab.grab(bbox=(x, y, x + w, y + h))
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "gui_v2.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        img.save(out)
        print("saved:", out)
    except Exception as e:
        print("shot err:", e)
    finally:
        app.destroy()

app.after(800, shoot)
app.mainloop()
