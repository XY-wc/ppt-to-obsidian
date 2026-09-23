# -*- coding: utf-8 -*-
"""
Vault 感知层 (Vault-Aware Layer) —— P0 差异化护城河。

核心目标: 让工具"越用越懂你"。当一份新 PPT 转进一个**已经积累过笔记**的
Obsidian vault 时, 不再无脑新建孤立文件, 而是:

  1. 扫描目标 vault, 建立已有笔记索引(文件名 / frontmatter title / professional / 双链出边)。
  2. 把本次要写入的概念笔记与 vault 里已有的"同名/同义概念"做**对齐**:
       - 命中且同专业   -> 不新建副本, 而是把新来源、新 definition、新 detail
                           增量**合并**进既有原子笔记(追加来源块 + 补充说明),
                           并把其它相关笔记的 [[双链]] 指向既有文件。
       - 命中但不同专业 -> 保留新笔记, 但记录跨专业同名, 提示做概念消歧。
       - 未命中         -> 正常新建; 同时检测 MOC/概念里提到的未建 [[链接]],
                           可选补一张"待补"占位, 触发生长(见 build_cover_notes_text)。
  3. 返回一份"合并/新建/重复"报告, 交给 GUI 在日志里清楚展示"又认识了哪个老概念、
     这次给谁补了新知识" —— 这正是一句能说出口的卖点: *它不是翻译, 是给你的第二大脑增量写回。*

文件命名注意(与本模块 merge 策略一致):
  - 复用既有文件时用既有 abs_path, 不再生成 "<name>_2.md" 那种重复文件。
  - 只有真正全新概念才新建。

对外 API:
  - index_vault(vault_dir)                     -> VaultIndex
  - plan_merge(index, bundle_notes, prof)      -> MergePlan  (哪些合并/哪些新建/哪些重复)
  - apply_merge(plan)                          -> 实际读写文件
  - 设计为"纯函数式规划 + 一次性落盘", 便于测试与日志。
"""
import os
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional

_NORM_RE = re.compile(r"[\s\.\-_·/\\:：（）()]+")
_WIKI_RE = re.compile(r"\[\[([^\]]+)\]\]")
_FRONTMATTER_RE_TMPL = r"^\s*{}\s*:\s*(.+)$"
_QUOTE_DEF_RE = re.compile(
    r"> \[!quote\] 定义\s*\n> (.*?)(?:\n>\s*\n|\n> \[!info\]|\n## |\Z)", re.S)
_DETAIL_RE = re.compile(
    r"## 📝 详细说明\s*\n(.*?)(?:\n## |\n## 来源|\Z)", re.S)
_SRC_RE = re.compile(r"PPT:\s*《([^》]*)》")


def _norm(s: str) -> str:
    """规范化用于匹配: 去空白/点/下划线/连字符, 小写。Obsidian 文件名常带这些分隔。"""
    if not s:
        return ""
    return _NORM_RE.sub("", str(s)).lower()


def _strip_ext(fn: str) -> str:
    return os.path.splitext(os.path.basename(fn))[0]


def nt_slug(name: str) -> str:
    """本地安全文件名(与 note_template.slugify 保持一致, 避免跨模块耦合)。"""
    from obsidian.note_template import slugify
    return slugify(name)


def _frontmatter_field(text: str, key: str) -> str:
    """从 YAML frontmatter 里读一个字段值(去引号)。"""
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    block = text[3: end if end != -1 else len(text)]
    pat = re.compile(_FRONTMATTER_RE_TMPL.format(re.escape(key)), re.M)
    m = pat.search(block)
    if not m:
        return ""
    val = m.group(1).strip().strip('"\'')
    # tags: [a, b] 只取第一个比较
    if val.startswith("["):
        val = val[1:].split(",")[0].strip().strip('"\'')
    return val


def _wikilinks(text: str) -> List[str]:
    """提取所有 [[目标|别名]] 的目标名。"""
    return [x.split("|")[0].split("#")[0].strip() for x in _WIKI_RE.findall(text)]


