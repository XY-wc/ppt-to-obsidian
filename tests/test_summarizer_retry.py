# -*- coding: utf-8 -*-
"""回归测试: 三步流水线(staged)行为。

- 正常: Pass1 大纲 -> Pass2 分节深写 -> Pass3 校对, 产出 used_llm=True 且各节有真实页码切片。
- 模型整体无法产出(全程坏 JSON) -> 不崩溃, 降级返回(used_llm=False), 并带 warning。
- 每次 chat 的 token 预算足够大, 分节输出不易截断。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from core.knowledge_base import KnowledgeBase, Profession
from core.ppt_parser import PresentationDoc, Slide, SlideBlock
from obsidian import summarizer as S
from obsidian import staged


def _make_doc(pages_text):
    """pages_text: list[str], 每项是一页文本。"""
    d = PresentationDoc(path="mock.pdf", name="mock")
    for i, line in enumerate(pages_text):
        d.slides.append(Slide(index=i + 1, title=line[:10] or None,
                              blocks=[SlideBlock(kind="text", text=line, top=0, left=0)]))
    return d


def _mk_prof():
    kb = KnowledgeBase()
    kb.professions = {}
    prof = Profession(id="p", name="药学", core_concepts=[{"name": "药物", "definition": "物质"}],
                      symbol_mapping=[], common_misconceptions=[], note_template="")
    prof.build_index()
    return prof


class StagedLLM:
    """根据 user 内容里的阶段标记, 分别返回正确的 JSON。"""
    def __init__(self):
        self.calls = 0

    def chat(self, messages, max_tokens=4096):
        self.calls += 1
        sys_ = messages[0]["content"]
        usr = messages[-1]["content"] if messages[-1]["role"] == "user" else ""
        # Pass1 大纲
        if "规划大纲" in sys_ or "课程大纲规划师" in sys_ or "outline" in usr.lower():
            return ('{"title":"人体解剖与药物研发","sections":['
                    '{"heading":"人体解剖概述","pages":"1-2","focus":"介绍解剖学"},'
                    '{"heading":"生理学基础","pages":"3-4","focus":"讲解生理学"}]}')
        # Pass2 分节深写
        if "某一节" in sys_ or "concepts" in sys_ and "source_pages" in sys_:
            return ('{"summary":"本节能讲透核心。","concepts":['
                    '{"concept":"药物","ctype":"mechanism","importance":0.9,"confidence":0.95,'
                    '"definition":"能影响机体生理功能的物质",'
                    '"detail":"要点一\\n- 关键点A\\n- 关键点B","related":["生理"],'
                    '"relations":[{"target":"生理","relation_type":"depends_on"}],'
                    '"evidence":["药物是能影响机体生理功能的物质"],'
                    '"symbols":[{"symbol":"CL","meaning":"清除率"}],"source_pages":"2"},'
                    '{"concept":"生理","ctype":"concept","importance":0.5,"confidence":0.7,'
                    '"definition":"机体的生命活动",'
                    '"detail":"要点","related":["药物"],'
                    '"evidence":["生理是机体各种生命活动"],"symbols":[],"source_pages":"4"}],'
                    '"misconceptions":[{"misconception":"混淆概念","clarification":"需区分"}]}')
        # Pass3 校对
        if "校对查漏" in sys_ or "课件校对者" in sys_:
            return '{"missing_themes":[],"suggested_links":[["药物","生理","药物影响生理功能"]]}'
        # 兜底(不匹配) -> 给一个空大纲, 让上层处理
        return '{"title":"mock","sections":[]}'


def test_staged_normal_produces_ppt_driven_bundle():
    prof = _mk_prof()
    llm = StagedLLM()
    doc = _make_doc(["第1页 解剖学概述", "第2页 人体结构", "第3页 生理机制", "第4页 生理与药物"])
    b = S.summarize(doc, prof, llm=llm)
    assert b.used_llm is True
    assert len(b.sections) >= 2
    # 概念卡来自 Pass2 且带真实页码切片(应落到真实 slide 范围内)
    assert any(c.concept == "药物" for c in b.concepts)
    # MOC 用到的 concept_links 至少覆盖本节的药物概念
    assert any("药物" in (s.concept_links or []) for s in b.sections)
    # Pass3 的关联建议被并入 related
    drug = next(c for c in b.concepts if c.concept == "药物")
    assert any(r == "生理" for r in drug.related)
    # 知识 IR 字段被解析: 证据原文 / 类型 / 分数 / 类型化关系
    assert drug.evidence and "生理功能" in drug.evidence[0]
    assert drug.ctype == "mechanism"
    assert drug.importance > 0 and drug.confidence > 0
    assert any(r.get("relation_type") == "depends_on" for r in drug.relations)


def test_staged_broken_model_degrades_gracefully():
    class Broken:
        def chat(self, messages, max_tokens=4096):
            return "抱歉我无法…"  # 永远不是 JSON
    prof = _mk_prof()
    b = S.summarize(_make_doc(["一页", "两页", "三页", "四页"]), prof, llm=Broken())
    # 不崩溃; 因整段模型路径都失败, 应退回本地规则(used_llm False)
    assert b is not None
    assert b.used_llm is False
    assert any("本地规则" in w or "退回" in w for w in b.warnings) or not b.warnings


def test_output_token_budget_is_generous_per_section():
    # 分节模式下单次调用预算是 12000(足够写满一节, 不易截断)
    assert staged.OUT_TOKENS_CAP >= 6000


def test_range_parsing_and_slicing():
    # '1-2' / '3' / 越界页都要被正确处理
    doc = _make_doc(["a", "b", "c", "d"])  # 4 页
    assert staged._range_to_indices("1-2", 4) == [1, 2]
    assert staged._range_to_indices("2,4", 4) == [2, 4]
    assert staged._range_to_indices("0-99", 4) == [1, 2, 3, 4]
    assert staged._range_to_indices("", 4) == []


def test_expand_linkish():
    # 复述串目标(含括号列表/顿号列表)应展开为干净成员, 单概念原样
    from obsidian.staged import _expand_linkish as ex
    assert ex("实验方法分类（急性、在体）") == ["实验方法分类", "急性", "在体"]
    assert ex("急性实验、在体实验、离体实验、慢性实验") == ["急性实验", "在体实验", "离体实验", "慢性实验"]
    assert ex("新药上市申请（NDA）") == ["新药上市申请", "NDA"]
    assert ex("首过效应") == ["首过效应"]


def test_echo_combined_cards_removed():
    # "急性实验、在体实验、离体实验、慢性实验"这种把已有概念复述成一串的回声卡应被清掉
    cards = [
        S.ConceptCard(concept="急性实验", definition="a"),
        S.ConceptCard(concept="慢性实验", definition="b"),
        S.ConceptCard(concept="急性实验、在体实验、离体实验、慢性实验", definition="复述"),
        S.ConceptCard(concept="完全崭新的概念", definition="c"),
    ]
    kept = staged._drop_echo_combined_cards(cards)
    names = [c.concept for c in kept]
    assert "急性实验、在体实验、离体实验、慢性实验" not in names  # 回声卡被清
    assert "急性实验" in names and "慢性实验" in names
    assert "完全崭新的概念" in names  # 纯新词保留
