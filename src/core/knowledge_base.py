# -*- coding: utf-8 -*-
"""
专业知识库加载器 (Knowledge Base Loader)

职责:
  - 扫描 professions/ 目录, 加载每个专业的规范 JSON 框架。
  - 对外提供统一的数据访问接口, 供 summarizer 注入、概念检索、符号还原等使用。
  - 未来新增专业 = 往 professions/ 放入一个符合 schema 的 JSON 文件即可, 无需改代码。

Schema (per file):
{
  "id": "pharmacy",
  "name": "药学",
  "desc": "...",
  "tags": [...],
  "note_template": "...",
  "core_concepts":       [{"name": "...", "definition": "..."}],
  "symbol_mapping":      [{"symbol": "...", "meaning": "..."}],
  "common_misconceptions":[{"misconception":"...", "clarification":"..."}]
}
"""
import json
import os
import re
import sys
from dataclasses import dataclass, field
from typing import List, Dict, Optional

# 期望的 schema 顶层字段, 用于宽松校验/兜底
_REQUIRED = ["id", "name", "core_concepts", "symbol_mapping", "note_template"]

# 常见键名别名(兼容未来不同命名)
_KEY_ALIASES = {
    "core_concepts": ["core_concepts", "concepts", "terms"],
    "symbol_mapping": ["symbol_mapping", "symbols", "abbreviations"],
    "common_misconceptions": ["common_misconceptions", "misconceptions", "common_mistakes"],
    "note_template": ["note_template", "template"],
}


def _pick(obj: dict, keys) -> object:
    for k in keys:
        if k in obj and obj[k]:
            return obj[k]
    return None


@dataclass
class Profession:
    """一个已加载的专业知识框架。"""
    id: str
    name: str
    desc: str = ""
    tags: List[str] = field(default_factory=list)
    note_template: str = ""
    core_concepts: List[Dict] = field(default_factory=list)
    symbol_mapping: List[Dict] = field(default_factory=list)
    common_misconceptions: List[Dict] = field(default_factory=list)
    source_file: str = ""

    # ---- 便捷索引 ----
    _concept_by_name: Dict[str, str] = field(default_factory=dict)   # name -> definition
    _concept_norm: Dict[str, str] = field(default_factory=dict)      # norm(name) -> name
    _symbol_norm: Dict[str, str] = field(default_factory=dict)       # norm(symbol) -> meaning

    def build_index(self):
        self._concept_by_name = {}
        self._concept_norm = {}
        for c in self.core_concepts:
            nm = c.get("name", "").strip()
            if not nm:
                continue
            self._concept_by_name[nm] = c.get("definition", "").strip()
            self._concept_norm[_norm(nm)] = nm
        self._symbol_norm = {}
        for s in self.symbol_mapping:
            sym = s.get("symbol", "").strip()
            if not sym:
                continue
            # 记录原始与规范化两种, 便于匹配(长度长的优先)
            self._symbol_norm[_norm(sym)] = s.get("meaning", "").strip()
            self._symbol_norm.setdefault(_norm(sym), s.get("meaning", ""))

    # ---- 查询接口 ----
    def concept_count(self) -> int:
        return len(self.core_concepts)

    def find_concept_definition(self, name: str) -> Optional[str]:
        key = _norm(name)
        if key in self._concept_norm:
            return self._concept_by_name[self._concept_norm[key]]
        return None

    def contains_concept(self, name: str) -> bool:
        return _norm(name) in self._concept_norm

    def decode_symbol(self, symbol: str) -> Optional[str]:
        """符号还原: 输入如 't1/2' 返回 '消除半衰期'。大小写/空格宽松匹配。"""
        key = _norm(symbol)
        if not key:
            return None
        return self._symbol_norm.get(key)


def _norm(s: str) -> str:
    """规范化字符串用于匹配: 去首尾空白、转小写、去内部空白与点。"""
    if not s:
        return ""
    return re.sub(r"[\s\.]", "", s).lower()


class KnowledgeBase:
    """专业集合, 由 professions/ 目录驱动。"""

    def __init__(self, professions_dir: Optional[str] = None):
        self.professions_dir = professions_dir or _default_dir()
        self.professions: Dict[str, Profession] = {}   # id -> Profession
        self._order: List[str] = []

    def load_all(self) -> List[Profession]:
        """扫描目录加载全部专业 JSON。返回按文件名排序的列表。"""
        self.professions = {}
        self._order = []
        if not os.path.isdir(self.professions_dir):
            return []
        files = sorted(
            f for f in os.listdir(self.professions_dir)
            if f.lower().endswith(".json")
        )
        for fn in files:
            path = os.path.join(self.professions_dir, fn)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                prof = self._parse(obj, path)
            except Exception:
                continue
            if prof is not None:
                self.professions[prof.id] = prof
                self._order.append(prof.id)
        return list(self.professions.values())

    @staticmethod
    def _parse(obj: dict, path: str) -> Optional[Profession]:
        if not isinstance(obj, dict):
            return None
        name = str(obj.get("name", "")).strip()
        if not name:
            # 尝试从文件名推断
            name = os.path.splitext(os.path.basename(path))[0]
        pid = str(obj.get("id") or name).strip()

        core = _pick(obj, _KEY_ALIASES["core_concepts"]) or []
        syms = _pick(obj, _KEY_ALIASES["symbol_mapping"]) or []
        mis = _pick(obj, _KEY_ALIASES["common_misconceptions"]) or []
        tpl = str(_pick(obj, _KEY_ALIASES["note_template"]) or "").strip()

        p = Profession(
            id=pid,
            name=name,
            desc=str(obj.get("desc", "") or "").strip(),
            tags=[str(t) for t in (obj.get("tags") or []) if str(t).strip()],
            note_template=tpl,
            core_concepts=list(core),
            symbol_mapping=list(syms),
            common_misconceptions=list(mis),
            source_file=os.path.basename(path),
        )
        p.build_index()
        return p

    def get(self, pid: str) -> Optional[Profession]:
        return self.professions.get(pid)

    def list_ids(self) -> List[str]:
        return list(self._order)

    def summary(self) -> str:
        lines = []
        for pid in self._order:
            p = self.professions[pid]
            lines.append(
                f"  · {p.name} ({pid}): 概念{p.concept_count()} 个, "
                f"符号 {len(p.symbol_mapping)} 个, 易错点 {len(p.common_misconceptions)} 个"
            )
        return "\n".join(lines)


def _default_dir():
    """默认 professions 目录。
    - 源码运行: 项目根下的 professions/
    - PyInstaller 打包后: 数据文件打包在 sys._MEIPASS/professions/
    """
    # 打包场景(frozen)
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        cand = os.path.join(bundle_dir, "professions")
        if os.path.isdir(cand):
            return cand
        # 单目录模式 _internal
        cand = os.path.join(bundle_dir, "_internal", "professions")
        if os.path.isdir(cand):
            return cand
    here = os.path.dirname(os.path.abspath(__file__))
    # src/core/... -> 上一级(..) 是 src, 再上一级是项目根
    root = os.path.dirname(os.path.dirname(here))
    return os.path.join(root, "professions")


if __name__ == "__main__":
    kb = KnowledgeBase()
    profs = kb.load_all()
    print(f"已加载 {len(profs)} 个专业:\n{kb.summary()}")
    # 自检: 符号还原 & 概念查询
    p = kb.get("pharmacy")
    if p:
        print("\n符号自检 t1/2 ->", p.decode_symbol("t1/2"))
        print("概念自检 '半衰期' ->", p.find_concept_definition("半衰期"))
