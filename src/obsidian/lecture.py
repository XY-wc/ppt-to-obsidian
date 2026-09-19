# -*- coding: utf-8 -*-
"""
讲义式笔记生成器 (Lecture-style Notes Generator) —— 正式默认输出形态。

针对用户反馈("原子卡太碎、内容像一段文字、不像笔记")重做的生成路线:

  大纲(按课件原生小节, 如 1.1/1.2/...)  ->  逐节深写为"结构化讲义"
    - 每个课件小节 = 一个 .md 文件(与 MOC/概念卡不同, 信息不碎)
    - 文件内部: ### 子题 + 表格/要点列表/LaTeX 公式 + 少量连贯叙述 + Obsidian callout
    - 关键图/表/例题页: 模型用 `<!--截图:pNN-->` 占位, 落盘时渲染 PPT/PDF 对应页为 PNG 嵌入
    - 正文可内联 [[相关小节]] 链接(落盘时映射为真实文件名, 形成知识关联)

对外: summarize_lecture(doc, prof, llm, progress, extra_context) -> LectureBundle
      build_lecture_files(bundle, course_dir, source_path) -> 写入 index+各节+attachments
"""
import os
import os
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable

from core.knowledge_base import Profession
from core.ppt_parser import PresentationDoc
from obsidian import staged
from obsidian.staged import (_fill, _chat_json, _range_to_indices, _slice_by_indices,
                             _per_slide_preview, RETRY_PER_CALL, OUT_TOKENS_CAP)

# 大纲节数范围(讲义粒度: 一课件小节一文件)
LECT_MIN_SECTIONS = 8
LECT_MAX_SECTIONS = 14
# 每节最多截图占位(避免堆图)
MAX_SHOTS_PER_SECTION = 3
# 一页文本 < 该阈值视为"少字页(可能图/公式为主)"
SHORT_PAGE_CHARS = 18

SHOT_RE = re.compile(r"<!--\s*截图\s*:\s*(?:p|第)?\s*(\d{1,4})\s*页?\s*-->")


# ---------------- 数据容器 ----------------

@dataclass
class LectureSection:
    num: str = ""           # 顺序/课件编号, 如 '1.5'
    title: str = ""         # 无编号标题, 如 '焓'
    stem: str = ""          # 落盘文件名主干, 如 '1.5-焓'
    pages: List[int] = field(default_factory=list)
    body: str = ""          # markdown 正文(可含截图占位与 [[链接]])


@dataclass
class LectureBundle:
    """一次讲义式转换的完整结果。"""
    title: str = ""
    source_file: str = ""
    sections: List[LectureSection] = field(default_factory=list)
    used_llm: bool = False
    warnings: List[str] = field(default_factory=list)

    @property
    def image_pages(self) -> List[int]:
        """正文里被标注要截图的页面(保序去重)。"""
        out, seen = [], set()
        for s in self.sections:
            for m in SHOT_RE.finditer(s.body or ""):
                p = int(m.group(1))
                if p not in seen and p in (s.pages or []):
                    seen.add(p)
                    out.append(p)
        return out


# ---------------- Prompt ----------------

LECT_OUTLINE_SYSTEM = """你是一位严谨的{prof_name}课程大纲规划师。你会拿到课件每一页的【标题 + 一段很短的预览】。
这份课件通常自带**小节编号**(如 '1.1 热力学概论'、'1.2 热力学基本概念'), 请**沿用编号把课件切成 8~14 个小节**:
一个小节(如 1.1)就是一节, 不要随意把多个编号小节合并成大节; 只有课件本身确实没有编号时, 才按主题合理切节。

## 规则
- 章节覆盖课件从头到尾各大块; 复习/习题页若占一大段(多页成块), 单独作为一节(标题 '复习与习题' 之类)。
- heading 直接写课件编号标题, 例如 "1.1 热力学概论"; 无编号的按主题命名。
- 每节给出 pages(覆盖页码范围, 如 '4-6', 1-based); 相邻节可在边界页重叠, 我们会去重。
- 不要漏掉任何大段主题; 过渡页/纯图页不要硬凑成节。
- 若【本专业概念清单】存在, 只作命名词典, 不是切节依据。

## 输出(严格 JSON, 不要 markdown 围栏)
{{
  "title": "本课件/本章主标题(简短, 如 '物理化学 · 热力学第一定律')",
  "sections": [
    {{"heading": "1.1 热力学概论", "pages": "4-6", "focus": "这一节重点讲什么(一句)"}}
  ]
}}
"""

