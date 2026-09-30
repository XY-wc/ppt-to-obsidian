# -*- coding: utf-8 -*-
"""Obsidian 关系图配色/美化(graph_config) + 笔记层级/课程 tag 回归测试。

覆盖:
  - 图谱配置的创建 / 合并 / 幂等 / 容错
  - 用户已有分组保留、新分组插入到兜底之前
  - 各层笔记 frontmatter 确实带上 层级/* tag
  - 课程 tag 清洗、每课一色、标签枢纽(showTags)、美观参数不覆盖用户自定义
  - 已删除课程的旧配色会被清理
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
    assert len(tags) == 5
    assert all(t.startswith("层级/") for t in tags)
    assert G.TAG_COURSE in tags


def test_creates_graph_json_when_missing():
    d = tempfile.mkdtemp()
    assert G.apply_graph_colors(d) is True
    data = _read(_graph_path(d))
    qs = [g["query"] for g in data["colorGroups"]]
    assert "tag:#层级/课程" in qs
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
    idx = [w for w in written if w.endswith("物理化学.md")][0]
    sec = [w for w in written if w.endswith("1.1-概论.md")][0]
    assert "层级/目录" in open(idx, encoding="utf-8").read()
    assert "层级/正文" in open(sec, encoding="utf-8").read()


def test_course_tag_sanitized():
    assert G.course_tag("物理化学") == "课程/物理化学"
    assert G.course_tag("有机 化学") == "课程/有机化学"      # 标签里不能有空格
    assert G.course_tag("高数（上）") == "课程/高数上"
    assert G.course_tag("") == "课程"
    assert " " not in G.course_tag("A B C")


def test_course_scheme_groups_order_and_colors():
    courses = ["物理化学", "有机化学", "分析化学"]
    groups = G.build_groups(courses, "course")
    qs = [q for q, _ in groups]
    # 课程组排在最前(Obsidian 先匹配到的组优先)
    assert set(qs[:3]) == {"tag:#课程/物理化学", "tag:#课程/有机化学", "tag:#课程/分析化学"}
    # 层级组仍在(此时主要给 层级/* 标签枢纽上色, 并兜住零散笔记)
    assert "tag:#层级/正文" in qs
    # 三门的颜色互不相同
    assert len({c for _, c in groups[:3]}) == 3
    # layer 方案只有层级组
    assert [q for q, _ in G.build_groups(courses, "layer")] == [q for q, _ in G.LAYER_GROUPS]


def test_course_color_distinct_and_deterministic():
    courses = ["物理化学", "有机化学", "分析化学"]
    a = G.assign_course_colors(courses)
    b = G.assign_course_colors(list(reversed(courses)))
    assert a == b                                    # 与传入顺序无关
    assert len(set(a.values())) == len(courses)      # 同一批课程互不撞色
    assert set(a.values()) <= set(G.COURSE_PALETTE)
    assert G.course_color("物理化学") in G.COURSE_PALETTE


def test_apply_graph_colors_beautifies_and_clusters():
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, ".obsidian"))
    with open(os.path.join(root, ".obsidian", "app.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    course = os.path.join(root, "物理化学")
    os.makedirs(course)
    with open(os.path.join(course, "a.md"), "w", encoding="utf-8") as f:
        f.write("# a\n")
    assert G.apply_graph_colors(course) is True          # 课程从仓库根自动发现
    data = _read(os.path.join(root, ".obsidian", "graph.json"))
    qs = [g["query"] for g in data["colorGroups"]]
    assert "tag:#课程/物理化学" in qs
    assert data["showTags"] is True                      # 标签枢纽 -> 同课聚簇
    assert data["showArrow"] is True                     # 层级流向
    assert data["localJumps"] == 2                       # 局部关系图 2 跳
    n = len(data["colorGroups"])
    G.apply_graph_colors(course)                         # 幂等
    assert len(_read(os.path.join(root, ".obsidian", "graph.json"))["colorGroups"]) == n


def test_user_custom_graph_appearance_preserved():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, ".obsidian"))
    with open(_graph_path(d), "w", encoding="utf-8") as f:
        json.dump({"repelStrength": 30, "nodeSizeMultiplier": 3}, f)
    G.apply_graph_colors(d)
    data = _read(_graph_path(d))
    # 用户调过的值不能被我们覆盖
    assert data["repelStrength"] == 30
    assert data["nodeSizeMultiplier"] == 3


def test_stale_course_group_removed_user_group_kept():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, ".obsidian"))
    with open(_graph_path(d), "w", encoding="utf-8") as f:
        json.dump({"colorGroups": [
            {"query": "tag:#课程/已删除课程", "color": {"a": 1, "rgb": 123}},
            {"query": "tag:#我的笔记", "color": {"a": 1, "rgb": 456}},
        ]}, f)
    G.apply_graph_colors(d, courses=["物理化学"])
    qs = [g["query"] for g in _read(_graph_path(d))["colorGroups"]]
    assert "tag:#课程/已删除课程" not in qs   # 已删除课程的旧配色被清掉
    assert "tag:#我的笔记" in qs              # 用户自己的分组保留
    assert "tag:#课程/物理化学" in qs


def test_lecture_notes_carry_course_tag():
    from obsidian import lecture as L
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, ".obsidian"))
    course = os.path.join(root, "物理化学")
    b = L.LectureBundle(title="热力学第一定律", source_file="c.pdf", used_llm=True)
    b.sections = [L.LectureSection(num="1.1", title="概论", stem="1.1-概论",
                                   pages=[1], body="正文")]
    written = L.build_lecture_files(b, course, "c.pdf")
    hub = L.build_course_index(course)
    idx = [w for w in written if w.endswith("热力学第一定律.md")][0]
    sec = [w for w in written if w.endswith("1.1-概论.md")][0]
    assert "课程/物理化学" in open(idx, encoding="utf-8").read()
    assert "课程/物理化学" in open(sec, encoding="utf-8").read()
    assert "课程/物理化学" in open(hub, encoding="utf-8").read()


def test_discover_courses():
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, ".obsidian"))
    os.makedirs(os.path.join(root, "物理化学", "第一章"))
    with open(os.path.join(root, "物理化学", "第一章", "x.md"), "w", encoding="utf-8") as f:
        f.write("# x")
    os.makedirs(os.path.join(root, "attachments"))
    os.makedirs(os.path.join(root, ".trash"))
    assert G.discover_courses(root) == ["物理化学"]
    assert G.discover_courses("") == []


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
