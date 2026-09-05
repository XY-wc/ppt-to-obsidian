# -*- coding: utf-8 -*-
"""
PPT → Obsidian 笔记 全流程编排 (pipeline orchestrator)。

把各子模块串成一条可被 GUI 调用的完整流水线:
   选择专业 → 选PPT → 解析 → (绑定的模型)专业驱动总结 → Obsidian原子笔记渲染 → 落盘

对外提供:
  - run_conversion(ppt_path, profession_id, vault_dir, account, llm=None, progress=...)
    → ConversionResult(含生成的 .md 文件清单 + 图省事的 bundle)
"""
import os
import sys
import traceback
from dataclasses import dataclass, field
from typing import List, Optional, Callable

# 保证可被当脚本运行
_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.dirname(_HERE)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from core.knowledge_base import KnowledgeBase
from core.document_loader import ingest_document, PresentationDoc
from obsidian.summarizer import summarize, NoteBundle
from obsidian import note_template as nt
from obsidian import vault_aware as va
from obsidian import kb_calibration as kbc
from obsidian.validator import validate_bundle, ValidationResult
from obsidian import lecture as lec
from llm.client import LLMClient, build_llm
from core.app_config import Account, AppPaths


@dataclass
class GeneratedNote:
    rel_path: str          # vault 相对路径(用 / )
    abs_path: str
    title: str
    kind: str              # moc | concept | misconception
    content: str
    source_pages: str = "" # 概念对应 PPT 页码(用于合并来源标注)
    source_file: str = ""


@dataclass
class ConversionResult:
    bundle: NoteBundle
    notes: List[GeneratedNote] = field(default_factory=list)
    vault_dir: str = ""
    ok: bool = False
    error: str = ""
    source_fulltext: str = ""   # PPT 全文(供对话修订上下文)
    validation: Optional["ValidationResult"] = None   # 知识质量体检(可空)
    written_files: List[str] = field(default_factory=list)   # 实际落盘文件(讲义模式等)


def _safe_name(name: str) -> str:
    return nt.slugify(name)


def _page_range_of_concept(concept) -> str:
    return concept.source_pages or ""


def render_bundle(bundle: NoteBundle, professional_name: str, source_file: str) -> List[GeneratedNote]:
    """把 NoteBundle 渲染成一组 Obsidian 笔记(纯内存, 未落盘)。"""
    notes: List[GeneratedNote] = []
    # 1) MOC 首页
    moc_sections = []
    for sec in bundle.sections:
        moc_sections.append({
            "heading": sec.heading,
            "summary": sec.summary,
            "concept_links": sec.concept_links,
        })
    moc_title = f"{bundle.title} · MOC"
    moc_md = nt.build_moc_note(
        moc_title=moc_title, professional=professional_name,
        source_file=source_file, sections=moc_sections,
        misconceptions=bundle.misconceptions or None,
    )
    notes.append(GeneratedNote(
        rel_path=f"{_safe_name(bundle.title)}/00_MOC.md",
        abs_path="", title=moc_title, kind="moc", content=moc_md))

    # 2) 每个概念一张原子笔记
    for c in bundle.concepts:
        # 双链 = related + 语义关系目标(去重, 保证图谱不缺链)
        backlinks = list(c.related or [])
        for r in (c.relations or []):
            tgt = str((r or {}).get("target", "")).strip()
            if tgt and tgt not in backlinks:
                backlinks.append(tgt)
        note_md = nt.build_atomic_note(
            concept=c.concept,
            definition=c.definition or "(见原文)",
            professional=professional_name,
            source_file=source_file,
            source_pages=c.source_pages,
            detail=c.detail,
            backlinks=backlinks,
            symbols=c.symbols,
            tags=[_safe_name(bundle.title)],
            ctype=c.ctype,
            importance=c.importance,
            confidence=c.confidence,
            evidence=c.evidence,
        )
        fn = _safe_name(c.concept)
        notes.append(GeneratedNote(
            rel_path=f"{_safe_name(bundle.title)}/{fn}.md",
            abs_path="", title=c.concept, kind="concept", content=note_md,
            source_pages=c.source_pages, source_file=source_file))

    # 3) 若命中了易错点, 单独一张卡
    if bundle.misconceptions:
        mis_md = nt.build_misconception_note(
            title=f"{bundle.title} · 易错点", professional=professional_name,
            source_file=source_file, misconceptions=bundle.misconceptions)
        notes.append(GeneratedNote(
            rel_path=f"{_safe_name(bundle.title)}/{_safe_name(bundle.title)}_易错点.md",
            abs_path="", title=f"{bundle.title}·易错点", kind="misconception", content=mis_md))

    return notes


