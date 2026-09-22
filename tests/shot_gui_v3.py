# -*- coding: utf-8 -*-
"""BitBlt 窗口截图到 docs/gui_v3.png。"""
import os
import sys
import time
import tempfile
import ctypes
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import gui.app_v3 as ga
from core.app_config import AccountManager, AppPaths

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
SRCCOPY = 0x00CC0020
GA_ROOT = 2


def bitblt_window(hwnd, width, height):
    hdc = user32.GetWindowDC(hwnd)
    memdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, width, height)
    gdi32.SelectObject(memdc, bmp)
    gdi32.BitBlt(memdc, 0, 0, width, height, hdc, 0, 0, SRCCOPY)

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    bi = BITMAPINFOHEADER()
    bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.biWidth = width
    bi.biHeight = -height
    bi.biPlanes = 1
    bi.biBitCount = 32
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memdc, bmp, 0, height, buf, ctypes.byref(bi), 0)
    from PIL import Image
    img = Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1).convert("RGB")
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(hwnd, hdc)
    return img


tmp = os.path.join(tempfile.gettempdir(), "ppt2obsidian_preview3")
os.makedirs(tmp, exist_ok=True)
app = ga.App()
app.mgr = AccountManager(AppPaths(os.path.join(tmp, "appdata")))
if app.mgr.all_usernames():
    try:
        app.account = app.mgr.login(app.mgr.all_usernames()[0], "pass123")
    except Exception:
        app.account = app.mgr.register("演示账号2", "pass123")
else:
    app.account = app.mgr.register("演示账号", "pass123")
    app.account.add_model("DeepSeek", "https://api.deepseek.com/v1", "sk-xxxxxxxx", "deepseek-chat")

app.switch_to_main()
try:
    app._pick_prof("pharmacy")
except Exception:
    pass

ppt = os.path.join(ROOT, "outputs", "sample_pharmacy.pptx")
if os.path.isfile(ppt):
    app._set_ppt(ppt)

vault = os.path.join(tmp, "myvault", "药理学")
os.makedirs(vault, exist_ok=True)
app._vault_dir = vault
app.vault_entry.delete(0, "end")
app.vault_entry.insert(0, vault)
app._vault_root = os.path.join(tmp, "myvault")
app._courses = [
    {"name": "药理学", "path": vault},
    {"name": "物理化学", "path": os.path.join(tmp, "myvault", "物理化学")},
]
app._active_course = "药理学"
os.makedirs(os.path.join(tmp, "myvault", "物理化学"), exist_ok=True)
app._refresh_course_ui()
app._refresh_course_files()
app.log("准备就绪，等待生成…", "dim")
app.log("提示：可拖入 PPT / PDF 课件", "info")
app._update_ready_checklist()

# 校验 dropzone 文案
print("dz state", app.drop_zone._state, "name", app.drop_zone._filename)
print("dz title", app.drop_zone.title_id.cget("text"))
print("dz sub", app.drop_zone.sub_id.cget("text"))
print("dz holder h", app.drop_zone.winfo_height(), "req", app.drop_zone.winfo_reqheight())

app.geometry("1220x980+60+20")
app.deiconify()
app.lift()
user32.SetForegroundWindow(int(app.winfo_id()))
user32.BringWindowToTop(int(app.winfo_id()))
app.update_idletasks()
app.update()


def grab():
    try:
        for _ in range(15):
            app.update_idletasks()
            app.update()
        time.sleep(0.5)
        # 滚到生成区，让就绪检查可见
        try:
            app._right_canvas.yview_moveto(0.55)
        except Exception:
            pass
        app.update_idletasks()
        app.update()
        hwnd = user32.GetAncestor(int(app.winfo_id()), GA_ROOT)
        w = app.winfo_width()
        h = app.winfo_height()
        # 含边框
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        gw = int(rect.right - rect.left)
        gh = int(rect.bottom - rect.top)
        img = bitblt_window(hwnd, gw, gh)
        out = os.path.join(ROOT, "docs", "gui_v3.png")
        img.save(out)
        print("saved:", out, "size:", img.size, "winrect", gw, gh, "hwnd", hwnd)
    except Exception:
        import traceback
        traceback.print_exc()
    finally:
        try:
            app.destroy()
        except Exception:
            pass


app.after(1200, grab)
app.mainloop()
