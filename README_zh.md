# model-reader-skill

> **Claude Code skill** — 让 LLM 语义化读取 AnyLogic 模型。解析 AnyLogic 项目 `_alp/` 目录下的 XML 源码，输出高信息密度的 Markdown，剥离 Id/坐标/Label/UI 配置等展示性噪音。**零依赖**（仅 Python 标准库）。

## 功能

AnyLogic 模型源码 `_alp/` 是 XML，**约 90% 是噪音**：

```xml
<Variable Class="PlainVariable">
    <Id>1781674452943</Id>          ← 时间戳 ID，无意义
    <Name><![CDATA[baseX]]></Name>  ← 语义 ✓
    <X>-810</X><Y>150</Y>           ← 画布坐标
    <Label><X>10</X><Y>0</Y></Label> ← 标签位置
    <PublicFlag>false</PublicFlag>  ← 展示标志
    ...
    <Type><![CDATA[double]]></Type> ← 语义 ✓
    <InitialValue><Code>area.getX()</Code></InitialValue> ← 语义 ✓
```

单个 `Level.level.xml` 可达 **15873 行**。直接把原始 XML 喂给 LLM 是灾难。本 skill 剥离噪音，只输出语义核心。

### 提取内容（按 agent）

| 部分 | 内容 |
|------|------|
| `class` | 名称、类型（ENTITY/TRANSPORTER）、Import、附加类代码、启动代码、销毁代码 |
| `variables` | 变量表（名称 \| 类型 \| 初始值 \| 说明） |
| `params` | 参数表（名称 \| 类型 \| 默认值 \| 说明） |
| `collections` | 集合表（名称 \| 集合类型 \| 初始化） |
| `functions` | 函数签名 + **完整 Java 函数体**（经 `/*ALCODESTART::id*/` 关联） |
| `embedded` | 嵌入对象（名称 \| 类型 \| 有值的参数 \| 代码回调） |
| `connectors` | 流程链（数据流方向 out → in） |
| `statechart` | 状态机（状态表 + 转移表 + 初始指向，ID 引用已解析为名称） |
| `events` | 事件（触发类型 \| 周期 \| 代码） |
| `network` | 网络节点 + 路径表（SourceId/TargetId 已解析为节点名） |

---

## 目标用户

**AnyLogic 开发者及其 LLM 助手**，适用场景：

- 想快速了解模型里有什么——变量、函数、状态机、嵌入模块，而不想翻原始 XML
- 项目里有多个实验模型（`experiment1/2/3`、`UGV-WebSocket-FollowPath` 等）需要逐个理解
- 想要一份可长期引用、随时再生成的模型语义参考文档（用 `--output`）

---

## 前置条件

- Python 3.8+（仅标准库，**无需 pip 安装任何依赖**）

---

## 安装与使用

### 1. 克隆并复制到项目

```bash
git clone https://github.com/Jamlet-T/model-reader-skill.git
```

复制（或符号链接）到你的 Claude Code 项目 skills 目录：

```bash
# 在目标 Claude Code 项目中
mkdir -p .claude/skills/
cp -r path/to/model-reader-skill .claude/skills/model-reader/
```

### 2. 测试

```bash
# 模型结构总览（agent 列表 + 文件清单 + 数据库摘要，如果模型有本地数据库）
python .claude/skills/model-reader/xml2md.py "<模型目录>" --toc

# 单个 agent 全量
python .claude/skills/model-reader/xml2md.py "<模型目录>" --agent Main

# 单 agent 单部分（省 token）
python .claude/skills/model-reader/xml2md.py "<模型目录>" --agent UGV --section statechart

# 生成持久参考文档
python .claude/skills/model-reader/xml2md.py "<模型目录>" --output "<模型目录>/CODE-REFERENCE.md"
```

---

## 命令速查

