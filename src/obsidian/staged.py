# -*- coding: utf-8 -*-
"""
三步流水线总结器 (Staged Summarizer)

把"一次请求出全部 JSON"重构为"像人备课一样分步走", 这是让笔记质量超过
"直接把 PPT 粘给大模型" 的核心手段:

  Pass 1 · 大纲规划 (outline)
      只看每页的"标题+少量预览", 让模型先产出一份**对齐课件自身骨架**的章节大纲,
      并给出每节覆盖的页码范围与"这节到底重点教什么"的 focus 提示。
      好处: 主次由模型先想清楚; 生成正文前有"地图", 不会跑偏、不会只跟专业清单走。

  Pass 2 · 分节深写 (per-section deep write)
      按 Pass 1 的页码范围把 PPT 原文切片, **每节单独调用一次模型**, 只写这一节的
      MOC summary + 这一节的概念卡。每节的 context/token 独立且足够 -> 细节写得深、
      不截断、页码对得上、符号不张冠李戴。

  Pass 3 · 校对查漏 (coverage audit)
      把每页预览 + 已产出的概念/章节摘要给模型做一遍"查漏": 找出被漏掉的大段主题,
      并建议缺失的概念间关联。有漏则对该缺失页码范围再补一轮 Pass 2; 关联建议并入 related。

对外: summarize_staged(...) 返回与旧版一致的 NoteBundle(professional_name/sections/concepts/...),
不改变落盘 / GUI / vault merge 层 —— 只是"怎么生成"更好。
"""
import re
from typing import List, Dict, Optional

from core.knowledge_base import Profession
from core.ppt_parser import PresentationDoc
from obsidian.summarizer import (
    NoteBundle, Section, ConceptCard,
    _norm, _kb_context, _extract_json,
    detect_concepts_from, detect_symbols_used,
    os_basename,
)

# ---- 可调参数 ----
MAX_SECTIONS = 7          # Pass1 最多分几节
MIN_SECTIONS = 3
MAX_CONCEPTS_PER_SEC = 5  # 每节最多概念卡(正文深写阶段)
RETRY_PER_CALL = 2        # 每次 chat 解析失败的重试次数(不含首次)
OUT_TOKENS_CAP = 12000


def _per_slide_preview(doc: PresentationDoc, width: int = 110) -> List[Dict]:
    """每页一行: index/title/正文预览/是否仅图。供 Pass1/Pass3 用(轻量, 不含全页大文本)。"""
    out = []
    for s in doc.slides:
        txt = s.to_text()
        body = txt
        # 去掉首行 "# 标题"(避免与 title 重复)
        lines = body.splitlines()
        if lines and lines[0].strip().startswith("#"):
            lines = lines[1:]
        body = "\n".join(lines).strip()
        flat = re.sub(r"\s+", " ", body)
        out.append({
            "index": s.index,
            "title": s.title or "",
            "preview": flat[:width],
            "image_only": (not flat.strip() and (s.image_count or 0) > 0),
            "empty": not flat.strip(),
        })
    return out


_RANGE_RE = re.compile(r"^\s*(\d+)\s*[-~至到]\s*(\d+)\s*$")


