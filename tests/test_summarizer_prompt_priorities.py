# -*- coding: utf-8 -*-
"""回归测试: 确保「PPT 为内容来源、框架仅为命名词典」的纪律仍在 prompt 中。

背景 bug: 用户反馈"选了专业后，输出全被最初的数据框架(核心概念清单)牵着走，
与 PPT 主体无关"——解剖/生理等大篇幅内容被压成一句，反而按药学清单词开了卡。
修复: 在 SYSTEM_PROMPT/USER_TMPL 强化 PPT 唯一内容来源 + 覆盖纪律。
本测试只校验提示词纪律与模板占位符，不依赖任何网络/解析。
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from obsidian import summarizer as S


def test_system_prompt_keeps_ppt_first_rules():
    p = S.SYSTEM_PROMPT_TMPL
    # 关键纪律关键词都应在
    assert "唯一的内容来源" in p
    assert "命名词典" in p          # 框架只是命名用
    assert "不是内容清单" in p
    # 覆盖纪律: 不能让清单决定产出
    assert "绝不决定你要整理哪些内容" in p
    assert "source_pages" in p       # 每卡都要指向真实页码
    # 不应再有"命名必须能与清单建立联系"这类强约束(原 bug 措辞)
    assert "必须能与" not in p and "概念清单】建立联系" not in p


def test_user_template_keeps_ppt_as_only_source():
    u = S.USER_TMPL
    assert "唯一依据" in u
    assert "{fulltext}" in u
    assert "{kb}" in u
    assert "{hits}" in u
    assert "{filename}" in u


def test_user_template_fill_produces_both_blocks():
    # 占位符能被 _fill_template 安全替换(不破坏 JSON 花括号)
    out = S._fill_template(S.USER_TMPL, filename="a.pdf", kb="【核心概念清单】甲、乙",
                           fulltext="第1页讲……", hits="甲", symbols_hint="无")
    assert "a.pdf" in out
    assert "【核心概念清单】甲、乙" in out
    assert "第1页讲……" in out
    # 框架段被明确标注为仅命名用
    assert "命名词典" in out or "仅用于规范命名" in out or "术语词典" in out
