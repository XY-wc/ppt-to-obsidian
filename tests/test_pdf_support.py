# -*- coding: utf-8 -*-
"""PDF 支持验证: 解析器结构正确 + document_loader 路由 + pipeline 走通 PDF。"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

import pymupdf  # noqa
from core import document_loader as dl
from core.pdf_parser import parse_pdf
from pipeline import run_conversion


def _make_pdf(path):
    """生成一份"课件式"PDF(1页标题 + 1页小节), 模拟 PPT 导出。"""
    doc = pymupdf.open()
    fontfile = "C:/Windows/Fonts/simhei.ttf"
    if not os.path.exists(fontfile):
        fontfile = "C:/Windows/Fonts/msyh.ttc"
    pages = [
        [("药物代谢动力学概述", 26), ("吸收(Absorption)", 16),
         ("药物自给药部位进入体循环的过程。", 12)],
        [("分布(Distribution)", 20), ("随血液分布至各组织, 受血浆蛋白结合影响。", 12)],
    ]
    for lines in pages:
        page = doc.new_page(width=720, height=540)
        try:
            page.insert_font(fontname="f", fontfile=fontfile)
        except Exception:
            pass
        y = 90
        for text, size in lines:
            try:
                page.insert_text((60, y), text, fontsize=size, fontname="f")
            except Exception:
                page.insert_text((60, y), text, fontsize=size)
            y += size * 1.9
    doc.save(path)
    doc.close()


def _mock_llm_factory():
    """返回一个极简 stage-aware mock LLM, 按阶段输出合法 JSON。"""
    class MockLLM:
        model = "mock-pdf"
        def chat(self, messages, max_tokens=4096):
            sys_ = messages[0]["content"]
            # Pass1 大纲
            if "课程大纲规划师" in sys_:
                return json.dumps({
                    "title": "药物代谢动力学概述",
                    "sections": [
                        {"heading": "吸收", "pages": "1", "focus": "药物如何进入体循环"},
                        {"heading": "分布", "pages": "2", "focus": "药物如何分布至组织"},
                    ],
                }, ensure_ascii=False)
            # Pass2 分节深写
            if "某一节" in sys_:
                return json.dumps({
                    "summary": "本节能讲透核心。",
                    "concepts": [
                        {"concept": "生物利用度",
                         "definition": "药物进入体循环的相对程度",
                         "detail": "- 反映吸收程度",
                         "related": [], "symbols": [], "source_pages": "1"},
                    ],
                    "misconceptions": [],
                }, ensure_ascii=False)
            # Pass3 校对
            if "课件校对者" in sys_:
                return json.dumps({"missing_themes": [], "suggested_links": []}, ensure_ascii=False)
            return json.dumps({"title": "x", "sections": []}, ensure_ascii=False)
    return MockLLM()


def test_pdf_parse_structure():
    tmp = tempfile.mkdtemp()
    pdf = os.path.join(tmp, "course.pdf")
    _make_pdf(pdf)
    d = parse_pdf(pdf)
    assert d.total_pages == 2
    assert d.name == "course"
    # 第1页标题应从最大字号行抽出
    first_title = d.slides[0].title
    assert first_title and "药物" in first_title or "药代" in first_title or "概述" in first_title
    # 至少要有 body 文本块
    texts = [b.text for b in d.slides[0].blocks]
    assert any("体循环" in t for t in texts)


def test_document_loader_routes_by_ext():
    tmp = tempfile.mkdtemp()
    pdf = os.path.join(tmp, "a.pdf")
    _make_pdf(pdf)
    d = parse_pdf(pdf)
    routed = dl.ingest_document(pdf)
    assert routed.total_pages == 2
    # .pptx 走 ppt 解析
    pptx = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs", "sample_pharmacy.pptx")
    if os.path.exists(pptx):
        from core.ppt_parser import parse_pptx
        r2 = dl.ingest_document(pptx)
        assert r2.total_pages == parse_pptx(pptx).total_pages
    # 不支持类型应报错
    try:
        dl.ingest_document(os.path.join(tmp, "x.docx"))
        assert False, "应拒绝 .docx"
    except ValueError:
        pass


def test_pipeline_with_pdf():
    tmp = tempfile.mkdtemp()
    pdf = os.path.join(tmp, "course.pdf")
    _make_pdf(pdf)
    out = os.path.join(tmp, "vault")
    res = run_conversion(pdf, "pharmacy", out, llm=_mock_llm_factory(),
                         progress=lambda m: None, mode="cards")   # 卡片式(验证 vault merge/质量报告)
    assert res.ok, res.error
    assert res.bundle.used_llm
    assert len(res.notes) >= 1
    # 知识质量体检已跑出结果, 且报告 md 已落盘到与 MOC 同课夹
    assert res.validation is not None
    assert res.validation.overall_score > 0
    report = os.path.join(out, "药物代谢动力学概述", "99_知识质量报告.md")
    assert os.path.isfile(report), "质量报告应落盘"
    with open(report, "r", encoding="utf-8") as f:
        assert "Knowledge Quality" in f.read()


if __name__ == "__main__":
    test_pdf_parse_structure()
    print("✓ test_pdf_parse_structure")
    test_document_loader_routes_by_ext()
    print("✓ test_document_loader_routes_by_ext")
    test_pipeline_with_pdf()
    print("✓ test_pipeline_with_pdf")
    print("\n全部通过")
