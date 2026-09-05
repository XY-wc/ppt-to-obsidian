#!/usr/bin/env python3
"""把 knowledge_base.json(实为 md，内嵌4个专业JSON) 转成 professions/ 下规范的单文件JSON。

每个专业 JSON 结构:
{
  "id": "pharmacy",           # 机器id
  "name": "药学",              # 显示名
  "desc": "...",              # 简介(可在界面展示)
  "tags": ["...","..."],
  "note_template": "...",
  "core_concepts": [...],
  "symbol_mapping": [...],
  "common_misconceptions": [...]
}
"""
import json, re, os, sys

SRC = os.path.join(os.path.dirname(__file__), "..", "..", "knowledge_base.json")
DST = os.path.join(os.path.dirname(__file__), "..", "professions")

PROF_META = {
    "药学":   {"id": "pharmacy",  "tags": ["药理", "药代", "药剂", "临床药学"]},
    "法学":   {"id": "law",       "tags": ["法理", "刑法", "民法", "行政法"]},
    "中医学": {"id": "tcm",       "tags": ["阴阳五行", "辨证论治", "方剂", "针灸"]},
    "人工智能": {"id": "ai",      "tags": ["机器学习", "深度学习", "NLP", "CV"]},
}

def parse_md(md):
    """按 '### 专业名' 分段, 提取其后第一个 ```json ``` 代码块内容。"""
    # 定位所有 ### 标题行
    lines = md.splitlines()
    blocks = []  # (name, [json_lines])
    cur_name = None
    in_fence = False
    fence_buf = []
    i = 0
    while i < len(lines):
        ln = lines[i]
        m = re.match(r'^###\s+(.+)$', ln.strip())
        if m:
            cur_name = m.group(1).strip()
            in_fence = False
            fence_buf = []
            i += 1
            continue
        if ln.strip().startswith("```"):
            if in_fence:
                blocks.append((cur_name, fence_buf))
                fence_buf = []
                in_fence = False
            else:
                in_fence = True
            i += 1
            continue
        if in_fence:
            fence_buf.append(ln)
        i += 1
    # 若结尾未闭合(不应发生)
    if in_fence and fence_buf:
        blocks.append((cur_name, fence_buf))
    return blocks

def main():
    with open(SRC, "r", encoding="utf-8") as f:
        md = f.read()
    blocks = parse_md(md)
    os.makedirs(DST, exist_ok=True)
    report = []
    for name, buf in blocks:
        if name is None:
            continue
        try:
            obj = json.loads("\n".join(buf))
        except Exception as e:
            report.append(f"[解析失败] {name}: {e}")
            continue
        meta = PROF_META.get(name, {"id": name, "tags": []})
        out = {
            "id": meta["id"],
            "name": name,
            "desc": f"{name} 专业知识框架",
            "tags": meta["tags"],
            "note_template": obj.get("note_template", ""),
            "core_concepts": obj.get("core_concepts", []),
            "symbol_mapping": obj.get("symbol_mapping", []),
            "common_misconceptions": obj.get("common_misconceptions", []),
        }
        fn = os.path.join(DST, f"{meta['id']}.json")
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        report.append(f"[OK] {name} -> {os.path.basename(fn)} 概念{len(out['core_concepts'])} 符号{len(out['symbol_mapping'])} 误区{len(out['common_misconceptions'])}")
    print("\n".join(report))

if __name__ == "__main__":
    main()