@dataclass
class ExistingNote:
    """vault 里的一篇既有 .md 笔记(轻量元数据)。"""
    abs_path: str
    filename: str            # 无扩展名
    title: str               # frontmatter title 或文件名
    professional: str = ""   # frontmatter professional
    tags: str = ""
    source_file: str = ""
    content_head: str = ""   # 前 ~400 字, 供合并时定位"来源"区
    links_out: List[str] = field(default_factory=list)


@dataclass
class VaultIndex:
    vault_dir: str
    by_filename_norm: Dict[str, ExistingNote] = field(default_factory=dict)
    by_title_norm: Dict[str, ExistingNote] = field(default_factory=dict)
    professional_of: Dict[str, str] = field(default_factory=dict)  # filename -> professional

    def find_by_concept(self, concept: str) -> Optional[ExistingNote]:
        """按概念名(规范术语)定位既有原子笔记: 先精确文件名/标题, 再放宽同义词。"""
        nk = _norm(concept)
        if not nk:
            return None
        if nk in self.by_filename_norm:
            return self.by_filename_norm[nk]
        if nk in self.by_title_norm:
            return self.by_title_norm[nk]
        return None


def index_vault(vault_dir: str) -> VaultIndex:
    """扫描 vault 下所有 .md, 构建按规范化文件名/标题的索引。"""
    idx = VaultIndex(vault_dir=vault_dir or "")
    if not vault_dir or not os.path.isdir(vault_dir):
        return idx
    for root, _, files in os.walk(vault_dir):
        # 跳过 Obsidian 配置与 .ppt2obsidian 系统目录
        if ".obsidian" in root or ".ppt2obsidian" in root or ".git" in root:
            continue
        for fn in files:
            if not fn.lower().endswith(".md"):
                continue
            p = os.path.join(root, fn)
            try:
                with open(p, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
            except Exception:
                continue
            stem = _strip_ext(fn)
            note = ExistingNote(
                abs_path=p,
                filename=stem,
                title=_frontmatter_field(text, "title") or stem,
                professional=_frontmatter_field(text, "professional"),
                tags=_frontmatter_field(text, "tags"),
                source_file=_frontmatter_field(text, "source"),
                content_head=text[:600],
                links_out=_wikilinks(text),
            )
            idx.by_filename_norm[_norm(stem)] = note
            if note.title:
                idx.by_title_norm.setdefault(_norm(note.title), note)
            if note.professional:
                idx.professional_of[_norm(stem)] = note.professional
    return idx


# ---------------- 合并规划 ----------------

@dataclass
class MergeAction:
    kind: str                    # 'new' | 'merge' | 'dup_same_prof' | 'cross_prof' | 'create_moc'
    concept: str = ""
    existing_path: str = ""      # 若复用
    target_path: str = ""        # 写入路径
    content: str = ""            # 若 new: 整篇; 若 merge: 附加文本(来源块)
    source_pages: str = ""
    note_title: str = ""


@dataclass
class MergeReport:
    vault_dir: str
    actions: List[MergeAction] = field(default_factory=list)
    merged: List[str] = field(default_factory=list)       # 复用并增量合并的概念
    created: List[str] = field(default_factory=list)      # 全新新建的概念
    cross_prof: List[str] = field(default_factory=list)   # 跨专业同名(建议消歧)

    @property
    def summary_lines(self) -> List[str]:
        lines = []
        if self.merged:
            lines.append(f"🔗 复用并扩充 {len(self.merged)} 个 vault 已有概念: {', '.join(self.merged[:6])}")
        if self.created:
            lines.append(f"🆕 新建 {len(self.created)} 个全新概念: {', '.join(self.created[:6])}")
        if self.cross_prof:
            lines.append(f"⚠️ 跨专业同名 {len(self.cross_prof)} 个(建议在图谱中区分): {', '.join(self.cross_prof[:4])}")
        return lines


def _split_bundle_notes(notes):
    """把 GeneratedNote 拆成 moc / concept / misconception 三类。"""
    moc, concepts, misc = [], [], []
    for n in notes:
        if n.kind == "moc":
            moc.append(n)
        elif n.kind == "concept":
            concepts.append(n)
        elif n.kind == "misconception":
            misc.append(n)
    return moc, concepts, misc


def plan_merge(index: VaultIndex, notes, professional_name: str, vault_dir: str,
               moc_path: Optional[str] = None) -> MergeReport:
    """把本次渲染好的一组笔记与 vault 现有索引对齐, 产出合并计划(不落盘)。"""
    report = MergeReport(vault_dir=vault_dir)
    moc, concepts, misc = _split_bundle_notes(notes)
    _moc_content = moc[0].content if moc else None

    # 预先把 MOC 的相对目录算出来(新概念要放进与 MOC 同目录, 保持"一课一夹")
    moc_folder_rel = ""
    if moc:
        moc_rel = moc[0].rel_path
        moc_folder_rel = os.path.dirname(moc_rel)

    for c in concepts:
        existing = index.find_by_concept(c.title)
        # 概念笔记应放的文件名(由 render 给出的 rel_path 推导, 保证与 MOC 双链一致)
        _filename = _strip_ext(os.path.basename(c.rel_path or "")) or nt_slug(c.title)
        if existing is None:
            # 全新概念 -> 正常新建(放入与 MOC 相同的一课一夹, 若 MOC 同目录)
            rel = os.path.join(moc_folder_rel, f"{_filename}.md") if moc_folder_rel else c.rel_path
            report.actions.append(MergeAction(
                kind="new", concept=c.title,
                target_path=os.path.join(vault_dir, rel.replace("/", os.sep)),
                content=c.content, source_pages=c.source_pages, note_title=c.title))
            report.created.append(c.title)
            continue

        # 命中既有笔记
        if existing.professional and _norm(existing.professional) != _norm(professional_name):
            # 跨专业同名 -> 保留新笔记, 标记消歧
            rel = os.path.join(moc_folder_rel, f"{_filename}.md") if moc_folder_rel else c.rel_path
            report.actions.append(MergeAction(
                kind="cross_prof", concept=c.title,
                target_path=os.path.join(vault_dir, rel.replace("/", os.sep)),
                content=c.content, source_pages=c.source_pages, note_title=c.title))
            report.created.append(c.title)
            report.cross_prof.append(c.title)
            continue

        # 同专业命中 -> 增量合并进既有原子笔记(不新建)
        addendum = _build_merge_addendum(c, professional_name, existing_content=existing.content_head)
        if addendum:
            report.actions.append(MergeAction(
                kind="merge", concept=c.title, existing_path=existing.abs_path,
                target_path=existing.abs_path, content=addendum,
                source_pages=c.source_pages, note_title=c.title))
            report.merged.append(c.title)
        else:
            # 内容完全重复/无可补充 -> 视为重复, 跳过写入
            report.actions.append(MergeAction(
                kind="dup_same_prof", concept=c.title, existing_path=existing.abs_path,
                note_title=c.title))

    # MOC: 若本期有合并进既有概念, MOC 仍在"本课目录"写一份(它是本课地图), 但把双链指向复用文件
    # 这里简单策略: MOC / 易错点照常新建(属于本课, 不合并)。概念复用不影响 MOC 是新课地图。
    if _moc_content:
        rel = moc[0].rel_path if moc else ""
        report.actions.append(MergeAction(
            kind="create_moc", concept=moc[0].title if moc else "MOC",
            target_path=os.path.join(vault_dir, rel.replace("/", os.sep)),
            content=_moc_content))
    for m in misc:
        report.actions.append(MergeAction(
            kind="create_moc" if False else "new", concept=m.title,
            target_path=os.path.join(vault_dir, m.rel_path.replace("/", os.sep)),
            content=m.content))
    return report


def _extract_definition(content: str) -> str:
    """从渲染好的原子笔记里抠出"定义" quote 块的内容。"""
    m = _QUOTE_DEF_RE.search(content)
    if not m:
        return ""
    return m.group(1).strip()


def _extract_detail(content: str) -> str:
    """从渲染好的原子笔记里抠出 '## 📝 详细说明' 段的内容。"""
    m = _DETAIL_RE.search(content)
    if not m:
        return ""
    return m.group(1).strip()


def _build_merge_addendum(concept_note, professional_name: str, existing_content: str = "") -> str:
    """为"合并进既有概念"构造要追加的文本块: 新来源 + 从新课件提取的增量定义/说明。

    返回空串表示本次没有新的可用信息(纯重复), 判定跳过以避免制造重复内容。
    只有当新课件确实带出与既有笔记不同的补充视角时才追加, 避免无意义堆积。
    """
    content = getattr(concept_note, "content", "") or ""
    src = getattr(concept_note, "source_file", "") or ""
    # GeneratedNote 里没有 source_file, 从 content 的"来源"行反推, 或直接用 title
    pages = concept_note.source_pages or ""

    definition = _extract_definition(content)
    detail = _extract_detail(content)

    blocks = []
    loc = ""
    # 来源文件: 从 content 尾部的 "PPT: 《xxx》" 提取
    m_src = _SRC_RE.search(content)
    if m_src:
        loc = m_src.group(1)
    if pages:
        loc = f"{loc} (第{pages}页)" if loc else f"(第{pages}页)"

    # 1) 若新 definition 与既有定义文字不同 -> 作为"另一来源的定义"补充
    _same_def = definition and existing_content and _norm(definition) in _norm(existing_content)
    if definition and not _same_def:
        blocks.append(f"> **补充定义(来自另一课件)**: {definition}")
    # 2) detail 里若有列表要点 -> 拆成小条目追加(真正的"增量知识")
    if detail:
        bullets = [l.strip().lstrip("- ").strip()
                   for l in detail.splitlines()
                   if l.strip().startswith("-")]
        if bullets:
            intro = f"**另一课件补充要点**{(' ('+loc+')') if loc else ''}:"
            blocks.append(intro)
            for b in bullets[:6]:
                blocks.append(f"- {b}")

    if not blocks:
        # 连可补充的定义/要点都没有, 但至少来源不同仍可记录一条"亦见于"
        if loc:
            blocks.append(f"> **亦见于**:《{loc}》")
        else:
            return ""

    head = f"\n\n---\n### 🔁 增量 · {professional_name} 补充"
    return head + "\n" + "\n".join(blocks) + "\n"


def apply_merge(report: MergeReport, log=None) -> List[str]:
    """执行合并计划, 返回写入/合并的文件路径清单。落盘一次性完成。"""
    written = []
    def _log(m):
        if log:
            log(m)
    for act in report.actions:
        if act.kind == "dup_same_prof":
            _log(f"  · 概念「{act.concept}」已在 vault 存在且无可补充, 跳过新建(避免重复节点)")
            continue
        if act.kind == "merge":
            try:
                with open(act.target_path, "a", encoding="utf-8") as f:
                    f.write(act.content)
                written.append(act.target_path)
                _log(f"  · 合并「{act.concept}」→ 已有笔记, 补充了新来源")
            except Exception as e:
                _log(f"  · 合并「{act.concept}」失败: {e}")
            continue
        # new / cross_prof / create_moc -> 全量写入(可能覆盖同名旧MOC属正常刷新)
        os.makedirs(os.path.dirname(act.target_path) or ".", exist_ok=True)
        try:
            with open(act.target_path, "w", encoding="utf-8") as f:
                f.write(act.content)
            written.append(act.target_path)
        except Exception as e:
            _log(f"  · 写入 {act.target_path} 失败: {e}")
    return written
