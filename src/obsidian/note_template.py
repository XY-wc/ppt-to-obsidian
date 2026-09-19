# -*- coding: utf-8 -*-
"""
Obsidian 笔记生成器 —— 把"总结结果"渲染成真正符合 Obsidian 特点的笔记。

核心理念: 不做"一次性的长篇总结文档", 而是产出一组**可生长、可链接、可检索**的
Obsidian 原生内容:

  A. 每份PPT对应:
      1) 一个 MOC (Map of Content) 首页 —— 汇总本讲框架, 用 [[wikilink]] 链接到原子笔记。
      2) 若干张"原子笔记"(atomic note) —— 每个核心知识点一张, 便于双向链接/复用。
      3) 一张"易错点"卡片 —— 依据专业知识库 common_misconceptions 校准。

  这样后续用户把多份 PPT 转进同一 vault, Obsidian 图谱会自动把重复概念连起来,
  形成跨课程的"概念网络" —— 这是直接与大模型对话给不出的增量价值。

渲染细节(Obsidian 特有):
  - 每张笔记带 YAML frontmatter (tags / source / professional / created / course)。
  - 概念之间用 [[双链]], 首次出现标记为待建(红色即灰色链接), 触发生长。
  - 符号用 (symbol) 保留原样但加注释, 兼顾可读与可还原。
  - 支持 Dataview 可解析的前置字段。
"""
import re
from datetime import datetime
from typing import List, Dict, Optional


def slugify(name: str) -> str:
    """文件名安全化: 中文保留, 去掉非法字符。"""
    s = re.sub(r'[\\/:*?"<>|\r\n]+', "_", str(name)).strip()
    return s[:80] or "note"


def build_frontmatter(title: str, tags: List[str], professional: str,
                      source_file: str, source_pages: str = "", extra: Optional[Dict] = None) -> str:
    quoted = ", ".join('"' + t + '"' for t in tags)
    lines = ["---"]
    lines.append("title: \"%s\"" % title)
    lines.append("tags: [%s]" % quoted)
    lines.append("professional: \"%s\"" % professional)
    lines.append(f"source: \"{source_file}\"")
    if source_pages:
        lines.append(f"source_pages: \"{source_pages}\"")
    lines.append(f"created: {datetime.now().strftime('%Y-%m-%d')}")
    for k, v in (extra or {}).items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines)


def build_moc_note(
    moc_title: str,
    professional: str,
    source_file: str,
    sections: List[Dict],      # [{heading, summary, concept_links:[...] , pages}]
    misconceptions: List[Dict] = None,
    source_pages: str = "",
    tags_extra: List[str] = None,
) -> str:
    """构建 MOC 首页 markdown。sections: 结构化章节。"""
    # "层级/目录" 供 Obsidian 关系图按层级上色(见 obsidian/graph_config.py)
    tags = [professional, "MOC", "层级/目录"] + (tags_extra or [])
    body = [build_frontmatter(moc_title, tags, professional, source_file, source_pages)]
    body.append("")
    body.append(f"> [!abstract] {professional} | 由《{source_file}》自动整理")
    body.append("")
    body.append("## 🗺️ 本讲框架")
    body.append("")

    for sec in sections:
        head = sec.get("heading", "章节")
        summary = sec.get("summary", "")
        body.append(f"### {head}")
        if summary:
            body.append(summary.strip())
            body.append("")
        links = sec.get("concept_links", [])
        if links:
            body.append("相关概念:")
            for link in links:
                # 统一渲染为 wikilink
                body.append(f"- [[{link}]]")
            body.append("")

    if misconceptions:
        body.append("## ⚠️ 本讲易错点")
        body.append("")
        for m in misconceptions:
            body.append(f"- **{m.get('misconception','')}** — {m.get('clarification','')}")
            body.append("")

    body.append("## 🔗 关联")
    body.append("")
    body.append("```dataview")
    body.append("LIST WHERE contains(tags, \"" + professional + "\") AND file.name != this.file.name")
    body.append("```")
    body.append("")
    return "\n".join(body)


def build_atomic_note(
    concept: str,
    definition: str,
    professional: str,
    source_file: str,
    source_pages: str,
    tags: List[str] = None,
    detail: str = "",
    backlinks: List[str] = None,     # 关联概念(渲染为双链)
    symbols: List[Dict] = None,      # 本概念用到的符号 {symbol, meaning}
    # ---- 知识 IR 增强(可溯源/可量化) ----
    ctype: str = "",
    importance: float = 0.0,
    confidence: float = 0.0,
    evidence: List[str] = None,      # 支撑定义的 PPT 原文句
) -> str:
    """构建一张原子概念笔记。"""
    # "层级/概念" 供 Obsidian 关系图按层级上色(见 obsidian/graph_config.py)
    all_tags = [professional, "层级/概念"] + (tags or [])
    extra = {}
    if ctype:
        extra["knowledge_type"] = '"%s"' % ctype
    if importance > 0:
        extra["importance"] = round(importance, 3)
    if confidence > 0:
        extra["confidence"] = round(confidence, 3)
    body = [build_frontmatter(concept, all_tags, professional, source_file, source_pages, extra)]
    body.append("")
    body.append(f"# {concept}")
    body.append("")
    body.append(f"> [!quote] 定义")
    body.append(f"> {definition}")
    body.append("")
    if evidence:
        loc = f" · PPT 第 {source_pages} 页" if source_pages else ""
        body.append(f"> [!example]- 📎 证据{loc}")
        for e in evidence:
            line = str(e).strip()
            if line:
                body.append(f"> “{line}”")
        body.append("")
    if symbols:
        body.append("> [!info]- 涉及符号")
        body.append("> ")
        for s in symbols:
            body.append(f"> `{s['symbol']}` — {s['meaning']}")
        body.append("")
    if detail:
        body.append("## 📝 详细说明")
        body.append("")
        body.append(detail.strip())
        body.append("")
    if backlinks:
        body.append("## 🔗 相关概念")
        body.append("")
        for b in backlinks:
            body.append(f"- [[{b}]]")
        body.append("")
    body.append(f"## 来源")
    body.append(f"- PPT: 《{source_file}》 {('('+source_pages+')') if source_pages else ''}")
    body.append("")
    return "\n".join(body)


def build_misconception_note(
    title: str, professional: str, source_file: str,
    misconceptions: List[Dict],
) -> str:
    """构建易错点卡片(汇总本PPT命中的易错点)。"""
    # "层级/附录" 供 Obsidian 关系图按层级上色(见 obsidian/graph_config.py)
    tags = [professional, "易错点", "层级/附录"]
    body = [build_frontmatter(title, tags, professional, source_file)]
    body.append("")
    body.append(f"# ⚠️ 易错点 | {professional}")
    body.append("")
    for m in misconceptions:
        body.append(f"### ❌ {m.get('misconception','')}")
        body.append("")
        body.append(f"✅ **正解**: {m.get('clarification','')}")
        body.append("")
    return "\n".join(body)


def build_cover_notes_text(concept: str, backlinks: List[str] = None) -> str:
    """为尚未建立的 wikilink 提供占位笔记正文(可选, 建立"空笔记"以激活链接)。"""
    body = [f"# {concept}", "", "> 本概念在后续课程中会逐步补充。", ""]
    if backlinks:
        body.append("相关概念:")
        for b in backlinks:
            body.append(f"- [[{b}]]")
    return "\n".join(body)
