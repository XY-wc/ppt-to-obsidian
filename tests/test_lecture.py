# -*- coding: utf-8 -*-
"""讲义式生成器(lecture)回归测试: 大纲分节/正文/链接映射/截图占位/落盘。"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from core.knowledge_base import KnowledgeBase, Profession
from core.ppt_parser import PresentationDoc, Slide, SlideBlock
from obsidian import summarizer as S
from obsidian import lecture as L


def _make_doc(pages_text):
    d = PresentationDoc(path="mock.pdf", name="mock")
    for i, line in enumerate(pages_text):
        d.slides.append(Slide(index=i + 1, title=line[:10] or None,
                              blocks=[SlideBlock(kind="text", text=line, top=0, left=0)]))
    return d


def _mk_prof():
    kb = KnowledgeBase()
    kb.professions = {}
    prof = Profession(id="p", name="物理化学", core_concepts=[],
                      symbol_mapping=[], common_misconceptions=[], note_template="")
    prof.build_index()
    return prof


class LectureMock:
    """按阶段(sys 关键词)分发: lecture 大纲 / lecture 正文 / cards 大纲(不应触发)。"""
    def __init__(self):
        self.calls = 0

    def chat(self, messages, max_tokens=4096):
        self.calls += 1
        sys_ = messages[0]["content"]
        # lecture 大纲
        if "小节编号" in sys_:
            return ('{"title":"热力学第一定律","sections":['
                    '{"heading":"1.1 热力学概论","pages":"1-2","focus":"概论"},'
                    '{"heading":"1.2 热力学基本概念","pages":"3-4","focus":"概念"}]}')
        # lecture 分节正文
        if "学霸" in sys_:
            return ('### 1. 系统与环境\n\n'
                    '系统是研究对象。\n\n'
                    '| 类型 | 说明 |\n| --- | --- |\n| 封闭 | 无物质交换 |\n\n'
                    '$$dU = \\delta Q + \\delta W$$\n\n'
                    '参见[[1.2 热力学基本概念]]。\n\n'
                    '<!--截图:p99-->\n')   # p99 越界(仅1-2页)会被过滤
        return '{"title":"x","sections":[]}'


def test_helpers():
    assert L._num_title("1.1 热力学概论") == ("1.1", "热力学概论")
    assert L._num_title("复习与习题") == (None, "复习与习题")
    secs = L._reindex_sections([{"heading": "1.1 a", "pages": "1"}, {"heading": "b", "pages": "2"}])
    assert [(s["num"], s["title"], s["stem"]) for s in secs] == [
        ("1.1", "a", "1.1-a"), ("1.2", "b", "1.2-b")]
    # 全带编号且递增 -> 沿用
    secs2 = L._reindex_sections([{"heading": "2.1 x", "pages": "1"}, {"heading": "2.2 y", "pages": "2"}])
    assert [s["num"] for s in secs2] == ["2.1", "2.2"]


def test_link_mapping():
    secs = [{"num": "1.1", "title": "热力学概论", "stem": "1.1-热力学概论"},
            {"num": "1.2", "title": "热力学基本概念", "stem": "1.2-热力学基本概念"}]
    out = L._map_wikilinks("见[[热力学概论]]和[[1.2 热力学基本概念]]; [[无关]]", secs)
    assert "[[1.1-热力学概论]]" in out
    assert "[[1.2-热力学基本概念]]" in out
    assert "[[无关]]" in out


def test_summarize_lecture_produces_sections():
    prof = _mk_prof()
    llm = LectureMock()
    doc = _make_doc(["概论页", "概论页2", "概念页", "概念页2"])
    b = L.summarize_lecture(doc, prof, llm)
    assert b.used_llm is True
    assert len(b.sections) == 2
    assert b.sections[0].num == "1.1" and b.sections[0].title == "热力学概论"
    # 正文是结构化 markdown, 含公式块
    assert "### " in b.sections[0].body
    assert "$$" in b.sections[0].body
    # 越界截图占位 p99 被过滤(1-2 页内才保留)
    assert "截图" not in b.sections[0].body
    # 目录内指向其它小节的链接映射为真实文件名
    assert "[[1.2-热力学基本概念]]" in b.sections[0].body
    # 指向自己的链接被过滤(第一节 mock 若链 1.1 会被去自链)
    assert "[[1.1-热力学概论]]" not in b.sections[0].body


def test_shot_normalize_and_example_fallback():
    # 中文截图占位归一化为 HTML 注释
    assert L._normalize_shots("见【课件截图：第12页】") == "见<!--截图:12-->"
    assert L._normalize_shots("见【截图:p5】") == "见<!--截图:5-->"
    # 例题页检测
    doc = _make_doc(["绪论", "例：计算某反应的焓变", "普通概念页"])
    ex = L._example_pages(doc, [1, 2, 3])
    assert 2 in ex
    # summarize_lecture 兜底: 模型没标占位时, 例题页自动附在节末
    class NoShotLLM(LectureMock):
        def chat(self, messages, max_tokens=4096):
            if "小节编号" in messages[0]["content"]:
                return ('{"title":"T","sections":[{"heading":"1.1 绪论","pages":"1-3","focus":"f"}]}')
            if "学霸" in messages[0]["content"]:
                return "### 绪论要点\n\n$$x$$\n\n正文内容。"
            return '{"title":"x","sections":[]}'
    prof = _mk_prof()
    b = L.summarize_lecture(doc, prof, NoShotLLM())
    assert b.used_llm
    assert "<!--截图:2-->" in b.sections[0].body  # 例题页被自动附录


def test_build_files_and_screenshots():
    # 用真实 mini PDF 验证截图路径 + index + 各节落盘
    import pymupdf
    tmp = tempfile.mkdtemp()
    pdf = os.path.join(tmp, "c.pdf")
    d = pymupdf.open()
    pg = d.new_page(width=300, height=200)
    try:
        pg.insert_text((30, 60), "demo page", fontsize=12)
    except Exception:
        pass
    d.save(pdf); d.close()
    pdoc = _make_doc(["第1页内容", "第2页内容"])
    pdoc.path = pdf
    b = L.LectureBundle(title="物理化学", source_file="c.pdf", used_llm=True)
    b.sections = [L.LectureSection(num="1.1", title="概论", stem="1.1-概论", pages=[1, 2],
                                   body="### 子题\n\n参见[[1.1-概论]]\n\n<!--截图:p1-->\n\n正文")]

    out_dir = os.path.join(tmp, "vault")
    written = L.build_lecture_files(b, out_dir, pdf)
    md_files = [w for w in written if w.endswith(".md")]
    png_files = [w for w in written if w.endswith(".png")]
    assert any("index.md" in w for w in md_files)
    assert any("1.1-概论.md" in w for w in md_files)
    assert len(png_files) == 1  # p1 截图成功
    # 正文里占位被替换成图片引用
    content = open([w for w in md_files if "1.1-概论.md" in w][0], encoding="utf-8").read()
    assert "attachments/c_p1.png" in content
    assert "<!--截图" not in content


def test_build_files_screenshot_fail_keeps_text():
    tmp = tempfile.mkdtemp()
    b = L.LectureBundle(title="T", source_file="nonexist.pdf", used_llm=True)
    b.sections = [L.LectureSection(num="1.1", title="a", stem="1.1-a", pages=[5],
                                   body="正文\n\n<!--截图:p5-->\n")]
    written = L.build_lecture_files(b, os.path.join(tmp, "v"), os.path.join(tmp, "missing.pdf"))
    content = open([w for w in written if w.endswith("1.1-a.md")][0], encoding="utf-8").read()
    assert "<!--截图" not in content  # 渲染失败时占位被清除, 不残留


if __name__ == "__main__":
    test_helpers(); print("✓ helpers")
    test_link_mapping(); print("✓ links")
    test_summarize_lecture_produces_sections(); print("✓ summarize_lecture")
    test_build_files_and_screenshots(); print("✓ files+screenshots")
    test_build_files_screenshot_fail_keeps_text(); print("✓ screenshot fail")
    print("\n全部通过")
