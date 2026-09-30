# -*- coding: utf-8 -*-
"""「课程总目录」+ 关系图按课程聚簇 的回归测试。

覆盖:
  - 仓库根探测 find_vault_root: 向上找 .obsidian, 跳过课程夹里的伪 .obsidian
  - graph.json 写到真正的仓库根(而不是课程子夹, 否则 Obsidian 读不到配色)
  - build_course_index: 生成/刷新课程总目录、串联同课各课件、幂等、空课程清理
  - 讲义内部双链写成「仓库相对路径」, 避免不同课程同名小节互相串链
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from obsidian import graph_config as G
from obsidian import lecture as L


def _mk_vault():
    """造一个带 .obsidian/app.json 的仓库根。"""
    root = tempfile.mkdtemp()
    os.makedirs(os.path.join(root, ".obsidian"))
    with open(os.path.join(root, ".obsidian", "app.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    return root


def _mk_ppt(course_dir, folder, title, source="c.pdf", n_sections=2, index=None):
    """在课程夹下造一个课件子夹(目录页 + 若干小节)。

    目录页默认与课件夹同名(folder-note); 传 index="index.md" 可造旧版产出以测兼容。
    """
    d = os.path.join(course_dir, folder)
    os.makedirs(os.path.join(d, "attachments"), exist_ok=True)
    with open(os.path.join(d, index or f"{folder}.md"), "w", encoding="utf-8") as f:
        f.write(f'---\ntitle: "{title}"\ntype: knowledge\nsource: "{source}"\n'
                f'tags:\n  - 课程\n  - 层级/目录\n---\n\n# {title}\n')
    for i in range(1, n_sections + 1):
        with open(os.path.join(d, f"1.{i}-小节{i}.md"), "w", encoding="utf-8") as f:
            f.write(f'---\ntitle: "小节{i}"\n---\n\n正文\n')
    return d


# ---------------- 仓库根探测 ----------------

def test_find_vault_root_upwards():
    root = _mk_vault()
    course = os.path.join(root, "物理化学")
    os.makedirs(course)
    assert G.find_vault_root(course) == root
    assert G.find_vault_root(root) == root


def test_find_vault_root_skips_stray_obsidian():
    """课程夹里被旧版本误建的伪 .obsidian 不应被当成仓库根。"""
    root = _mk_vault()
    course = os.path.join(root, "物理化学")
    os.makedirs(os.path.join(course, ".obsidian"))
    with open(os.path.join(course, ".obsidian", "graph.json"), "w", encoding="utf-8") as f:
        f.write("{}")
    assert G.find_vault_root(course) == root


def test_rel_prefix():
    root = _mk_vault()
    course = os.path.join(root, "物理化学")
    os.makedirs(course)
    assert G.rel_prefix(course, root) == "物理化学"
    assert G.rel_prefix(root, root) == ""


def test_graph_written_to_real_vault_root():
    """配色必须落在仓库根; 写在课程子夹里 Obsidian 读不到。"""
    root = _mk_vault()
    course = os.path.join(root, "物理化学")
    os.makedirs(course)
    assert G.apply_graph_colors(course) is True
    assert os.path.isfile(os.path.join(root, ".obsidian", "graph.json"))
    assert not os.path.isfile(os.path.join(course, ".obsidian", "graph.json"))


# ---------------- 课程总目录 ----------------

def test_course_hub_links_all_ppts():
    root = _mk_vault()
    course = os.path.join(root, "物理化学")
    os.makedirs(course)
    _mk_ppt(course, "热力学第一定律", "物理化学 · 热力学第一定律", "l1.pdf", 3)
    _mk_ppt(course, "热力学第二定律", "物理化学 · 热力学第二定律", "l2.pdf", 4)

    hub = L.build_course_index(course, vault_root=root)
    assert hub and os.path.isfile(hub)
    assert os.path.basename(hub) == "物理化学 课程总目录.md"
    txt = open(hub, encoding="utf-8").read()
    assert "层级/课程" in txt                                   # 关系图里单独上色
    assert "[[物理化学/热力学第一定律/热力学第一定律\\|物理化学 · 热力学第一定律]]" in txt
    assert "[[物理化学/热力学第二定律/热力学第二定律\\|物理化学 · 热力学第二定律]]" in txt
    assert "| 3 |" in txt and "| 4 |" in txt                    # 各课件的节数

    n1 = len(txt)
    L.build_course_index(course, vault_root=root)               # 幂等: 重复转换不叠加
    assert len(open(hub, encoding="utf-8").read()) == n1


def test_course_hub_removed_when_empty():
    root = _mk_vault()
    course = os.path.join(root, "物理化学")
    os.makedirs(course)
    _mk_ppt(course, "热力学第一定律", "热力学第一定律", "l1.pdf", 2)
    hub = L.build_course_index(course, vault_root=root)
    assert hub and os.path.isfile(hub)
    shutil.rmtree(os.path.join(course, "热力学第一定律"))
    assert L.build_course_index(course, vault_root=root) is None
    assert not os.path.exists(hub)


# ---------------- 讲义双链按仓库相对路径 ----------------

def test_lecture_links_are_vault_relative():
    root = _mk_vault()
    course = os.path.join(root, "物理化学")
    os.makedirs(course)
    b = L.LectureBundle(title="热力学第一定律", source_file="l1.pdf", used_llm=True)
    b.sections = [
        L.LectureSection(num="1.1", title="概论", stem="1.1-概论", pages=[1],
                         body="### 子题\n\n正文, 参见[[1.2-焓]]\n"),
        L.LectureSection(num="1.2", title="焓", stem="1.2-焓", pages=[2],
                         body="### 子题\n\n正文\n"),
    ]
    written = L.build_lecture_files(b, course, "l1.pdf", vault_root=root)
    idx = [w for w in written if w.endswith("热力学第一定律.md")][0]
    sec1 = [w for w in written if w.endswith("1.1-概论.md")][0]
    itxt = open(idx, encoding="utf-8").read()
    stxt = open(sec1, encoding="utf-8").read()
    assert "[[物理化学/热力学第一定律/1.1-概论|概论]]" in itxt        # 目录 -> 小节
    assert "[[物理化学/物理化学 课程总目录|" in itxt                    # 目录 -> 课程总目录 回链
    assert "[[物理化学/热力学第一定律/1.2-焓|1.2 焓]]" in stxt          # 正文内联链接
    assert "[[物理化学/热力学第一定律/1.2-焓|1.2 焓]]" in stxt          # 节末导航


# ---------------- 目录页改用课件名(folder-note) ----------------

def test_index_named_after_course_folder():
    """目录页应为 {课件夹名}.md, 关系图节点即课件标题, 而非一堆 index。"""
    d = tempfile.mkdtemp()
    b = L.LectureBundle(title="热力学第一定律", source_file="c.pdf", used_llm=True)
    b.sections = [L.LectureSection(num="1.1", title="概论", stem="1.1-概论",
                                   pages=[1], body="正文")]
    written = L.build_lecture_files(b, d, "c.pdf")
    names = sorted(os.path.basename(w) for w in written if w.endswith(".md"))
    assert names == ["1.1-概论.md", "热力学第一定律.md"]
    assert not os.path.isfile(os.path.join(d, "热力学第一定律", "index.md"))


def test_index_name_fallback_on_clash():
    """小节与课件同名时, 目录页退让加后缀, 不覆盖小节正文。"""
    d = tempfile.mkdtemp()
    b = L.LectureBundle(title="绪论", source_file="c.pdf", used_llm=True)
    b.sections = [L.LectureSection(num="", title="绪论", stem="绪论", pages=[1],
                                   body="这是正文本节内容")]
    written = L.build_lecture_files(b, d, "c.pdf")
    names = sorted(os.path.basename(w) for w in written if w.endswith(".md"))
    assert names == ["绪论-目录.md", "绪论.md"]
    sec = open(os.path.join(d, "绪论", "绪论.md"), encoding="utf-8").read()
    assert "这是正文本节内容" in sec          # 小节未被目录页覆盖


def test_legacy_index_still_readable():
    """旧产出(index.md)仍能被识别为目录页, 不因改名而失效。"""
    root = _mk_vault()
    course = os.path.join(root, "物理化学")
    os.makedirs(course)
    _mk_ppt(course, "热力学第一定律", "物理化学 · 热力学第一定律", "l1.pdf", 2,
            index="index.md")
    pdir = os.path.join(course, "热力学第一定律")
    assert L.find_ppt_index(pdir) == os.path.join(pdir, "index.md")
    hub = L.build_course_index(course, vault_root=root)
    assert "[[物理化学/热力学第一定律/index\\|" in open(hub, encoding="utf-8").read()


if __name__ == "__main__":
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
