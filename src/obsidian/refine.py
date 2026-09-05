# -*- coding: utf-8 -*-
"""
对话修改 (Refine) —— 把 GUI 右侧的"聊天修改建议"变成真正的二次学习。

用户生成笔记后, 可能发现某概念定义不准、少了要点、符号含义错误、易错点漏了。
直接让用户手动改 markdown 门槛高且不沉淀。本模块提供:

  1. revise_note_text(...): 把「用户建议 + PPT 原文 + 专业框架(官方+自学沉淀) + 当前笔记」
     打包喂给大模型, 让它就地修订那张 Obsidian 原子笔记(或整体 MOC/任意 markdown),
     返回修订后的 markdown(保留 frontmatter / 结构)。
  2. extract_delta(...): 再问一次, 让模型把"本次修正/补充的学科知识点"输出成结构化 JSON
     (纠正的定义 / 新增符号 / 新增易错点), 用于喂回知识库校准。
  3. CalibrationStore.merge_knowledge_delta(...): 用户认可的高置信修正直接入库并标记
     confirmed, 让后续转换"越改越准、越用越富"。

GUI 流程:
  用户在右侧选中一个已生成概念 -> 输入修改意见 -> 预览修订 -> 点"应用到笔记"
  -> 写回 vault(替换该概念笔记正文) + 沉淀知识 delta。
"""
import json
import re
from dataclasses import dataclass, field
from typing import List, Dict, Optional

from core.ppt_parser import PresentationDoc
from obsidian.kb_calibration import CalibrationStore


@dataclass
class RefineResult:
    revised_md: str = ""
    used_llm: bool = False
    error: str = ""
    delta: Dict = field(default_factory=dict)   # extract_delta 得到的结构化修正


# ---------------- 修订 prompt ----------------

REVISE_SYSTEM_TMPL = """你是一位严谨的{prof_name}笔记整理助手。用户刚用本工具把一份 PPT 转成了 Obsidian 笔记,
现在对其中一篇提出修改意见。你的任务: **就地修订该篇笔记**, 使其更准确、更贴合本专业规范, 并吸收用户的合理建议。

必须遵守:
1. 只输出修订后的整篇笔记 markdown, 不要任何解释/客套/评语。
2. 保留笔记原有的 YAML frontmatter(title/tags/professional/source/created 等)与正文结构。
3. 若用户建议与 PPT 原文或学科事实冲突, 以**学科规范正确**为准, 并在相应位置修正。
4. 概念命名尽量使用专业规范术语; 补充的内容要有依据, 不要臆造清单/PPT 都没有的东西。
5. 涉及公式符号时给出含义注释; 若修正了概念定义/新增要点/纠正易错点, 请确保写进正文。

下方给出现有笔记、PPT 相关原文、专业上下文。请直接输出修订后的完整 markdown。
"""


def _slice_ppt_around(pages_hint: str, ppt_text: str, radius: int = 400) -> str:
    """从整份 PPT 全文里取与 source_pages 相关的一段, 控制 token 避免塞整份。"""
    if not ppt_text:
        return ""
    # 按"第 N 页"分块(仅用于定位, 不真正分割也安全)
    if not pages_hint:
        return ppt_text[:2000]
    # 简单策略: 定位第一个命中页码的分块附近
    m = re.search(r"\d+", str(pages_hint))
    if not m:
        return ppt_text[:2000]
    target = int(m.group())
    # 直接返回包含该页的行(粗取前后)
    idx = ppt_text.find(f"第 {target} 页")
    if idx == -1:
        return ppt_text[:2000]
    start = max(0, idx - radius)
    return ppt_text[start: start + radius * 2]


def build_revise_messages(*, concept_or_target: str, current_md: str,
                          user_request: str, professional_name: str,
                          ppt_text: str = "", pages_hint: str = "",
                          kb_context: str = "", extra_context: str = "") -> List[Dict]:
    """组装一次"修订某篇笔记"的 messages。"""
    sys_msg = REVISE_SYSTEM_TMPL.replace("{prof_name}", professional_name)
    user_parts = []
    user_parts.append(f"要修订的笔记: 《{concept_or_target}》")
    user_parts.append("=== 当前笔记内容 ===")
    user_parts.append(current_md)
    snippet = _slice_ppt_around(pages_hint, ppt_text)
    if snippet:
        user_parts.append("\n=== PPT 相关原文 ===")
        user_parts.append(snippet)
    if kb_context:
        user_parts.append("\n=== 专业上下文 ===")
        user_parts.append(kb_context)
    if extra_context:
        user_parts.append("\n" + extra_context)
    user_parts.append(f"\n=== 用户的修改意见 ===\n{user_request}")
    user_parts.append("\n请输出修订后的完整 markdown。")
    return [
        {"role": "system", "content": sys_msg},
        {"role": "user", "content": "\n".join(user_parts)},
    ]


