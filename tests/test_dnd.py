# -*- coding: utf-8 -*-
"""拖拽上传(tkdnd)回归测试。

覆盖:
  - tkdnd 扩展是否成功挂到 Tk root
  - 拖拽区(含全部子控件)是否都注册为 drop target
  - tkdnd 的 %D 数据解析(空格路径 / {} 包裹 / 多文件)
  - 拖入受支持 / 不受支持 / 文件夹 的三种分支

说明: 真实的"鼠标拖拽"由 tkdnd 原生窗口消息驱动, 无头环境无法模拟,
所以这里只验证注册结果与落盘回调逻辑。
运行: python tests/test_dnd.py   (无需 pytest)
"""
import os
import sys
import tempfile
import types
from tkinter import messagebox

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from gui import app_v3


class _FakeEvent:
    """模拟 tkinterdnd2 的 DnDEvent(只用到 widget / data)。"""

    def __init__(self, data="", widget=None):
        self.data = data
        self.widget = widget


def _make_app():
    """建一个隐藏的 App 实例(不进入 mainloop)。"""
    app = app_v3.App()
    app.withdraw()
    return app


def _walk(w):
    yield w
    for ch in w.winfo_children():
        yield from _walk(ch)


def test_tkdnd_loaded():
    assert app_v3.HAS_DND is True, "缺少 tkinterdnd2 依赖"
    app = _make_app()
    try:
        assert app._dnd_ready is True, "tkdnd 扩展未加载成功"
    finally:
        app.destroy()


def test_drop_zone_subtree_registered():
    app = _make_app()
    try:
        zone = app.drop_zone
        not_reg = [str(w) for w in _walk(zone) if not getattr(w, "_dnd_reg", False)]
        assert not not_reg, f"拖拽区仍有未注册的子控件: {not_reg}"
        # 主界面其它控件也应可接收拖入(拖到任意处都能选文件)
        assert getattr(app.drop_zone.master, "_dnd_reg", False), "主界面未整体注册拖放"
    finally:
        app.destroy()


def test_parse_drop_paths():
    app = _make_app()
    try:
        # tkdnd: 含空格的路径用 {} 包裹
        raw = "{C:/a b/新课 件.pdf} C:/plain/x.pptx"
        got = app._parse_drop_paths(raw)
        assert got == ["C:/a b/新课 件.pdf", "C:/plain/x.pptx"], got
    finally:
        app.destroy()


def test_drop_supported_file_sets_ppt():
    app = _make_app()
    try:
        with tempfile.TemporaryDirectory() as d:
            pdf = os.path.join(d, "第 1 讲 绪论.pdf")
            with open(pdf, "wb") as f:
                f.write(b"%PDF-1.4\n")
            # tkdnd 对含空格路径会加 {}
            ev = _FakeEvent("{%s}" % pdf.replace("\\", "/"), app.drop_zone)
            rv = app._on_drop(ev)
            assert rv == app_v3._DND_COPY
            assert os.path.abspath(app._ppt_path) == os.path.abspath(pdf)
            assert "绪论" in app.ppt_file_label.cget("text")
    finally:
        app.destroy()


def test_drop_multiple_takes_first(monkeypatch_warn=None):
    app = _make_app()
    try:
        with tempfile.TemporaryDirectory() as d:
            a = os.path.join(d, "a.pdf")
            b = os.path.join(d, "b.pptx")
            for p in (a, b):
                with open(p, "wb") as f:
                    f.write(b"x")
            ev = _FakeEvent(f"{{{a}}} {{{b}}}", app.drop_zone)
            app._on_drop(ev)
            assert os.path.abspath(app._ppt_path) == os.path.abspath(a)
    finally:
        app.destroy()


def test_drop_unsupported_shows_hint():
    app = _make_app()
    try:
        with tempfile.TemporaryDirectory() as d:
            txt = os.path.join(d, "readme.txt")
            with open(txt, "w", encoding="utf-8") as f:
                f.write("hi")
            app._ppt_path = ""
            calls = []
            orig = messagebox.showwarning
            messagebox.showwarning = lambda *a, **k: calls.append(a)
            try:
                ev = _FakeEvent(txt.replace("\\", "/"), app.drop_zone)
                rv = app._on_drop(ev)
            finally:
                messagebox.showwarning = orig
            assert rv == app_v3._DND_NONE
            assert app._ppt_path == "", "不支持的文件不应被选中"
            assert calls, "应弹出格式不支持的提示"
    finally:
        app.destroy()


def test_drop_folder_shows_hint():
    app = _make_app()
    try:
        with tempfile.TemporaryDirectory() as d:
            app._ppt_path = ""
            calls = []
            orig = messagebox.showwarning
            messagebox.showwarning = lambda *a, **k: calls.append(a)
            try:
                ev = _FakeEvent(d.replace("\\", "/"), app.drop_zone)
                rv = app._on_drop(ev)
            finally:
                messagebox.showwarning = orig
            assert rv == app_v3._DND_NONE
            assert calls and "文件夹" in calls[0][1]
    finally:
        app.destroy()


def test_in_drop_zone_detection():
    app = _make_app()
    try:
        assert app._is_in_drop_zone(app.drop_zone) is True
        assert app._is_in_drop_zone(app.drop_zone.title_id) is True
        assert app._is_in_drop_zone(app.user_label) is False
    finally:
        app.destroy()


def test_drag_enter_highlights_zone():
    app = _make_app()
    try:
        app._ppt_path = ""
        app.drop_zone.set_state("normal")
        app._on_drag_enter(_FakeEvent("", app.drop_zone.title_id))
        assert app.drop_zone._state == "hover"
        app._on_drag_leave(_FakeEvent("", app.drop_zone.title_id))
        assert app.drop_zone._state == "normal"
    finally:
        app.destroy()


def _main():
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and isinstance(v, types.FunctionType)]
    ok = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
            ok += 1
        except Exception as e:
            print(f"FAIL  {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{ok}/{len(tests)} passed")
    return 0 if ok == len(tests) else 1


if __name__ == "__main__":
    sys.exit(_main())
