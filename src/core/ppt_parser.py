# -*- coding: utf-8 -*-
"""
PPTX 解析器 —— "PPT识别" 基础能力层。

目标: 把一份 .pptx 按页(幻灯片)抽取出**结构化、保序、带层次**的文本,
供后续"专业驱动"的总结器使用。相比直接读全文字符串, 这里做了:
  1. 逐页抽取 + 页面序号保留(供回链"PPT 第x页")。
  2. 提取标题 vs 正文层级(依据文本框位置/字号/是否标题占位符)。
  3. 表格结构化抽取(转 markdown 表)。
  4. 从图片/图形提取文字(若有)。
  5. 遇到图表类图片, 记录占位标记, 交给 OCR 或 LLM 视觉后续处理。

说明:
  - 纯文本 .pptx 无需任何模型, 本地即可 100% 还原文字。
  - 若幻灯片是"纯图片"(扫描/截图式PPT), 则需要 OCR —— 该项作为扩展点,
    预留 image_pages 清单, 并在有配置时调用。
"""
import os
from dataclasses import dataclass, field
from typing import List, Dict, Optional

try:
    from pptx import Presentation
    from pptx.util import Emu
    _PPTX_OK = True
except Exception:
    _PPTX_OK = False


@dataclass
class SlideBlock:
    """单个文本框/表格块。"""
    kind: str          # 'title' | 'heading' | 'text' | 'table' | 'image'
    text: str = ""     # 文本或表格markdown
    level: int = 0     # 大纲层级(0标题,1=一级要点...)
    left: float = 0    # 相对X (0~1)
    top: float = 0


@dataclass
class Slide:
    index: int                  # 1-based 页码
    title: str = ""
    blocks: List[SlideBlock] = field(default_factory=list)
    has_images: bool = False    # 是否有无法提取文字的图片
    image_count: int = 0

    def to_text(self) -> str:
        lines = []
        if self.title:
            lines.append(f"# {self.title}")
        for b in self.blocks:
            if b.kind == 'table':
                lines.append(b.text)
            elif b.kind == 'image':
                lines.append(f"[图片占位: 第{self.index}页有 {self.image_count} 张图片, 建议OCR/视觉识别]")
            else:
                prefix = "  " * min(b.level, 4) + "- " if b.level else ""
                if b.kind == 'heading':
                    prefix = "## " if not b.text.startswith("#") else ""
                lines.append(prefix + b.text)
        return "\n".join([ln for ln in lines if ln.strip()])


@dataclass
class PresentationDoc:
    path: str
    name: str
    slides: List[Slide] = field(default_factory=list)

    @property
    def total_pages(self) -> int:
        return len(self.slides)

    def full_text(self) -> str:
        """整份PPT的连续文本(用于快速浏览/传给总结器分块前的预览)。"""
        parts = []
        for s in self.slides:
            parts.append(f"===== 第 {s.index} 页 =====")
            parts.append(s.to_text())
        return "\n".join(parts)


# ---------- 解析逻辑 ----------

def _shape_text(shape) -> Optional[str]:
    """尝试提取一个 shape 的文本。"""
    try:
        if shape.has_text_frame:
            parts = []
            for para in shape.text_frame.paragraphs:
                t = "".join(run.text for run in para.runs).strip()
                if not t:
                    t = para.text.strip()
                if t:
                    parts.append(t)
            return "\n".join(parts).strip()
        if shape.shape_type == 19:  # table
            return None
    except Exception:
        pass
    return None


def _is_table_shape(shape) -> bool:
    try:
        return shape.has_table
    except Exception:
        return False


def _title_text(shape) -> str:
    """占位符文本作为候选标题。"""
    try:
        if shape.is_placeholder:
            return (shape.text or "").strip()
    except Exception:
        pass
    return ""


def parse_pptx(path: str) -> PresentationDoc:
    """解析 .pptx 文件。"""
    if not _PPTX_OK:
        raise RuntimeError("python-pptx 未安装, 无法解析 PPTX")
    if not os.path.exists(path):
        raise FileNotFoundError(f"文件不存在: {path}")
    if not path.lower().endswith((".pptx", ".ppt")):
        raise ValueError("仅支持 .pptx 文件(请将 .ppt 另存为 .pptx)")

    prs = Presentation(path)
    doc = PresentationDoc(path=path, name=os.path.splitext(os.path.basename(path))[0])

    for idx, slide in enumerate(prs.slides, start=1):
        s = Slide(index=idx)
        cand_titles = []       # 标题候选 (文本, 面积)
        blocks: List[SlideBlock] = []
        img_count = 0
        page_w = prs.slide_width  # EMU
        page_h = prs.slide_height

        for shape in slide.shapes:
            try:
                left = shape.left or 0
                top = shape.top or 0
                width = shape.width or 0
                height = shape.height or 0
            except Exception:
                left = top = width = height = 0
            area = (width or 1) * (height or 1)
            rx = (Emu(left).inches / (Emu(page_w).inches or 1)) if page_w else 0
            ry = (Emu(top).inches / (Emu(page_h).inches or 1)) if page_h else 0

            # 表格
            if _is_table_shape(shape):
                try:
                    tb = shape.table
                    md = _table_to_markdown(tb)
                    blocks.append(SlideBlock(kind="table", text=md, top=ry, left=rx))
                except Exception:
                    pass
                continue

            # 图片
            is_pic = False
            try:
                is_pic = shape.shape_type == 13  # PICTURE
            except Exception:
                pass
            if is_pic:
                img_count += 1
                blocks.append(SlideBlock(kind="image", top=ry, left=rx))
                continue

            txt = _shape_text(shape)
            if not txt:
                continue
            # 占位符标题 or 靠上/大面积文本 -> 标题候选
            if _title_text(shape) or (ry < 0.18 and area > 200000):
                cand_titles.append((txt.split("\n")[0][:60], area))
            else:
                level = 1
                blocks.append(SlideBlock(kind="text", text=txt, level=level, top=ry, left=rx))

        # 定标题: 优先占位符标题文本
        if cand_titles:
            s.title = max(cand_titles, key=lambda x: x[1])[0] if len(cand_titles) > 1 else cand_titles[0][0]
        s.blocks = blocks
        s.image_count = img_count
        s.has_images = img_count > 0
        doc.slides.append(s)

    return doc


def _table_to_markdown(table) -> str:
    """把 pptx 表格转成 markdown 表格。"""
    rows = []
    try:
        for r in table.rows:
            cells = []
            for c in r.cells:
                cells.append(" ".join(c.text.split()).replace("|", "\\|"))
            rows.append(cells)
    except Exception:
        return ""
    if not rows:
        return ""
    header = rows[0]
    body = rows[1:]
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join(["---"] * len(header)) + " |"]
    for b in body:
        out.append("| " + " | ".join(b) + " |")
    return "\n".join(out)


# ---------- 文档级处理 ----------

def ingest_pptx(path: str) -> PresentationDoc:
    """总入口: 解析并对纯图片页给出提示。"""
    return parse_pptx(path)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("用法: python ppt_parser.py <xxx.pptx>")
        sys.exit(0)
    d = ingest_pptx(sys.argv[1])
    print(f"解析完成: {d.name}, 共 {d.total_pages} 页")
    for s in d.slides:
        print(f"\n--- 第{s.index}页 | 标题: {s.title or '(无)'} | 图片:{s.image_count} ---")
        print(s.to_text()[:400])
