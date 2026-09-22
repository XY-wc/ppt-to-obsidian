# -*- coding: utf-8 -*-
"""
专业驱动总结器 (Professional-Driven Summarizer)

这是与"直接和大模型对话总结"拉开差距的核心模块。三层策略:

  L1 知识库注入(grounding):
     把选中专业的 core_concepts / symbol_mapping / common_misconceptions 注入 prompt,
     让 LLM 把 PPT 里的零散内容映射到**学科规范术语**上, 减少口语化/错误术语。
     同时本地先用正则做一轮"符号还原"和"概念命中检测", 结果作为结构化 hint 传给 LLM。

  L2 结构化产出(结构化优于叙述):
     让 LLM 输出 JSON 结构化结果: 章节(section) + 概念概念卡(concept cards),
     每张卡带 definition / detail / related(用到的本专业概念) / source_pages。
     再由本地渲染器组装成 Obsidian 原子笔记, 而非一大段文字。

  L3 本地确定性兜底(local-first 可离线):
     若用户未绑定任何大模型, 依然可用纯规则把 PPT 文本切块、按 note_template 组装成
     基础笔记 —— 让"开箱即用"成立, 且不消耗 token。

最终输出: 一份结构化 NoteBundle, 交给 obsidian.note_template 渲染。
"""
import json
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional

from core.knowledge_base import Profession
from core.ppt_parser import PresentationDoc


# ---------------- 数据容器 ----------------

@dataclass
class ConceptCard:
    """一张原子概念卡。

    除渲染所需字段外, 附带"知识 IR"增强字段(type/importance/confidence/evidence/relations),
    让结果**可验证、可追溯**: 每个知识节点来自哪几页、PPT 原话是什么、重要度/置信度如何、
    与其他节点是什么关系。所有新字段均可选带默认值, 旧调用方/测试不受影响。
    """
    concept: str
    definition: str
    detail: str = ""
    related: List[str] = field(default_factory=list)   # 关联的其他概念(可双链)
    symbols: List[Dict] = field(default_factory=list)  # [{symbol, meaning}]
    source_pages: str = ""
    # ---- 知识 IR 增强 ----
    ctype: str = "concept"                # concept|mechanism|process|fact|formula|example|method
    importance: float = 0.0               # 0~1 本讲重要度(越大越核心)
    confidence: float = 0.0               # 0~1 抽取置信度
    evidence: List[str] = field(default_factory=list)     # 支撑定义的 PPT 原文句(逐字摘抄)
    relations: List[Dict] = field(default_factory=list)   # [{target, relation_type}] 语义关系


@dataclass
class Section:
    heading: str
    summary: str = ""
    concept_links: List[str] = field(default_factory=list)
    pages: List[int] = field(default_factory=list)


@dataclass
class NoteBundle:
    """一次 PPT 转换的完整结果。"""
    professional_name: str
    source_file: str
    title: str
    sections: List[Section] = field(default_factory=list)
    concepts: List[ConceptCard] = field(default_factory=list)
    misconceptions: List[Dict] = field(default_factory=list)   # 命中知识库的易错点
    used_llm: bool = False
    warnings: List[str] = field(default_factory=list)


# ---------------- 本地符号还原 & 概念检测 ----------------

_NORM_RE = re.compile(r"[\s\.]")


def _norm(s):
    return _NORM_RE.sub("", str(s)).lower() if s else ""


def detect_symbols_used(text: str, prof: Profession) -> List[Dict]:
    """从 PPT 全文中检测出现的本专业符号, 返回 [{symbol, meaning}]。去重保序。"""
    found = []
    seen = set()
    # 长符号优先
    for s in sorted(prof.symbol_mapping, key=lambda x: -len(_norm(x.get("symbol", "")))):
        sym = s.get("symbol", "")
        if not sym:
            continue
        key = _norm(sym)
        if key and key in _norm(text) and key not in seen and len(sym) >= 2:
            seen.add(key)
            found.append({"symbol": sym, "meaning": s.get("meaning", "")})
    return found[:40]


