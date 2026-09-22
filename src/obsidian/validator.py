# -*- coding: utf-8 -*-
"""
知识质量验证器 (Knowledge Validator) —— 让"够不够好"从感觉变成可量化、可追溯。

回答四类问题:
  1) Coverage  课件各大部分有没有变成知识节点? (漏了哪些页/章节)
  2) Evidence  每个知识节点有没有"来自哪一页 + PPT 原话"可溯源?
  3) Links     笔记里的 [[双链]] 是否都指向本讲真实存在的概念(有没有断链)?
  4) Trust     哪些概念名在 PPT 原文里找不到(规范命名映射 / 模型自行补充, 提示复核)?

对外:
  validate_bundle(doc, bundle) -> ValidationResult
  ValidationResult.to_markdown()  生成《知识质量报告》md(由 pipeline 落盘)
  ValidationResult.to_brief()     一两行摘要(供 GUI 完成弹窗)
"""
import re
from dataclasses import dataclass, field
from typing import List, Dict

from obsidian.summarizer import NoteBundle, Section, ConceptCard

KNOWLEDGE_TYPES = ["concept", "mechanism", "process", "fact", "formula", "example", "method"]
RELATION_TYPES = ["related_to", "part_of", "depends_on", "causes", "example_of", "contrasts_with"]


def _norm(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"[\s\.\-_·/\\:：（）()]+", "", str(s)).lower()


def _parse_range(s: str) -> List[int]:
    """'3' / '4-5' / '2,4-6' -> 页号列表(1-based, 越界容忍)。"""
    out = set()
    if not s:
        return []
    for part in re.split(r"[,\s;，；、]+", str(s)):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(\d+)\s*[-~至到]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out.update(range(min(a, b), max(a, b) + 1))
        elif part.isdigit():
            out.add(int(part))
    return sorted(out)


_PAREN_RE = re.compile(r"[（(]([^（）()]*)[）)]")
_SEP_RE = re.compile(r"[、，,；;]")


def _split_link_target(name: str) -> List[str]:
    """链接目标可能是复习页的"复述串"(如 '急性实验、在体实验、离体实验、慢性实验'
    或 '实验方法分类（急性、在体、离体、慢性）')。把列表成员拆出来分别判定,
    括号内外的非列表主体(如 '新药上市申请（NDA）' -> '新药上市申请' + 'NDA')也拆开,
    避免半截/脏灰链。单个概念名原样返回。"""
    name = str(name).strip()
    if not name:
        return []
    cands = []
    # 去掉括号后的主体优先(本身可能是列表或单个概念)
    body = _PAREN_RE.sub("", name).strip()
    body_parts = [p.strip() for p in _SEP_RE.split(body) if p.strip()]
    body_items = body_parts if len(body_parts) > 1 else ([body] if body else [])
    cands += body_items
    # 括号内容按分隔符拆成成员
    for inner in _PAREN_RE.findall(name):
        parts = [p.strip() for p in _SEP_RE.split(inner) if p.strip()]
        for p in (parts if len(parts) > 1 else [inner.strip()]):
            if p and p not in cands:
                cands.append(p)
    return cands or [name]


