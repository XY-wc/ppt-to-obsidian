#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PyInstaller 打包脚本 + 桌面快捷方式生成。

用法:
  python build_exe.py

产物:
  dist/PPT2Obsidian/          单目录 exe + 依赖
  dist/PPT2Obsidian.exe       单文件 exe (可选, 体积更大)
  桌面快捷方式: PPT2Obsidian.lnk
"""
import os
import sys
import subprocess
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(HERE, "dist")
BUILD_DIR = os.path.join(HERE, "build")
EXE_NAME = "PPT2Obsidian"


def clean():
    """清理旧 dist/build。失败也不影响打包, 因为 PyInstaller 会自动覆盖。"""
    for d in [DIST_DIR, BUILD_DIR]:
        if os.path.isdir(d):
            try:
                shutil.rmtree(d)
                print(f"清理: {d}")
            except Exception as e:
                print(f"跳过清理 {d}: {e} (PyInstaller 会自动覆盖)")


def build():
    """用 PyInstaller 打包。

    由于 sandbox 对大目录的 shutil.rmtree 受限 (SAFE_DELETE_BULK_CONFIRM_REQUIRED),
    使用临时输出目录, 打包完后再让调用方决定如何迁移到 dist/。
    """
    pyi = None
    # 优先当前解释器同环境的 pyinstaller（venv）
    for cand in [
        os.path.join(os.path.dirname(sys.executable), "Scripts", "pyinstaller.exe"),
        os.path.join(os.path.dirname(sys.executable), "pyinstaller.exe"),
    ]:
        if os.path.exists(cand):
            pyi = [cand]
            break
    if not pyi:
        pyi = [sys.executable, "-m", "PyInstaller"]

    # 数据文件: professions/*.json -> 打包后 professions/
    data_args = []
    prof_dir = os.path.join(HERE, "professions")
    if os.path.isdir(prof_dir):
        data_args.append(f"--add-data={prof_dir}{os.pathsep}professions")
    # assets/ 资源(icon.png / icon.ico)随包附带, 供窗口图标使用
    assets_dir = os.path.join(HERE, "assets")
    if os.path.isdir(assets_dir):
        data_args.append(f"--add-data={assets_dir}{os.pathsep}assets")

    # 使用临时输出避免与现有 dist 冲突
    tmp_out = os.path.join(HERE, "_dist_tmp")
    tmp_work = os.path.join(HERE, "_build_tmp")
    os.makedirs(tmp_out, exist_ok=True)
    os.makedirs(tmp_work, exist_ok=True)

    cmd = pyi + [
        "run.py",
        "--name", EXE_NAME,
        "--onedir",           # 单目录模式, 启动快, 体积小
        "--windowed",         # 不弹控制台黑框
        "--noconfirm",
        # 注意: 不加 --clean, 因 sandbox 对大目录清理受限
        "--distpath", tmp_out,
        "--workpath", tmp_work,
        "--paths", os.path.join(HERE, "src"),
        "--icon", os.path.join(HERE, "assets", "icon.ico"),   # Windows exe 图标(多尺寸 ico)
        "--hidden-import", "gui.app_v3",
        "--hidden-import", "core.knowledge_base",
        "--hidden-import", "core.ppt_parser",
        "--hidden-import", "core.pdf_parser",
        "--hidden-import", "core.document_loader",
        "--hidden-import", "core.app_config",
        "--hidden-import", "llm.client",
        "--hidden-import", "obsidian.summarizer",
        "--hidden-import", "obsidian.staged",
        "--hidden-import", "obsidian.lecture",
        "--hidden-import", "obsidian.graph_config",
        "--hidden-import", "obsidian.validator",
        "--hidden-import", "obsidian.note_template",
        "--hidden-import", "pipeline",
        "--hidden-import", "pymupdf",
        "--hidden-import", "fitz",
    ] + data_args

    print("执行:", " ".join(cmd))
    result = subprocess.run(cmd, cwd=HERE)
    if result.returncode != 0:
        print("PyInstaller 失败")
        sys.exit(1)
    src_app = os.path.join(tmp_out, EXE_NAME)
    dst_app = os.path.join(DIST_DIR, EXE_NAME)
    if os.path.isdir(dst_app):
        try:
            shutil.rmtree(dst_app)
        except Exception as e:
            print("清理旧 dist 失败:", e)
    os.makedirs(DIST_DIR, exist_ok=True)
    try:
        shutil.copytree(src_app, dst_app)
    except Exception as e:
        print("复制到 dist 失败:", e)
        print(f"产物仍在: {src_app}")
        sys.exit(1)
    # 写一份给接收方的简短说明
    readme = os.path.join(dst_app, "使用说明.txt")
    try:
        with open(readme, "w", encoding="utf-8") as f:
            f.write(
                "【PPT → Obsidian 智能笔记】\n"
                "1. 解压整个文件夹后，双击 PPT2Obsidian.exe 即可运行（不要只拷贝 exe）。\n"
                "2. 首次使用请注册本地账号；数据保存在本机用户目录 .ppt2obsidian。\n"
                "3. 建议先「设置知识库根目录」再新建课程，然后拖入 PPT/PDF 生成笔记。\n"
                "4. 未绑定大模型也可离线生成；绑定 API 后结构更准。\n"
            )
    except Exception:
        pass
    print(f"打包完成: {dst_app}")
    print(f"可执行文件: {os.path.join(dst_app, EXE_NAME + '.exe')}")


def create_desktop_shortcut():
    """在桌面创建 .lnk 快捷方式(Windows)。"""
    if sys.platform != "win32":
        print("非 Windows, 跳过桌面快捷方式")
        return
    try:
        import winshell
        from win32com.client import Dispatch
    except ImportError:
        print("缺少 winshell/pywin32, 尝试 pip 安装...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "winshell", "pywin32"],
                       capture_output=True)
        try:
            import winshell
            from win32com.client import Dispatch
        except Exception as e:
            print(f"安装后仍无法导入: {e}, 跳过快捷方式")
            return

    exe_path = os.path.join(DIST_DIR, EXE_NAME, f"{EXE_NAME}.exe")
    if not os.path.exists(exe_path):
        print(f"未找到 exe: {exe_path}")
        return

    desktop = winshell.desktop()
    lnk_path = os.path.join(desktop, f"{EXE_NAME}.lnk")
    shell = Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(lnk_path)
    shortcut.Targetpath = exe_path
    shortcut.WorkingDirectory = os.path.dirname(exe_path)
    shortcut.IconLocation = exe_path
    shortcut.save()
    print(f"桌面快捷方式已创建: {lnk_path}")


def create_bat_shortcut():
    """备用: 生成一个 .bat 启动器放在桌面(无需 winshell)。"""
    if sys.platform != "win32":
        return
    desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    exe_path = os.path.join(DIST_DIR, EXE_NAME, f"{EXE_NAME}.exe")
    bat_path = os.path.join(desktop, f"{EXE_NAME}.bat")
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(f'@echo off\nstart "" "{exe_path}"\n')
    print(f"桌面启动脚本已创建: {bat_path}")


def main():
    print("=" * 50)
    print("PPT → Obsidian 智能笔记 打包工具")
    print("=" * 50)
    # 注意: 不删除旧 dist/build, PyInstaller 会自动覆盖 (sandbox 里 delete 受限)
    build()
    try:
        create_desktop_shortcut()
    except Exception as e:
        print(f"快捷方式创建失败({e}), 改用 .bat 启动器")
        create_bat_shortcut()
    print("\n✅ 全部完成!")
    print(f"   可执行文件: {DIST_DIR}\\{EXE_NAME}\\{EXE_NAME}.exe")
    print("   双击即可运行, 无需安装 Python。")


if __name__ == "__main__":
    main()