def revise_note_text(*, llm, concept_or_target: str, current_md: str,
                     user_request: str, professional_name: str,
                     ppt_text: str = "", pages_hint: str = "",
                     kb_context: str = "", extra_context: str = "",
                     progress=None) -> RefineResult:
    """调用模型就地修订一篇笔记, 返回修订后 markdown。"""
    res = RefineResult()
    try:
        msgs = build_revise_messages(
            concept_or_target=concept_or_target, current_md=current_md,
            user_request=user_request, professional_name=professional_name,
            ppt_text=ppt_text, pages_hint=pages_hint,
            kb_context=kb_context, extra_context=extra_context)
        if progress:
            progress("正在让模型修订笔记…")
        out = llm.chat(msgs, max_tokens=4096)
        revised = (out or "").strip()
        # 去掉可能的 ```markdown 围栏
        fence = re.search(r"```(?:markdown|md)?\s*(.*?)```", revised, re.S)
        if fence:
            revised = fence.group(1).strip()
        if not revised:
            res.error = "模型未返回修订内容"
            return res
        res.revised_md = revised
        res.used_llm = True
        return res
    except Exception as e:
        res.error = str(e)
        return res


# ---------------- 知识 delta 提取 ----------------

DELTA_PROMPT = """从刚才这次对笔记《{target}》的修改中, 提炼出**值得沉淀进学科知识库**的结构化修正项。
只输出 JSON, 不要多余文字, 不要 markdown 代码块。若某项没有, 给空数组。

{{
  "corrected_definitions": [{{"name":"被修正/明确的概念名","definition":"修正后的规范定义"}}],
  "new_symbols": [{{"symbol":"本次补充/修正的符号或缩写","meaning":"其规范含义"}}],
  "new_misconceptions": [{{"misconception":"本次澄清的常见误解","clarification":"正确理解"}}]
}}
"""


def extract_delta(*, llm, target: str, original_md: str, revised_md: str,
                  professional_name: str, progress=None) -> Dict:
    """让模型把"用户认可的修改"转成结构化知识修正项(供校准沉淀)。失败则返回空。"""
    try:
        sys_msg = (f"你是严谨的{professional_name}知识库维护助手, 只输出 JSON。")
        user_parts = [
            f"原笔记《{target}》:",
            original_md,
            "\n修改后:",
            revised_md,
            "\n" + DELTA_PROMPT.format(target=target),
        ]
        if progress:
            progress("正在提炼本次修正为可沉淀的知识…")
        out = llm.chat([{"role": "system", "content": sys_msg},
                        {"role": "user", "content": "\n".join(user_parts)}], max_tokens=1200)
        data = _parse_json(out)
        return data or {}
    except Exception:
        return {}


def _parse_json(text: str) -> Optional[Dict]:
    if not text:
        return None
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1 or e <= s:
        return None
    cand = text[s:e + 1]
    try:
        return json.loads(cand)
    except Exception:
        return None


# ---------------- 校准入口 ----------------

def apply_user_correction(calib: Optional[CalibrationStore], delta: Dict) -> int:
    """把用户认可的修正项沉淀进该专业自学库(高置信, 直接 confirmed)。返回沉淀条数。"""
    if calib is None:
        return 0
    n = 0
    for item in (delta.get("corrected_definitions") or []):
        name = (item or {}).get("name", "").strip()
        definition = (item or {}).get("definition", "").strip()
        if not name or not definition:
            continue
        calib.upsert_definition(name, definition, confirmed=True)
        n += 1
    for item in (delta.get("new_symbols") or []):
        sym = (item or {}).get("symbol", "").strip()
        meaning = (item or {}).get("meaning", "").strip()
        if not sym or not meaning:
            continue
        calib.upsert_symbol(sym, meaning, confirmed=True)
        n += 1
    for item in (delta.get("new_misconceptions") or []):
        mc = (item or {}).get("misconception", "").strip()
        cl = (item or {}).get("clarification", "").strip()
        if not mc or not cl:
            continue
        calib.upsert_misconception(mc, cl, confirmed=True)
        n += 1
    return n


if __name__ == "__main__":
    print("refine 模块加载 OK")
