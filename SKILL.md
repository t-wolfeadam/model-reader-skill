---
name: model-reader
description: >-
  Parses the XML source code under an AnyLogic model's `_alp/` directory into
  semantic Markdown, stripping out presentational noise like Id/coordinates/
  Label/UI config. Uses the Python script xml2md.py to automatically extract
  agent class definitions, shared plain Java classes/records/enums,
  model-level option lists (AnyLogic's enum-like lists), variables, parameters, collections, functions (including Java function
  bodies), embedded objects (modules), flowchart chains, statecharts,
  synchronous step-event details, dynamic (schedulable-function) events,
  events, and network nodes/paths. Use when the user asks to read,
  understand, explore, or summarize an AnyLogic model's content, when they
  want to know what variables, functions, statecharts, or embedded objects a
  model contains, or when they mention "read the model", "what's in the
  model", "the XML is a mess", "I can't make sense of _alp", or similar.
  Scope: any AnyLogic model directory inside this repo (experiment1/2/3, the
  UGV-WebSocket-FollowPath project, etc).
---

# AnyLogic Model Semantic Reader

## Quick reference

| Action | Command |
|---|---|
| Model structure overview | `python xml2md.py "<model_dir>" --toc` |
| Full dump of one agent | `python xml2md.py "<model_dir>" --agent Main` |
| One section of one agent | `python xml2md.py "<model_dir>" --agent UGV --section statechart` |
| A shared plain Java class/record/enum | `python xml2md.py "<model_dir>" --class SomeUtilClass` |
| A model-level option list (enum) and its items | `python xml2md.py "<model_dir>" --optionlist SizeChoice` |
| Generate a persistent reference doc | `python xml2md.py "<model_dir>" --output "<output.md>"` |

Script location: `.claude/skills/model-reader/xml2md.py` (Python standard library, **zero dependencies, no venv needed**)

## Why use it (the core value)

An AnyLogic model's `_alp/` source is XML, and **90% of it is noise**:

```
<Variable Class="PlainVariable">
    <Id>1781674452943</Id>          ← timestamp-based ID, meaningless
    <Name><![CDATA[baseX]]></Name>  ← semantic ✓
    <X>-810</X><Y>150</Y>           ← canvas coordinates
    <Label><X>10</X><Y>0</Y></Label> ← label position
    <PublicFlag>false</PublicFlag>  ← display flag
    ...
    <Type><![CDATA[double]]></Type> ← semantic ✓
    <InitialValue><Code>area.getX()</Code></InitialValue> ← semantic ✓
```

A `Level.level.xml` can run as long as **15,873 lines**. Reading the raw XML
directly is a disaster. This skill uses the script to strip out the noise
and output only the semantic core.

## Output structure

### Per agent

| Section | Contents |
|---|---|
| `agent-class` | Name, type (ENTITY/TRANSPORTER), imports, startup code |
| `variables` | Variable table (name\|type\|initial value\|description) |
| `params` | Parameter table (name\|type\|default value\|description) |
| `collections` | Collection table (name\|collection type\|initializer) |
| `functions` | Function signatures + **full Java function bodies** (linked via `/*ALCODESTART::id*/`) |
| `embedded` | Embedded objects (name\|type\|params that have a value\|code callbacks) |
| `connectors` | Flowchart chain (data-flow direction out → in) |
| `statechart` | Statechart (state table + transition table + initial pointers; Id references resolved to names) |
| `steps` | Synchronous step-event details, where applicable (e.g. the on-enter / on-exit / timeout code carried by a flowchart step block) |
| `dynamic-events` | Objects that act as a schedulable function: their arguments and code body |
| `events` | Events (trigger type\|period\|code) |
| `network` | Network nodes + path table (SourceId/TargetId resolved to node names) |

### Top-level (not tied to a single agent)

| Flag | Contents |
|---|---|
| `--class <name>` | A plain Java class/record/enum shared globally across the model (not an AnyLogic agent) — its fields/members and full source, noise stripped |
| `--optionlist <name>` | A model-level option list and the items it contains. **Option lists are effectively enums**: a named, fixed set of options (e.g. `SizeChoice` = `SMALL`, `MEDIUM`, `BIG`) that can be used as a type for parameters/variables and referenced in code as `SizeChoice.SMALL`. `--toc` also lists every option list with its items |

## Decision tree (consult this on every query)

```
User wants to understand an AnyLogic model →
  ┌─────────────────────────────────────────┴─────────────────────────────────────────┐
  ↓ Doesn't know what's in it                                                          ↓ Wants details on a specific agent
  python xml2md.py <model_dir> --toc                          python xml2md.py <model_dir> --agent <name>
  (lists every agent + file listing + sizes)                                ↓
                                                                    Only want one section?
  ↓ Wants a shared, non-agent Java class/record/enum          python xml2md.py <model_dir>
  python xml2md.py <model_dir> --class <name>                   --agent <name> --section <section>

  ↓ Wants to generate a reference doc in one shot
  python xml2md.py <model_dir> --output <path>
  (creates/updates CODE-REFERENCE.md)
```

