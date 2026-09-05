#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 assets/icon.png 缩放为多个尺寸并合成 assets/icon.ico(Windows 应用图标)。"""
import os
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "icon.png")
DST = os.path.join(HERE, "icon.ico")

# Windows 实际显示常用尺寸(任务栏 16/24, 资源管理器 32/48, 快捷方式 256)
SIZES = [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (24, 24), (16, 16)]

if not os.path.isfile(SRC):
    raise SystemExit(f"缺少源图: {SRC}")

img = Image.open(SRC)
if img.mode != "RGBA":
    img = img.convert("RGBA")

icons = []
for size in SIZES:
    # LANCZOS 缩放保细节
    im2 = img.resize(size, Image.LANCZOS)
    icons.append(im2)

# Pillow 会自动把不同尺寸打包到一个 .ico
icons[0].save(
    DST,
    format="ICO",
    sizes=[im.size for im in icons],
    append_images=icons[1:],
)
print(f"已生成 {DST} ({os.path.getsize(DST)} bytes), 尺寸: {[im.size for im in icons]}")
