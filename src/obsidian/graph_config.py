# -*- coding: utf-8 -*-
"""Obsidian 关系图(Graph View)美化: 按「课程」聚簇 + 按「层级」上色。

生成笔记后由 pipeline 调用 ``apply_graph_colors(vault_dir, vault_root=...)``: 往
``<Obsidian仓库根>/.obsidian/graph.json`` 写入/合并配色与显示参数, 让 Obsidian 自带的
「关系图」直接变成一张**按课程分块的思维导图**。

三层设计
--------
1. **聚簇靠标签枢纽**(``showTags``): 每条笔记 frontmatter 带一个 ``课程/<课程名>`` 标签。
   打开 ``showTags`` 后, Obsidian 会把每个标签画成一个"枢纽节点", 同一门课的所有笔记
   都连到同一个枢纽上, 力导向下自然收成一团 —— 这是让"一门课聚一块"的正解
   (光靠文件夹不成团, 关系图只认链接)。
2. **配色靠颜色组**(``colorGroups``), 两种方案:
     - ``course``(默认): 每门课一个专属色, 一眼分清哪团是哪门课; 层级色退居
       ``层级/*`` 标签枢纽节点, 作为跨课程的结构骨架。
     - ``layer``: 完全按层级上色(课程/目录/正文/概念/附录), 强调"结构"而非"课程"。
   注意 Obsidian 的规则是**先匹配到的组优先**, 所以课程组排在层级组之前。
3. **美观靠显示参数**: ``showArrow`` 显示层级流向、文字常显、节点/连线加粗、
   力导向参数调成"簇内紧、簇间开", 外加局部关系图 2 跳 —— 点开任一笔记都像展开一张导图。

设计要点
--------
  - **落在仓库根**: 输出目录通常是「课程子夹」, 需向上探测到真正的 vault 根再写,
    否则 Obsidian 读不到配色(会在课程夹里生成一个无效的 .obsidian)。
  - **幂等**: 按 query 去重, 重复转换同一课程不会叠加重复分组; 已删除课程的旧分组会被清掉。
  - **非破坏**: 保留 vault 原有配色分组与用户自定义的图谱外观; 美观参数只在
    "用户没动过(仍为 Obsidian 出厂值)"时才升级, 绝不覆盖用户的个人偏好。
  - **容错**: graph.json 缺失 / 损坏时, 用 Obsidian 标准骨架重建。
  - **可移植**: 纯 JSON 读写, 不依赖 Obsidian 内部版本。
"""
import hashlib
import json
import os
import re
from typing import Callable, Dict, List, Optional, Tuple

# 图谱配置文件相对 vault 根的位置
GRAPH_REL = os.path.join(".obsidian", "graph.json")

# ---- 层级 tag(写进笔记 frontmatter, 供图谱按 tag 查询上色) ----
TAG_COURSE = "层级/课程"     # 课程总目录(把同一门课的多个课件串成一团)
TAG_DIR = "层级/目录"        # index(讲义) / MOC(卡片)
TAG_BODY = "层级/正文"       # 讲义各节
TAG_CONCEPT = "层级/概念"    # 概念卡
TAG_APPENDIX = "层级/附录"   # 易错点 / 质量报告

# ---- 课程 tag: 每门课一个, 让关系图按课程聚簇(showTags 打开后成为枢纽节点) ----
COURSE_TAG_ROOT = "课程"


def course_tag(course_name: str) -> str:
    """把课程名转成 Obsidian 合法标签 ``课程/<名字>``。

    Obsidian 标签只允许 字母/数字/下划线/连字符/斜杠, 中文可以但**空格与标点不行**
    (会被吃掉从而导致同名标签对不上), 所以这里做一次清洗。
    """
    s = str(course_name or "").strip()
    s = re.sub(r"\s+", "", s)          # 标签里不能有空格
    s = re.sub(r"[^\w\-/]", "", s)     # 只留 字母/数字/下划线/中文 + - /
    s = s.strip("/-")
    return f"{COURSE_TAG_ROOT}/{s}" if s else COURSE_TAG_ROOT