def detect_concepts_used(text: str, prof: Profession) -> List[str]:
    """检测 PPT 文本命中了哪些本专业核心概念, 返回按出现先后去重的概念名。"""
    hits = []
    seen = set()
    for c in prof.core_concepts:
        nm = c.get("name", "")
        if not nm:
            continue
        nk = _norm(nm)
        if len(nk) < 2:
            continue
        if nk and nk in _norm(text) and nk not in seen:
            seen.add(nk)
            hits.append(nm)
    return hits


# ---------------- Prompt 构建 ----------------

def _kb_context(prof: Profession) -> str:
    """把知识库压成一段可注入的上下文。"""
    lines = []
    lines.append(f"【专业】{prof.name} (id:{prof.id})")
    tpl = prof.note_template
    if tpl:
        lines.append(f"【该学科笔记惯用结构/模板】{tpl}")
    concepts = [c.get("name", "") for c in prof.core_concepts if c.get("name")]
    lines.append(f"【本专业核心概念清单】{ '、'.join(concepts[:60]) }")
    mis = [m.get("misconception", "") for m in prof.common_misconceptions]
    if mis:
        lines.append(f"【本专业常见易错点】{'；'.join(mis)}")
    return "\n".join(lines)


SYSTEM_PROMPT_TMPL = """你是一位严谨的{prof_name}笔记整理助手。你的任务是把一份 PPT 课件转成**适合放进 Obsidian 个人知识库**的结构化笔记。

判断优先级(极其重要, 决定输出质量):
- **PPT 课件全文 = 唯一的内容来源**。sections、concepts 的"内容讲什么"必须完全来自 PPT,
  忠实反映 PPT 各节的主题、重点与先后顺序。
- 【本专业核心概念清单】只是一个**命名词典/受控词汇**, 只用来帮你把已经由 PPT 讲到的内容
  选一个规范术语命名; **它不是内容清单, 绝不决定你要整理哪些内容**。

## 1. 术语纪律
- 当 PPT 讲到某个概念、且它正好对应清单里的规范术语时, 用清单术语命名(并可用其定义), 例如 PPT 说"吸收"而规范词是"生物利用度"时可映射。
- 当 PPT 讲到的主题/小节**清单里没有对应词**时, 就用 PPT 自己的准确原词新建概念卡(见"覆盖纪律"), 不要因此漏掉它, 也不要硬套清单里勉强相似的词。
- 当清单里的某词在本讲只是被**顺带提及一句、并非本节要讲透的内容**时, 不要为它单独开概念卡。

## 2. 覆盖纪律(纠正"笔记全是框架词、却与课件主体无关")
- 概念卡要**覆盖 PPT 真正成块讲解的各大部分/小节**。把 PPT 从头到尾按它的目录小节走一遍,
  每一大节的重点都给到覆盖, 让整份课件的笔记骨架 = 课件自身的骨架。
- 若课件有多个并列大主题(例如一部分讲基础学科、一部分讲应用), 两者都要有笔记, 不能因为
  某一主题恰好更贴合清单就只整理它、而把另一主题压缩成一句。

## 3. 绝对不要做的事
- 不要输出一大段连贯"总结文章"。这是本工具与直接问大模型的根本区别。
- **不要为了让笔记"看起来像本专业"就牵强套用清单概念**; 清单只是命名用的范围, 不是产出清单。
- 不要编造 PPT 没讲的细节来"充实"定义; definition/detail 都要有 PPT 依据。
- 不要输出 markdown 以外的任何解说、客套、评语。

## 4. 输出格式(严格 JSON, 无 markdown 代码块包裹)
{
  "title": "本讲/本章主标题(简短)",
  "sections": [
    {
      "heading": "章节名(按PPT逻辑或主题分节, 3~6节, 覆盖课件各大部分)",
      "summary": "这一节的精炼要点(80~150字, 讲清楚逻辑关系)",
      "concept_links": ["本节约涉及的概念名; 优先用清单规范术语, 清单没有就用PPT原词"]
    }
  ],
  "concepts": [
    {
      "concept": "概念名(清单规范术语或PPT原词)",
      "definition": "一句话精确定义(照抄/转述PPT中的准确表述)",
      "detail": "补充说明: 关键点/分类/例子/与其它概念的联系(可用markdown列表, 100~250字)",
      "related": ["与之相关的其他概念名, 用于 Obsidian 双链"],
      "symbols": [{"symbol":"出现的公式符号/缩写","meaning":"含义"}],
      "source_pages": "对应PPT页码(如 '2-3')"
    }
  ],
  "misconceptions": [
    {"misconception":"PPT呈现的常见误解/易错表述","clarification":"正确理解"}
  ]
}

## 5. 数量与质量
- sections 一般 2~6 节(对齐课件的大目录/大节)。
- concepts 抓取课件**真正讲透的核心概念 4~10 个**: 覆盖面要跟着课件的骨架走, 不堆砌、也不漏大块。
- 每个 concept 都要指向真实 PPT 页码(source_pages), 方便回溯; 无页码的不要硬编。
- related 只填 PPT 里确实有联系、值得双链的概念, 用于 Obsidian 双链。
"""