LECT_OUTLINE_USER = """===专业/课件信息===
{kb}

===课件每页预览(标题+片段)===
{preview}

请输出按小节切好的大纲 JSON。
"""

LECT_SECTION_SYSTEM = """你是一位经验丰富的大学课程笔记整理者。给你一份课件【某一节】的全部页面文字，
把它整理成可直接放进 Obsidian 的高质量课程笔记(markdown)。风格参照"学霸整理的教材配套笔记":
结构清晰、信息密集、一眼能扫, 但绝不是把课件文字整段照抄。

## 硬性规则
1. 只写本节能看到的页面(页面文字是唯一事实来源); 不许编造课件没讲的结论。
2. 禁止大段连续散文。长句/列表项必须拆成结构化元素: 表格 / 项目符号 / 编号步骤 / 公式块。
3. 每个子主题用 `### 主题名` 起头, 与课件该节内部的小标题一一对应, 一个不漏。
4. 能提炼成表的一定给表(分类对比/特点/适用条件/符号含义); 表头清楚、行文精简。
5. 数学公式用 LaTeX: 行内符号用 $Q$、$W$、$\\Delta U$; 成块公式单独一行用 $$ ... $$。
   只写课件实际出现的公式; 若文本中符号缺失/乱码, 按该领域通用标准式补全, 不额外发挥。
6. 每个 `###` 下允许 1~3 句简短引导性叙述(讲清这组要点的逻辑), 之后立即用表格/列表展开。
7. 重点/易混淆处用 **加粗** 或 1 行 `> [!note]`/`> [!warning]`/`> [!tip]` 简注; 不要堆砌。
8. **知识关联链接**: 下方会给【本课全部小节目录】。若本节讲到与其它小节直接相关的概念/内容,
   在该处行尾写一个 Obsidian 双链, 例如 `(详见[[焓]])` 或 `见[[1.4 体积功与可逆过程]]`。
   链接目标只从目录里选, 每节最多 4 个, 不硬凑。
9. **关键页截图**: 若本节包含【例题 / For Example / 整页题目 / 难以用文字重建的示意图/流程框图】页,
   在对应子主题内容之后**另起一行**写 `【课件截图：第N页】`, N 为页号(必须在本节页面清单内, 例题页优先)。
   每节不超过 {shotcap} 个; 纯普通文字页不要标; 拿不准就不要标, 系统会自动在节末补例题原页。
10. 不要输出 YAML、不要 ```md 围栏、不要出现"以下是…"等解说性客套。直接从第一个 `###` 开始输出正文。

输出 = 该节 Obsidian 笔记正文(纯 markdown)。
"""

LECT_SECTION_USER = """===本节标题===
{heading}

===本节重点===
{focus}

===本课全部小节目录(链接只从这里面选)===
{toc}

===本节页面清单(供截图选择; [少字] 表示可能以图/公式为主)===
{page_meta}

===本节页面原文(第 {pages} 页)开始===
{section_text}
===本节页面原文结束===

请输出该节整理笔记正文。"""


# 统一占位格式: 支持"【课件截图：第N页】"与旧 HTML 注释; 归一为 <!--截图:N-->
def _normalize_shots(body: str) -> str:
    return re.sub(r"【\s*(?:课件)?截图\s*[:：]?\s*(?:第\s*|p\s*)?(\d{1,4})\s*页?\s*】",
                  r"<!--截图:\1-->", body or "")


# 例题/原题页线索(文字重建困难, 适合放原页截图)
_EX_PAT = re.compile(r"For Example|Example|例\s*[：:]|例题|试计算|计算下列|已知\s*[：:]|"
                     r"求\s*[：:]|习题|练习|多选题|单选题|填空题", re.I)


def _example_pages(doc, indices: List[int], cap: int = 2) -> List[int]:
    """在该节页码里找"例题/原题/自测页"(文字版公式难重建 -> 附录截图兜底)。"""
    by_idx = {s.index: s for s in doc.slides}
    hits = []
    for p in sorted(set(indices)):
        s = by_idx.get(p)
        if s is None:
            continue
        txt = _clean_page_noise((s.to_text() or ""))
        if txt and _EX_PAT.search(txt):
            hits.append(p)
    return hits[:cap]