# (查询, 颜色 RGB) —— 「按层级」配色; 列表顺序即图谱匹配优先级, 越靠前越优先
LAYER_GROUPS: List[Tuple[str, int]] = [
    ("tag:#层级/课程", 0x9B51E0),    # 紫:   课程总目录(整门课的枢纽节点)
    ("tag:#层级/目录", 0xF5A623),    # 金橙: 课件目录/索引
    ("tag:#层级/正文", 0x4A90E2),    # 蓝:   讲义正文(主体)
    ("tag:#层级/概念", 0x52B788),    # 绿:   概念卡
    ("tag:#层级/附录", 0x9AA5B1),    # 灰蓝: 易错点/质量报告
    ("path:attachments", 0xC8CDD3),  # 浅灰: 课件截图附件
]

# 兼容旧引用(此前只有层级配色)
DEFAULT_GROUPS = LAYER_GROUPS

# 「按课程」配色调色板: 12 色, 尽量色相分散, 在深浅主题下都清晰可辨
COURSE_PALETTE: List[int] = [
    0xE4572E,  # 朱红
    0x2E86DE,  # 湖蓝
    0x27AE60,  # 翠绿
    0xF2A600,  # 琥珀
    0x9B51E0,  # 紫罗兰
    0x00A6A6,  # 靛青
    0xD6417F,  # 玫红
    0x6C5CE7,  # 蓝紫
    0x7CB342,  # 黄绿
    0xB5651D,  # 赭石
    0x546E7A,  # 石板蓝灰
    0x00897B,  # 松绿
]

DEFAULT_SCHEME = "course"           # course(每课一色) | layer(按层级)
_COURSE_QUERY_PREFIX = f"tag:#{COURSE_TAG_ROOT}/"


def _hash_index(name: str, n: int) -> int:
    h = hashlib.md5(str(name).encode("utf-8")).hexdigest()
    return int(h[:8], 16) % n


def assign_course_colors(names: List[str]) -> Dict[str, int]:
    """给一组课程名分配**互不重复**的颜色(超过调色板容量才循环复用)。

    按课程名排序后依次取色: 同一批课程里颜色一定不撞, 且与传入顺序无关;
    新增一门课时, 只有排在它后面的课程可能换色(稳定优先于绝对不变)。
    """
    out: Dict[str, int] = {}
    n = max(1, len(COURSE_PALETTE))
    for i, name in enumerate(sorted(set(names or []))):
        out[name] = COURSE_PALETTE[i % n]
    return out


def course_color(name: str) -> int:
    """单门课的颜色(不保证与其它课程不撞色, 用于零散调用)。"""
    return COURSE_PALETTE[_hash_index(name, len(COURSE_PALETTE))]


def build_groups(courses: Optional[List[str]] = None,
                 scheme: str = DEFAULT_SCHEME) -> List[Tuple[str, int]]:
    """按方案拼出颜色组列表(顺序 = 优先级)。

    - ``layer`` : 只要层级组。
    - ``course``: 课程组在前(每课一色), 层级组在后(此时主要给 ``层级/*`` 标签枢纽上色,
      同时兜住不在任何课程夹里的零散笔记)。
    """
    if str(scheme or DEFAULT_SCHEME).strip().lower() == "layer":
        return list(LAYER_GROUPS)
    groups: List[Tuple[str, int]] = []
    colors = assign_course_colors(courses or [])
    for name in sorted(colors):
        groups.append((_COURSE_QUERY_PREFIX + course_tag(name).split("/", 1)[-1], colors[name]))
    groups.extend(LAYER_GROUPS)
    return groups


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
    "localBacklinks": True,
    "localForelinks": True,
    "localInterlinks": True,
    "localJumps": 2,
    "scale": 1,
    "close": False,
}

# 想要的美观值: 只在"用户没改过(仍为出厂值)"时应用, 避免覆盖个人偏好
BEAUTY: Dict = {
    "showTags": True,            # 【关键】标签变枢纽节点 -> 同课聚成一团
    "showArrow": True,           # 箭头显示层级流向, 更像思维导图
    "nodeSizeMultiplier": 1.2,   # 节点稍大, 标签更好认
    "lineSizeMultiplier": 1.1,   # 连线稍粗, 结构更清楚
    "centerStrength": 0.45,      # 中心引力略降 -> 各课程团散得开
    "repelStrength": 11,         # 斥力略升 -> 团与团之间留白
    "linkStrength": 1.1,         # 连着的节点收得更紧 -> 簇更凝实
    "linkDistance": 220,         # 连线基准距离略短 -> 整图更聚拢
    "localInterlinks": True,     # 局部关系图显示邻居之间的连线
    "localJumps": 2,             # 局部关系图看 2 跳 -> 点开任一笔记都像展开导图
}