@dataclass
class ValidationResult:
    """一次 PPT 转换的结构化质量体检结果。分数均为 0~100。"""
    title: str = ""
    source_file: str = ""
    total_pages: int = 0
    content_pages: int = 0          # 有文本内容的页(纯图页不计入分母)
    covered_pages: int = 0          # 被章节覆盖的页
    n_sections: int = 0
    n_concepts: int = 0
    coverage_score: float = 0.0
    evidence_score: float = 0.0
    link_score: float = 0.0
    overall_score: float = 0.0
    # 明细清单
    missing_concepts: List[str] = field(default_factory=list)      # 大段未覆盖页(如 "3-5(页)")
    unsupported_concepts: List[str] = field(default_factory=list)  # 无页码/无证据原文的卡
    duplicate_concepts: List[str] = field(default_factory=list)    # 本讲内部重名
    broken_links: List[str] = field(default_factory=list)          # 真断链: PPT原文也没有的幻影目标
    uncreated_targets: List[str] = field(default_factory=list)     # 灰链: 课件确提过但本讲未单独成卡
    ai_supplemented: List[str] = field(default_factory=list)       # PPT 原文无此术语(疑似规范映射/补充)
    notes: List[str] = field(default_factory=list)

    # ---------- 摘要 ----------
    def to_brief(self) -> str:
        return (f"知识质量: 覆盖率 {self.coverage_score:.0f}% · 证据 {self.evidence_score:.0f}% · "
                f"链接 {self.link_score:.0f}% (总评 {self.overall_score:.0f}%)")

    # ---------- 报告 ----------
    def to_markdown(self) -> str:
        L = []
        L.append("---")
        L.append(f'title: "知识质量报告"')
        L.append(f'source: "{self.source_file}"')
        L.append("tags: [质量报告, 层级/附录]")
        L.append("---")
        L.append("")
        L.append("# 📊 知识质量报告")
        L.append("")
        L.append(f"> 来源课件: 《{self.source_file}》 · 共 {self.total_pages} 页")
        L.append("")
        L.append("```text")
        L.append("Knowledge Quality")
        L.append("────────────────────")
        L.append(f"Coverage : {self.coverage_score:5.1f}%   课件大块内容是否都变成了知识节点")
        L.append(f"Evidence : {self.evidence_score:5.1f}%   每个节点是否可溯源到页码+PPT原话")
        L.append(f"Links    : {self.link_score:5.1f}%   笔记双链是否都指向真实存在的笔记")
        L.append(f"Overall  : {self.overall_score:5.1f}%")
        L.append("────────────────────")
        L.append(f"Sections : {self.n_sections}")
        L.append(f"Concepts : {self.n_concepts}")
        L.append(f"Pages    : 覆盖 {self.covered_pages}/{self.content_pages} 个有内容的页面")
        L.append("```")
        L.append("")
        if self.missing_concepts:
            L.append("## ❗ 可能漏掉的大段内容(建议补看)")
            L.append("")
            for m in self.missing_concepts[:12]:
                L.append(f"- {m}")
            L.append("")
        if self.unsupported_concepts:
            L.append("## ⚠️ 无溯源的节点(缺页码或PPT原文证据)")
            L.append("")
            for u in self.unsupported_concepts[:15]:
                L.append(f"- {u}")
            L.append("")
        if self.ai_supplemented:
            L.append("## 🤖 需复核: 术语未在PPT原文中逐字出现")
            L.append("")
            L.append("> 这些概念名可能是模型做的**规范术语映射**(如原文'吸收'→规范词'生物利用度'),")
            L.append("> 也可能是模型自行补充。请对照课件确认是否准确。")
            L.append("")
            for a in self.ai_supplemented[:15]:
                L.append(f"- {a}")
            L.append("")
        if self.broken_links:
            L.append("## 🔗 真断链(指向本讲与PPT都没有的幻影概念)")
            L.append("")
            for b in self.broken_links[:15]:
                L.append(f"- [[{b}]]")
            L.append("")
        if self.uncreated_targets:
            L.append("## 🟡 灰链: 课件提到但本讲未单独成卡")
            L.append("")
            L.append("> 这些链接指向的术语在课件中确实出现过, 但本次没有为它单独开卡。")
            L.append("> Obsidian 中会显示为未创建的链接——若它是重点, 双击即可补建; 若只是顺带提及可忽略。")
            L.append("")
            for u in self.uncreated_targets[:20]:
                L.append(f"- [[{u}]]")
            L.append("")
        if self.duplicate_concepts:
            L.append("## ♻️ 本讲内重名概念")
            L.append("")
            for d in self.duplicate_concepts[:10]:
                L.append(f"- {d}")
            L.append("")
        if self.notes:
            L.append("## 💡 说明")
            L.append("")
            for n in self.notes:
                L.append(f"- {n}")
            L.append("")
        L.append("---")
        L.append("*由工具在转换后自动生成, 用于自查与改进笔记质量。*")
        return "\n".join(L)


def _section_covered(s: Section, concept_pages: Dict[int, int]) -> bool:
    """该节是否至少有一张概念卡落在它的页码范围里。"""
    pages = set(s.pages or [])
    if not pages:
        return False
    return any(p in pages for p in concept_pages)


