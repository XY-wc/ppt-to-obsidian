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
import time
import platform
import subprocess
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(HERE, "dist")
BUILD_DIR = os.path.join(HERE, "build")
EXE_NAME = "PPT2Obsidian"


def _tkdnd_platform_dir():
    """返回 tkinterdnd2 里当前平台对应的 tkdnd 目录名(如 win-x64)。"""
    arch = os.environ.get("PROCESSOR_ARCHITECTURE") or platform.machine()
    if sys.platform.startswith("win"):
        if arch == "ARM64":
            return "win-arm64"
        return "win-x86" if sys.maxsize <= 2 ** 31 else "win-x64"
    if sys.platform == "darwin":
        return "osx-arm64" if arch == "arm64" else "osx-x64"
    return "linux-arm64" if arch == "aarch64" else "linux-x64"


def tkdnd_data_args():
    """显式把 tkinterdnd2 的 tkdnd 原生库(拖拽上传所需)加进包里。

    PyInstaller 自带的 hook-tkinterdnd2 靠 PROCESSOR_ARCHITECTURE /
    platform.machine() 判断平台目录，两者在某些精简环境里为空会静默漏收，
    所以这里再显式补一份，保证打包版拖拽可用。
    """
    try:
        import tkinterdnd2
    except Exception:
        print("⚠️ 未安装 tkinterdnd2，打包版将无法拖拽上传")
        return []
    base = os.path.dirname(os.path.abspath(tkinterdnd2.__file__))
    tkdnd = os.path.join(base, "tkdnd")
    if not os.path.isdir(tkdnd):
        print("⚠️ 未找到 tkinterdnd2/tkdnd 目录")
        return []

    name = _tkdnd_platform_dir()
    tcl9 = name + "-tcl9"
    if os.path.isdir(os.path.join(tkdnd, tcl9)):
        try:
            import tkinter
            tcl_major = int(str(tkinter.Tcl().eval("info tclversion")).split(".")[0])
        except Exception:
            tcl_major = 8
        if tcl_major >= 9:
            name = tcl9

    src = os.path.join(tkdnd, name)
    if not os.path.isdir(src):
        print(f"⚠️ 缺少 tkdnd 平台目录 {src}")
        return []
    dest = os.path.join("tkinterdnd2", "tkdnd", name)
    print(f"随包 tkdnd 原生库: {src}")
    return [f"--add-data={src}{os.pathsep}{dest}"]


def verify_tkdnd(dst_app):
    """构建后校验 tkdnd 原生库确实随包(漏收则打包版拖拽失效)。"""
    for dirpath, _dirnames, filenames in os.walk(dst_app):
        if "tkdnd" not in dirpath.split(os.sep):
            continue
        for f in filenames:
            if f.lower().endswith((".dll", ".so", ".dylib")):
                print(f"✅ tkdnd 原生库已随包: {dirpath}")
                return True
    print("❌ 警告: 未发现 tkdnd 原生库，打包版的拖拽上传会失效！")
    return False


def _safe_rmtree(path):
    """删除目录；删不掉(被占用/受限)时改名让路，避免下次构建冲突。"""
    if not path or not os.path.isdir(path):
        return
    try:
        shutil.rmtree(path)
        print(f"清理: {path}")
        return
    except Exception as e:
        print(f"删除 {path} 失败({e})，改为重命名让路")
    stamp = time.strftime("%Y%m%d%H%M%S")
    for i in range(50):
        cand = f"{path}.old{stamp}{'' if i == 0 else f'-{i}'}"
        if not os.path.exists(cand):
            try:
                os.rename(path, cand)
                print(f"已重命名为: {cand}")
            except Exception as e:
                print("重命名失败:", e)
            return


def clean():
    """清理旧的运行目录与构建缓存(保留 dist 里的分享包)。"""
    _safe_rmtree(os.path.join(DIST_DIR, EXE_NAME))
    _safe_rmtree(BUILD_DIR)


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
    # 只带运行所需资源, 不把 assets 下的 .py 等源码带进分享包
    data_args = []
    prof_dir = os.path.join(HERE, "professions")
    if os.path.isdir(prof_dir):
        data_args.append(f"--add-data={prof_dir}{os.pathsep}professions")
    assets_dir = os.path.join(HERE, "assets")
    if os.path.isdir(assets_dir):
        data_args.append(f"--add-data={assets_dir}{os.pathsep}assets")

    # 使用临时输出避免与现有 dist 冲突
    tmp_out = os.path.join(HERE, "_dist_tmp")
    tmp_work = os.path.join(HERE, "_build_tmp")
    # PyInstaller 会尝试清空已存在的输出目录, 受限环境下会直接失败,
    # 所以先自己让路(删不掉就改名)。
    _safe_rmtree(os.path.join(tmp_out, EXE_NAME))
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
        # 拖拽上传: tkinterdnd2 内含 tkdnd 动态库, 由 hooks-contrib 的
        # hook-tkinterdnd2 收集到 tkinterdnd2/tkdnd/<平台>/ 下
        "--hidden-import", "tkinterdnd2",
    ] + data_args + tkdnd_data_args()

    print("执行:", " ".join(cmd))
    # 补齐架构环境变量: PyInstaller 的 hook-tkinterdnd2 与 tkdnd 加载都依赖它
    env = os.environ.copy()
    if sys.platform.startswith("win"):
        env.setdefault("PROCESSOR_ARCHITECTURE",
                       "AMD64" if sys.maxsize > 2 ** 31 else "x86")
    result = subprocess.run(cmd, cwd=HERE, env=env)
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
    strip_source_files(dst_app)
    verify_tkdnd(dst_app)
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
                "5. 本包为编译后的运行版，不含可编辑源码。\n"
            )
    except Exception:
        pass
    print(f"打包完成: {dst_app}")
    print(f"可执行文件: {os.path.join(dst_app, EXE_NAME + '.exe')}")
    make_share_zip(dst_app)


def strip_source_files(root):
    """删除分享包里的可读源码/脚本，只保留运行所需文件。

    业务 .py 已由 PyInstaller 打进 exe 的 PYZ（字节码），这里清掉
    以 --add-data 等方式混入的 .py/.pyi 等，避免接收方直接看到源码。
    """
    removed = 0
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if name.lower().endswith((".py", ".pyc", ".pyo", ".pyi")):
                path = os.path.join(dirpath, name)
                try:
                    os.remove(path)
                    removed += 1
                    print("移除源码文件:", path)
                except Exception as e:
                    print("移除失败:", path, e)
    print(f"源码清理完成, 共移除 {removed} 个文件")


def make_share_zip(app_dir):
    """生成分享 zip：只含 PPT2Obsidian/ 运行目录，不含工程源码。"""
    import zipfile

    zip_path = os.path.join(DIST_DIR, "PPT2Obsidian_win64.zip")
    if os.path.isfile(zip_path):
        try:
            os.remove(zip_path)
        except Exception as e:
            print("旧 zip 删除失败:", e)
    base = os.path.basename(app_dir.rstrip("\\/"))
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for dirpath, dirnames, filenames in os.walk(app_dir):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", ".git")]
            for name in filenames:
                if name.lower().endswith((".py", ".pyc", ".pyo", ".pyi")):
                    continue
                full = os.path.join(dirpath, name)
                rel = os.path.join(base, os.path.relpath(full, app_dir))
                zf.write(full, rel.replace("\\", "/"))
    print(f"分享包: {zip_path}")


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
