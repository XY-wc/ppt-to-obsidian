#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PPT→Obsidian 应用启动器(推荐入口)。用法: python run.py"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

def main():
    os.chdir(_HERE)
    from gui.app_v3 import App
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()