def validate_bundle(doc, bundle: NoteBundle) -> ValidationResult:
    """对一次转换做结构化质量体检。doc: 解析后的 PresentationDoc; bundle: summarize 输出。"""
    res = ValidationResult(
        title=bundle.title or doc.name,
        source_file=bundle.source_file,
        total_pages=getattr(doc, "total_pages", 0) or (len(getattr(doc, "slides", []))),
    )
    if not bundle.sections and not bundle.concepts:
        res.notes.append("本次未产出任何章节/概念(可能走了兜底或课件解析为空)。")
        return res

    # ---- 页面维度: 哪些页被章节覆盖(只看有文本内容的页) ----
    content_pages = []
    for s in getattr(doc, "slides", []):
        if (s.to_text() or "").strip():
            content_pages.append(s.index)
    res.content_pages = len(content_pages)
    covered = set()
    for sec in bundle.sections:
        covered.update(sec.pages or [])
    res.covered_pages = len([p for p in content_pages if p in covered])

    # ---- 概念维度: 证据 / 溯源 / 重名 ----
    page_of_concept: Dict[int, int] = {}
    total_cards = len(bundle.concepts)
    with_evidence = 0
    names_norm = set()
    dups = []
    unsupported = []
    for c in bundle.concepts:
        if not c.concept:
            continue
        nk = _norm(c.concept)
        if nk in names_norm:
            dups.append(c.concept)
        names_norm.add(nk)
        cpages = _parse_range(c.source_pages or "")
        for p in cpages:
            page_of_concept[p] = page_of_concept.get(p, 0) + 1
        missing_parts = []
        if not cpages:
            missing_parts.append("缺页码")
        if not (c.evidence or []):
            missing_parts.append("缺PPT原文证据")
        if missing_parts:
            unsupported.append(f"{c.concept}（{'、'.join(missing_parts)}）")
        if cpages and c.evidence:
            with_evidence += 1
    res.n_concepts = total_cards
    res.evidence_score = round(100.0 * with_evidence / total_cards, 1) if total_cards else 0.0
    res.duplicate_concepts = dups
    res.unsupported_concepts = unsupported

    # ---- 覆盖分数: 页覆盖率 60% + 每节都有卡 40% ----
    page_ratio = res.covered_pages / res.content_pages if res.content_pages else 0.0
    sec_ratio = 0.0
    if bundle.sections:
        hit = sum(1 for sec in bundle.sections if _section_covered(sec, page_of_concept))
        sec_ratio = hit / len(bundle.sections)
    res.n_sections = len(bundle.sections)
    res.coverage_score = round(100.0 * (0.6 * page_ratio + 0.4 * sec_ratio), 1)

    # ---- 大段未覆盖: 连续未覆盖内容页 >=2 段 -> 提醒 ----
    content_set = set(content_pages)
    missing_pages = sorted(content_set - covered)
    if missing_pages:
        runs, start, prev = [], missing_pages[0], missing_pages[0]
        for p in missing_pages[1:]:
            if p == prev + 1:
                prev = p
            else:
                runs.append((start, prev))
                start = prev = p
        runs.append((start, prev))
        for a, b in runs:
            if b - a >= 1:  # 至少 2 个连续内容页没被任何节覆盖
                res.missing_concepts.append(f"第 {a}-{b} 页未被章节/概念覆盖（请核对是否遗漏大段主题）")
            else:
                res.missing_concepts.append(f"第 {a} 页未被覆盖（过渡页可忽略）")
    if bundle.sections:
        empty_sec = [sec.heading for sec in bundle.sections if not (sec.pages or [])]
        if empty_sec:
            res.notes.append(f"{len(empty_sec)} 个章节没有页码范围标注: {'、'.join(empty_sec[:5])}")

    # ---- 断链检查: 双链目标三级判定(已建卡 / 课件提到未开卡(灰链) / 真断链) ----
    try:
        full_norm = _norm(doc.full_text())
    except Exception:
        full_norm = ""
    targets = []
    for sec in bundle.sections:
        targets += [x for x in (sec.concept_links or []) if x]
    for c in bundle.concepts:
        targets += [x for x in (c.related or []) if x]
        targets += [str(r.get("target", "")) for r in (c.relations or []) if isinstance(r, dict)]
    unique = []
    seen_t = set()
    for t in targets:
        for part in _split_link_target(t):   # 拆掉"顿号串"式复述目标
            tk = _norm(part)
            if tk and tk not in seen_t:
                seen_t.add(tk)
                unique.append(part)
    broken, uncreated = [], []
    for t in unique:
        if _norm(t) in names_norm:
            continue  # 本讲有卡 -> 已解析
        if full_norm and _norm(t) and _norm(t) in full_norm:
            uncreated.append(t)   # 课件确提过 -> 灰链(不扣分)
        else:
            broken.append(t)      # PPT 也没有 -> 真断链(疑似幻觉)
    res.broken_links = broken
    res.uncreated_targets = uncreated
    res.link_score = round(100.0 * (len(unique) - len(broken)) / len(unique), 1) if unique else 100.0

    # ---- AI 补充/规范映射: 概念名在 PPT 原文里找不到 ----
    if full_norm:
        ai = [c.concept for c in bundle.concepts
              if c.concept and _norm(c.concept) not in full_norm]
        res.ai_supplemented = ai

    # ---- 总分 ----
    scores = [s for s in (res.coverage_score, res.evidence_score, res.link_score) if s > 0]
    res.overall_score = round(sum(scores) / len(scores), 1) if scores else 0.0

    # 提示
    if bundle.used_llm is False:
        res.notes.append("本讲由本地规则模式生成(未绑定大模型), 分数普遍偏低属正常; 绑定模型后重转可显著提升。")
    if res.unsupported_concepts and res.evidence_score < 60:
        res.notes.append("证据分偏低: 模型未按提示摘抄PPT原文。可换更强模型或重试一次。")
    return res


if __name__ == "__main__":
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from core.document_loader import ingest_document
    from core.knowledge_base import KnowledgeBase
    from obsidian.summarizer import summarize
    p = sys.argv[1] if len(sys.argv) > 1 else ""
    if not p:
        print("用法: python -m obsidian.validator <课件路径.ppt/.pptx/.pdf>")
        sys.exit(1)
    kb = KnowledgeBase(); kb.load_all()
    prof = kb.get("pharmacy")
    doc = ingest_document(p)
    b = summarize(doc, prof, llm=None)
    v = validate_bundle(doc, b)
    print(v.to_markdown())
