# -*- coding: utf-8 -*-
"""
文档加载器 —— 统一入口: 按扩展名自动路由解析器。

支持:
  - .pptx / .ppt  → PPTX 解析器 (core.ppt_parser)
  - .pdf          → PDF 解析器   (core.pdf_parser)

对外统一返回 PresentationDoc(页 = slide), 上层无需感知来源格式。
"""
import os

from core.ppt_parser import PresentationDoc


def supported_extensions() -> tuple:
    return (".pptx", ".ppt", ".pdf")


def is_supported(path: str) -> bool:
    return os.path.splitext(path or "")[1].lower() in supported_extensions()


def ingest_document(path: str) -> PresentationDoc:
    """按扩展名解析 path → PresentationDoc。"""
    low = (path or "").lower()
    if low.endswith(".pdf"):
        from core.pdf_parser import parse_pdf
        return parse_pdf(path)
    if low.endswith((".pptx", ".ppt")):
        from core.ppt_parser import parse_pptx
        return parse_pptx(path)
    raise ValueError(
        "不支持的文件格式, 请选择 .pptx / .ppt / .pdf 文件"
    )


# 兼容旧调用名
ingest_pptx = ingest_document