| 操作 | 命令 |
|------|------|
| 模型结构总览（若有数据库，包含表/列/行数） | `python xml2md.py "<模型目录>" --toc` |
| 单个 agent 全量 | `python xml2md.py "<模型目录>" --agent Main` |
| 单 agent 单部分 | `python xml2md.py "<模型目录>" --agent UGV --section statechart` |
| 数据库表的行（默认 5 行；`:N`、`:A-B`、`:all`） | `python xml2md.py "<模型目录>" --database my_table:10-20` || 生成持久参考文档 | `python xml2md.py "<模型目录>" --output "<输出.md>"` |

`--agent` 接受 agent 名称或目录名（不区分大小写）。`--section` 支持：`class|variables|params|collections|functions|embedded|connectors|statechart|events|network`。

---

## 工作原理（架构）

```
用户向 Claude 询问模型内容
    ↓
Claude 调用本 skill（SKILL.md）
    ↓
xml2md.py 解析模型 _alp/ 目录下的 XML
    ↓
┌─ 解析路径 ─────────────────────────────────────┐
│  按 agent 解析 XML（AgentName.xml）+ 模型 XML    │
│    ↓                                           │
│  递归剥离展示性噪音（NOISE_TAGS）                │
│    ↓                                           │
│  分节提取：class/variables/params/collections/  │
│  functions/embedded/connectors/statechart/      │
│  events/network                                 │
│    ↓                                           │
│  ID 引用解析为名称（状态、节点）                 │
│  函数体经 /*ALCODESTART::<id>*/ 从 .java 关联   │
└───────────────────────────────────────────────┘
    ↓
干净的语义化 Markdown（stdout 或 --output 文件）
    ↓
Claude 回答用户的问题
```

### 关键设计决策

- **零依赖**：只用 `xml.etree.ElementTree`——无需 venv、无需 `pip install`，任何有 Python 的环境都能跑
- **噪音优先**：在提取之前先递归剥离纯展示标签（Id、坐标、颜色、Label、滑块配置）
- **ID 解析**：状态机/网络的 `SourceId`/`TargetId` 引用解析为可读名称
- **函数体**：经 `/*ALCODESTART::<id>*/` 标记从 `.java` 关联——拿到的是真实代码，不是桩
- **持久文档**：`--output` 生成可提交的 `CODE-REFERENCE.md`，模型更新后重新生成即可

---

## 仓库结构

```
model-reader-skill/
├── SKILL.md            # Claude Code skill 定义（Claude 如何使用）
├── xml2md.py           # 核心脚本 — XML → 语义化 Markdown 解析器（零依赖）
├── README.md           # 英文文档
├── README_zh.md        # 本文档
├── LICENSE             # MIT
└── .gitignore
```

---

## FAQ

**Q: 需要安装依赖吗？**
A: 不需要。`xml2md.py` 只用了 Python 标准库（`xml.etree`），Python 3.8+ 即可。

**Q: 支持哪些 AnyLogic 项目？**
A: 任何带标准 `_alp/` 目录的 AnyLogic 模型。已在 `experiment1/2/3` 和 `UGV-WebSocket-FollowPath` 项目上验证。

**Q: 某些函数函数体为空，为什么？**
A: 函数体只从 `/*ALCODESTART::<数字id>*/` 块提取。若源码改用了纯命名标记（无数字 id），函数体为空，需直接读 `.java`。

**Q: 非 Claude 的 LLM 能用吗？**
A: 能。`xml2md.py` 是独立 CLI 工具，任何能调用 shell 命令的 LLM 都能用，只需为你的 LLM 平台写一份等价的 `SKILL.md`。

**Q: 和 anylogic-help-skill 有什么区别？**
A: [anylogic-help-skill](https://github.com/Jamlet-T/anylogic-help-skill) 读的是 AnyLogic **离线帮助文档**（HTML JavaDoc）；本 skill 读的是**你自己模型的源码**（`_alp/` XML）。两者互补：一个回答"AnyLogic 能做什么"，一个回答"*这个模型*做了什么"。

---

## License

MIT © 2026 Jamlet-T