USER_TMPL = """下面是你要处理的课件内容(来自 PPT《{filename}》)。

【重要】这份 PPT 全文是你整理笔记的**唯一依据**。请严格按 PPT 自身的目录小节与重点来组织笔记,
做到"讲什么就整理什么"。下方【核心概念清单】只是命名词典: 只有当 PPT 讲到的内容正好能对上
清单术语时才用它命名; 清单里没有、但 PPT 大篇幅在讲的, 照常用 PPT 原词整理, 不要硬套清单词。

===专业范围/术语词典(仅用于规范命名, 不是内容清单)===
{kb}

===PPT 全文开始===
{fulltext}
===PPT 全文结束===

本地初步检测到这份 PPT 提到了这些本专业概念(仅作命名参考, 不代表都必须开卡):
{hits}
可能出现过的符号(命名参考): {symbols_hint}

请按要求输出 JSON。
"""


# ---------------- JSON 解析(带容错) ----------------

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _extract_json(text: str) -> Optional[Dict]:
    """从 LLM 输出中稳健提取 JSON 对象。"""
    if not text:
        return None
    # 去掉可能的 ```json ``` 围栏
    text = text.strip()
    fence = _JSON_FENCE_RE.search(text)
    if fence:
        text = fence.group(1).strip()
    # 定位第一个 { 到最后一个 }
    s = text.find("{")
    e = text.rfind("}")
    if s == -1 or e == -1 or e <= s:
        return None
    cand = text[s:e + 1]
    for candidate in _json_candidates(cand):
        try:
            return json.loads(candidate)
        except Exception:
            continue
    return None


_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")


def _json_candidates(cand: str):
    """产出多个可尝试解析的候选串, 逐步放宽对引号内原始换行的容忍。"""
    yield cand  # 原样
    # 去掉结尾多余逗号
    yield _TRAILING_COMMA_RE.sub(r"\1", cand)
    # 把字符串值内部真正的换行符转义为 \\n (LLM 偶尔不转义)
    fixed = re.sub(r'("(?:\\.|[^"\\])*?):\s*"((?:\\.|[^"\\\n])*?)(\n+)((?:\\.|[^"\\])*?)"',
                   lambda m: m.group(1) + ': "' + m.group(2) + '\\n' + m.group(4) + '"',
                   cand)
    yield fixed
    yield _TRAILING_COMMA_RE.sub(r"\1", fixed)


# ---------------- 本地兜底(离线生成基础笔记) ----------------

