# -*- coding: utf-8 -*-
"""
PDF 解析器 —— 让 "PPT→Obsidian 原子笔记" 同样接受 **PDF 课件**。

背景: 用户课件除 .pptx 外, 还常以 PDF 形式分发(尤其是打印导出版 / 排版稿)。
本模块把一份 PDF 按「页」抽取成与 PPTX 解析器**同构**的结构(PresentationDoc),
上层 sumarizer / pipeline / vault_aware 无需区分来源, 一套流程通吃。

处理策略:
  1. 每一 PDF 页 = 一个 Slide(page)。这与"把 PPT 导成 PDF 后一页一屏"的课件高度契合;
     对普通文档 PDF 则退化为"逐页切片", 仍是合理的最小原子单元。
  2. 用 PyMuPDF(fitz) 的 dict 模式取回**带字号 + 坐标**的文本 spans,
     据此做: 标题识别(页面顶部最大字号) + 正文分层(≥ 主体字号阈值判为小标题)。
  3. 纯图片页(扫描件)无文字 → 记录 image 占位, 提示后续 OCR/视觉。

依赖: PyMuPDF (pymupdf)。若未安装会抛出带 pip 提示的 RuntimeError。
"""
import os
from typing import List, Optional

try:
    import fitz  # PyMuPDF
    _PDF_OK = True
except Exception:
    _PDF_OK = False

from core.ppt_parser import PresentationDoc, Slide, SlideBlock


# 页面上部多少比例算"标题区" (相对页高)
_TITLE_ZONE = 0.22
# 相对基准字号达到该倍数才判定为"小标题"(避免正文误判)
_HEADING_SIZE_RATIO = 1.13
# 粗体辅助判题: 需同时满足"短行(更像标题)"才加分, 防止正文加粗整段误判
_HEADING_BOLD_MAX_LEN = 40


def _block_kind(base_size, size, is_bold, text_len):
    """把一行文本归类为 heading / text(标题已单独抽出)。

    规则: 字号明显大于正文基准 → 标题; 否则仅当"短行 + 粗体 + 略大"时也算标题;
    一律不因纯粗体把整段正文升成标题。
    """
    if size >= base_size * _HEADING_SIZE_RATIO:
        return "heading"
    if is_bold and text_len <= _HEADING_BOLD_MAX_LEN and size >= base_size * 1.02:
        return "heading"
    return "text"


def _available() -> bool:
    return _PDF_OK


def _ensure_deps():
    if not _PDF_OK:
        raise RuntimeError(
            "解析 PDF 需要 PyMuPDF。请先安装:  pip install pymupdf"
        )


def _extract_line_spans(page, block):
    """把 PyMuPDF 的一个文本 block 压平成 (y, x, size, text, flags) 的线列表。

    返回按阅读顺序(先 y 再 x)排序的行, 每行含最大字号, 以便判断标题级别。
    """
    lines = []
    for line in block.get("lines", []):
        spans = line.get("spans", [])
        if not spans:
            continue
        # 同一行可能多 span, 合并文本并按该行最大字号为准
        text = ""
        max_size = 0.0
        min_x = min((s.get("origin", (1e9, 0))[0]) for s in spans)
        for s in spans:
            text += s.get("text", "") or ""
            max_size = max(max_size, s.get("size", 0) or 0)
        y = line.get("bbox", [0, 0, 0, 0])[1]
        if not text.strip():
            continue
        # PyMuPDF flags 位掩码: bit 4(值16)=粗体
        try:
            is_bold = bool((spans[0].get("flags", 0) or 0) & 16)
        except Exception:
            is_bold = False
        lines.append((y, min_x, max_size, text, is_bold))
    # 稳定的阅读顺序: 先 y, 后 x
    lines.sort(key=lambda t: (round(t[0], 1), t[1]))
    return lines


