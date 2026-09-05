# -*- coding: utf-8 -*-
"""
P2a refine 数据层单测:
1) revise_note_text: 正确组装上下文, 调用模型(带 PPT 原文/专业上下文/用户建议),
   返回修订后 markdown(去掉围栏)。
2) extract_delta + apply_user_correction: 用户认可的修正沉淀进隔离自学库并 confirmed。
3) 校验 to_extra_context 后续可把新符号/易错点带给下次转换。
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from obsidian import refine
from obsidian.refine import revise_note_text, extract_delta, apply_user_correction
from obsidian.kb_calibration import CalibrationStore
from core.knowledge_base import KnowledgeBase


class ReviseMockLLM:
    model = "mock-refine"

    def __init__(self, revised_md, delta):
        self._revised = revised_md
        self._delta = delta
        self.calls = []

    def chat(self, messages, max_tokens=4096):
        self.calls.append(messages)
        # 第二次调用(extract_delta)返回 delta JSON; 第一次返回修订正文
        if len(self.calls) == 1:
            return self._revised
        return json.dumps(self._delta, ensure_ascii=False)


def main():
    tmp = tempfile.mkdtemp()
    kb = KnowledgeBase()
    kb.load_all()
    prof = kb.get("pharmacy")

    current_md = ("---\ntitle: \"生物利用度\"\ntags: [\"药学\"]\nprofessional: \"药学\"\n"
                  "source: \"sample.pptx\"\nsource_pages: \"2\"\n---\n\n# 生物利用度\n\n"
                  "> [!quote] 定义\n> 药物被吸收进入血液循环的相对程度\n\n"
                  "## 📝 详细说明\n- 受首过效应影响\n")
    revised_md = ("---\ntitle: \"生物利用度\"\ntags: [\"药学\"]\nprofessional: \"药学\"\n"
                  "source: \"sample.pptx\"\nsource_pages: \"2\"\n---\n\n# 生物利用度\n\n"
                  "> [!quote] 定义\n> 药物活性成分被吸收进入体循环的程度与速率\n\n"
                  "## 📝 详细说明\n- 受首过效应影响\n- 缓释制剂可提高依从性\n")

    mock = ReviseMockLLM(
        revised_md,
        {"corrected_definitions": [{"name": "生物利用度",
                                    "definition": "药物活性成分被吸收进入体循环的程度与速率"}],
         "new_symbols": [{"symbol": "F", "meaning": "生物利用度"}],
         "new_misconceptions": [{"misconception": "生物利用度只与吸收有关",
                                 "clarification": "还包括首过效应等对体循环可利用程度的影响"}]},
    )

    # 1) revise
    r = revise_note_text(llm=mock, concept_or_target="生物利用度", current_md=current_md,
                         user_request="定义应强调'活性成分'与'程度和速率'", professional_name=prof.name,
                         ppt_text="===== 第 2 页 =====\n生物利用度…吸收\n===== 第 3 页 =====\n缓释",
                         pages_hint="2", kb_context="【专业】药学")
    print("revise used_llm:", r.used_llm, "| err:", r.error)
    assert r.used_llm and "程度与速率" in r.revised_md
    # 应把 PPT 原文/专业上下文/建议都塞进第一次调用
    first_msgs = mock.calls[0]
    joined = " ".join(m["content"] for m in first_msgs)
    assert "用户的修改意见" in joined and "缓释" in joined or "生物利用度" in joined

    # 2) extract_delta
    delta = extract_delta(llm=mock, target="生物利用度", original_md=current_md,
                          revised_md=revised_md, professional_name=prof.name)
    print("delta keys:", list(delta.keys()))
    assert delta.get("corrected_definitions")

    # 3) apply 到隔离自学库
    st = CalibrationStore("pharmacy", base_dir=tmp)
    n = apply_user_correction(st, delta)
    print("沉淀条数:", n)
    assert n == 3
    sym = [s for s in st.extra_symbols() if s.get("symbol") == "F"]
    assert sym and sym[0].get("confirmed") is True
    mis = [m for m in st.extra_misconceptions() if "只与吸收有关" in m.get("misconception", "")]
    assert mis and mis[0].get("confirmed") is True
    # 定义沉淀
    defs = [c for c in st.extra_concepts() if c.get("name") == "生物利用度"]
    assert defs and "程度与速率" in defs[0].get("definition", "")

    # 4) 下次转换上下文能带上 F 符号
    ctx = st.to_extra_context()
    print("补充上下文:", ctx)
    assert "F(生物利用度)" in ctx or "F" in ctx
    print("\n全部 refine 断言通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