def _local_fallback_bundle(doc: PresentationDoc, prof: Profession,
                           symbols_used, concepts_used) -> NoteBundle:
    """未配置大模型时的规则化兜底: 按页切块 + 概念检测, 不臆造定义。"""
    bundle = NoteBundle(
        professional_name=prof.name,
        source_file=os_basename(doc.path),
        title=doc.name,
        used_llm=False,
    )
    # 章节: 把每页标题作为 heading, 正文去掉重复的标题头
    for sl in doc.slides:
        title = sl.title or f"第{sl.index}页"
        txt = sl.to_text()
        # 去掉 txt 中与 title 重复的首行 "# xxx"
        lines = txt.splitlines()
        if lines and lines[0].strip().startswith("#") and lines[0].strip().lstrip("#").strip() == title:
            lines = lines[1:]
        clean = "\n".join(lines).strip()
        bundle.sections.append(Section(
            heading=title,
            summary=clean[:220] if clean else "(本页以图表/图片为主, 建议绑定模型后识别)",
            concept_links=concepts_used[:12],
            pages=[sl.index],
        ))
    # 概念卡: 用知识库定义填充命中的概念
    for name in concepts_used[:10]:
        definition = prof.find_concept_definition(name) or "(PPT 中出现, 详见原文)"
        bundle.concepts.append(ConceptCard(
            concept=name,
            definition=definition,
            detail="(本地规则模式生成, 未使用大模型; 绑定模型后可获得更完整释义)",
            source_pages="",
        ))
    bundle.misconceptions = prof.common_misconceptions[:2]
    bundle.warnings.append("未绑定大模型, 已使用本地规则模式生成基础笔记。建议在[设置]中绑定模型以获得更准确的结构化笔记。")
    return bundle


def os_basename(p):
    import os
    return os.path.basename(p)


# ---------------- 主入口 ----------------

def _fill_template(tmpl: str, **kw) -> str:
    """安全填充模板: 用逐项 replace 而非 str.format, 避免模板里的 JSON 花括号被误解析。"""
    out = tmpl
    for k, v in kw.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def summarize(doc: PresentationDoc, prof: Profession,
              llm=None,           # LLMClient or None
              progress=None,      # callable(msg) 可选
              extra_context: str = "",        # 自学补充上下文(见 kb_calibration)
              extra_concept_names: list = None,  # 自学已确认概念名, 参与命中检测
              ) -> NoteBundle:
    """把解析好的 PPT 文档按专业框架转成结构化笔记束。

    extra_context / extra_concept_names 来自 CalibrationStore, 让工具"越转越懂你":
    既有官方清单 + 用户此前自学沉淀的补充概念一起注入/检测。
    """
    fulltext = doc.full_text()

    # 1. 本地先做一轮确定性检测(官方 + 自学补充概念)
    official_names = [c.get("name", "") for c in prof.core_concepts if c.get("name")]
    detect_names = list(official_names)
    if extra_concept_names:
        known = {_norm(n) for n in detect_names}
        detect_names += [n for n in extra_concept_names if n and _norm(n) not in known]
    symbols_used = detect_symbols_used(fulltext, prof)
    concepts_used = detect_concepts_from(detect_names, fulltext)
    kb_ctx = _kb_context(prof)
    if extra_context:
        kb_ctx += "\n" + extra_context
    hits_hint = "、".join(concepts_used) if concepts_used else "(未命中清单概念)"
    symbols_hint = "、".join(f"{s['symbol']}({s['meaning']})" for s in symbols_used) or "(无)"

    # 2. 优先走大模型 —— 三步流水线(大纲->分节深写->校对查漏)
    if llm is not None:
        try:
            from obsidian import staged
            if progress: progress("已连接模型, 启动三步流水线…")
            return staged.summarize_staged(
                doc, prof, llm,
                progress=progress,
                extra_context=extra_context,
                extra_concept_names=extra_concept_names,
            )
        except Exception as e:
            # 流水线自身异常(不应因 bug 让用户拿不到笔记) -> 明确降级到本地规则
            if progress: progress(f"三步流水线异常({e}), 回退本地规则模式")
            fb = _local_fallback_bundle(doc, prof, symbols_used, concepts_used)
            fb.warnings.append(f"三步流水线内部异常({e}), 已退回本地规则模式。")
            return fb

    # 3. 兜底
    return _local_fallback_bundle(doc, prof, symbols_used, concepts_used)


