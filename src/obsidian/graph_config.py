# -*- coding: utf-8 -*-
"""Obsidian 关系图(Graph View)按层级自动配色。

生成笔记后由 pipeline 调用 ``apply_graph_colors(vault_dir)``: 往
``<vault>/.obsidian/graph.json`` 写入/合并 ``colorGroups``, 让用户在 Obsidian
的「关系网络」里一眼看清笔记层级:

    目录(index / MOC)  →  正文(讲义各节 / 概念卡)  →  附录(易错点 / 质量报告)  →  附件(截图)

设计要点:
  - **幂等**: 按 query 去重, 重复转换同一课程不会叠加重复分组。
  - **非破坏**: 保留 vault 原有配色分组; 新分组插入到「兜底空 query」之前,
    从而优先级高于兜底灰, 又不抢占用户自定义分组。
  - **容错**: graph.json 缺失 / 损坏时, 用 Obsidian 标准骨架重建。
  - **可移植**: 纯 JSON 读写, 不依赖 Obsidian 内部版本。
"""
import json
import os
from typing import Callable, Dict, List, Optional, Tuple

# 图谱配置文件相对 vault 根的位置
GRAPH_REL = os.path.join(".obsidian", "graph.json")

# ---- 层级 tag(写进笔记 frontmatter, 供图谱按 tag 查询上色) ----
TAG_DIR = "层级/目录"        # index(讲义) / MOC(卡片)
TAG_BODY = "层级/正文"       # 讲义各节
TAG_CONCEPT = "层级/概念"    # 概念卡
TAG_APPENDIX = "层级/附录"   # 易错点 / 质量报告

# (查询, 颜色 RGB) —— 列表顺序即图谱匹配优先级, 越靠前越优先
DEFAULT_GROUPS: List[Tuple[str, int]] = [
    ("tag:#层级/目录", 0xF5A623),    # 金橙: 目录/索引(中枢节点)
    ("tag:#层级/正文", 0x4A90E2),    # 蓝:   讲义正文(主体)
    ("tag:#层级/概念", 0x52B788),    # 绿:   概念卡
    ("tag:#层级/附录", 0x9AA5B1),    # 灰蓝: 易错点/质量报告
    ("path:attachments", 0xC8CDD3),  # 浅灰: 课件截图附件
]

# Obsidian graph.json 的标准骨架(缺失字段时补齐, 不影响用户已有值)
_SKELETON: Dict = {
    "collapse-filter": True,
    "search": "",
    "showTags": False,
    "showAttachments": False,
    "hideUnresolved": False,
    "showOrphans": True,
    "collapse-color-groups": False,
    "colorGroups": [],
    "collapse-display": True,
    "showArrow": False,
    "textFadeMultiplier": 0,
    "nodeSizeMultiplier": 1,
    "lineSizeMultiplier": 1,
    "collapse-forces": True,
    "centerStrength": 0.518713248970312,
    "repelStrength": 10,
    "linkStrength": 1,
    "linkDistance": 250,
    "scale": 1,
    "close": False,
}


def _load(path: str) -> Dict:
    """读取 graph.json; 不存在或损坏时返回标准骨架副本。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        return dict(_SKELETON)


def _insert_before_fallback(kept: List[Dict], new_items: List[Dict]) -> List[Dict]:
    """把新分组插到「兜底空 query」之前; 没有兜底则追加到末尾。"""
    pos = len(kept)
    for i, g in enumerate(kept):
        if isinstance(g, dict) and not str(g.get("query") or "").strip():
            pos = i
            break
    return kept[:pos] + new_items + kept[pos:]


def apply_graph_colors(vault_dir: str,
                       groups: Optional[List[Tuple[str, int]]] = None,
                       progress: Optional[Callable[[str], None]] = None) -> bool:
    """把层级配色合并进 ``<vault>/.obsidian/graph.json``。

    :param vault_dir: Obsidian vault 根目录
    :param groups:    自定义 (query, rgb) 列表; 默认 DEFAULT_GROUPS
    :param progress:  进度回调(可空)
    :return: 成功写入返回 True
    """
    if not vault_dir:
        if progress:
            progress("(未指定 vault 目录, 跳过图谱配色)")
        return False

    obs_dir = os.path.join(vault_dir, ".obsidian")
    path = os.path.join(obs_dir, "graph.json")
    want = list(groups or DEFAULT_GROUPS)
    want_queries = {q for q, _ in want}

    try:
        os.makedirs(obs_dir, exist_ok=True)
        data = _load(path)

        cur = data.get("colorGroups")
        if not isinstance(cur, list):
            cur = []
        # 幂等: 先剔除我们此前写入的同 query 条目, 再统一追加
        kept = [g for g in cur
                if not (isinstance(g, dict) and g.get("query") in want_queries)]

        new_items = [{"query": q, "color": {"a": 1, "rgb": int(rgb)}} for q, rgb in want]
        data["colorGroups"] = _insert_before_fallback(kept, new_items)

        for k, v in _SKELETON.items():
            data.setdefault(k, v)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        if progress:
            progress(f"🎨 已配置 Obsidian 关系图层级配色({len(new_items)} 组); "
                     f"重开 Obsidian 在「关系图」查看 -> .obsidian/graph.json")
        return True
    except Exception as e:
        if progress:
            progress(f"(图谱配色写入失败, 已跳过: {e})")
        return False


def layer_tags() -> List[str]:
    """返回本项目使用的全部层级 tag(便于测试与文档)。"""
    return [TAG_DIR, TAG_BODY, TAG_CONCEPT, TAG_APPENDIX]


if __name__ == "__main__":
    import sys
    import tempfile
    d = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
    ok = apply_graph_colors(d, progress=lambda m: print(" ·", m))
    print("OK:", ok, "|", os.path.join(d, GRAPH_REL))
