# -*- coding: utf-8 -*-
"""
P1 知识库校准(自学复利)单测:
1) CalibrationStore 从 LLM 候选学习 -> 沉淀官方清单外的新概念。
2) confirm_tally 把实际命中概念 +1; 达到阈值标记 confirmed。
3) to_extra_context 拼出可注入补充上下文。
4) promote_to_official 把 confirmed 写回官方(用临时 copy, 不污染真实 professions)。
5) pipeline 集成冒烟: 离线模式带隔离 base_dir 走通。
"""
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from obsidian import kb_calibration as kbc
from obsidian.kb_calibration import CalibrationStore


def _official_names(prof_json_path):
    with open(prof_json_path, encoding="utf-8") as f:
        return [c["name"] for c in json.load(f)["core_concepts"]]


def main():
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "professions", "pharmacy.json")
    official = _official_names(src)
    # "给药间隔" 不在官方清单(实测), 作为自学新词
    NEW = "给药间隔"
    assert NEW not in official, f"{NEW} 已在官方, 需换测试词"

    base = tempfile.mkdtemp()
    st = CalibrationStore("pharmacy", base_dir=base)
    st.learn_from_text("给药间隔长短影响血药浓度波动。", official, candidate_terms=[NEW, "稳态血药浓度"])
    names = st.concept_names()
    print("learn 后概念:", names)
    assert NEW in names, "应沉淀官方清单外候选"
    assert "稳态血药浓度" not in names, "官方概念不应被重复沉淀进自学库"

    # confirm 到阈值 -> confirmed
    for _ in range(kbc.CONFIRM_THRESHOLD):
        st.confirm_tally([NEW])
    st2 = CalibrationStore("pharmacy", base_dir=base)
    c = [x for x in st2.extra_concepts() if x["name"] == NEW][0]
    print("confirm 后 count:", c["count"], "confirmed:", c["confirmed"])
    assert c["confirmed"] is True

    ctx = st2.to_extra_context()
    print("补充上下文:", ctx)
    assert NEW in ctx

    # promote 回官方副本
    cp = os.path.join(base, "pharmacy_copy.json")
    shutil.copy(src, cp)
    added = st2.promote_to_official(cp)
    print("promote 新增官方概念数:", added)
    assert added == 1
    assert NEW in _official_names(cp)
    # promote 后 confirmed 项应被清除
    assert all(not x.get("confirmed") for x in st2.extra_concepts())

    # pipeline 集成冒烟(离线, 隔离 base_dir)
    tmp = tempfile.mkdtemp()
    ppt = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs", "sample_pharmacy.pptx")
    if os.path.exists(ppt):
        old = kbc._default_base_dir
        kbc._default_base_dir = lambda: base
        try:
            from pipeline import run_conversion
            r = run_conversion(ppt, "pharmacy", tmp, progress=lambda x: None)
            print("离线集成 OK:", r.ok)
            assert r.ok
        finally:
            kbc._default_base_dir = old

    print("\n全部 P1 断言通过 ✔")
    return 0


if __name__ == "__main__":
    sys.exit(main())