def _range_to_indices(range_str: str, total: int) -> List[int]:
    """把 '3-9' / '3,5-9' / '3' 解析成页面 index 列表(1-based, 夹在 1..total)。"""
    indices = set()
    if not range_str:
        return []
    for part in re.split(r"[,\s;，；、]+", str(range_str)):
        part = part.strip()
        if not part:
            continue
        m = _RANGE_RE.match(part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            lo, hi = min(a, b), max(a, b)
            indices.update(range(lo, hi + 1))
        elif part.isdigit():
            indices.add(int(part))
    return sorted(i for i in indices if 1 <= i <= total)


def _indices_cover(indices: List[int], total: int) -> List[int]:
    """确保页码范围不重叠(线性扫描去重并排序)。"""
    return sorted(set(indices))


# ---------------- Prompt 片段 ----------------

OUTLINE_SYSTEM = """你是一位严谨的{prof_name}课程大纲规划师。你只会拿到课件每一页的【标题 + 一段很短的预览】,
请据此把这份课件**按它自身的目录/逻辑骨架**切分成几大节, 并标出每一节大致覆盖哪些页、以及这一节到底重点在讲什么。

## 规则
- 章节要覆盖课件从头到尾的各大块内容, 不要漏掉任何大段主题; 也不要把过渡页/复习页硬凑成一个重点节。
- 章节数控制在 {MIN}~{MAX} 节。
- 每节给出 pages(本节大致覆盖的页码范围, 用 '3-9' 或 '3,10-12' 这种, 1-based), 用于后续按页切片。
- pages 可重叠一个边界页(例如上一节 '1-6'、下一节 '6-12'), 重叠页允许, 我们会去重。
- 【专业核心概念清单】只是命名词典, 不是切节依据; 课件是唯一依据。

## 输出(严格 JSON, 不要 markdown 围栏)
{{
  "title": "本讲/本章主标题(简短, 不超过20字)",
  "sections": [
    {{"heading": "节标题", "pages": "3-9", "focus": "这一节重点讲什么(一句, 供后续深写锚定)"}}
  ]
}}
"""

OUTLINE_USER = """===专业范围/术语词典(仅命名参考)===
{kb}

===课件每页预览(标题+片段)===
{preview}

请输出大纲 JSON。
"""

# 分节深写的 system(比整体版更聚焦: 只针对你拿到的这一批页)
SECTION_SYSTEM = """你是一位严谨的{prof_name}笔记整理助手。你现在要处理的是课件中【某一节】的页面原文,
请只基于这些页面, 产出:
 1) 这一节的精炼 summary(80~160字, 讲清这节内容的逻辑关系);
 2) 这一节真正讲透的核心概念卡(不多于 {cap} 张), 概念命名遵循下方"命名纪律"。

## 命名纪律(仅约束"用什么词命名", 不决定"整理什么")
- PPT 讲到的内容若正好对应【本专业核心概念清单】的规范术语, 就用规范术语命名;
- 清单里没有、但本节页面确在重点讲的, 就用 PPT 自己的准确原词命名, 不要硬套清单词;
- 清单里某词仅被顺带一提、本节并未讲透的, 不要为它开卡。
- 概念名必须是**单一概念**; 不要把两个并列概念并成一张卡(如"急性实验与慢性实验/吸收与分布")。
  并列的多个概念应分别开卡; 若是复习页把一串旧概念复述出来, 不要为它们重复开卡, 放到 summary 即可。

## 内容纪律
- definition 必须照抄/转述本节 PPT 里的准确表述, 一句话精确定义。
- detail 写得饱满、有逻辑: 关键点/分类/例子/与相关概念的因果联系, 100~280字, 允许用 markdown 列表。
- source_pages 填这些概念实际出自的页(如 '4-5'), 必须真实, 不要硬编页码。
- evidence: 从本节 PPT 原文里**逐字摘抄** 1~2 句最能支撑该概念定义的原始句子(不要改写、不要自己总结), 这是"知识溯源"的依据。
- relations: 表达与其它概念的**语义关系**, target 用规范术语名或 PPT 原词; 关系类型只从六种里选
  (related_to 泛泛相关 / part_of 组成 / depends_on 依赖 / causes 因果 / example_of 举例 / contrasts_with 对比)。
  只有确实讲到的关系才填, 不要硬凑; 泛泛相关可只放 related。
- 只写本节能看到的; 不要臆造本节没讲的内容去"充实"。

## 输出(严格 JSON, 不要 markdown 围栏)
{{
  "summary": "这一节的精炼要点",
  "concepts": [
    {{
      "concept": "概念名",
      "ctype": "概念类型: concept/mechanism/process/fact/formula/example/method 之一(不肯定给 concept)",
      "importance": "0~1 之间的小数, 表示这个概念在本讲的相对重要度",
      "confidence": "0~1 之间的小数, 表示你对这一定义/内容的理解把握",
      "definition": "一句话精确定义",
      "detail": "补充说明(关键点/分类/例子/联系)",
      "related": ["与之相关的其他概念名, 用于双链"],
      "relations": [{{"target": "相关概念名", "relation_type": "related_to|part_of|depends_on|causes|example_of|contrasts_with"}}],
      "evidence": ["支撑定义的PPT原文句(逐字摘抄1~2句)"],
      "symbols": [{{"symbol":"符号/缩写","meaning":"含义"}}],
      "source_pages": "页码"
    }}
  ],
  "misconceptions": [
    {{"misconception":"本节出现的常见误解/易错表述","clarification":"正确理解"}}
  ]
}}
"""
SECTION_USER = """本节标题:{heading}
本节重点提示: {focus}

===专业范围/术语词典(仅命名参考)===
{kb}

本地检测到本批页面提到这些本专业概念(仅命名参考, 不代表都必须开卡): {hits}
可能出现过的符号(参考): {symbols_hint}

===本节 PPT 原文(第 {pages} 页)开始===
{section_text}
===本节 PPT 原文结束===

请只基于上述页面, 输出本节的 summary 与概念卡 JSON。
"""

# 校对查漏
AUDIT_SYSTEM = """你是一位严谨的{prof_name}课件校对者。你会拿到: 课件每一页的【标题+短预览】,
以及已经整理出来的章节摘要和概念卡标题。请做两件事:

1) **查漏**: 找出课件里被明显遗漏的"大段主题"——即某几页明显在围绕一个主题展开, 但最终
   笔记的章节摘要和概念卡都没有覆盖到它。过渡页/复习页/纯图页不算漏。返回它的页码范围与主题。
2) **关联建议**: 找出产出概念之间本该建立、但还没有的双链(两两相关)。

## 输出(严格 JSON, 不要 markdown 围栏)
{{
  "missing_themes": [
    {{"pages": "12-16", "topic": "被漏掉的主题(一句话)", "hint": "从预览看这一块主要在讲什么"}}
  ],
  "suggested_links": [
    ["概念A", "概念B", "为何相关(一句话)"]
  ]
}}
若没有遗漏或建议, missing_themes / suggested_links 给空数组。
"""
AUDIT_USER = """===课件每页预览===
{preview}

===已整理出的章节摘要===
{section_summaries}

===已产出的概念卡标题===
{concept_titles}

请校对查漏并输出 JSON。
"""


def _fill(tmpl, **kw):
    out = tmpl
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out


# ---------------- 单次调用 + 重试 ----------------

def _chat_json(llm, system: str, user: str, progress=None,
               stage="", max_tokens: int = 6000) -> Optional[Dict]:
    """一次 chat 并稳健取 JSON; 解析失败重试 RETRY_PER_CALL 次。"""
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    last = ""
    for attempt in range(RETRY_PER_CALL + 1):
        try:
            if progress and stage:
                progress(f"正在{stage}…" + (f"(第{attempt + 1}次)" if attempt else ""))
            raw = llm.chat(messages, max_tokens=max_tokens)
            if not raw or not raw.strip():
                last = "空输出"
                break
            data = _extract_json(raw)
            if data is not None:
                return data
            last = "解析失败(疑似截断/半截)"
            messages = messages + [
                {"role": "assistant", "content": raw},
                {"role": "user",
                 "content": "上面的输出不完整或不是合法 JSON。请【直接】重新输出一份完整、可解析的 JSON(不要再重复上面，不要任何解释或代码块围栏)。"},
            ]
        except Exception as e:  # 网络/限额等
            last = str(e)
            if progress and stage:
                progress(f"  {stage}调用出错({e})")
            break
    if progress and stage:
        progress(f"  {stage}未产出可用结果({last or '无'})")
    return None


# ---------------- Pass 1: 大纲 ----------------

def _stage_outline(doc, prof, kb_ctx, llm, progress) -> Optional[Dict]:
    previews = _per_slide_preview(doc)
    pv_txt = "\n".join(
        f"[第{p['index']}页]{(' ' + p['title']) if p['title'] else ''}"
        + ("" if p["image_only"] or p["empty"] else f" — {p['preview']}")
        + ((" [仅图/需视觉]" if p["image_only"] else "") if (p["image_only"]) else "")
        for p in previews
    )
    sys_msg = _fill(OUTLINE_SYSTEM, prof_name=prof.name,
                    MIN=MIN_SECTIONS, MAX=MAX_SECTIONS)
    usr = _fill(OUTLINE_USER, kb=kb_ctx, preview=pv_txt)
    return _chat_json(llm, sys_msg, usr, progress, stage="Pass1 规划大纲", max_tokens=4000)


# ---------------- Pass 2: 分节深写 ----------------

def _slice_by_indices(doc, indices: List[int]) -> str:
    """取属于 indices 页的 slide 文本, 拼成一段。"""
    want = set(indices)
    parts = []
    for s in doc.slides:
        if s.index in want:
            parts.append(f"第 {s.index} 页:")
            parts.append(s.to_text())
    return "\n".join(parts)


def _stage_write_section(doc, prof, kb_ctx, concepts_used, symbols_used,
                         sec, llm, progress) -> Optional[Dict]:
    """对一个大纲节, 深写 summary+concept 卡。sec: {heading, pages, focus}。"""
    indices = _range_to_indices(sec.get("pages", ""), doc.total_pages)
    if not indices:
        return None
    section_text = _slice_by_indices(doc, indices)
    if not section_text.strip():
        return None
    hits = detect_concepts_from([c["name"] for c in prof.core_concepts], section_text)
    syms = detect_symbols_used(section_text, prof)
    sys_msg = _fill(SECTION_SYSTEM, prof_name=prof.name, cap=MAX_CONCEPTS_PER_SEC)
    usr = _fill(SECTION_USER,
                heading=sec.get("heading", ""),
                focus=sec.get("focus", ""),
                kb=kb_ctx,
                hits="、".join(hits) or "(未命中清单概念)",
                symbols_hint="、".join(f"{s['symbol']}({s['meaning']})" for s in syms) or "(无)",
                pages=f"{indices[0]}-{indices[-1]}",
                section_text=section_text)
    return _chat_json(llm, sys_msg, usr, progress,
                      stage=f"Pass2 深写「{sec.get('heading','')}」", max_tokens=OUT_TOKENS_CAP)


# ---------------- 聚合去重 ----------------

_KNOWN_CTYPES = {"concept", "mechanism", "process", "fact", "formula", "example", "method"}
_REL_TYPES = {"related_to", "part_of", "depends_on", "causes", "example_of", "contrasts_with"}


def _to_float(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _card_from_dict(d) -> Optional[ConceptCard]:
    """把 LLM 输出的一节概念 JSON 解析成 ConceptCard(容错: 缺字段给默认, 类型不合法归位)。"""
    if not isinstance(d, dict):
        return None
    nm = str(d.get("concept", "")).strip()
    if not nm:
        return None
    ctype = str(d.get("ctype", "")).strip().lower()
    if ctype not in _KNOWN_CTYPES:
        ctype = "concept"
    evidence = []
    for e in (d.get("evidence") or []):
        s = str(e).strip().strip("\"'")
        if s and s not in evidence:
            evidence.append(s)
    relations, seen_r = [], set()
    for r in (d.get("relations") or []):
        if not isinstance(r, dict):
            continue
        t = str(r.get("target", "")).strip()
        rt = str(r.get("relation_type", "")).strip().lower()
        if t and rt in _REL_TYPES:
            key = (_norm(t), rt)
            if key not in seen_r:
                seen_r.add(key)
                relations.append({"target": t, "relation_type": rt})
    return ConceptCard(
        concept=nm,
        definition=str(d.get("definition", "")).strip(),
        detail=str(d.get("detail", "")).strip(),
        related=[str(x).strip() for x in (d.get("related") or []) if str(x).strip()],
        symbols=d.get("symbols") or [],
        source_pages=str(d.get("source_pages", "")).strip(),
        ctype=ctype,
        importance=_to_float(d.get("importance"), 0.0),
        confidence=_to_float(d.get("confidence"), 0.0),
        evidence=evidence,
        relations=relations,
    )


def _merge_concept_into(cards: List[ConceptCard], c: ConceptCard) -> None:
    """按规范名把一张新卡并入列表(同概念合并 related/symbols/evidence/relations/分数)。"""
    key = _norm(c.concept)
    for ex in cards:
        if _norm(ex.concept) == key:
            for r in c.related:
                if r and _norm(r) != key and r not in ex.related:
                    ex.related.append(r)
            seen = {_norm(x.get("symbol")) for x in ex.symbols if isinstance(x, dict)}
            for s in c.symbols:
                if isinstance(s, dict) and _norm(s.get("symbol")) not in seen:
                    ex.symbols.append(s)
            # 证据原文: 追加去重(跨节重复页的证据不重复)
            for e in c.evidence:
                if e not in ex.evidence:
                    ex.evidence.append(e)
            # 语义关系: 合并去重(target+type)
            seen_r = {(_norm(r.get("target")), r.get("relation_type")) for r in ex.relations}
            for r in c.relations:
                rk = (_norm(r.get("target")), r.get("relation_type"))
                if rk not in seen_r:
                    seen_r.add(rk)
                    ex.relations.append(r)
            # 分数取高; ctype 冲突取"先到"的非默认值
            if c.ctype != "concept" and ex.ctype == "concept":
                ex.ctype = c.ctype
            ex.importance = max(ex.importance, c.importance)
            ex.confidence = max(ex.confidence, c.confidence)
            # detail 若新卡更充实则保留更长的; definition 若空则补
            if not ex.definition and c.definition:
                ex.definition = c.definition
            if len(c.detail or "") > len(ex.detail or ""):
                ex.detail = c.detail or ex.detail
            if c.source_pages and c.source_pages not in (ex.source_pages or ""):
                ex.source_pages = (ex.source_pages + "," + c.source_pages).strip(",")
            return
    cards.append(c)


def _dedupe_links(links):
    out = []
    for l in links:
        if isinstance(l, (list, tuple)) and len(l) >= 2 and l not in out:
            out.append(list(l[:3]))
    return out


def _drop_echo_combined_cards(cards: List[ConceptCard]) -> List[ConceptCard]:
    """清理"复述式"合并卡: 复习/总结页常把已讲过的一串概念用顿号并成一个卡(如
    '急性实验、在体实验、离体实验、慢性实验'), 制造图谱里的脏节点。
    若某卡的名称含顿号等分隔符、且其中任一成员已作为独立概念卡存在, 则丢弃整张并合卡
    (独立卡之间本可互相链接, 不需要一张 4 合 1 卡)。全部成员都是新词时才保留。
    """
    existing = {_norm(c.concept) for c in cards}
    kept = []
    for c in cards:
        parts = [p.strip() for p in re.split(r"[、,，/／]", c.concept) if p.strip()]
        if len(parts) > 1 and any(_norm(p) and _norm(p) in existing and _norm(p) != _norm(c.concept) for p in parts):
            continue  # 这是把已存在概念复述在一起的回声卡
        kept.append(c)
    return kept


# 带捕获组：sub 去掉整段括号，findall 取括号内文
_PAREN_RE = re.compile(r"[（(]([^（）()]*)[）)]")


def _expand_linkish(name) -> List[str]:
    """把可能含"复述串"的链接目标展开为成员列表:
    括号内容按分隔符拆(急性、在体 -> 急性/在体), 括号主体若也是列表则拆, 单个概念原样。
    例如 '实验方法分类（急性、在体、离体、慢性）' -> [实验方法分类,急性,在体,离体,慢性]。
    """
    name = str(name).strip()
    if not name:
        return []
    cands = []
    body = _PAREN_RE.sub("", name).strip()
    body_parts = [p.strip() for p in re.split(r"[、，,；;]", body) if p.strip()]
    body_items = body_parts if len(body_parts) > 1 else ([body] if body else [])
    cands += body_items
    for inner in _PAREN_RE.findall(name):
        parts = [p.strip() for p in re.split(r"[、，,；;]", inner) if p.strip()]
        for p in (parts if len(parts) > 1 else [inner.strip()]):
            if p and p not in cands:
                cands.append(p)
    return cands or [name]


def _normalize_final_cards(cards: List[ConceptCard]) -> List[ConceptCard]:
    """落盘前的最后清洗:
    1) related/relations 里若出现"顿号复述串"(如复习页整行文字被当链接目标), 拆成各成员;
    2) 去掉指向自己的自链与重复链接。
    让渲染出的 [[双链]] 与质量报告都干净。
    """
    for c in cards:
        related = []
        for r in (c.related or []):
            for part in _expand_linkish(r):
                if part and part not in related:
                    related.append(part)
        for r in list(c.relations or []):
            if not isinstance(r, dict):
                continue
            parts = _expand_linkish(r.get("target", ""))
            if len(parts) > 1:
                # 复述串 -> 拆成普通相关, 丢弃这条(无法对串整体标语义类型)
                for p in parts:
                    if p and p not in related:
                        related.append(p)
                c.relations.remove(r)
        out = []
        for r in related:
            if r and _norm(r) != _norm(c.concept) and r not in out:
                out.append(r)
        c.related = out
    return cards


# ---------------- Pass 3: 校对查漏 ----------------

def _stage_audit(doc, prof, sec_bundles, llm, progress) -> Optional[Dict]:
    previews = _per_slide_preview(doc)
    pv_txt = "\n".join(
        f"[第{p['index']}页]{(' ' + p['title']) if p['title'] else ''}"
        + ("" if p["image_only"] or p["empty"] else f" — {p['preview']}")
        for p in previews
    )
    # 章节摘要行
    sec_lines = []
    for heading, sb in sec_bundles:
        summary = (sb or {}).get("summary", "")
        sec_lines.append(f"- {heading}: {summary[:120]}")
    concept_titles = []
    for _h, sb in sec_bundles:
        for c in (sb or {}).get("concepts", []) or []:
            t = str(c.get("concept", "")).strip()
            if t and t not in concept_titles:
                concept_titles.append(t)
    sys_msg = _fill(AUDIT_SYSTEM, prof_name=prof.name)
    usr = _fill(AUDIT_USER,
                preview=pv_txt,
                section_summaries="\n".join(sec_lines) or "(无章节摘要)",
                concept_titles="、".join(concept_titles) or "(无概念卡)")
    return _chat_json(llm, sys_msg, usr, progress, stage="Pass3 校对查漏", max_tokens=3000)


# ---------------- 主流程 ----------------

def summarize_staged(doc: PresentationDoc, prof: Profession, llm,
                     progress=None, extra_context: str = "",
                     extra_concept_names: list = None) -> NoteBundle:
    """三步流水线入口。失败时逐级降级: 在线>大纲仍可用则按页兜底>本地规则。"""
    source_file = os_basename(doc.path)
    kb_ctx = _kb_context(prof)
    if extra_context:
        kb_ctx += "\n" + extra_context

    bundle = NoteBundle(
        professional_name=prof.name,
        source_file=source_file,
        title=doc.name,
        used_llm=False,
    )
    # 失败信息逐级收集, 供降级提示
    reason = ""

    if progress:
        progress("开始三步流水线: 先规划大纲…")

    # ---- Pass 1 ----
    outline = _stage_outline(doc, prof, kb_ctx, llm, progress)
    if not outline or not outline.get("sections"):
        reason = "大纲阶段未能产出结构"
        return _fallback_with(doc, prof, kb_ctx, source_file, reason, progress)

    bundle.title = str(outline.get("title") or doc.name).strip()
    secs = outline["sections"][:MAX_SECTIONS]
    # 转成可读节列表并收集总页码覆盖(去重), 供 Pass3/局部兜底
    planned = []
    for sec in secs:
        heading = str(sec.get("heading", "")).strip() or "未命名节"
        planned.append({
            "heading": heading,
            "pages": str(sec.get("pages", "")).strip(),
            "focus": str(sec.get("focus", "")).strip(),
        })

    # ---- Pass 2 (逐节深写) ----
    sec_bundles = []          # [(heading, dict)]
    all_concepts: List[ConceptCard] = []
    all_mis: List[Dict] = []
    for i, sec in enumerate(planned):
        sb = _stage_write_section(doc, prof, kb_ctx,
                                  [c["name"] for c in prof.core_concepts],
                                  None, sec, llm, progress)
        if sb is None:
            reason = f"分节深写「{sec['heading']}」失败"
            if progress:
                progress(f"  该节未能产出, 跳过(其余继续)")
            continue
        sec_bundles.append((sec["heading"], sb))
        summary = str(sb.get("summary", "")).strip()
        # 本节约有哪些概念 -> 用于 MOC 的"相关概念"双链列表
        sec_concept_names = []
        for c in sb.get("concepts", []) or []:
            if isinstance(c, dict):
                t = str(c.get("concept", "")).strip()
                if t and t not in sec_concept_names:
                    sec_concept_names.append(t)
        bundle.sections.append(Section(
            heading=sec["heading"],
            summary=summary,
            concept_links=sec_concept_names,
            pages=_range_to_indices(sec.get("pages", ""), doc.total_pages),
        ))
        # 概念卡
        for c in sb.get("concepts", []) or []:
            card = _card_from_dict(c)
            if card is not None:
                _merge_concept_into(all_concepts, card)
        for m in sb.get("misconceptions", []) or []:
            if isinstance(m, dict) and str(m.get("misconception", "")).strip():
                all_mis.append({
                    "misconception": str(m.get("misconception", "")).strip(),
                    "clarification": str(m.get("clarification", "")).strip(),
                })

    # 若全部节都失败(极少数), 走大纲+页级兜底
    if not all_concepts and not bundle.sections:
        return _fallback_with(doc, prof, kb_ctx, source_file,
                              reason or "分节深写全部失败", progress)

    # 清理"复述式"合并卡(复习/总结页常把已讲过概念并成一个卡)
    all_concepts = _drop_echo_combined_cards(all_concepts)
    # ---- Pass 3 校对查漏 ----
    audit = None
    if llm is not None:
        audit = _stage_audit(doc, prof, sec_bundles, llm, progress)
    if audit:
        # 1) 对缺失的大段主题补写一节
        missing = audit.get("missing_themes") or []
        for m in missing[:2]:
            pages = str(m.get("pages", "")).strip()
            topic = str(m.get("topic", "")).strip()
            if not pages or not topic:
                continue
            indices = _range_to_indices(pages, doc.total_pages)
            if not indices:
                continue
            # 跳过已被现有节覆盖的页(大致)
            covered = set()
            for s in bundle.sections:
                covered.update(s.pages or [])
            fresh = [i for i in indices if i not in covered]
            if len(fresh) < 2:
                continue
            sec_dummy = {"heading": topic, "pages": pages, "focus": str(m.get("hint", ""))}
            sb = _stage_write_section(doc, prof, kb_ctx,
                                      [c["name"] for c in prof.core_concepts],
                                      None, sec_dummy, llm, progress)
            if sb:
                bundle.sections.append(Section(
                    heading=topic,
                    summary=str(sb.get("summary", "")).strip(),
                    concept_links=[], pages=fresh,
                ))
                for c in sb.get("concepts", []) or []:
                    card = _card_from_dict(c)
                    if card is not None:
                        _merge_concept_into(all_concepts, card)
        # 2) 关联建议并入 related
        for sug in _dedupe_links(audit.get("suggested_links") or []):
            a, b = sug[0], sug[1]
            why = sug[2] if len(sug) > 2 else ""
            for c in all_concepts:
                if _norm(c.concept) == _norm(a) and b not in c.related and _norm(b) != _norm(a):
                    c.related.append(b)
                elif _norm(c.concept) == _norm(b) and a not in c.related:
                    c.related.append(a)

    # 落盘前归一化: 拆掉顿号复述串链接、去自链
    all_concepts = _normalize_final_cards(all_concepts)
    bundle.concepts = all_concepts
    bundle.misconceptions = all_mis
    bundle.used_llm = True
    return bundle


def _fallback_with(doc, prof, kb_ctx, source_file, reason, progress):
    """在线流水线不可用时, 复用 summarizer 的本地规则兜底并写明原因。"""
    from obsidian.summarizer import _local_fallback_bundle
    fulltext = doc.full_text()
    symbols_used = detect_symbols_used(fulltext, prof)
    names = [c.get("name", "") for c in prof.core_concepts if c.get("name")]
    concepts_used = detect_concepts_from(names, fulltext)
    fb = _local_fallback_bundle(doc, prof, symbols_used, concepts_used)
    fb.warnings.append(f"在线三步流水线未能产出({reason or '未知'}), 已退回本地规则模式。")
    return fb
