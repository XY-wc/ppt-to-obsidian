# -*- coding: utf-8 -*-
"""知识质量验证器(validator)回归测试: 覆盖 / 证据 / 断链 / 重名 / AI补充 各维度。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from core.ppt_parser import PresentationDoc, Slide, SlideBlock
from obsidian.summarizer import NoteBundle, Section, ConceptCard
from obsidian.validator import validate_bundle, _norm


def _make_doc(pages_text):
    d = PresentationDoc(path="mock.pdf", name="mock")
    for i, line in enumerate(pages_text):
        d.slides.append(Slide(index=i + 1, title=(line[:10] or None),
                              blocks=[SlideBlock(kind="text", text=line, top=0, left=0)]))
    return d


def _make_bundle():
    b = NoteBundle(professional_name="药学", source_file="mock.pdf", title="测试讲", used_llm=True)
    b.sections = [
        Section(heading="吸收与生物利用度", summary="…", concept_links=["生物利用度"], pages=[1, 2]),
        Section(heading="首过与分布", summary="…", concept_links=[], pages=[3, 4]),
    ]
    b.concepts = [
        ConceptCard(concept="生物利用度", definition="进入体循环比例", detail="…",
                    related=["吸收"], evidence=["药物进入体循环的比例"], source_pages="2"),
        ConceptCard(concept="吸收", definition="进入体循环", detail="…",
                    evidence=[], source_pages="1"),
        ConceptCard(concept="首过效应", definition="肝脏首过", detail="…",
                    related=["不存在的概念Y"], evidence=["肝脏首过代谢"], source_pages="3"),
        ConceptCard(concept="分布容积", definition="表观容积", detail="…",
                    evidence=["表观分布容积概念"], source_pages="4"),
        ConceptCard(concept="完全虚构术语", definition="AI补充", detail="…",
                    evidence=["编造"], source_pages="4"),
        ConceptCard(concept="生物 利用度", definition="重名", detail="…", source_pages=""),
    ]
    return b


def test_validate_detects_all_dimensions():
    doc = _make_doc([
        "吸收 药物进入体循环的过程",          # p1
        "生物利用度 进入体循环的比例",          # p2
        "首过效应 肝脏首过代谢",              # p3
        "分布容积 表观分布容积的概念",          # p4
        "复习 本章要点回顾",                 # p5
        "复习 补充练习",                     # p6
    ])
    b = _make_bundle()
    v = validate_bundle(doc, b)

    # 覆盖: 6 个有内容页, 章节只覆盖 1-4 -> 分数 <100 且 >0; p5-6 被列为大段未覆盖
    assert 0 < v.coverage_score < 100
    assert any("5-6" in m for m in v.missing_concepts)
    # 证据: 有页码+证据的 4/6 张(吸收缺证据, 重名卡双缺) -> <100
    assert 0 < v.evidence_score < 100
    # 断链: "不存在的概念Y" 指向本讲没有的卡
    assert any("不存在的概念Y" in x for x in v.broken_links)
    assert v.link_score < 100
    # 重名: "生物利用度" 与 "生物 利用度" 规范化后相同
    dup_norms = {_norm(x) for x in v.duplicate_concepts}
    assert "生物利用度" in dup_norms
    # AI补充: 概念名不出现在 PPT 原文
    assert any("完全虚构术语" in x for x in v.ai_supplemented)
    # 无溯源的卡: 吸收(缺证据)与重名卡(缺页码/证据)都应在列
    assert any("吸收" in x for x in v.unsupported_concepts)
    assert v.overall_score > 0
    # 报告文本可用
    md = v.to_markdown()
    assert "Knowledge Quality" in md and "Coverage" in md and "完全虚构术语" in md
    assert v.to_brief()


def test_validate_full_marks_when_everything_covered():
    doc = _make_doc([
        "首过效应 肝脏首过代谢是重要概念",    # p1
        "生物利用度 进入体循环的比例",        # p2
    ])
    b = NoteBundle(professional_name="药学", source_file="mock.pdf", title="T", used_llm=True)
    b.sections = [Section(heading="药动学", concept_links=["首过效应"], pages=[1, 2])]
    b.concepts = [
        ConceptCard(concept="首过效应", definition="…", evidence=["肝脏首过代谢是重要概念"], source_pages="1"),
        ConceptCard(concept="生物利用度", definition="…", related=["首过效应"],
                    evidence=["进入体循环的比例"], source_pages="2"),
    ]
    v = validate_bundle(doc, b)
    assert v.missing_concepts == []
    assert v.broken_links == []
    assert v.duplicate_concepts == []
    assert v.evidence_score == 100
    assert v.link_score == 100
    assert v.coverage_score == 100


def test_link_target_splitting():
    # 复述串/括号列表串都应拆成干净成员(供灰链/断链判定与渲染)
    from obsidian.validator import _split_link_target as sp
    assert sp("实验方法分类（急性、在体、离体、慢性）") == ["实验方法分类", "急性", "在体", "离体", "慢性"]
    assert sp("新药上市申请（NDA）") == ["新药上市申请", "NDA"]
    assert sp("急性实验、在体实验、离体实验、慢性实验") == ["急性实验", "在体实验", "离体实验", "慢性实验"]
    assert sp("首过效应") == ["首过效应"]


def test_validate_empty_bundle_does_not_crash():
    doc = _make_doc(["a", "b"])
    b = NoteBundle(professional_name="药学", source_file="mock.pdf", title="T", used_llm=False)
    v = validate_bundle(doc, b)
    assert v.overall_score == 0
    assert v.to_markdown()


if __name__ == "__main__":
    test_validate_detects_all_dimensions()
    print("✓ test_validate_detects_all_dimensions")
    test_validate_full_marks_when_everything_covered()
    print("✓ test_validate_full_marks_when_everything_covered")
    test_validate_empty_bundle_does_not_crash()
    print("✓ test_validate_empty_bundle_does_not_crash")
    print("\n全部通过")
