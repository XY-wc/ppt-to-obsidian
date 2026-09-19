# PPT → Obsidian 智能笔记

把课程 **PPT / PDF** 自动转成**结构化讲义式笔记**，一键放进 Obsidian。

> **本地优先 (local-first)** — 所有数据留在你自己的电脑，不依赖中心化服务器。
> 大模型用**你自己绑定的 API**（DeepSeek / OpenAI / Kimi / 智谱 / 通义 / Ollama 等任意 OpenAI 兼容接口），未绑定也能用**本地规则模式**离线出基础笔记。

---

## ✨ 特点

| 能力 | 说明 |
|------|------|
| **讲义式输出（默认）** | 一份课件 → 一个 `index.md` 索引 + 每节一个 `.md`（按课件原生小节编号，如 `1.3 热力学概论.md`），节末自带上下节导航，公式 LaTeX 化 |
| **关键页截图** | 例题/原题页自动从 PDF 截出插图，或按模型标注的 `【课件截图：第N页】` 占位落图 |
| **专业知识框架 (grounding)** | 内置「药学 / 法学 / 中医学 / 人工智能」规范术语库，prompt 强制按学科规范输出 |
| **学科符号还原** | `AUC` / `t1/2` / `Dx` / `§` 等缩写自动还原为中文含义，模型不再瞎猜 |
| **多模型兼容** | 一个 LLMClient 通吃所有 OpenAI 兼容厂商 + 本地 Ollama；可关闭深度思考 |
| **Vault 隔离** | 一门课一个文件夹，多份课件长期积累，跨课程不混杂 |
| **离线兜底** | 未绑定模型时，用学科概念库做确定性提取，开箱即用 |
| **卡片模式（可选）** | GUI 下拉切换为旧版「原子概念卡 + MOC」输出，适合想建概念图谱的场景 |
| **关系图分层配色** | 生成后自动写好 Obsidian 关系图配色：目录 / 正文 / 概念 / 附录 / 附件分层着色，并保留你已有的分组 |

---

## 🚀 运行方式

### 方式 A：直接运行 exe（推荐，免装 Python）

从 [Releases](../../releases) 下载 `PPT2Obsidian_win64.zip`，**整文件夹解压**（不要只拿 .exe），双击 `PPT2Obsidian.exe`。

> 首次运行会在你的用户目录生成配置 `~/.ppt2obsidian/`（账号 / 模型 / 进度），属于正常行为。

### 方式 B：源码运行（开发 / 调试）

```bash
git clone <本仓库>
cd ppt2obsidian
python -m pip install -r requirements.txt
python run.py            # 也可双击 start.bat
```

依赖：Python 3.9+，需要 `tkinter`（Windows 官方安装包默认勾选）。

### 打包成 exe

```bash
python build_exe.py      # 产物 dist/PPT2Obsidian/，同时尝试创建桌面快捷方式
```

`.spec` 文件不入库，由 `build_exe.py` 用相对路径自动生成。

---

## 📖 使用流程

1. **登录 / 注册**：首次启动「注册新账号」，密码以加盐 SHA-256 哈希存放在 `~/.ppt2obsidian/`（本地单机，无云）。
2. **设置 → 管理模型**：添加你的大模型（下拉选厂商自动填 `base_url`，粘贴 API Key 与模型名）→「测试连接」验证。
   > 不绑定也可继续，工具会用本地规则模式离线跑（准确度较低）。
3. **主界面**：
   - ① **选择专业** — 决定知识框架（药学 / 法学 / 中医学 / 人工智能…）
   - ② **拖入 PPT / PDF**（`.pptx / .ppt / .pdf`，可拖拽或点选），并**指定一个 Obsidian Vault 根目录**
   - ③ **选择笔记形态**（默认 **讲义式**；可选 **概念卡片**）
   - ④ 点「生成 Obsidian 笔记」
4. 完成后点「在 Obsidian 中打开笔记」预览 `index.md` 与各节文件。

> **常见坑：Base URL 填成了网页 / 文档地址 → 连接失败**
> Base URL 必须填**厂商 API 接口地址**（OpenAI 兼容端点，通常以 `/v1` 结尾），不是官网首页或文档页。
> 选「快捷厂商」下拉即可自动填好。

---

## 📁 目录结构

