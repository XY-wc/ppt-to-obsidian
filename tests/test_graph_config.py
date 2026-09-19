# -*- coding: utf-8 -*-
"""Obsidian 关系图配色(graph_config) + 笔记层级 tag 回归测试。

覆盖:
  - 图谱配置的创建 / 合并 / 幂等 / 容错
  - 用户已有分组保留、新分组插入到兜底之前
  - 各层笔记 frontmatter 确实带上 层级/* tag
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from obsidian import graph_config as G
from obsidian.note_template import (
    build_moc_note, build_atomic_note, build_misconception_note,
)


def _read(p):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)


def _graph_path(d):
    return os.path.join(d, ".obsidian", "graph.json")


def test_layer_tags_defined():
    tags = G.layer_tags()
    assert len(tags) == 4
    assert all(t.startswith("层级/") for t in tags)


def test_creates_graph_json_when_missing():
    d = tempfile.mkdtemp()
    assert G.apply_graph_colors(d) is True
    data = _read(_graph_path(d))
    qs = [g["query"] for g in data["colorGroups"]]
    assert "tag:#层级/目录" in qs
    assert "tag:#层级/正文" in qs
    assert "tag:#层级/概念" in qs
    assert "tag:#层级/附录" in qs
    assert "path:attachments" in qs
    for g in data["colorGroups"]:
        assert g["color"]["a"] == 1
        assert isinstance(g["color"]["rgb"], int) and g["color"]["rgb"] > 0
    # 补齐了标准骨架字段
    assert "repelStrength" in data and "showTags" in data


def test_preserves_user_groups_and_inserts_before_fallback():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, ".obsidian"))
    user = {"colorGroups": [
        {"query": "tag:#我的笔记", "color": {"a": 1, "rgb": 111111}},
        {"query": "", "color": {"a": 1, "rgb": 8421504}},   # 兜底(空 query)
    ]}
    with open(_graph_path(d), "w", encoding="utf-8") as f:
        json.dump(user, f)
    G.apply_graph_colors(d)
    qs = [g["query"] for g in _read(_graph_path(d))["colorGroups"]]
    assert "tag:#我的笔记" in qs                    # 用户分组保留
    assert qs[-1] == ""                             # 兜底仍在最后
    assert qs.index("tag:#层级/目录") < qs.index("")  # 新分组在兜底之前(优先级更高)


def test_idempotent():
    d = tempfile.mkdtemp()
    G.apply_graph_colors(d)
    n1 = len(_read(_graph_path(d))["colorGroups"])
    G.apply_graph_colors(d)   # 再跑一次不应叠加
    n2 = len(_read(_graph_path(d))["colorGroups"])
    assert n1 == n2 == len(G.DEFAULT_GROUPS)


def test_corrupt_json_recovered():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, ".obsidian"))
    with open(_graph_path(d), "w", encoding="utf-8") as f:
        f.write("{ 这不是合法 json ")
    assert G.apply_graph_colors(d) is True
    data = _read(_graph_path(d))
    assert len(data["colorGroups"]) == len(G.DEFAULT_GROUPS)


def test_empty_vault_dir_no_crash():
    assert G.apply_graph_colors("") is False


def test_note_templates_carry_layer_tags():
    moc = build_moc_note("T · MOC", "药学", "a.pptx", [], None)
    assert "层级/目录" in moc
    atom = build_atomic_note("半衰期", "定义", "药学", "a.pptx", "1")
    assert "层级/概念" in atom
    mis = build_misconception_note(
        "T·易错点", "药学", "a.pptx",
        [{"misconception": "x", "clarification": "y"}])
    assert "层级/附录" in mis


def test_lecture_files_carry_layer_tags():
    from obsidian import lecture as L
    d = tempfile.mkdtemp()
    b = L.LectureBundle(title="物理化学", source_file="c.pdf", used_llm=True)
    b.sections = [L.LectureSection(num="1.1", title="概论", stem="1.1-概论",
                                   pages=[1], body="### 子题\n\n正文内容")]
    written = L.build_lecture_files(b, d, "c.pdf")   # 无截图占位 -> 不触发渲染
    idx = [w for w in written if w.endswith("index.md")][0]
    sec = [w for w in written if w.endswith("1.1-概论.md")][0]
    assert "层级/目录" in open(idx, encoding="utf-8").read()
    assert "层级/正文" in open(sec, encoding="utf-8").read()


if __name__ == "__main__":
    # 无 pytest 时也能直接跑: python tests/test_graph_config.py
    import traceback
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS  {fn.__name__}")
        except Exception:
            failed += 1
            print(f"FAIL  {fn.__name__}")
            traceback.print_exc()
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    raise SystemExit(1 if failed else 0)