## Command reference

### 1. Model structure overview

```bash
python xml2md.py "<model_dir>" --toc
```

Outputs the model's option lists (with their items), every shared Java class,
and every agent's name, type, and the file listing (with sizes) in its
directory. **Run this first** to get the lay of the land.

### 2. Look at a single agent

```bash
# Full dump (every section, in order)
python xml2md.py "<model_dir>" --agent Main

# Just the variables
python xml2md.py "<model_dir>" --agent Main --section variables

# Just the embedded objects (module configuration)
python xml2md.py "<model_dir>" --agent Main --section embedded

# Just the synchronous step-event details
python xml2md.py "<model_dir>" --agent Main --section steps

# Just the schedulable-function (dynamic event) objects
python xml2md.py "<model_dir>" --agent Main --section dynamic-events
```

`--agent` accepts either the agent's name or its directory name (case-insensitive).

### 3. Look at a shared, non-agent Java class

```bash
python xml2md.py "<model_dir>" --class SomeUtilClass
```

Use `--class` for plain Java classes, records, and enums that live outside
any agent and are shared globally across the model (utility classes, DTOs,
enum types), as opposed to `--agent` for AnyLogic agent types.

### 4. Look at a model-level option list (enum)

```bash
python xml2md.py "<model_dir>" --optionlist MajorChoice
```

Option lists are effectively **enums**: a named, fixed set of options that a
parameter or variable can take as its type. They are model-level — they don't
belong to any agent.
When an agent's variable/parameter has a type that isn't a Java built-in or a
shared class, check `--toc` / `--optionlist` for a matching option list. The
name match is case-insensitive.

### 5. Generate a persistent reference document

```bash
python xml2md.py "<model_dir>" --output "<model_dir>/CODE-REFERENCE.md"
```

Produces a full semantic reference document for the entire model, suitable
for committing to the repo for long-term reference. **Regenerate it after
the model changes** and it will always stay in sync with the source.

## Quick lookup patterns

| Looking for | Command |
|---|---|
| "What variables does Main have?" | `--agent Main --section variables` |
| "What is a given population configured with?" | `--agent Main --section embedded` |
| "Which blocks does a wafer flow between?" | `--agent Main --section connectors` |
| "What does a given function do?" | `--agent Main --section functions` (includes the full code) |
| "What runs during a synchronized step?" | `--agent Main --section steps` |
| "What does this schedulable-function object do, and with what arguments?" | `--agent Main --section dynamic-events` |
| "What's in this shared utility class/record/enum?" | `--class <name>` |
| "What values can this option list take?" | `--optionlist <name>` |

## Token-optimization rules

| Scenario | Strategy |
|---|---|
| Getting the lay of the land | `--toc` (once, a few dozen lines) |
| Looking up something specific | Go straight to `--agent X --section Y`, don't read the full dump |
| Full dump of every agent | Can run 2,000+ lines — only use it when you need the complete reference |
| Function code is long | `--section functions` already has the full code; no need to also read the .java file |
| Too many network nodes | `--section network` lists every node (experiment3's Main has 200+) — use only as needed |
| Steps/dynamic-events sections | Small and targeted by design — prefer them over the full agent dump when the question is specifically about step timing or a schedulable function |

## Known limitations

1. **Embedded objects only output "parameters that have a value"** — parameters
   with no Value are unset defaults (AnyLogic fills them in automatically) and
   are not output. To look up what a parameter's default actually means, check
   the `anylogic-help` skill.
2. **`--output` overwrites**  — it overwrites any existing file at the same
   path; confirm the path before generating.
3. **Network node coordinates are canvas pixels** — meant for semantic
   reference; to convert to real-world coordinates, consult the model's own
   coordinate-conversion functions (e.g. `simToReal`).
4. **Function bodies are only extracted from `/*ALCODESTART::<numeric id>*/`
   blocks** — if the source instead uses a purely named marker (no numeric
   id), that function's body will come back empty and you'll need to read the
   .java file directly. experiment1/2/3 all currently use numeric ids, so
   this isn't an issue there.
5. **A statechart transition's Condition is shown in the "Condition/Message"
   column** — on a message trigger it shows filter+message; on a timeout
   trigger it shows the timeout value; both may not be shown at once.
6. **`--toc` does not parse XML content** — it only lists file sizes, not
   parsed content (fast). You need `--agent` for semantic information.
7. **Flowchart-chain direction is rendered as data flow (out → in)** — this is
   the reverse of the Connector's physical connection direction. If you see
   `A.in → B.out`, that means block B is physically connected downstream of
   block A.
8. **The `steps` section only applies where a block exposes synchronous
   entry/exit/timeout code** — ordinary blocks with no such code produce
   nothing here.
9. **The `dynamic-events` section only lists objects explicitly configured as
   a schedulable function** — events triggered the normal way (message,
   condition, cyclic, etc.) remain under `events`.