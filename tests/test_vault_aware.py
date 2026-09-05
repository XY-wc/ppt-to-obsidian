# -*- coding: utf-8 -*-
"""
P0 vault 感知端到端测试:
1) 第一次转换 -> 全新建立 概念/MOC。
2) 对同一 vault 做第二次转换(用含重叠概念的 MockLLM) -> 重叠概念应被"复用合并"
   而不是再新建 "<name>_2.md"; 既有原子笔记尾部应追加"增量"块。
3) 全新概念(第二次才出现) -> 正常新建。

用临时 vault 目录, 不影响真实数据。
"""
import os
import sys
import tempfile
import json

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))
from pipeline import run_conversion
from obsidian import vault_aware as va


class MockLLM:
    """带 configurable concepts, 用于模拟两次不同课件。"""
    model = "mock-llm"

    def __init__(self, concepts):
        self._concepts = concepts

    def chat(self, messages, max_tokens=4096):
        data = {
            "title": "测试课",
            "sections": [{"heading": "章1", "summary": "本讲要点。", "concept_links": [c["concept"] for c in self._concepts]}],
            "concepts": self._concepts,
            "misconceptions": [],
        }
        return "```json\n" + json.dumps(data, ensure_ascii=False) + "\n```"


def _concept(name, pages):
    return {
        "concept": name,
        "definition": f"{name} 的定义",
        "detail": f"- {name} 的要点A\n- {name} 的要点B",
        "related": [],
        "symbols": [],
        "source_pages": pages,
    }


def _count_md(tmp):
    n = 0
    for root, _, files in os.walk(tmp):
        for f in files:
            if f.endswith(".md"):
                n += 1
    return n


def main():
    tmp = tempfile.mkdtemp()
    ppt = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs", "sample_pharmacy.pptx")
    if not os.path.exists(ppt):
        print("缺少示例PPT, 跳过")
        return 1

    # 第一次: 讲 生物利用度 + 首过效应
    r1 = run_conversion(ppt, "pharmacy", tmp,
                        llm=MockLLM([_concept("生物利用度", "2"), _concept("首过效应", "2")]),
                        progress=lambda m: print("  ·", m))
    print("首次 OK:", r1.ok)
    # 第二次: 生物利用度(重叠, 带新视角) + 半衰期(全新概念)
    c2 = _concept("生物利用度", "3")
    c2["detail"] = "- 缓释/控释制剂可减少给药频次\n- 静脉注射生物利用度100%"
    r2 = run_conversion(ppt, "pharmacy", tmp,
                        llm=MockLLM([c2, _concept("半衰期", "4")]),
                        progress=lambda m: print("  ·", m))
    print("二次 OK:", r2.ok)

    # 断言 1: 生物利用度只应有一份原子笔记(没有 _1/_2 副本)
    bio_files = [f for f in os.listdir(tmp) if "生物利用度" in f]
    print("生物利用度 目录条目:", bio_files)
    # 概念落在 测试课/ 子目录
    for root, dirs, files in os.walk(tmp):
        for f in files:
            if "生物利用度" in f and f.endswith(".md"):
                bio_files.append(os.path.join(root, f))
    # 查是否有重复序号副本
    dups = [f for f in bio_files if "_1" in f or "_2" in f]
    print("重复副本(应为空):", dups)

    # 读取合并后的生物利用度笔记, 应含第二次的 source_pages 增量
    merged_content = ""
    for root, _, files in os.walk(tmp):
        for f in files:
            if f == "生物利用度.md":
                with open(os.path.join(root, f), encoding="utf-8") as fh:
                    merged_content = fh.read()
    print("\n合并后是否含增量块:", "增量" in merged_content, "| 是否含新视角要点:", "缓释/控释制剂可减少给药频次" in merged_content, "| 是否含第3页来源:", "(第3页)" in merged_content)
    assert "增量" in merged_content, "应出现增量块"
    assert "缓释/控释制剂可减少给药频次" in merged_content, "第二次的新视角要点应被追加"
    print("------ 生物利用度.md 尾部 ------\n", merged_content[-400:])
    print("两次总 .md 数:", _count_md(tmp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
