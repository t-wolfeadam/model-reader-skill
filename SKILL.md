---
name: model-reader
description: >-
  解析 AnyLogic 模型 _alp/ 目录下的 XML 源码，输出语义化 Markdown，
  剥离 Id/坐标/Label/UI 配置等展示性噪音。用 Python 脚本 xml2md.py 自动提取
  变量、参数、集合、函数（含 Java 函数体）、嵌入对象（模块）、流程链、状态机、
  事件、网络节点/路径。Use when the user asks to read, understand, explore, or
  summarize an AnyLogic model's content, when they want to know what variables,
  functions, statecharts, or embedded objects a model contains, or when they
  mention "read the model", "模型内容", "XML 太乱", "看不懂 _alp", or similar.
  Scope: any AnyLogic model directory inside this repo (experiment1/2/3, the
  UGV-WebSocket-FollowPath project, etc).
---

# AnyLogic 模型语义读取器

## 速查

| 操作 | 命令 |
|---|---|
| 模型结构总览 | `python xml2md.py "<模型目录>" --toc` |
| 单个 agent 全量 | `python xml2md.py "<模型目录>" --agent Main` |
| 单 agent 单部分 | `python xml2md.py "<模型目录>" --agent UGV --section statechart` |
| 生成持久参考文档 | `python xml2md.py "<模型目录>" --output "<输出.md>"` |

脚本位置：`.claude/skills/model-reader/xml2md.py`（Python 标准库，**零依赖，无需 venv**）

## 为什么要用它（核心价值）

AnyLogic 模型源码 `_alp/` 是 XML，**90% 是噪音**：

```
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

`Level.level.xml` 可高达 **15873 行**。直接用 Read 读 XML 是灾难。
本 skill 用脚本剥离噪音，只输出语义核心。

## 输出结构（每个 agent）

| 部分 | 内容 |
|---|---|
| `class` | 名称、类型（ENTITY/TRANSPORTER）、Import、启动代码 |
| `variables` | 变量表（名称\|类型\|初始值\|说明） |
| `params` | 参数表（名称\|类型\|默认值\|说明） |
| `collections` | 集合表（名称\|集合类型\|初始化） |
| `functions` | 函数签名 + **完整 Java 函数体**（经 `/*ALCODESTART::id*/` 关联） |
| `embedded` | 嵌入对象（名称\|类型\|有值的参数\|代码回调） |
| `connectors` | 流程链（数据流方向 out → in） |
| `statechart` | 状态机（状态表 + 转移表 + 初始指向，ID 引用已解析为名称） |
| `events` | 事件（触发类型\|周期\|代码） |
| `network` | 网络节点 + 路径表（SourceId/TargetId 已解析为节点名） |

## 决策树（每次查询时参考）

```
用户想了解某 AnyLogic 模型 →
  ┌──────────────────────────────┴──────────────────────────────┐
  ↓ 不知道有哪些内容                                            ↓ 想看某个 agent 细节
  python xml2md.py <模型目录> --toc            python xml2md.py <模型目录> --agent <名称>
  （列出所有 agent + 文件清单 + 大小）               ↓
                                                  想只看某部分？
  ↓ 想一次性生成参考文档                           python xml2md.py <模型目录>
  python xml2md.py <模型目录> --output <path>       --agent <名称> --section <section>
  （生成/更新 CODE-REFERENCE.md）
```

## 命令详解

### 1. 模型结构总览

```bash
python xml2md.py "<模型目录>" --toc
```

输出所有 agent 名称、类型、目录下的文件清单及大小。**先跑这个**了解模型全貌。

### 2. 查看单个 agent

```bash
# 全量（所有部分依次输出）
python xml2md.py "<模型目录>" --agent Main

# 只看状态机（省 token）
python xml2md.py "<模型目录>" --agent UGV --section statechart

# 只看变量
python xml2md.py "<模型目录>" --agent Main --section variables

# 只看嵌入对象（模块配置）
python xml2md.py "<模型目录>" --agent Main --section embedded
```

`--agent` 接受 agent 名称或目录名（不区分大小写）。

### 3. 生成持久参考文档

```bash
python xml2md.py "<模型目录>" --output "<模型目录>/CODE-REFERENCE.md"
```

生成整个模型的语义参考文档，可入库长期引用。**模型更新后重新生成**即可，
永远与源码一致。

## 常见问题定位技巧

| 想找什么 | 命令 |
|---|---|
| "Main 有哪些变量？" | `--agent Main --section variables` |
| "UGV 状态机怎么跳转？" | `--agent UGV --section statechart` |
| "某个 fleet 配置了什么？" | `--agent Main --section embedded`（找 TransporterFleet） |
| "wafer 在哪些块之间流转？" | `--agent Main --section connectors` |
| "某个函数逻辑？" | `--agent Main --section functions`（含完整代码） |
| "有哪些路径/节点？" | `--agent Main --section network` |

## Token 优化规则

| 场景 | 策略 |
|---|---|
| 了解模型全貌 | `--toc`（一次，几十行） |
| 查特定内容 | 直接 `--agent X --section Y`，不读全量 |
| 全 agent 全量 | 输出可能 2000+ 行，仅在需要完整参考时用 |
| 函数代码很长 | `--section functions` 已有完整代码，无需再读 .java |
| 网络节点太多 | `--section network` 会列出全部节点（experiment3 Main 有 200+），按需用 |

## 已知限制

1. **嵌入对象只输出「有值的参数」** — 无 Value 的参数是默认未设置（AnyLogic 会自动填默认值），不输出。如需查看某参数的默认值含义，查 `anylogic-help` skill。
2. **`--output` 是覆盖写** — 会覆盖同路径已有文件，生成前确认路径。
3. **网络节点坐标是画布像素** — 语义参考用；如需换算现实坐标，参考模型内坐标系统函数（如 `simToReal`）。
4. **函数体只提取 `/*ALCODESTART::<数字id>*/` 块** — 若源码改用了纯命名标记（无数字 id），该函数 body 为空，需要直接读 .java。当前 experiment1/2/3 均使用数字 id，不受影响。
5. **状态机转移的 Condition 显示在「条件/消息」列** — message 触发时显示 filter+消息；timeout 触发时显示超时值；可能不同时全显。
6. **`--toc` 不解析 XML 内容** — 只列文件大小，不解析（快速）。要语义信息需 `--agent`。
7. **流程链方向按数据流（out → in）渲染** — 与 Connector 物理连接方向相反。若看到 `A.in → B.out`，说明 B 块连到了 A 块下游。