```
ppt2obsidian/
├─ run.py                   # 启动入口
├─ start.bat                # Windows 双击启动
├─ build_exe.py             # PyInstaller 打包 + 桌面快捷方式
├─ create_shortcut.ps1      # 备用: PowerShell 创建桌面快捷方式
├─ create_desktop_lnk.py    # 备用: Python 创建桌面快捷方式
├─ requirements.txt
├─ assets/                  # 图标资源
├─ professions/             # ★ 专业知识框架(每专业一个 JSON)
│   ├─ pharmacy.json        #   药学
│   ├─ law.json             #   法学
│   ├─ tcm.json             #   中医学
│   └─ ai.json              #   人工智能
├─ scripts/convert_kb.py    # 开发期: 把 markdown 知识库批量转 professions/*.json
├─ src/
│   ├─ core/                # 基础层
│   │   ├─ knowledge_base.py    # 知识库加载器
│   │   ├─ ppt_parser.py        # PPT 逐页提取
│   │   ├─ pdf_parser.py        # PDF 逐页提取(依赖 pymupdf)
│   │   ├─ document_loader.py   # 统一入口(按扩展名自动路由)
│   │   └─ app_config.py        # 本地账号 + 模型凭据
│   ├─ llm/client.py        # OpenAI 兼容多厂商客户端
│   ├─ obsidian/
│   │   ├─ lecture.py           # ★ 讲义式生成(默认, index+分节+LaTeX+截图)
│   │   ├─ graph_config.py      #   关系图分层配色(写 vault 的 .obsidian/graph.json)
│   │   ├─ staged.py            #   旧版概念卡流水线(卡片模式)
│   │   ├─ summarizer.py        #   专业驱动总结(底层能力)
│   │   ├─ note_template.py     #   Obsidian 笔记渲染(frontmatter/双链/标签)
│   │   ├─ kb_calibration.py    #   知识库命中度校准
│   │   ├─ validator.py         #   质量评估(覆盖/证据/链接)
│   │   ├─ vault_aware.py       #   Vault 内已有笔记的索引(去重/回链)
│   │   └─ refine.py            #   对话式修订
│   ├─ pipeline.py          # 编排: 选专业→解析→总结→渲染→落盘
│   └─ gui/
│       ├─ app_v3.py        # ★ 当前默认 GUI(单页工作流)
│       ├─ app_v2.py        # 历史版本, 保留供回退
│       └─ app.py           # 早期版本, 保留供回退
└─ tests/                   # pytest 用例 + GUI 探针脚本
```

---

## 🔧 新增专业知识（无需改代码）

在 `professions/` 目录新增一个 JSON 即可，**无需改任何代码**，重启后「① 选择专业」下拉会自动出现：

```json
{
  "id": "economics",
  "name": "经济学",
  "tags": ["微观", "宏观"],
  "note_template": "概念-模型假设-推导-政策含义-现实案例",
  "core_concepts": [
    {"name": "需求弹性", "definition": "需求量对价格变动的敏感程度"}
  ],
  "symbol_mapping": [
    {"symbol": "Qd", "meaning": "需求量"},
    {"symbol": "Ed", "meaning": "需求价格弹性"}
  ],
  "common_misconceptions": [
    {"misconception": "供给减少价格一定下跌", "clarification": "供给减少在其他条件不变下使价格上升"}
  ]
}
```

### Schema 说明（字段均可选，宽松兼容）

- `core_concepts[].name` — 规范术语名
- `core_concepts[].definition` — 一句话定义（离线模式会用作概念卡内容）
- `symbol_mapping` — 缩写 / 符号 → 中文含义，用于「符号还原」
- `common_misconceptions` — 学科常见易错点，用于生成「易错点卡」
- `note_template` — 学科笔记惯用结构，注入 prompt 引导排版

---

## 🧠 技术设计要点

1. **讲义式 (lecture) 与卡片式 (cards) 双形态** — `pipeline.run_conversion(..., mode=...)` 切换；默认讲义式不碎、不靠堆砌 MOC。
2. **账号 = 本地档案** — 加盐 SHA-256 哈希存 `~/.ppt2obsidian/`，不依赖中心服务器，方便迁移 / 导出。
3. **模型 = OpenAI 兼容端点** — 一个 client 通吃所有厂商与本地 Ollama；可指定自定义 `auth_header`（如 `api-key` 而非标准 `Authorization: Bearer`）。
4. **知识库注入 + 结构化输出** — prompt 让模型输出 JSON（按课件原生小节切分 / 大纲 / 内嵌页码），本地渲染器组装 Obsidian 笔记，截图按例题页自动兜底。
5. **容错 JSON 解析** — 容忍围栏、夹带文字、多余逗号、字符串内裸换行。
6. **本地确定性检测** — 不论是否用模型，先用学科词库做「符号还原 + 概念命中」作为 prompt 线索，并在无模型时兜底生成。
7. **Vault 感知** — `vault_aware` 模块在写入前扫描已有笔记，避免重复命名、识别可回链的概念节点。
8. **关系图分层配色** — 生成后自动把 `层级/*` 标签对应的配色合并进 vault 的 `.obsidian/graph.json`：
   目录(金) → 正文(蓝) → 概念(绿) → 附录(灰) → 附件(浅灰)。采用**合并**而非覆盖，你的自定义分组与新分组互不冲突。

---

## ⚠️ 已知边界 / 后续可优化

- **纯图片型 PPT / PDF**（扫描 / 截图）需要 OCR 或视觉模型：当前会标记 `[图片占位…]`。可后续在解析层挂 OCR 或接多模态模型。
- **PPTX 截图**依赖 LibreOffice (`soffice`) 转 PDF，未安装时跳过截图；PDF 截图由 `pymupdf` 直接渲染。
- **模型输出的小节切分** 依赖模型质量；如需更精确，可按页切块分批送模型。
- **账号体系是本地单机**；若要多人 / 云同步，需评估服务端与更强加密。
- 卡片模式 (`mode="cards"`) 已成熟但功能集比讲义模式旧，新建笔记建议优先用默认讲义式。

---

## 📜 许可证

[MIT](./LICENSE) — 自由使用、修改、商用，只需保留版权声明。