# Obsidian 各字段的**出厂默认值**(用于判断"用户是否动过")
_FACTORY: Dict = {
    "showTags": False,
    "showArrow": False,
    "textFadeMultiplier": 0,
    "nodeSizeMultiplier": 1,
    "lineSizeMultiplier": 1,
    "centerStrength": 0.518713248970312,
    "repelStrength": 10,
    "linkStrength": 1,
    "linkDistance": 250,
    "collapse-color-groups": False,
    "collapse-display": True,
    "collapse-filter": True,
    "collapse-forces": True,
    "showAttachments": False,
    "hideUnresolved": False,
    "showOrphans": True,
    "localBacklinks": True,
    "localForelinks": True,
    "localInterlinks": False,
    "localJumps": 1,
}


def _load(path: str) -> Dict:
    """读取 graph.json; 不存在或损坏时返回标准骨架副本。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return dict(_SKELETON)


def _insert_before_fallback(kept: List[Dict], new_items: List[Dict]) -> List[Dict]:
    """把新分组插到「兜底空 query」之前; 没有兜底则追加到末尾。"""
    pos = len(kept)
    for i, g in enumerate(kept):
        if isinstance(g, dict) and not str(g.get("query") or "").strip():
            pos = i
            break
    return kept[:pos] + new_items + kept[pos:]


def _upgrade_beauty(data: Dict) -> List[str]:
    """把"用户没动过"的显示参数升级成我们调好的美观值; 返回被改动的字段名。"""
    changed = []
    for k, v in BEAUTY.items():
        if k not in data or data.get(k) == _FACTORY.get(k):
            if data.get(k) != v:
                data[k] = v
                changed.append(k)
    return changed


def _is_our_stray_obsidian(obs_dir: str) -> bool:
    """判断该 .obsidian 是否只是我们自己误建的(里面仅有 graph.json)。

    Obsidian 真正的 .obsidian 一定还有 app.json / workspace.json 等文件;
    早期版本把 graph.json 写进了「课程子夹」, 会在课程夹里留下一个假 vault 目录,
    这里识别出来并跳过, 避免一直把课程夹当成仓库根。
    """
    try:
        names = sorted(n for n in os.listdir(obs_dir) if not n.startswith("."))
    except Exception:
        return False
    return names == ["graph.json"]


def find_vault_root(path: str) -> str:
    """从 path 逐级向上找到第一个真正的 Obsidian 仓库根(含 .obsidian 的目录)。

    找不到时返回 path 自身(调用方会在那里新建 .obsidian)。
    会跳过仅有 graph.json 的"伪 .obsidian"(见 _is_our_stray_obsidian)。
    """
    if not path:
        return path
    start = os.path.abspath(path)
    cur = start
    while True:
        obs = os.path.join(cur, ".obsidian")
        if os.path.isdir(obs) and not _is_our_stray_obsidian(obs):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return start
        cur = parent


def rel_prefix(child: str, root: str) -> str:
    """child 相对 root 的仓库相对路径(用 / ), 供 Obsidian 双链定位。

    例: root=D:/vault, child=D:/vault/物理化学 -> '物理化学'; 同一层 -> ''。
    """
    if not child or not root:
        return ""
    try:
        rel = os.path.relpath(os.path.abspath(child), os.path.abspath(root))
    except Exception:
        return ""
    if rel in (".", ""):
        return ""
    return rel.replace(os.sep, "/")


def _has_markdown(d: str, depth: int = 2) -> bool:
    """目录(含浅层子目录)里是否有 .md, 用于判断它是不是一个"课程夹"。"""
    try:
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if os.path.isfile(p) and name.lower().endswith(".md"):
                return True
            if depth > 0 and os.path.isdir(p) and not name.startswith("."):
                if _has_markdown(p, depth - 1):
                    return True
    except Exception:
        pass
    return False


def discover_courses(root: str) -> List[str]:
    """列出 vault 根下的一级课程夹名(排除隐藏夹 / attachments / 空夹)。"""
    if not root or not os.path.isdir(root):
        return []
    out = []
    try:
        for name in sorted(os.listdir(root)):
            if name.startswith(".") or name.lower() == "attachments":
                continue
            p = os.path.join(root, name)
            if os.path.isdir(p) and _has_markdown(p):
                out.append(name)
    except Exception:
        pass
    return out


def apply_graph_colors(vault_dir: str,
                       groups: Optional[List[Tuple[str, int]]] = None,
                       progress: Optional[callable] = None,
                       vault_root: Optional[str] = None,
                       courses: Optional[List[str]] = None,
                       scheme: Optional[str] = None) -> bool:
    """把配色与美观参数合并进 ``<vault根>/.obsidian/graph.json``。

    :param vault_dir:  本次输出目录(通常是「课程子夹」)
    :param groups:     自定义 (query, rgb) 列表; 默认按 scheme + courses 生成
    :param progress:   进度回调(可空)
    :param vault_root: Obsidian 仓库根; 不给则从 vault_dir 向上自动探测
    :param courses:    参与配色的课程名; 不给则从仓库根自动发现
    :param scheme:     'course'(每课一色, 默认) | 'layer'(按层级上色)
    :return: 成功写入返回 True
    """
    if not vault_dir:
        if progress:
            progress("(未指定 vault 目录, 跳过图谱配色)")
        return False

    # 关键: graph.json 必须落在 Obsidian 仓库根, 写在课程子夹里 Obsidian 读不到
    root = vault_root or find_vault_root(vault_dir)
    obs_dir = os.path.join(root, ".obsidian")
    path = os.path.join(obs_dir, "graph.json")
    sch = str(scheme or DEFAULT_SCHEME).strip().lower()
    if groups is None:
        if courses is None:
            courses = discover_courses(root)
        groups = build_groups(courses, sch)
    want = list(groups)
    want_queries = {q for q, _ in want}

    try:
        os.makedirs(obs_dir, exist_ok=True)
        data = _load(path)

        cur = data.get("colorGroups")
        if not isinstance(cur, list):
            cur = []
        # 幂等: 先剔除我们此前写入的同 query 条目 + 已消失课程的旧课程组, 再统一追加
        kept = []
        for g in cur:
            if not isinstance(g, dict):
                kept.append(g)
                continue
            q = str(g.get("query") or "")
            if q in want_queries:
                continue
            if q.startswith(_COURSE_QUERY_PREFIX):
                continue          # 课程组由我们全量接管(课程删了就不该再留着配色)
            kept.append(g)

        new_items = [{"query": q, "color": {"a": 1, "rgb": int(rgb)}} for q, rgb in want]
        data["colorGroups"] = _insert_before_fallback(kept, new_items)

        for k, v in _SKELETON.items():
            data.setdefault(k, v)
        # 只在用户没动过时升级外观, 不覆盖个人偏好
        _upgrade_beauty(data)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        if progress:
            scheme_txt = "按课程一色 + 层级骨架" if sch != "layer" else "按层级"
            n_course = max(0, len(new_items) - len(LAYER_GROUPS))
            extra = f", 课程 {n_course} 门" if n_course else ""
            progress(f"🎨 已在「关系图」配好配色({scheme_txt}; {len(new_items)} 组{extra}) @ {root}; "
                     f"打开 Obsidian 关系图即可看到同课聚成一团")
        return True
    except Exception as e:
        if progress:
            progress(f"(图谱配色写入失败, 已跳过: {e})")
        return False


def legend_lines(scheme: Optional[str] = None) -> List[str]:
    """返回给用户看的配色图例(可写进笔记/日志/文档)。"""
    sch = str(scheme or DEFAULT_SCHEME).strip().lower()
    if sch == "layer":
        return ["紫＝课程枢纽", "金橙＝课件目录", "蓝＝正文", "绿＝概念", "灰蓝＝附录/附件"]
    return ["每门课一个专属色", "紫＝课程枢纽 tag", "金橙＝课件目录", "蓝＝正文", "绿＝概念", "灰蓝＝附录"]


def layer_tags() -> List[str]:
    """返回本项目使用的全部层级 tag(便于测试与文档)。"""
    return [TAG_COURSE, TAG_DIR, TAG_BODY, TAG_CONCEPT, TAG_APPENDIX]


if __name__ == "__main__":
    import sys
    import tempfile
    d = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
    ok = apply_graph_colors(d, progress=lambda m: print(" ·", m))
    print("OK:", ok, "|", os.path.join(d, GRAPH_REL))