_DATE_NOISE_RE = re.compile(r"\d{4}\s*[年/\-]\s*\d{1,2}\s*[月/\-]\s*\d{1,2}\s*日?\s*(?:星期\w)?|"
                            r"[-–]\s*年\s*[-–]\s*月\s*[-–]\s*日")


def _clean_page_noise(txt: str) -> str:
    """去掉课件页脚日期戳(如 '2026 8 30 年 月 日星期日' / '- 年 - 月 - 日')。"""
    return _DATE_NOISE_RE.sub("", txt or "").strip()


# ---------------- 工具 ----------------

def _num_title(heading: str):
    """'1.1 热力学概论' -> ('1.1', '热力学概论'); '复习与习题' -> (None, '复习与习题')"""
    h = str(heading).strip()
    m = re.match(r"^(\d+(?:\.\d+)?)\s*[、.\s]*(.*)$", h)
    if m and m.group(2).strip():
        return m.group(1), m.group(2).strip()
    return None, h


def _reindex_sections(sections: List[dict]) -> List[dict]:
    """规整编号: 全带编号且严格递增则沿用; 否则按出现顺序从 1.1 重编。"""
    nums = []
    for s in sections:
        num, _ = _num_title(str(s.get("heading", "")))
        nums.append(num)
    # 判断是否可沿用
    prev = None
    usable = True
    for n in nums:
        if n is None:
            usable = False
            break
        cur = tuple(int(x) for x in n.split("."))
        if prev is not None and not (cur > prev):
            usable = False
            break
        prev = cur
    out = []
    if usable:
        for i, s in enumerate(sections):
            num, title = _num_title(str(s.get("heading", "")))
            title = title or "未命名节"
            out.append({"num": num, "title": title, "heading": str(s.get("heading", "")).strip(),
                        "pages": str(s.get("pages", "")).strip(), "focus": str(s.get("focus", "")).strip(),
                        "stem": f"{num}-{_slug(title)}"})
        return out
    # 重编
    for i, s in enumerate(sections):
        num = f"1.{i + 1}"
        _, title = _num_title(str(s.get("heading", "")))
        title = title or "未命名节"
        out.append({"num": num, "title": title, "heading": str(s.get("heading", "")).strip(),
                    "pages": str(s.get("pages", "")).strip(), "focus": str(s.get("focus", "")).strip(),
                    "stem": f"{num}-{_slug(title)}"})
    return out


def _slug(name: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\r\n]+', "_", str(name)).strip()
    return s[:60] or "note"


def _page_meta_lines(doc, indices: List[int]) -> str:
    """每页一行: p23(108字·文字) / p30(6字·[少字]) 供模型选截图页。"""
    by_idx = {s.index: s for s in doc.slides}
    lines = []
    for p in sorted(set(indices)):
        s = by_idx.get(p)
        if s is None:
            continue
        t = _clean_page_noise((s.to_text() or ""))
        n = len(t)
        tag = " [少字·可能图/公式]" if n < SHORT_PAGE_CHARS else " [文字页]"
        head = re.sub(r"\s+", " ", t)[:28]
        lines.append(f"  p{p} ({n}字){tag} | {head}")
    return "\n".join(lines)


# ---------------- 生成 ----------------

def _lecture_outline(doc, prof, kb_ctx, llm, progress) -> Optional[Dict]:
    previews = _per_slide_preview(doc, width=100)
    pv_txt = "\n".join(
        f"[第{p['index']}页]{(' ' + p['title']) if p['title'] else ''}"
        + ("" if p["image_only"] or p["empty"] else f" — {p['preview']}")
        for p in previews
    )
    sys_msg = _fill(LECT_OUTLINE_SYSTEM, prof_name=prof.name,
                    MIN=LECT_MIN_SECTIONS, MAX=LECT_MAX_SECTIONS)
    usr = _fill(LECT_OUTLINE_USER, kb=kb_ctx or "(无专业概念清单)", preview=pv_txt)
    data = _chat_json(llm, sys_msg, usr, progress, stage="规划课件小节大纲", max_tokens=5000)
    return data