def _page_to_slide(page, index: int, page_h: float) -> Slide:
    """把一个 PDF 页转成 Slide。"""
    s = Slide(index=index)
    text_blocks = []

    try:
        d = page.get_text("dict")
    except Exception:
        d = {}

    # 收集所有 span, 计算正文基准字号(取出现最多的字号) & 收集行
    size_counts = {}
    block_lines = []  # (y, x, size, text, is_bold)
    for b in d.get("blocks", []):
        if b.get("type", 0) != 0:   # 非文本(如图片)跳过, 图片另行统计
            continue
        lines = _extract_line_spans(page, b)
        for (y, x, size, text, is_bold) in lines:
            block_lines.append((y, x, size, text, is_bold))
            size_counts[size] = size_counts.get(size, 0) + len(text)

    if not block_lines:
        # 纯图片/扫描页
        s.has_images = True
        s.image_count = 1
        s.blocks.append(SlideBlock(kind="image"))
        return s

    # 主体字号: 最常见的字号作为正文基准
    base_size = max(size_counts, key=size_counts.get) or 14.0
    # 图片块统计
    try:
        img_count = len([b for b in d.get("blocks", [])
                         if b.get("type", 0) == 1 or b.get("image", None)])
    except Exception:
        img_count = 0
    if img_count:
        s.has_images = True
        s.image_count = img_count

    # 按阅读顺序排列已排序的 block_lines(已经 y,x 排序过)
    block_lines.sort(key=lambda t: (round(t[0], 1), t[1]))

    # 标题: 页面顶部、字号显著大于正文 或 首行
    title_found = False
    first_title = ""
    for (y, x, size, text, is_bold) in block_lines:
        is_top = y < page_h * _TITLE_ZONE if page_h else True
        if is_top and (size > base_size * 1.25 or (size > base_size and is_bold)):
            candidate = text.strip()
            # 标题通常较短, 避免把整段顶部小字当标题
            if len(candidate) <= 80:
                first_title = first_title or candidate
                title_found = True
                break
    if first_title:
        s.title = first_title[:60]

    # 组装块: 跳过已被当作标题的文本(仅跳过标题行文本本身)
    skip_first_line_match = first_title
    for (y, x, size, text, is_bold) in block_lines:
        stripped = text.strip()
        if not stripped:
            continue
        kind = _block_kind(base_size, size, is_bold, len(stripped))
        # 标题行: 只呈现一次, 放进 slide.title, 正文里不再重复
        if title_found and stripped == skip_first_line_match:
            continue
        if kind == "heading":
            s.blocks.append(SlideBlock(kind="heading", text=stripped,
                                       left=x, top=y / page_h if page_h else 0))
        else:
            s.blocks.append(SlideBlock(kind="text", text=stripped, level=1,
                                       left=x, top=y / page_h if page_h else 0))
    return s


def parse_pdf(path: str) -> PresentationDoc:
    """解析 .pdf 文件, 返回与 PPTX 同构的 PresentationDoc。"""
    _ensure_deps()
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在: {path}")
    if not path.lower().endswith(".pdf"):
        raise ValueError("仅支持 .pdf 文件")

    doc = fitz.open(path)
    try:
        pdoc = PresentationDoc(path=path,
                               name=os.path.splitext(os.path.basename(path))[0])
        for idx in range(len(doc)):
            page = doc[idx]
            page_h = page.rect.height or 0
            pdoc.slides.append(_page_to_slide(page, idx + 1, page_h))
        return pdoc
    finally:
        doc.close()


def ingest_document(path: str) -> PresentationDoc:
    """总入口: 按扩展名自动选择解析器(.pptx/.ppt → pptx; .pdf → pdf)。"""
    low = path.lower()
    if low.endswith(".pdf"):
        return parse_pdf(path)
    from core import ppt_parser
    return ppt_parser.parse_pptx(path)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python pdf_parser.py <xxx.pdf>")
        sys.exit(0)
    dd = parse_pdf(sys.argv[1])
    print(f"解析完成: {dd.name}, 共 {dd.total_pages} 页")
    for ss in dd.slides:
        print(f"\n--- 第{ss.index}页 | 标题: {ss.title or '(无)'} | 图片:{ss.image_count} ---")
        print(ss.to_text()[:400])