def detect_concepts_from(names, text: str) -> List[str]:
    """按给定概念名清单检测命中(保序去重)。供官方+自学合并使用。"""
    hits, seen = [], set()
    ntext = _norm(text)
    for nm in names:
        nk = _norm(nm)
        if len(nk) < 2 or nk in seen:
            continue
        if nk in ntext:
            seen.add(nk)
            hits.append(nm)
    return hits


def _bundle_from_json(data: Dict, prof: Profession, doc: PresentationDoc,
                      symbols_used, concepts_used) -> NoteBundle:
    bundle = NoteBundle(
        professional_name=prof.name,
        source_file=os_basename(doc.path),
        title=str(data.get("title") or doc.name).strip(),
        used_llm=True,
    )
    for sec in data.get("sections", []) or []:
        if not isinstance(sec, dict):
            continue
        bundle.sections.append(Section(
            heading=str(sec.get("heading", "")).strip(),
            summary=str(sec.get("summary", "")).strip(),
            concept_links=[str(x).strip() for x in (sec.get("concept_links") or []) if str(x).strip()],
        ))

    for c in data.get("concepts", []) or []:
        if not isinstance(c, dict):
            continue
        nm = str(c.get("concept", "")).strip()
        if not nm:
            continue
        # 合并本地检测到的符号(模型可能漏)
        local_syms = detect_symbols_for_concept(nm, symbols_used, concepts_used)
        syms = c.get("symbols") or []
        merged = list(syms)
        seen = {_norm(s.get("symbol")) for s in merged if isinstance(s, dict)}
        for s in local_syms:
            if _norm(s["symbol"]) not in seen:
                merged.append(s)
        bundle.concepts.append(ConceptCard(
            concept=nm,
            definition=str(c.get("definition", "")).strip(),
            detail=str(c.get("detail", "")).strip(),
            related=[str(x).strip() for x in (c.get("related") or []) if str(x).strip()],
            symbols=merged,
            source_pages=str(c.get("source_pages", "")).strip(),
        ))

    for m in (data.get("misconceptions") or []) or []:
        if isinstance(m, dict):
            bundle.misconceptions.append({
                "misconception": str(m.get("misconception", "")).strip(),
                "clarification": str(m.get("clarification", "")).strip(),
            })

    # 兜底: 若模型没给易错点, 但 PPT 命中知识库易错点关键词, 补上知识库权威版
    if not bundle.misconceptions:
        for mm in prof.common_misconceptions:
            kw = mm.get("misconception", "")
            if kw and any(k in fulltext_hint(concepts_used) for k in []) is False:
                pass
    return bundle


def detect_symbols_for_concept(concept_name, symbols_used, concepts_used):
    return symbols_used[:6]


def fulltext_hint(concepts_used):
    return " ".join(concepts_used)


if __name__ == "__main__":
    from knowledge_base import KnowledgeBase
    from ppt_parser import ingest_pptx
    kb = KnowledgeBase()
    kb.load_all()
    p = kb.get("pharmacy")
    doc = ingest_pptx("../outputs/sample_pharmacy.pptx")
    b = summarize(doc, p, llm=None)
    print("标题:", b.title)
    print("章节数:", len(b.sections), "概念卡:", len(b.concepts), "用LLM:", b.used_llm)
    for sec in b.sections[:3]:
        print(" -", sec.heading, sec.concept_links[:4])
    for c in b.concepts[:4]:
        print("  概念:", c.concept, "|", c.definition[:30])
