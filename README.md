# model-reader-skill

> **Claude Code skill** — gives LLMs the ability to read and understand AnyLogic models semantically. Parses the XML source under an AnyLogic project's `_alp/` directory and outputs high-information-density Markdown, stripping display noise (Ids, coordinates, labels, UI config). **Zero dependencies** — pure Python standard library.

## What it does

AnyLogic model source (`_alp/`) is XML, and **~90% of it is noise**:

```xml
<Variable Class="PlainVariable">
    <Id>1781674452943</Id>          ← timestamp ID, meaningless
    <Name><![CDATA[baseX]]></Name>  ← semantic ✓
    <X>-810</X><Y>150</Y>           ← canvas coordinates
    <Label><X>10</X><Y>0</Y></Label> ← label position
    <PublicFlag>false</PublicFlag>  ← presentation flag
    ...
    <Type><![CDATA[double]]></Type> ← semantic ✓
    <InitialValue><Code>area.getX()</Code></InitialValue> ← semantic ✓
```

A single `Level.level.xml` can reach **15,873 lines**. Feeding raw XML to an LLM is a disaster. This skill strips the noise and outputs only the semantic core.

### Extracted content (per agent)

| Section | Content |
|---------|---------|
| `agent-class` | Name, type (ENTITY/TRANSPORTER), imports, additional class code, startup code, destroy code |
| `variables` | Variable table (name \| type \| initial value \| description) |
| `params` | Parameter table (name \| type \| default \| description) |
| `collections` | Collection table (name \| collection type \| initialization) |
| `functions` | Function signatures + **full Java bodies** (via `/*ALCODESTART::id*/` markers) |
| `embedded` | Embedded objects (name \| type \| parameters with values \| callbacks) |
| `connectors` | Flow chains (data-flow direction out → in) |
| `statechart` | State machines (states + transitions + initial state, ID refs resolved to names) |
| `events` | Events (trigger type \| period \| code) |
| `dynamic-events` | Arguments and code body of this schedulable function type |
| `steps` | Details about synchronous event steps (when enabled) |
| `network` | Network nodes + paths (SourceId/TargetId resolved to node names) |

---

## Who is this for

**AnyLogic developers and their LLM assistants** who:

- Need to quickly understand what a model contains — variables, functions, statecharts, embedded modules — without wading through raw XML
- Read models in a project with many experiments (`experiment1/2/3`, `UGV-WebSocket-FollowPath`, ...)
- Want a persistent, regenerable semantic reference doc for a model (via `--output`)

---

## Prerequisites

- Python 3.8+ (standard library only — **no pip install needed**)

---

## Quick start

### 1. Clone & copy into your project

```bash
git clone https://github.com/Jamlet-T/model-reader-skill.git
```

Copy (or symlink) the repo into your project's Claude Code skills directory:

```bash
# In your target Claude Code project
mkdir -p .claude/skills/
cp -r path/to/model-reader-skill .claude/skills/model-reader/
```

### 2. Test

```bash
# Model structure overview (agent list + file inventory + database summary, if the model has one)
python .claude/skills/model-reader/xml2md.py "<model-dir>" --toc

# Full dump of one agent
python .claude/skills/model-reader/xml2md.py "<model-dir>" --agent Main

# One section of one agent (token-efficient)
python .claude/skills/model-reader/xml2md.py "<model-dir>" --agent UGV --section statechart

# Generate a persistent reference doc
python .claude/skills/model-reader/xml2md.py "<model-dir>" --output "<model-dir>/CODE-REFERENCE.md"
```

---

## Command reference

| Operation | Command |
|-----------|---------|
| Model structure overview (includes database tables/columns/row counts if present) | `python xml2md.py "<model-dir>" --toc` |
| Single agent, full dump | `python xml2md.py "<model-dir>" --agent Main` |
| Single agent, one section | `python xml2md.py "<model-dir>" --agent UGV --section statechart` |
| Database table rows (default 5; `:N`, `:A-B`, `:all`) | `python xml2md.py "<model-dir>" --database my_table:10-20` || Persistent reference doc | `python xml2md.py "<model-dir>" --output "<output.md>"` |

`--agent` accepts an agent name or directory name (case-insensitive). `--section` accepts: `class|variables|params|collections|functions|embedded|connectors|statechart|events|network`.

---

## How it works (architecture)

```
User asks Claude about an AnyLogic model
    ↓
Claude invokes the skill (SKILL.md)
    ↓
xml2md.py parses the model's _alp/ XML files
    ↓
┌─ Parsing path ───────────────────────────────────┐
│  Per-agent XML (AgentName.xml) + model XML        │
│    ↓                                              │
│  Recursive noise stripping (NOISE_TAGS)           │
│    ↓                                              │
│  Section extraction: class/variables/params/      │
│  collections/functions/embedded/connectors/       │
│  statechart/events/network                        │
│    ↓                                              │
│  ID references resolved to names (states, nodes)  │
│  Function bodies joined from .java via            │
│  /*ALCODESTART::<id>*/ markers                    │
└──────────────────────────────────────────────────┘
    ↓
Clean semantic Markdown (stdout or --output file)
    ↓
Claude answers the user's question
```

### Key design decisions

- **Zero dependencies**: `xml.etree.ElementTree` only — no venv, no `pip install`, works anywhere Python runs
- **Noise-first**: recursive stripping of pure-presentation tags (Ids, coordinates, colors, labels, slider config) before any extraction
- **ID resolution**: statechart/network `SourceId`/`TargetId` references resolved to human-readable names
- **Function bodies**: joined from `.java` files via `/*ALCODESTART::<id>*/` markers — you get the real code, not a stub
- **Persistent docs**: `--output` generates a `CODE-REFERENCE.md` you can commit and re-generate after model updates

---

## Repository structure

```
model-reader-skill/
├── SKILL.md            # Claude Code skill definition (how Claude uses this)
├── xml2md.py           # Core script — XML → semantic Markdown parser (zero-dependency)
├── README.md           # This file
├── README_zh.md        # Chinese documentation
├── LICENSE             # MIT
└── .gitignore
```

---

## FAQ

**Q: Do I need to install anything?**
A: No. `xml2md.py` uses only the Python standard library (`xml.etree`). Python 3.8+ is all you need.

**Q: Which AnyLogic projects does this work on?**
A: Any AnyLogic model with a standard `_alp/` directory. Tested on the `experiment1/2/3` and `UGV-WebSocket-FollowPath` projects.

**Q: Function bodies are empty for some functions. Why?**
A: Bodies are extracted from `/*ALCODESTART::<numeric-id>*/` blocks. If the source switched to pure named markers (no numeric id), the body is empty and you need to read the `.java` directly.

**Q: Can this work with non-Claude LLMs?**
A: Yes — `xml2md.py` is a standalone CLI tool. Any LLM that can invoke shell commands can use it. You'd just need to write the equivalent of `SKILL.md` for your LLM platform.

**Q: What's the difference between this and anylogic-help-skill?**
A: [anylogic-help-skill](https://github.com/Jamlet-T/anylogic-help-skill) reads AnyLogic's **offline help documentation** (the HTML JavaDoc). This skill reads **your model's own source code** (`_alp/` XML). They complement each other: one answers "what can AnyLogic do", the other answers "what does *this model* do".

---

## License

MIT © 2026 Jamlet-T
