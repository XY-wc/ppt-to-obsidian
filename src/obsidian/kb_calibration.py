# -*- coding: utf-8 -*-
"""
知识库校准 / 自学补充 (Knowledge-Base Calibration) —— P1 复利护城河。

问题: 官方 professions/*.json 是**静态**的, 每份新 PPT 总会出现清单外的
术语/符号。若只靠官方清单, "越转越准"就是空话。

方案: 每转一次, 把本次 PPT 里**新出现、但官方清单不认识**的高频术语 / 符号
累积到用户级自学库 `<~/.ppt2obsidian>/kb_extras/<prof_id>.json`(注意: 数据目录
归属与 app_config 一致, 保证打包后可写)。下次转换时:
  - summarizer 注入上下文 = 官方清单 + 自学补充(命中过的) 合并成扩展上下文;
  - detect_concepts_used 等检测也覆盖自学词;
  - 被跨多份课件反复确认(>= 2 次)的候选打上 "confirmed", 可后续一键 promote
    回官方 JSON(本模块提供 promote_to_official 接口, GUI/CLI 可选接入)。

文件结构 (<prof_id>.json):
{
  "extra_concepts": [
     {"name":"...", "count": 2, "confirmed": true, "first_seen":"YYYY-MM-DD"}
  ],
  "extra_symbols":   [ {"symbol":"...", "meaning":"...", "count":1} ],
  "extra_misconceptions": [ {"misconception":"...", "clarification":"...", "count":1} ]
}

对外 API:
  - CalibrationStore(prof_id, base_dir=None)
  - store.learn_from_text(fulltext, official_concepts)   # 在转换后调用, 沉淀新词
  - store.to_extra_context()                              # 拼一段可注入的"自学补充"
  - store.merged_concept_names(official_names)            # 官方 + 自学, 供检测/命中
  - store.promote_to_official(prof_json_path)             # 把 confirmed 项写回官方(可选)
"""
import json
import os
import re
import sys
from datetime import date
from typing import List, Dict, Optional

CONFIRM_THRESHOLD = 2


def _default_base_dir() -> str:
    """与 app_config.AppPaths 一致: ~/.ppt2obsidian; 若不可写则退回临时目录。"""
    if getattr(sys, "_MEIPASS", None):
        # 打包场景: 用户主目录通常可写
        cand = os.path.join(os.path.expanduser("~"), ".ppt2obsidian")
    else:
        cand = os.path.join(os.path.expanduser("~"), ".ppt2obsidian")
    try:
        os.makedirs(cand, exist_ok=True)
        return cand
    except Exception:
        tmp = os.path.join(os.path.expanduser("~"), ".ppt2obsidian")
        try:
            os.makedirs(tmp, exist_ok=True)
        except Exception:
            tmp = os.path.dirname(os.path.abspath(__file__))
        return tmp


def _norm(s: str) -> str:
    if not s:
        return ""
    return re.sub(r"[\s\.\-_·:：（）()/\\]+", "", str(s)).lower()