def _write_one_section(doc, sec: dict, toc_txt: str, llm, progress) -> Optional[str]:
    """深写一节正文; 返回 markdown(含截图占位)。"""
    indices = _range_to_indices(sec.get("pages", ""), doc.total_pages)
    if not indices:
        return None
    section_text = _slice_by_indices(doc, indices)
    if not section_text.strip():
        return None
    heading = f"{sec['num']} {sec['title']}" if sec.get("num") else sec.get("title", "未命名节")
    sys_msg = _fill(LECT_SECTION_SYSTEM, shotcap=MAX_SHOTS_PER_SECTION)
    usr = _fill(LECT_SECTION_USER,
                heading=heading,
                focus=sec.get("focus", ""),
                toc=toc_txt,
                page_meta=_page_meta_lines(doc, indices),
                pages=f"{indices[0]}-{indices[-1]}",
                section_text=section_text)
    last = ""
    for attempt in range(RETRY_PER_CALL + 1):
        try:
            if progress:
                progress(f"正在整理「{heading}」…" + (f"(第{attempt + 1}次)" if attempt else ""))
            raw = llm.chat([{"role": "system", "content": sys_msg},
                            {"role": "user", "content": usr}], max_tokens=OUT_TOKENS_CAP)
            raw = (raw or "").strip()
            if not raw:
                last = "空输出"
                continue
            raw = re.sub(r"^```(?:markdown|md)?\s*", "", raw)
            raw = re.sub(r"\s*```$", "", raw)
            if raw:
                return raw
        except Exception as e:
            last = str(e)
            if progress:
                progress(f"  「{heading}」调用出错({e})")
            break
    if progress:
        progress(f"  「{heading}」未能产出({last or '无'}); 跳过该节")
    return None


def _map_wikilinks(body: str, sections: List[dict]) -> str:
    """把正文 [[标题]] 链接映射到真实文件名(num-stem)。
    目录匹配: 完整 '1.5 焓' 或 '焓' 或 '1.5-焓'(已是文件名则保留)。"""
    stems = {s["stem"] for s in sections}
    title_aliases = []  # (norm, stem)
    for s in sections:
        title_aliases.append((_norm(s["title"]), s["stem"]))
        full = f"{s['num']} {s['title']}" if s.get("num") else s["title"]
        title_aliases.append((_norm(full), s["stem"]))
    full_aliases = {n: st for n, st in title_aliases if n}

    def _rep(m):
        target = m.group(1).strip()
        if target in stems:
            return f"[[{target}]]"
        nk = _norm(target)
        st = full_aliases.get(nk)
        if st:
            return f"[[{st}]]"
        return m.group(0)

    return re.sub(r"\[\[([^\]\|]+)\]\]", _rep, body)


def _norm(s: str) -> str:
    return re.sub(r"[\s\.\-_·/\\:：（）()]+", "", str(s)).lower() if s else ""


def summarize_lecture(doc: PresentationDoc, prof: Profession, llm,
                      progress: Optional[Callable[[str], None]] = None,
                      extra_context: str = "",
                      max_sections: int = LECT_MAX_SECTIONS) -> LectureBundle:
    """讲义式生成入口(在线)。失败/无模型时返回 warnings, 由上层决定降级。"""
    bundle = LectureBundle(title=doc.name, source_file=os.path.basename(doc.path))
    if llm is None:
        bundle.warnings.append("讲义式需要在线大模型; 未绑定模型时请改用概念卡/本地模式。")
        return bundle
    kb_ctx = f"【专业】{prof.name}"
    if prof.core_concepts:
        kb_ctx += "\n【本专业核心概念清单(仅命名参考)】" + "、".join(
            c.get("name", "") for c in prof.core_concepts[:40] if c.get("name"))
    if extra_context:
        kb_ctx += "\n" + extra_context

    # ---- 1. 大纲 ----
    data = _lecture_outline(doc, prof, kb_ctx, llm, progress)
    if not data or not data.get("sections"):
        bundle.warnings.append("大纲规划失败(未产出小节), 讲义式无法继续。")
        return bundle
    bundle.title = str(data.get("title") or doc.name).strip()
    raw_secs = data["sections"][:max_sections]
    sections = _reindex_sections(raw_secs)

    # 全课目录文本(供每节做关联链接)
    toc_txt = "\n".join(
        f"{s['num']} {s['title']}" for s in sections)

    # ---- 2. 逐节深写 ----
    done = []
    for i, s in enumerate(sections):
        # 目录: 其它小节才可链(不链自身)
        others = [x for x in sections if x["num"] != s["num"]]
        _toc = "\n".join(f"{x['num']} {x['title']}" for x in others) or "(仅一节)"
        body = _write_one_section(doc, s, _toc, llm, progress)
        if not body:
            bundle.warnings.append(f"小节「{s['title']}」未能生成, 已跳过。")
            continue
        # 归一截图占位格式(支持【课件截图：第N页】)
        body = _normalize_shots(body)
        # 越界占位直接删除
        sec_indices = _range_to_indices(s.get("pages", ""), doc.total_pages)
        for m in SHOT_RE.finditer(body):
            p = int(m.group(1))
            if p not in sec_indices:
                body = body.replace(m.group(0), "")
        # 兜底: 该节没有任何截图占位时, 自动把例题/原题页附在节末(文字版公式难重建, 截图还原原题)
        if not SHOT_RE.search(body):
            auto = _example_pages(doc, sec_indices)
            if auto:
                block = "\n\n## 📎 本节课件原题/原页\n\n"
                for p in auto:
                    block += f"<!--截图:{p}-->\n\n"
                body = body.rstrip() + block
        # 链接映射
        body = _map_wikilinks(body, sections)
        # 过滤指向自己的链接
        body = re.sub(rf"\[\[{re.escape(s['stem'])}\]\]", "", body)
        done.append(LectureSection(
            num=s["num"], title=s["title"], stem=s["stem"], pages=sec_indices,
            body=body))
        if progress:
            progress(f"  ✓ 「{s['num']} {s['title']}」完成")
    bundle.sections = done
    bundle.used_llm = bool(done)
    if not done:
        bundle.warnings.append("所有小节均未能生成。")
    return bundle


