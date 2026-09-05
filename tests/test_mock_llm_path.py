# -*- coding: utf-8 -*-
"""用 MockLLM 验证 LLM 结构化输出 → Obsidian 渲染 的完整路径(无需真实API)。"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from pipeline import run_conversion


class MockLLM:
    model = "mock-llm"

    def chat(self, messages, max_tokens=4096):
        # 注意: 用 json.dumps 保证 detail 里的换行被正确转义, 模拟真实合法输出
        data = {
            "title": "药代动力学：药物体内过程",
            "sections": [
                {"heading": "吸收过程",
                 "summary": "药物由给药部位进入血液循环为吸收，受首过效应、pKa与胃肠蠕动影响。",
                 "concept_links": ["生物利用度", "首过效应"]},
                {"heading": "给药途径对比",
                 "summary": "静脉注射生物利用度100%，口服仅60%，体现吸收程度差异。",
                 "concept_links": ["生物利用度"]},
            ],
            "concepts": [
                {"concept": "生物利用度",
                 "definition": "药物被吸收进入血液循环的相对程度与速度",
                 "detail": "- AUC 反映吸收程度\n- Cmax 反映吸收速度\n- 首过效应显著降低口服生物利用度",
                 "related": ["首过效应", "药代动力学"],
                 "symbols": [{"symbol": "AUC", "meaning": "药时曲线下面积"},
                             {"symbol": "Cmax", "meaning": "血药峰浓度"}],
                 "source_pages": "2-3"},
                {"concept": "首过效应",
                 "definition": "药物经肝脏代谢后进入体循环前被部分消除",
                 "detail": "首过效应导致口服生物利用度下降，可改用静脉或舌下给药规避。",
                 "related": ["生物利用度"], "symbols": [], "source_pages": "2"},
            ],
            "misconceptions": [
                {"misconception": "口服生物利用度低=药效一定差",
                 "clarification": "生物利用度反映吸收程度，是否有效还取决于药效学特征与血药浓度。"},
            ],
        }
        # 模拟真实 LLM 会在外面裹点解释性文字的情况, 顺便测试容错
        return "好的, 已整理如下:\n```json\n" + json.dumps(data, ensure_ascii=False) + "\n```"


def main():
    tmp = tempfile.mkdtemp()
    ppt = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs", "sample_pharmacy.pptx")
    out = os.path.join(tmp, "vault")
    if not os.path.exists(ppt):
        print("缺少示例PPT")
        return
    res = run_conversion(ppt, "pharmacy", out, llm=MockLLM(),
                         progress=lambda m: print("  ·", m))
    print("OK:", res.ok, "| err:", res.error)
    print("用LLM模式:", res.bundle.used_llm)
    print("生成笔记数:", len(res.notes))
    for n in res.notes:
        print(f"  [{n.kind}] {n.rel_path}")
    # 打印一张概念笔记
    for n in res.notes:
        if n.kind == "concept":
            print("\n===== 示例概念笔记 =====")
            print(n.content)
            break


if __name__ == "__main__":
    main()
