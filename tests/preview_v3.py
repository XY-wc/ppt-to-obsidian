# -*- coding: utf-8 -*-
"""辅助: 截图 v3 GUI(单页工作流布局)"""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
import gui.app_v3 as ga
from core.app_config import AccountManager, AppPaths

tmp = os.path.join(tempfile.gettempdir(), "ppt2obsidian_preview3")
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
vault = os.path.join(tmp, "myvault")
os.makedirs(vault, exist_ok=True)
app._vault_dir = vault
app._set_ppt(app._ppt_path)
app.vault_entry.delete(0, "end"); app.vault_entry.insert(0, vault)
app.update_idletasks()

def shoot():
    # 强制窗口到最前并获得焦点
    app.geometry("1180x780+10+10")
    app.update_idletasks()
    app.deiconify()
    app.lift()
    app.focus_force()
    app.attributes("-topmost", True)
    app.update()
    app.grab_set_global  # 占焦点
    import time; time.sleep(1.5)
    app.attributes("-topmost", False)  # 解除 topmost 让 OS 正常合成
    app.update_idletasks()
    app.update()
    app.after(300, grab)

def grab():
    try:
        from PIL import ImageGrab
        for _ in range(5):
            app.update_idletasks(); app.update()
        import time; time.sleep(0.5)
        # 找到关键 widget 打印几何
        for attr in ['_pages']:
            pass
        # 找到 main page frame
        for child in app.winfo_children():
            for sub in child.winfo_children():
                if sub.winfo_width() > 100:
                    print(f"page: {sub.winfo_width()}x{sub.winfo_height()} @({sub.winfo_rootx()},{sub.winfo_rooty()})")
                    # body 是 page 的第 2 个子 (header + body)
                    for cs in sub.winfo_children():
                        if cs.winfo_width() > 100:
                            print(f"  body: {cs.winfo_width()}x{cs.winfo_height()} @({cs.winfo_rootx()},{cs.winfo_rooty()})")
                            for ccs in cs.winfo_children():
                                if ccs.winfo_width() > 50:
                                    print(f"    col: {ccs.winfo_width()}x{ccs.winfo_height()} @({ccs.winfo_x()},{ccs.winfo_y()})")
                                    for cccs in ccs.winfo_children():
                                        if cccs.winfo_width() > 30:
                                            print(f"      card: {cccs.winfo_width()}x{cccs.winfo_height()}")
        x = app.winfo_rootx(); y = app.winfo_rooty()
        w = app.winfo_width(); h = app.winfo_height()
        bbox = (x, y, x + w, y + h)
        img = ImageGrab.grab(bbox=bbox, all_screens=True)
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "gui_v3.png")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        img.save(out)
        print("saved:", out, "size:", img.size)
    except Exception as e:
        import traceback; traceback.print_exc()
    finally:
        app.destroy()

app.after(800, shoot)
app.mainloop()