# ================= 落盘渲染 + 截图 =================


def _frontmatter(title, source, tags, extra=None) -> str:
    lines = ["---", f'title: "{title}"', "type: knowledge", f'source: "{source}"',
             "tags:"]
    for t in tags:
        lines.append(f"  - {t}")
    lines.append("---")
    lines.append("")
    lines.append(f"# {title}")
    lines.append("")
    return "\n".join(lines)


def _render_pdf_page(src_pdf: str, page_no: int, out_png: str, dpi: int = 110) -> bool:
    """把 PDF 第 page_no(1-based) 页渲染成 PNG。"""
    try:
        import pymupdf  # PyMuPDF
    except Exception:
        import fitz as pymupdf
    try:
        doc = pymupdf.open(src_pdf)
        if 1 <= page_no <= doc.page_count:
            pix = doc[page_no - 1].get_pixmap(dpi=dpi)
            pix.save(out_png)
        doc.close()
        return os.path.isfile(out_png) and os.path.getsize(out_png) > 500
    except Exception:
        return False


def find_soffice() -> Optional[str]:
    """尽力查找 LibreOffice soffice(用于 PPT/PPTX -> PDF 以支持截图)。"""
    import shutil
    for cand in ("soffice", "libreoffice"):
        p = shutil.which(cand)
        if p:
            return p
    for cand in (r"C:\Program Files\LibreOffice\program\soffice.exe",
                 r"C:\Program Files (x86)\LibreOffice\program\soffice.exe"):
        if os.path.isfile(cand):
            return cand
    return None


def convert_ppt_to_pdf(src: str, out_dir: str) -> Optional[str]:
    """PPT/PPTX -> PDF(需 soffice); 失败返回 None。"""
    soffice = find_soffice()
    if not soffice:
        return None
    import subprocess
    try:
        subprocess.run([soffice, "--headless", "--convert-to", "pdf", "--outdir", out_dir, src],
                       timeout=120, check=True, capture_output=True)
        base = os.path.splitext(os.path.basename(src))[0]
        pdf = os.path.join(out_dir, base + ".pdf")
        return pdf if os.path.isfile(pdf) else None
    except Exception:
        return None


def _render_shot(source_path: str, page_no: int, out_png: str, tmp_dir: Optional[str] = None) -> bool:
    """source_path 为 pdf 直接渲染; 为 pptx 时临时转 pdf 后渲染(失败 False)。"""
    ext = os.path.splitext(source_path)[1].lower()
    if ext == ".pdf":
        return _render_pdf_page(source_path, page_no, out_png)
    if ext in (".ppt", ".pptx"):
        tmp = tmp_dir or os.path.dirname(out_png)
        pdf = convert_ppt_to_pdf(source_path, tmp)
        if not pdf:
            return False
        ok = _render_pdf_page(pdf, page_no, out_png)
        try:
            if pdf != source_path and os.path.isfile(pdf):
                os.remove(pdf)
        except Exception:
            pass
        return ok
    return False