def run_conversion(
    ppt_path: str,
    profession_id: str,
    vault_dir: str,
    account: Optional[Account] = None,
    model_index: int = 0,
    llm: Optional[LLMClient] = None,
    progress: Optional[Callable[[str], None]] = None,
    mode: str = "lecture",       # "lecture" 讲义式(默认) | "cards" 概念卡片式
) -> ConversionResult:
    """执行一次完整转换并落盘到 vault_dir。

    mode=lecture: 输出 index + 按课件小节分文件的结构化讲义(默认, 信息不碎、像人整理)。
    mode=cards:   旧版 原子概念卡 + MOC(适合想建概念图谱)。
    """
    res = ConversionResult(vault_dir=vault_dir, bundle=None)
    def log(m):
        if progress: progress(m)
    try:
        # 0) 校验
        if not os.path.isfile(ppt_path):
            raise ValueError(f"文件不存在: {ppt_path}")
        # 1) 选专业
        kb = KnowledgeBase()
        profs = kb.load_all()
        prof = kb.get(profession_id)
        if prof is None:
            if profs:
                prof = profs[0]
            else:
                raise ValueError("未加载到任何专业, 请检查 professions 目录")
        log(f"已选择专业: {prof.name}")

        # 2) 解析文档 (PPTX/PPT/PDF)
        log("正在解析文档…")
        doc = ingest_document(ppt_path)
        log(f"解析完成: {doc.name}, 共 {doc.total_pages} 页")

        # 3) 确定模型: 若外部没传 llm, 从账号模型构造
        client = llm
        if client is None and account is not None and model_index >= 0:
            models = account.get_models()
            if models:
                idx = model_index if 0 <= model_index < len(models) else 0
                m = models[idx]
                try:
                    client = build_llm(m)
                except Exception:
                    client = None
        if client is not None:
            log(f"使用模型: {client.model}")
        else:
            log("未绑定模型 → 使用本地规则模式(离线)")

        # 3b) 讲义式(默认): index + 按课件小节分文件 + 关键页截图 + 节内关联链接
        use_lecture = str(mode or "lecture").strip().lower() in ("lecture", "讲义", "讲义式")
        if use_lecture:
            if client is None:
                log("讲义式需要在线大模型; 当前未绑定模型 → 改用概念卡本地模式")
                use_lecture = False
            else:
                log("模式: 讲义式(按课件小节一文件 + 表格/公式/截图)")
                store = None
                extra_ctx = ""
                try:
                    store = kbc.CalibrationStore(prof.id, scope_dir=vault_dir or None)
                    extra_ctx = store.to_extra_context()
                except Exception:
                    store, extra_ctx = None, ""
                lb = lec.summarize_lecture(doc, prof, client, progress=log,
                                           extra_context=extra_ctx)
                if lb.used_llm and lb.sections:
                    if not vault_dir:
                        raise ValueError("未指定 Obsidian vault 输出目录")
                    os.makedirs(vault_dir, exist_ok=True)
                    written = lec.build_lecture_files(lb, vault_dir, ppt_path, progress=log)
                    res.written_files = written
                    # 供 GUI 列表/计数: 仅 .md 作为 notes
                    md_notes = []
                    for w in written:
                        if w.lower().endswith(".md"):
                            md_notes.append(GeneratedNote(
                                rel_path=os.path.relpath(w, vault_dir).replace(os.sep, "/"),
                                abs_path=w, title=os.path.basename(w)[:-3],
                                kind="lecture", content=""))
                    res.notes = md_notes
                    res.ok = True
                    for w in lb.warnings:
                        log(f"  (提示) {w}")
                    log(f"✅ 讲义完成: {len(lb.sections)} 节 + index, 输出 {len(written)} 个文件")
                    return res
                log(f"讲义生成未成功({('; '.join(lb.warnings))[:120]}), 自动降级为概念卡模式")
                use_lecture = False
        # 4) 概念卡片模式: 专业驱动总结(官方知识库 + 用户自学补充)
        store = None
        extra_ctx, extra_names = "", None
        try:
            # 记忆作用域 = 输出目录(整 Vault 分课程时即课程文件夹), 实现"一科一记忆"
            store = kbc.CalibrationStore(prof.id, scope_dir=vault_dir or None)
            extra_ctx = store.to_extra_context()
            extra_names = [c for c in store.concept_names() if c]
        except Exception:
            store, extra_ctx, extra_names = None, "", None
        if extra_ctx:
            log("已加载你的自学沉淀补充知识(越转越准)")

        bundle = summarize(doc, prof, llm=client, progress=log,
                           extra_context=extra_ctx,
                           extra_concept_names=extra_names)

        # 5) 渲染
        notes = render_bundle(bundle, prof.name, os.path.basename(ppt_path))
        res.notes = notes
        res.bundle = bundle

        # 5b) 知识质量体检: 覆盖 / 证据 / 链接 / AI补充 (可量化可追溯)
        try:
            validation = validate_bundle(doc, bundle)
            res.validation = validation
            log("📊 " + validation.to_brief())
            if validation.missing_concepts:
                log(f"   ⚠️ 可能漏掉大段内容 {len(validation.missing_concepts)} 处: {validation.missing_concepts[0]}")
            if validation.unsupported_concepts:
                log(f"   ⚠️ {len(validation.unsupported_concepts)} 个节点无溯源: {validation.unsupported_concepts[0]}")
            if validation.broken_links:
                log(f"   🔗 {len(validation.broken_links)} 个断链: [[{validation.broken_links[0]}]]")
        except Exception as e:
            log(f"(质量体检跳过: {e})")

        # 4b) 知识库自学: 把本讲新识别的概念沉淀进该专业补充库(复利)
        if store is not None:
            try:
                official = [c.get("name", "") for c in prof.core_concepts if c.get("name")]
                # LLM 结构化为真/本地兜底时 concept 名即命中清单概念
                llm_candidates = [c.concept for c in bundle.concepts
                                  if c.concept and not prof.contains_concept(c.concept)]
                if llm_candidates:
                    store.learn_from_text(doc.full_text(), official,
                                          candidate_terms=llm_candidates)
                    log(f"🧠 自学沉淀 {len(llm_candidates)} 个新概念到「{prof.name}」补充库")
                store.confirm_tally([c.concept for c in bundle.concepts])
            except Exception:
                pass

        # 6) Vault 感知落盘: 去重 / 合并 / 补双链
        if not vault_dir:
            raise ValueError("未指定 Obsidian vault 输出目录")
        os.makedirs(vault_dir, exist_ok=True)
        log("正在扫描 Vault 已有笔记…")
        index = va.index_vault(vault_dir)
        log(f"Vault 内已索引 {len(index.by_filename_norm)} 篇既有笔记")

        report = va.plan_merge(index, notes, prof.name, vault_dir)
        if report.merged:
            log(f"🔗 识别到 {len(report.merged)} 个 vault 已建立的概念, 将增量合并而不新建重复节点")
        if report.cross_prof:
            log(f"⚠️ {len(report.cross_prof)} 个跨专业同名概念, 保留新笔记并在报告中提示")
        if report.created:
            log(f"🆕 将新建 {len(report.created)} 个全新概念笔记")

        written = va.apply_merge(report, log=log)

        # 5c) 知识质量报告落盘(与概念同课夹, 不进 merge)
        try:
            if res.validation is not None:
                report_md = res.validation.to_markdown()
                if (report_md or "").strip():
                    title_slug = _safe_name(bundle.title) or "notes"
                    report_dir = os.path.join(vault_dir, title_slug)
                    os.makedirs(report_dir, exist_ok=True)
                    rp = os.path.join(report_dir, "99_知识质量报告.md")
                    with open(rp, "w", encoding="utf-8") as f:
                        f.write(report_md)
                    log(f"📄 质量报告已生成: {rp}")
        except Exception as e:
            log(f"(质量报告落盘失败: {e})")

        res.source_fulltext = doc.full_text()
        res.ok = True
        log(f"✅ 落盘完成: 写入 {len(written)} 个文件" +
            (f" (其中复用合并 {len(report.merged)} 个既有概念)" if report.merged else ""))
        if report.summary_lines:
            log("结果: " + "；".join(report.summary_lines))
        return res
    except Exception as e:
        res.error = f"{e}"
        log(f"错误: {e}")
        traceback.print_exc()
        res.ok = False
        return res


if __name__ == "__main__":
    # 简单自测(离线模式)
    import tempfile
    tmp = tempfile.mkdtemp()
    ppt = "../outputs/sample_pharmacy.pptx"
    ppt_abs = os.path.abspath(ppt) if os.path.exists(ppt) else os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "outputs", "sample_pharmacy.pptx")
    if not os.path.exists(ppt_abs):
        print("缺少示例PPT, 跳过。")
        sys.exit(0)
    out = os.path.join(tmp, "vault")
    r = run_conversion(ppt_abs, "pharmacy", out, progress=lambda m: print("  ·", m))
    print("OK:", r.ok, "| 错误:", r.error)
    if r.ok:
        for n in r.notes:
            print("  生成:", n.rel_path, f"({len(n.content)} 字符)")
