#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在桌面创建快捷方式(Windows)。用法: python create_desktop_lnk.py"""
import os
import sys
import subprocess

HERE = os.path.dirname(os.path.abspath(__file__))
EXE = os.path.join(HERE, "dist", "PPT2Obsidian", "PPT2Obsidian.exe")
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")

if not os.path.exists(EXE):
    print(f"错误: 未找到 {EXE}")
    print("请先运行 build_exe.py 打包")
    sys.exit(1)

# 方法1: 尝试 winshell
success = False
try:
    import winshell
    from win32com.client import Dispatch
    lnk = os.path.join(DESKTOP, "PPT2Obsidian.lnk")
    shell = Dispatch("WScript.Shell")
    shortcut = shell.CreateShortCut(lnk)
    shortcut.Targetpath = EXE
    shortcut.WorkingDirectory = os.path.dirname(EXE)
    shortcut.IconLocation = EXE
    shortcut.save()
    print(f"桌面快捷方式已创建: {lnk}")
    success = True
except Exception:
    pass

if not success:
    # 方法2: 用 VBS 脚本创建
    vbs = os.path.join(HERE, "_tmp_create_lnk.vbs")
    exe_escaped = EXE.replace("\\", "\\\\")
    dir_escaped = os.path.dirname(EXE).replace("\\", "\\\\")
    with open(vbs, "w", encoding="utf-8") as f:
        f.write('Set WshShell = WScript.CreateObject("WScript.Shell")\n')
        f.write('strDesktop = WshShell.SpecialFolders("Desktop")\n')
        f.write('Set oLink = WshShell.CreateShortcut(strDesktop & "\\PPT2Obsidian.lnk")\n')
        f.write('oLink.TargetPath = "' + exe_escaped + '"\n')
        f.write('oLink.WorkingDirectory = "' + dir_escaped + '"\n')
        f.write('oLink.IconLocation = "' + exe_escaped + '"\n')
        f.write('oLink.Save\n')
    try:
        subprocess.run(["cscript", "//nologo", vbs], check=True)
        print(f"桌面快捷方式已创建 (via VBS)")
        success = True
    except Exception as e:
        print(f"VBS 方式失败: {e}")
    finally:
        try:
            os.remove(vbs)
        except Exception:
            pass

if not success:
    # 方法3: 退而求其次, 放一个 .bat 启动器
    bat = os.path.join(DESKTOP, "PPT2Obsidian.bat")
    with open(bat, "w", encoding="utf-8") as f:
        f.write(f'@echo off\nstart "" "{EXE}"\n')
    print(f"已创建桌面启动脚本: {bat}")
    print("(如需 .lnk 快捷方式, 请手动创建)")