def build_lecture_files(bundle: LectureBundle, course_dir: str, source_path: str,
                        progress: Optional[Callable[[str], None]] = None) -> List[str]:
    """把讲义 bundle 落盘到 course_dir/课夹名/ 下: index.md + {stem}.md + attachments/截图。
    返回写出的文件路径清单。重复转换同课件时整夹刷新(覆盖式)。"""
    folder = _slug(bundle.title) or "notes"
    if folder in (".", ".."):
        folder = "notes"
    out_dir = os.path.join(course_dir, folder)
    # 该课件专属夹: 转换即整夹刷新(清掉上次切节变化残留的旧文件)
    if os.path.isdir(out_dir):
        import shutil
        shutil.rmtree(out_dir, ignore_errors=True)
    att_dir = os.path.join(out_dir, "attachments")
    os.makedirs(att_dir, exist_ok=True)
    written = []
    src_stem = _slug(os.path.splitext(bundle.source_file)[0])
    sections = list(bundle.sections)
    for s in sections:
        s.body = _normalize_shots(s.body)

    # ---- 1. 渲染截图(去重页)并把占位替换成图片引用 ----
    shot_map = {}
    shots_ok = 0
    for s in sections:
        for m in list(re.finditer(SHOT_RE, s.body or "")):
            p = int(m.group(1))
            if p not in shot_map:
                png_name = f"{src_stem}_p{p}.png"
                if _render_shot(source_path, p, os.path.join(att_dir, png_name)):
                    shot_map[p] = png_name
                    shots_ok += 1
                    written.append(os.path.join(att_dir, png_name))
                    if progress:
                        progress(f"  🖼️ 已截图第 {p} 页 -> attachments/{png_name}")
                else:
                    if progress:
                        progress(f"  (第 {p} 页截图失败, 移除占位)")
            if p in shot_map:
                s.body = s.body.replace(m.group(0),
                                        f"\n\n![课件原图 · 第{p}页](attachments/{shot_map[p]})\n", 1)
            else:
                s.body = s.body.replace(m.group(0), "", 1)  # 渲染失败 -> 不残留占位

    # ---- 2. 逐节写文件 ----
    for i, s in enumerate(sections):
        prev_ = sections[i - 1] if i > 0 else None
        nxt_ = sections[i + 1] if i < len(sections) - 1 else None
        parts = []
        if prev_:
            parts.append(f"← 上一节：[[{prev_.stem}|{prev_.num} {prev_.title}]]")
        if nxt_:
            parts.append(f"→ 下一节：[[{nxt_.stem}|{nxt_.num} {nxt_.title}]]")
        nav = ("\n\n---\n\n" + "　".join(parts)) if parts else ""
        title_line = f"{s.num} {s.title}" if s.num else s.title
        head = _frontmatter(title_line, bundle.source_file, ["层级/正文"])
        fp = os.path.join(out_dir, f"{s.stem}.md")
        with open(fp, "w", encoding="utf-8") as f:
            f.write(head + s.body.rstrip() + "\n" + nav + "\n")
        written.append(fp)

    # ---- 3. index ----
    # frontmatter 里的 层级/* tag 供 Obsidian 关系图按层级上色(见 obsidian/graph_config.py)
    idx = ["---", f'title: "{bundle.title}"', "type: knowledge",
           f'source: "{bundle.source_file}"', "tags:", "  - 课程", "  - 层级/目录", "---", "",
           f"# 📗 {bundle.title}", "",
           f"> 由《{bundle.source_file}》自动整理 · 本页为章目录，点击跳转各节笔记", "", "## 目录", ""]
    for s in sections:
        idx.append(f"- **{s.num}** {s.title} — [[{s.stem}]]")
    idx += ["", "---", "*每节含表格/公式/要点与课件截图；节末可跳上一节/下一节。*"]
    if shots_ok:
        idx.append(f"\n*含 {shots_ok} 张课件原页截图，见各节内嵌图片。*")
    ip = os.path.join(out_dir, "index.md")
    with open(ip, "w", encoding="utf-8") as f:
        f.write("\n".join(idx) + "\n")
    written.append(ip)

    if progress:
        progress(f"✅ 讲义落盘完成: {len(sections)} 节 + index, 截图 {shots_ok} 张 -> {out_dir}")
    return written


# 方便 pipeline 引用文件主干名
def section_file_names(bundle: LectureBundle) -> List[str]:
    return [f"{s.stem}.md" for s in bundle.sections]