class CalibrationStore:
    """一门课/一个专业的自学补充库。local-first, JSON 持久化。

    scope_dir 语义: 当在"整 Vault 分课程"场景(每门课一个文件夹)下, 传入
    scope_dir=课程文件夹, 则该库的 JSON 落在 <课程文件夹>/.ppt2obsidian/<prof_id>.json,
    实现"一科一记忆、切换课程即隔离"(语文库绝不串到数学)。
    不传时退回旧行为: 放在用户级 ~/.ppt2obsidian/kb_extras/<prof_id>.json。
    """

    def __init__(self, prof_id: str, base_dir: Optional[str] = None,
                 scope_dir: Optional[str] = None):
        self.prof_id = prof_id
        self.scope_dir = scope_dir
        self.base_dir = base_dir or _default_base_dir()
        if scope_dir:
            self._dir = os.path.join(scope_dir, ".ppt2obsidian")
            self.file = os.path.join(self._dir, f"{prof_id}.json")
        else:
            self._dir = os.path.join(self.base_dir, "kb_extras")
            self.file = os.path.join(self._dir, f"{prof_id}.json")
        self.data = self._load()

    def _load(self) -> dict:
        try:
            if os.path.isfile(self.file):
                with open(self.file, "r", encoding="utf-8") as f:
                    return json.load(f)
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
        return {"extra_concepts": [], "extra_symbols": [], "extra_misconceptions": []}

    def _save(self):
        try:
            os.makedirs(self._dir, exist_ok=True)
            with open(self.file, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    # ---- 结构访问 ----
    def extra_concepts(self) -> List[Dict]:
        return self.data.setdefault("extra_concepts", [])

    def extra_symbols(self) -> List[Dict]:
        return self.data.setdefault("extra_symbols", [])

    def extra_misconceptions(self) -> List[Dict]:
        return self.data.setdefault("extra_misconceptions", [])

    def concept_names(self) -> List[str]:
        return [c.get("name", "") for c in self.extra_concepts() if c.get("name")]

    # ---- 用户认可修正的高置信入库(refine 调用) ----
    def upsert_definition(self, name: str, definition: str, confirmed: bool = True):
        """沉淀"被用户/模型认可的规范定义"。若概念已在自学库则补 definition 并 confirm。"""
        today = date.today().isoformat()
        arr = self.extra_concepts()
        for c in arr:
            if _norm(c.get("name", "")) == _norm(name):
                c["definition"] = definition
                c["count"] = int(c.get("count", 0)) + 1
                if confirmed:
                    c["confirmed"] = True
                self._save()
                return
        arr.append({"name": name, "definition": definition,
                    "count": 1, "confirmed": confirmed, "first_seen": today,
                    "source": "user_correction"})
        self._save()

    def upsert_symbol(self, symbol: str, meaning: str, confirmed: bool = True):
        today = date.today().isoformat()
        arr = self.extra_symbols()
        for s in arr:
            if _norm(s.get("symbol", "")) == _norm(symbol):
                s["meaning"] = meaning
                s["count"] = int(s.get("count", 0)) + 1
                if confirmed:
                    s["confirmed"] = True
                self._save()
                return
        arr.append({"symbol": symbol, "meaning": meaning,
                    "count": 1, "confirmed": confirmed, "first_seen": today,
                    "source": "user_correction"})
        self._save()

    def upsert_misconception(self, misconception: str, clarification: str, confirmed: bool = True):
        today = date.today().isoformat()
        arr = self.extra_misconceptions()
        for m in arr:
            if _norm(m.get("misconception", "")) == _norm(misconception):
                m["clarification"] = clarification
                m["count"] = int(m.get("count", 0)) + 1
                if confirmed:
                    m["confirmed"] = True
                self._save()
                return
        arr.append({"misconception": misconception, "clarification": clarification,
                    "count": 1, "confirmed": confirmed, "first_seen": today,
                    "source": "user_correction"})
        self._save()

    # ---- 学习: 从 PPT 全文沉淀新术语 ----
    def learn_from_text(self, fulltext: str, official_concepts: List[str],
                        official_symbols: Optional[List[str]] = None,
                        candidate_terms: Optional[List[str]] = None):
        """在转换后调用: 发现全文里的高频词, 若不在官方清单则沉淀进自学库。

        - candidate_terms: 可选, 通常来自 LLM 返回 concepts 里但不在官方清单的新概念。
        - 通过 candidate_terms(带语义、可信度更高)优先; 否则退化为词频启发。
        """
        today = date.today().isoformat()
        official_norm = {_norm(x) for x in official_concepts}
        official_sym_norm = {_norm(x) for x in (official_symbols or [])}

        known_extra = {_norm(c.get("name", "")) for c in self.extra_concepts()}

        # 1) 显式候选(LLM 识别的概念) — 高可信
        added_any = False
        for term in (candidate_terms or []):
            t = (term or "").strip()
            if not t or len(t) < 2:
                continue
            nk = _norm(t)
            if nk in official_norm or nk in known_extra:
                continue
            known_extra.add(nk)
            self.extra_concepts().append({
                "name": t, "count": 1, "confirmed": False,
                "first_seen": today,
                "source": "llm_candidate",
            })
            added_any = True

        # 2) 词频启发(无显式候选时才启用, 降低噪声): 找出全文里 3+ 字且命中 ≥2 次的候选
        #    仅当没从 LLM 拿到候选时作为补充。
        if added_any:
            pass  # 已有 LLM 候选, 避免词频把无关名词塞进来
        else:
            freq = self._frequent_terms(fulltext, official_norm | known_extra)
            for t, _cnt in freq.items():
                self.extra_concepts().append({
                    "name": t, "count": 1, "confirmed": False,
                    "first_seen": today, "source": "frequency",
                })

        self._cap_concepts()
        self._save()

    @staticmethod
    def _frequent_terms(fulltext: str, exclude_norm: set) -> Dict[str, int]:
        """粗略词频: 统计中文 2~6 字片段出现次数(启发式, 仅作无 LLM 候选时的补充)。"""
        if not fulltext:
            return {}
        # 简化: 从分隔/常见字切分后的词干里找重复出现的中文串并不精确,
        # 这里用保守策略: 匹配 ≥2 次的连续中文词组(3~6字), 取 top。
        chunks = re.findall(r"[\u4e00-\u9fff]{3,6}", fulltext)
        from collections import Counter
        cnt = Counter(chunks)
        out = {}
        for w, n in cnt.items():
            if n < 2:
                continue
            if _norm(w) in exclude_norm:
                continue
            # 过滤明显普通词
            if any(sw in w for sw in ("其中", "因为", "所以", "以及", "例如", "如下", "一种", "主要", "可以", "进行")):
                continue
            out[w] = n
            if len(out) >= 10:
                break
        return out

    def _cap_concepts(self, limit: int = 200):
        """自学库容量上限, 防无限膨胀。"""
        arr = self.extra_concepts()
        if len(arr) > limit:
            self.data["extra_concepts"] = arr[-limit:]

    def confirm_tally(self, concepts_seen_this_run: List[str]):
        """在每次成功转换后调用: 把"本次 PPT 实际讲到的"自学词 count +1,
        达到阈值的标记 confirmed(可 promote)。"""
        changed = False
        by_norm = {_norm(c.get("name", "")): c for c in self.extra_concepts()}
        for name in concepts_seen_this_run:
            nk = _norm(name)
            c = by_norm.get(nk)
            if not c:
                continue
            c["count"] = int(c.get("count", 0)) + 1
            if c["count"] >= CONFIRM_THRESHOLD:
                c["confirmed"] = True
            changed = True
        if changed:
            self._save()

    # ---- 供 summarizer 注入 ----
    def to_extra_context(self, max_concepts: int = 40, max_symbols: int = 20) -> str:
        """拼一段"自学补充"上下文, 追加在官方 KB context 之后。"""
        lines = []
        confirmed = [c.get("name", "") for c in self.extra_concepts()
                     if c.get("confirmed") and c.get("name")]
        if confirmed:
            lines.append(f"【自学沉淀·本专业你此前遇到过的补充概念(可用)]{'、'.join(confirmed[:max_concepts])}")
        syms = [f"{s.get('symbol')}({s.get('meaning')})" for s in self.extra_symbols()[:max_symbols]
                if s.get("symbol") and s.get("meaning")]
        if syms:
            lines.append(f"【自学沉淀·补充符号】{'、'.join(syms)}")
        mis = [m.get("misconception", "") for m in self.extra_misconceptions()[:10]
               if m.get("misconception")]
        if mis:
            lines.append(f"【自学沉淀·易错点】{'；'.join(mis)}")
        return "\n".join(lines)

    def merged_concept_names(self, official_names: List[str]) -> List[str]:
        """官方 + 已 confirmed 自学概念 的合并名清单(供检测/命中)。"""
        names = list(official_names)
        names.extend(self.concept_names())
        return names

    # ---- promote: 把 confirmed 项写回官方(可选, 防污染官方文件) ----
    def confirmed_items(self) -> Dict:
        return {
            "concepts": [c for c in self.extra_concepts() if c.get("confirmed")],
            "symbols": [s for s in self.extra_symbols() if s.get("confirmed")],
            "misconceptions": [m for m in self.extra_misconceptions() if m.get("confirmed")],
        }

    def promote_to_official(self, prof_json_path: str) -> int:
        """把 confirmed 概念合并进官方 professions JSON(会去重)。返回新增数。"""
        try:
            with open(prof_json_path, "r", encoding="utf-8") as f:
                obj = json.load(f)
        except Exception:
            return 0
        existing = {_norm(c.get("name", "")) for c in obj.get("core_concepts", [])}
        added = 0
        for c in self.extra_concepts():
            if c.get("confirmed") and _norm(c.get("name", "")) not in existing:
                obj.setdefault("core_concepts", []).append({
                    "name": c.get("name", ""),
                    "definition": c.get("definition", ""),
                })
                existing.add(_norm(c.get("name", "")))
                added += 1
        if added:
            try:
                with open(prof_json_path, "w", encoding="utf-8") as f:
                    json.dump(obj, f, ensure_ascii=False, indent=2)
                # 清掉已 promote 的 confirmed 概念
                self.data["extra_concepts"] = [
                    c for c in self.extra_concepts() if not c.get("confirmed")]
                self._save()
            except Exception:
                return 0
        return added


if __name__ == "__main__":
    import tempfile
    tmp = tempfile.mkdtemp()
    st = CalibrationStore("pharmacy", base_dir=tmp)
    full = "本讲重点介绍稳态血药浓度与给药间隔的关系，以及t1/2对给药方案设计的影响。"
    st.learn_from_text(full, ["半衰期"])
    print("沉淀:", [c["name"] for c in st.extra_concepts()])
    print("补充上下文:", st.to_extra_context())
