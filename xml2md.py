#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xml2md.py — AnyLogic model XML → semantic Markdown parser
=====================================================================
Purpose:
  Parses the noisy XML source code found under an AnyLogic project's
  `_alp/` directory into semantic, information-dense Markdown, stripping
  out presentational noise like Id/coordinates/Label/color/slider config.

Features:
  - Zero dependencies (only Python's standard library xml.etree)
  - Organized by agent: agent class definitions / variables / parameters /
    collections / functions / embedded objects / flowchart chains /
    statecharts / events / dynamic events / network
  - Synchronous "steps" feature: environment-level step config (EnableSteps/
    StepDurationCode/Before-After step code) and per-agent step callbacks
    inherited from a parent agent type's steps
  - Function bodies, event bodies, and dynamic event bodies are linked from the
    associated .java file via `/*ALCODESTART::<Id>*/` markers (verified on experiment3)
  - Embedded objects only output "parameters that have a Value" (params
    without a Value are unset defaults and count as noise)
  - Statecharts / networks resolve Id references to names
    (Source/Target → state name / node name)
  - Global Java classes (_alp/Classes/) — plain classes/records/enums that
    belong to the model's global environment
  - Option lists — model-level enums

Usage:
  python xml2md.py <model_dir> [options]

Options:
  --toc              Output only the model structure overview (agent list + file listing)
  --agent NAME        Parse only the specified agent (e.g. --agent Main)
  --class NAME        Parse only the specified global Java class (e.g. --class MyDummyClass)
  --optionlist NAME   Show only the specified model-level option list and its items
  --section S         Output only a given section: agent-class|steps|variables|functions|embedded|connectors|statechart|events|dynamic-events|network
  --output FILE       Write the full result to a file (produces a persistent reference document)
  --quiet             Suppress progress messages
"""

import argparse
import datetime
import os
import re
import sys
import xml.etree.ElementTree as ET

# =====================================================================
# Constants
# =====================================================================

# Tags that are purely presentational/boilerplate noise in any XML node (stripped recursively)
NOISE_TAGS = {
    "Id", "X", "Y", "Z", "ZHeight", "Label", "PublicFlag", "PresentationFlag",
    "ShowLabel", "DrawMode", "EmbeddedIcon", "LineWidth", "LineColor",
    "LineMaterial", "LineStyle", "LineZOrder", "LineArrow", "LineArrowPosition",
    "FillColor", "FillMaterial", "Color", "Font", "Alignment", "Rotation",
    "Radius", "Lock", "FakeNode", "SpeedLimit", "SpeedLimitValue",
    "LimitNumberOfTransporters", "LimitNumberOfTransportersValue", "Bidirectional",
    "Points", "Point", "IconOffset", "SatisfiesExpression", "DefaultTransition",
    "Generic", "GenericParameter", "GenericParameterValue", "GenericParameterLabel",
    "GenericParameterSubstitute", "GenericParameterSubstituteReference",
    "GenericParameterSubstituteValue", "ReplicationFlag", "Replication",
    "CollectionType", "InitialLocationType", "ColumnCode", "RowCode",
    "LocationNameCode", "InitializationType", "InitializationDatabaseTableQuery",
    "InitializationDatabaseType", "QuantityColumn", "TableReference",
    "InEnvironment", "PresentationId", "ParentStateId", "CurrentLevel",
    "ConnectionsId", "SceneGridColor", "SceneBackgroundColor", "SceneSkybox",
    "MakeDefaultViewArea", "LimitNumberOfArrayElements", "ElementsLimitValue",
    "SamplesToKeep", "FreezeXAxis", "AutoUpdate", "RecurrenceProperties",
    "AutoCreate", "DatasetsCreationProperties",
    "ColumnsCountCode", "RowsCountCode", "NeigborhoodType", "LayoutType",
    "NetworkType", "ConnectionsPerAgentCode", "ConnectionsRangeCode",
    "NeighborLinkFractionCode", "MCode", "ScaleRuler", "InheritedFromParentAgentType",
    "ScaleType", "ModelLength", "LengthUnits", "Scale",
    "EnvironmentDefinesInitialLocation",
    "RotateAnimationTowardsMovement", "RotateAnimationVertically",
    "VelocityCode", "PhysicalLength", "PhysicalWidth", "PhysicalHeight",
    "Properties", "ParameterEditor", "EditorContolType", "MinSliderValue",
    "MaxSliderValue", "DelimeterType", "SdArray", "UnitType", "ModificatorType",
    "PackageName",  # In an ActiveObjectClass reference, ClassName is enough, so PackageName is stripped
    "FlowChartsUsage",  # Extracted separately, see parse_agent_class
}

# Direct-child pure noise (usually coexists with semantic child elements under the same parent node)
SKIP_ATTRS = {
    "SaveInSnapshot", "Constant", "AccessType", "StaticVariable", "ModificatorType",
    "Class", "Trigger", "Mode", "Source", "Target", "SourceId", "TargetId",
}

# =====================================================================
# Utility functions
# =====================================================================

def localname(tag: str) -> str:
    """Strip the namespace prefix, e.g. '{urn}x' → 'x'"""
    return tag.split('}')[-1] if tag.startswith('{') else tag


def cdata(elem) -> str:
    """Return the element's text content (including child-element code), stripped of leading/trailing whitespace"""
    if elem is None:
        return ""
    # If it only has text, use it directly
    if elem.text:
        return elem.text.strip()
    return ""


def find_child(elem, name):
    """Find a direct child element by local name"""
    for c in elem:
        if localname(c.tag) == name:
            return c
    return None


def find_children(elem, name):
    """Find all direct child elements by local name"""
    return [c for c in elem if localname(c.tag) == name]


def find_all_children(elem, name):
    """Recursively find all descendants with the given tag (matched by local name)"""
    out = []
    for c in elem:
        if localname(c.tag) == name:
            out.append(c)
        out.extend(find_all_children(c, name))
    return out


def code_value(elem):
    """Extract code text from the CodeUnitValue/CodeValue of a Value/InitialValue/DefaultValue element.
    Also handles EntityCodeValue (entity references such as newTransporter): returns the inner
    ActiveObjectClass.ClassName"""
    if elem is None:
        return ""
    # Directly a Code child element
    code = find_child(elem, "Code")
    if code is not None and code.text:
        return code.text.strip()
    # Entity reference (EntityCodeValue → EntityEmbeddedObject → ActiveObjectClass.ClassName)
    aoc = find_child(elem, "ActiveObjectClass")
    if aoc is not None:
        cls = find_child(aoc, "ClassName")
        if cls is not None and cls.text:
            return cls.text.strip()
    for aoc in find_all_children(elem, "ActiveObjectClass"):
        cls = find_child(aoc, "ClassName")
        if cls is not None and cls.text:
            return cls.text.strip()
    # Could be an empty <Code>...</Code>, or a unit value
    return cdata(elem)


def unit_label(elem):
    """Extract 'value unit' from a CodeUnitValue, e.g. '1 SECOND'"""
    if elem is None:
        return ""
    code = find_child(elem, "Code")
    unit = find_child(elem, "Unit")
    val = code.text.strip() if (code is not None and code.text) else ""
    u = ""
    if unit is not None:
        unit_child = unit
        # The Unit element itself has a Class attribute and empty text; the unit name is in Class
        # (e.g. SpeedUnits or LengthUnits)
        u = unit_child.get("Class", "")
        # Strip the "Units" suffix: SpeedUnits → MPS? No — Class=SpeedUnits's value lives in Code.
        # Just take the Unit element's text.
        u = unit_child.text or ""
    return f"{val} {u}".strip() if (val or u) else ""


# =====================================================================
# Per-file parsers
# =====================================================================

def parse_agent_class(aoc_path):
    """Parse AOC.*.xml → agent class definition (name, type, imports, startup code)"""
    info = {"name": "", "usage": "", "imports": "", "startup": ""}
    if not os.path.exists(aoc_path):
        return info
    tree = ET.parse(aoc_path)
    root = tree.getroot()
    name_el = find_child(root, "Name")
    info["name"] = cdata(name_el)
    usage_el = find_child(root, "FlowChartsUsage")
    info["usage"] = cdata(usage_el) if usage_el is not None else ""
    imp_el = find_child(root, "Import")
    info["imports"] = cdata(imp_el)
    start_el = find_child(root, "StartupCode")
    info["startup"] = cdata(start_el)
    return info


def parse_steps(aoc_path):
    """Parse AOC.*.xml → synchronous "steps" feature config.

    AnyLogic's steps feature has two halves that live in different tags:
      - EnvironmentProperties: set on the agent type that OWNS the space (e.g. Main).
        EnableSteps/StepDurationCode define whether/how often a step occurs; its own
        BeforeStepCode/AfterStepCode fire once per step for the environment itself.
      - AgentProperties: set on an agent type that gets PLACED into a population/space.
        Its BeforeStepCode/AfterStepCode fire once per step for EACH agent instance,
        but only take effect if the parent agent type's EnvironmentProperties has
        EnableSteps=true.
    """
    info = {
        "env_enabled": False, "env_duration": "", "env_before": "", "env_after": "",
        "agent_before": "", "agent_after": "",
    }
    if not os.path.exists(aoc_path):
        return info
    tree = ET.parse(aoc_path)
    root = tree.getroot()
    env = find_child(root, "EnvironmentProperties")
    if env is not None:
        info["env_enabled"] = cdata(find_child(env, "EnableSteps")).lower() == "true"
        info["env_duration"] = unit_label(find_child(env, "StepDurationCode"))
        info["env_before"] = cdata(find_child(env, "BeforeStepCode"))
        info["env_after"] = cdata(find_child(env, "AfterStepCode"))
    agent = find_child(root, "AgentProperties")
    if agent is not None:
        info["agent_before"] = cdata(find_child(agent, "BeforeStepCode"))
        info["agent_after"] = cdata(find_child(agent, "AfterStepCode"))
    return info


def parse_variables(xml_path):
    """Parse Variables.xml → three lists: variables / parameters / collections"""
    variables, params, collections = [], [], []
    if not os.path.exists(xml_path):
        return variables, params, collections
    tree = ET.parse(xml_path)
    root = tree.getroot()
    for var in find_children(root, "Variable"):
        vtype = var.get("Class", "")
        name = cdata(find_child(var, "Name"))
        desc = cdata(find_child(var, "Description"))
        props = find_child(var, "Properties")
        # Type
        type_el = find_child(props, "Type") if props is not None else None
        vtype_name = cdata(type_el)
        # Initial value / default value
        init = ""
        for tag in ("InitialValue", "DefaultValue"):
            el = find_child(props, tag) if props is not None else None
            if el is not None:
                init = code_value(el)
                break
        # Collection
        coll_class = cdata(find_child(props, "CollectionClass")) if props is not None else ""
        elem_class = cdata(find_child(props, "ElementClass")) if props is not None else ""
        val_class = cdata(find_child(props, "ValueElementClass")) if props is not None else ""

        init_code = ""
        init_el = find_child(props, "CollectionInitializer") if props is not None else None
        if init_el is not None:
            init_code = code_value(init_el)

        row = {"name": name, "type": vtype_name, "desc": desc, "init": init}
        if vtype == "Parameter":
            row["editor"] = vtype_name  # the parameter's "type" is itself the editor type
            params.append(row)
        elif vtype == "CollectionVariable":
            coll_info = f"{coll_class} <{elem_class}"
            if val_class:
                coll_info += f", {val_class}"
            coll_info += ">"
            if init_code:
                coll_info += f" = {init_code}"
            row["type"] = coll_info
            collections.append(row)
        else:
            variables.append(row)
    return variables, params, collections


def extract_java_bodies(java_path):
    """Extract /*ALCODESTART::<Id>*/ ... /*ALCODEEND*/ code blocks from a .java file → {id: code}
    Note: a single function in Functions.java may carry a nested named marker
    (e.g. /*ALCODESTART::1783922945239*/ immediately followed by
    /*ALCODESTART::send_emergency_stop_001*/); any inner nested
    /*ALCODESTART::...*/ markers are stripped out during extraction."""
    bodies = {}
    if not os.path.exists(java_path):
        return bodies
    with open(java_path, "r", encoding="utf-8") as f:
        text = f.read()
    # Regex: ALCODESTART::<numeric id> through ALCODEEND (non-greedy)
    for m in re.finditer(r"/\*ALCODESTART::(\d+)\*/(.*?)/\*ALCODEEND\*/", text, re.DOTALL):
        fid = m.group(1)
        code = m.group(2)
        # Strip any inner nested named ALCODESTART markers (e.g. send_emergency_stop_001)
        code = re.sub(r"/\*ALCODESTART::[^*]*?\*/", "", code)
        code = code.strip()
        if code:
            bodies[fid] = code
    return bodies


def parse_functions(xml_path, java_path=None):
    """Parse Functions.xml (+ Functions.java) → list of functions (signature + code)"""
    funcs = []
    if not os.path.exists(xml_path):
        return funcs
    bodies = extract_java_bodies(java_path) if java_path else {}
    tree = ET.parse(xml_path)
    root = tree.getroot()
    for fn in find_children(root, "Function"):
        fid = cdata(find_child(fn, "Id"))
        name = cdata(find_child(fn, "Name"))
        ret_mod = cdata(find_child(fn, "ReturnModificator"))
        ret_type = cdata(find_child(fn, "ReturnType"))
        desc = cdata(find_child(fn, "Description"))
        params = []
        for p in find_children(fn, "Parameter"):
            pname = cdata(find_child(p, "Name"))
            ptype = cdata(find_child(p, "Type"))
            params.append(f"{ptype} {pname}".strip())
        body = bodies.get(fid, "")
        funcs.append({
            "name": name, "return_mod": ret_mod, "return_type": ret_type,
            "desc": desc, "params": params, "body": body,
        })
    return funcs


def parse_embedded(xml_path):
    """Parse EmbeddedObjects.xml → list of embedded objects (name + type + params that have a value + code callbacks)"""
    objects = []
    if not os.path.exists(xml_path):
        return objects
    tree = ET.parse(xml_path)
    root = tree.getroot()
    for obj in find_children(root, "EmbeddedObject"):
        name = cdata(find_child(obj, "Name"))
        # Type
        aoc = find_child(obj, "ActiveObjectClass")
        cls = cdata(find_child(aoc, "ClassName")) if aoc is not None else ""
        # Generic entity type (GenericParameterSubstituteValue)
        gen_val = ""
        for g in find_all_children(obj, "GenericParameterSubstituteValue"):
            gen_val = cdata(g)
            if gen_val:
                break
        # Parameters that have a Value
        params = []
        for p in find_children(find_child(obj, "Parameters"), "Parameter"):
            pname = cdata(find_child(p, "Name"))
            val_el = find_child(p, "Value")
            code_el = find_child(p, "Code")
            if val_el is not None:
                val = code_value(val_el)
                params.append((pname, val))
            elif code_el is not None:
                params.append((pname, cdata(code_el)))
        objects.append({
            "name": name, "class": cls, "generic": gen_val, "params": params,
        })
    return objects


def parse_connectors(xml_path):
    """Parse Connectors.xml → list of flowchart-chain connections"""
    conns = []
    if not os.path.exists(xml_path):
        return conns
    tree = ET.parse(xml_path)
    root = tree.getroot()
    for c in find_children(root, "Connector"):
        src_obj = ""
        src_port = ""
        dst_obj = ""
        dst_port = ""
        for ref in find_children(c, "SourceEmbeddedObjectReference"):
            src_obj = cdata(find_child(ref, "ItemName"))
        for ref in find_children(c, "SourceConnectableItemReference"):
            src_port = cdata(find_child(ref, "ItemName"))
        for ref in find_children(c, "TargetEmbeddedObjectReference"):
            dst_obj = cdata(find_child(ref, "ItemName"))
        for ref in find_children(c, "TargetConnectableItemReference"):
            dst_port = cdata(find_child(ref, "ItemName"))
        # Data-flow direction = flows from the "out" port to the "in" port.
        # A Connector's Source = the physical connection start, Target = the end; but
        # a flowchart chain's semantics are "carries the agent from out to in". When
        # src_port ends in "in" and dst_port ends in "out", swap them so the pair
        # represents the data-flow direction (out → in).
        src = f"{src_obj}.{src_port}" if src_obj else src_port
        dst = f"{dst_obj}.{dst_port}" if dst_obj else dst_port
        # Physical connection: A.out → B.in means data flows A→B; if the source is "in"
        # or the target is "out", reverse it
        if src_port.endswith("in") and not dst_port.endswith("in"):
            src, dst = dst, src
        conns.append({"src": src, "dst": dst})
    return conns


def parse_statechart(aoc_path):
    """Parse the <StatechartElements> of AOC.*.xml → statechart (states, transitions, initial pointers)"""
    states = {}          # id → {name, entry, exit, parent}
    transitions = []     # {name, src, dst, trigger, cond, action, msg, timeout, filter}
    initials = []        # (from_id, to_id)
    if not os.path.exists(aoc_path):
        return states, transitions, initials
    tree = ET.parse(aoc_path)
    root = tree.getroot()
    sc = find_child(root, "StatechartElements")
    if sc is None:
        return states, transitions, initials

    # First pass: collect the names of all States (for id→name resolution)
    for el in find_children(sc, "StatechartElement"):
        cls = el.get("Class", "")
        if cls in ("State", "HistoryState"):
            sid = cdata(find_child(el, "Id"))
            states[sid] = {
                "name": cdata(find_child(el, "Name")),
                "entry": "", "exit": "", "parent": el.get("ParentState", ""),
            }
            props = find_child(el, "Properties")
            if props is not None:
                states[sid]["entry"] = cdata(find_child(props, "EntryAction"))
                states[sid]["exit"] = cdata(find_child(props, "ExitAction"))

    # Second pass: parse Transition / InitialStatePointer / EntryPoint
    for el in find_children(sc, "StatechartElement"):
        cls = el.get("Class", "")
        name = cdata(find_child(el, "Name"))
        if cls == "Transition":
            props = find_child(el, "Properties")
            src = props.get("Source", "") if props is not None else ""
            dst = props.get("Target", "") if props is not None else ""
            trigger = props.get("Trigger", "") if props is not None else ""
            cond = cdata(find_child(props, "Condition")) if props is not None else ""
            action = cdata(find_child(props, "Action")) if props is not None else ""
            msg_type = cdata(find_child(props, "MessageType")) if props is not None else ""
            filt = cdata(find_child(props, "FilterType")) if props is not None else ""
            eq = cdata(find_child(props, "EqualsExpression")) if props is not None else ""
            timeout = ""
            t_el = find_child(props, "Timeout") if props is not None else None
            if t_el is not None:
                timeout = code_value(t_el)
            transitions.append({
                "name": name, "src": src, "dst": dst, "trigger": trigger,
                "cond": cond, "action": action, "msg_type": msg_type,
                "filter": filt, "equals": eq, "timeout": timeout,
            })
        elif cls in ("InitialStatePointer", "EntryPoint"):
            props = find_child(el, "Properties")
            dst = props.get("Target", "") if props is not None else ""
            initials.append((cdata(find_child(el, "ParentState")), dst))

    return states, transitions, initials


def parse_events(xml_path, java_path=None):
    """Parse Events.xml (+ Events.java) → list of events"""
    events = []
    if not os.path.exists(xml_path):
        return events
    bodies = extract_java_bodies(java_path) if java_path else {}
    tree = ET.parse(xml_path)
    root = tree.getroot()
    for ev in find_children(root, "Event"):
        eid = cdata(find_child(ev, "Id"))
        name = cdata(find_child(ev, "Name"))
        props = find_child(ev, "Properties")
        trigger = props.get("TriggerType", "") if props is not None else ""
        mode = props.get("Mode", "") if props is not None else ""
        details = event_details(props, trigger, mode) if props is not None else []
        events.append({
            "name": name, "trigger": trigger, "mode": mode, "details": details,
            "body": bodies.get(eid, ""),
        })
    return events


def event_details(props, trigger, mode):
    """Pick the relevant (label, value) pairs from an Event's <Properties>.

    Every <Event> carries all of the fields regardless of its TriggerType/Mode, so
    only the ones that apply to the current combination are returned:
      timeout/occuresOnce  → OccurrenceTime or OccurrenceDate (per OccurrenceAtTime)
      timeout/userControls → Timeout
      timeout/cyclic       → as occuresOnce, plus RecurrenceCode
      rate                 → Rate
      condition            → Condition"""
    details = []

    def occurrence():
        at_time = cdata(find_child(props, "OccurrenceAtTime")).lower() == "true"
        if at_time:
            details.append(("First occurrence (model time)",
                            unit_label(find_child(props, "OccurrenceTime"))))
        else:
            ms = cdata(find_child(props, "OccurrenceDate"))
            try:
                when = datetime.datetime.fromtimestamp(int(ms) / 1000, tz=datetime.timezone.utc)
                ms = when.strftime("%Y-%m-%d %H:%M:%S UTC")
            except (ValueError, OverflowError, OSError):
                pass
            details.append(("First occurrence (date)", ms))

    if trigger == "timeout":
        if mode in ("occuresOnce", "cyclic"):
            occurrence()
            if mode == "cyclic":
                details.append(("Recurrence", unit_label(find_child(props, "RecurrenceCode"))))
        elif mode == "userControls":
            details.append(("Timeout", unit_label(find_child(props, "Timeout"))))
    elif trigger == "rate":
        details.append(("Rate", unit_label(find_child(props, "Rate"))))
    elif trigger == "condition":
        details.append(("Condition", cdata(find_child(props, "Condition"))))
    return [(k, v) for k, v in details if v]


def parse_dynamic_events(xml_path, java_path=None):
    """Parse DynamicEvents.xml (+ DynamicEvents.java) → list of dynamic events.

    A dynamic event is a hybrid of an Event and a Function: it takes typed
    parameters like a function, but instead of running immediately it is scheduled
    (e.g. via create()) to run its body at a later, computed time with the bound
    argument values."""
    events = []
    if not os.path.exists(xml_path):
        return events
    bodies = extract_java_bodies(java_path) if java_path else {}
    tree = ET.parse(xml_path)
    root = tree.getroot()
    for de in find_children(root, "DynamicEventClass"):
        eid = cdata(find_child(de, "Id"))
        name = cdata(find_child(de, "Name"))
        params = []
        for p in find_children(de, "Parameter"):
            pname = cdata(find_child(p, "Name"))
            ptype = cdata(find_child(p, "Type"))
            params.append(f"{ptype} {pname}".strip())
        events.append({
            "name": name, "params": params, "body": bodies.get(eid, ""),
        })
    return events


def parse_network(level_xml_path):
    """Parse the <Network> of Level*.xml → node table + path table"""
    nodes = {}       # id → {name, type, x, y}
    paths = []       # {name, src, dst, type}
    if not os.path.exists(level_xml_path):
        return nodes, paths
    tree = ET.parse(level_xml_path)
    root = tree.getroot()
    for net in find_all_children(root, "Network"):
        for node in find_all_children(net, "PointNode") + find_all_children(net, "RectangularNode"):
            nid = cdata(find_child(node, "Id"))
            nodes[nid] = {
                "name": cdata(find_child(node, "Name")),
                "type": localname(node.tag),
                "x": cdata(find_child(node, "X")),
                "y": cdata(find_child(node, "Y")),
            }
        for path in find_all_children(net, "Path"):
            pname = cdata(find_child(path, "Name"))
            src = path.get("SourceId", "")
            dst = path.get("TargetId", "")
            ptype = cdata(find_child(path, "PathType"))
            paths.append({
                "name": pname,
                "src": nodes.get(src, {}).get("name", src) if src else "",
                "dst": nodes.get(dst, {}).get("name", dst) if dst else "",
                "type": ptype,
            })
    return nodes, paths


def parse_java_class(java_path):
    """Parse a Class.<Name>.java file under _alp/Classes/ → {name, code}.

    These are plain Java source files (classes/records/enums/interfaces) declared
    directly in the model's "Classes" node. They belong to the model's global
    environment — like the agent types themselves — rather than to any individual
    agent type, so they are rendered as their own top-level section instead of being
    nested under an agent."""
    basename = os.path.basename(java_path)
    m = re.match(r"Class\.(.+)\.java$", basename)
    name = m.group(1) if m else basename
    with open(java_path, "r", encoding="utf-8") as f:
        code = f.read().strip()
    return {"name": name, "code": code}


def parse_option_lists(alpx_path):
    """Parse the <OptionLists> of the model's .alpx file → [{name, options}].

    Option lists (enum-like lists of named options) are defined on the model itself,
    not on any agent type, so they live in the .alpx rather than under `_alp/`."""
    lists = []
    if not alpx_path or not os.path.exists(alpx_path):
        return lists
    root = ET.parse(alpx_path).getroot()
    for ol_parent in find_all_children(root, "OptionLists"):
        for ol in find_children(ol_parent, "OptionList"):
            lists.append({
                "name": cdata(find_child(ol, "Name")),
                "options": [cdata(find_child(o, "Name")) for o in find_children(ol, "Option")],
            })
    return lists


# =====================================================================
# Agent aggregation
# =====================================================================

def parse_agent(agent_dir):
    """Parse all the semantic files under a single agent directory"""
    result = {}
    aoc_candidates = [f for f in os.listdir(agent_dir) if f.startswith("AOC.") and f.endswith(".xml")]
    aoc_path = os.path.join(agent_dir, aoc_candidates[0]) if aoc_candidates else None
    result["agent_class"] = parse_agent_class(aoc_path) if aoc_path else {"name": "", "usage": "", "imports": "", "startup": ""}
    result["steps"] = parse_steps(aoc_path) if aoc_path else {
        "env_enabled": False, "env_duration": "", "env_before": "", "env_after": "",
        "agent_before": "", "agent_after": "",
    }

    result["variables"], result["params"], result["collections"] = parse_variables(
        os.path.join(agent_dir, "Variables.xml"))

    func_java = os.path.join(agent_dir, "Code", "Functions.java")
    result["functions"] = parse_functions(
        os.path.join(agent_dir, "Code", "Functions.xml"), func_java)

    result["embedded"] = parse_embedded(os.path.join(agent_dir, "EmbeddedObjects.xml"))
    result["connectors"] = parse_connectors(os.path.join(agent_dir, "Connectors.xml"))
    result["statechart"], result["transitions"], result["initials"] = parse_statechart(aoc_path) if aoc_path else ({}, [], [])

    ev_java = os.path.join(agent_dir, "Code", "Events.java")
    result["events"] = parse_events(os.path.join(agent_dir, "Code", "Events.xml"), ev_java)

    de_java = os.path.join(agent_dir, "Code", "DynamicEvents.java")
    result["dynamic_events"] = parse_dynamic_events(
        os.path.join(agent_dir, "Code", "DynamicEvents.xml"), de_java)

    # Network: only one of the Levels/Level*.xml files may actually have a Network
    result["network"] = None
    levels_dir = os.path.join(agent_dir, "Levels")
    if os.path.isdir(levels_dir):
        for lf in sorted(os.listdir(levels_dir)):
            if lf.startswith("Level") and lf.endswith(".xml"):
                nodes, paths = parse_network(os.path.join(levels_dir, lf))
                if nodes or paths:
                    result["network"] = {"nodes": nodes, "paths": paths}
                    break
    return result


# =====================================================================
# Markdown rendering
# =====================================================================

def render_agent_class(info) -> str:
    lines = [f"### Agent class definition"]
    name = info.get("name", "")
    usage = info.get("usage", "")
    lines.append(f"- **Name**: `{name}`" + (f"  |  Type: `{usage}`" if usage else ""))
    if info.get("imports"):
        lines.append(f"- **Imports**:")
        lines.append(f"```java\n{info['imports'].strip()}\n```")
    if info.get("startup"):
        lines.append(f"- **Startup code**:")
        lines.append(f"```java\n{info['startup'].strip()}\n```")
    return "\n".join(lines)


def render_steps(steps) -> str:
    """Render the synchronous "steps" feature: environment-level step config (this
    agent type's own space) and per-agent step callbacks (fire per-instance when this
    agent type is placed inside a parent agent type/population whose space has steps enabled)"""
    if not steps:
        return ""
    env_enabled = steps.get("env_enabled", False)
    env_before = steps.get("env_before", "")
    env_after = steps.get("env_after", "")
    agent_before = steps.get("agent_before", "")
    agent_after = steps.get("agent_after", "")

    has_env = env_enabled or env_before or env_after
    has_agent = bool(agent_before or agent_after)
    if not has_env and not has_agent:
        return ""

    out = ["### Synchronous steps"]
    if has_env:
        out.append("#### Environment (this agent type's own space)")
        status = f"enabled, every `{steps.get('env_duration', '')}`" if env_enabled else "disabled"
        out.append(f"- **Steps**: {status}")
        if env_before:
            out.append("- **Before step**:")
            out.append(f"```java\n{env_before}\n```")
        if env_after:
            out.append("- **After step**:")
            out.append(f"```java\n{env_after}\n```")
    if has_agent:
        out.append("#### Per-agent step callbacks (require the parent agent type's steps to be enabled)")
        if agent_before:
            out.append("- **Before step**:")
            out.append(f"```java\n{agent_before}\n```")
        if agent_after:
            out.append("- **After step**:")
            out.append(f"```java\n{agent_after}\n```")
    return "\n".join(out)


def render_variables(vars_, params, collections) -> str:
    out = []
    if vars_:
        out.append("### Variables")
        out.append("| Name | Type | Initial value | Description |")
        out.append("|---|---|---|---|")
        for v in vars_:
            init = v["init"].replace("|", "\\|")
            desc = v["desc"].replace("|", "\\|")
            out.append(f"| `{v['name']}` | `{v['type']}` | `{init}` | {desc} |")
    if params:
        out.append("### Parameters")
        out.append("| Name | Type | Default value | Description |")
        out.append("|---|---|---|---|")
        for p in params:
            init = p["init"].replace("|", "\\|")
            desc = p["desc"].replace("|", "\\|")
            out.append(f"| `{p['name']}` | `{p['type']}` | `{init}` | {desc} |")
    if collections:
        out.append("### Collections")
        out.append("| Name | Collection type | Description |")
        out.append("|---|---|---|")
        for c in collections:
            desc = c["desc"].replace("|", "\\|")
            typ = c["type"]
            # Collection-initializer code (e.g. {node0,node1,...}) can be very long;
            # truncate it and show it on its own line
            if " = {" in typ and len(typ) > 120:
                typ = typ.split(" = {")[0] + " = {...}"
            out.append(f"| `{c['name']}` | `{typ}` | {desc} |")
    return "\n".join(out)


def render_functions(funcs) -> str:
    if not funcs:
        return ""
    out = ["### Functions"]
    for fn in funcs:
        params = ", ".join(fn["params"]) if fn["params"] else ""
        sig = f"{fn['name']}({params})"
        out.append(f"#### `{sig}`")
        parts = []
        if fn["return_mod"] != "VOID" or fn["return_type"]:
            parts.append(f"- **Returns**: `{fn['return_type']}`")
        if fn["desc"]:
            parts.append(f"- **Description**: {fn['desc']}")
        if parts:
            out.extend(parts)
        if fn["body"]:
            out.append(f"- **Code**:")
            out.append(f"```java\n{fn['body']}\n```")
    return "\n".join(out)


def render_embedded(objects) -> str:
    if not objects:
        return ""
    out = ["### Embedded objects"]
    for obj in objects:
        cls = obj["class"]
        gen = f"<{obj['generic']}>" if obj["generic"] else ""
        type_str = f"{cls}{gen}"
        out.append(f"#### `{obj['name']}` — `{type_str}`")
        if obj["params"]:
            # Split into simple values and code callbacks
            simple = [(k, v) for k, v in obj["params"] if "\n" not in v and len(v) < 80]
            code = [(k, v) for k, v in obj["params"] if "\n" in v or len(v) >= 80]
            if simple:
                out.append("| Parameter | Value |")
                out.append("|---|---|")
                for k, v in simple:
                    out.append(f"| `{k}` | `{v}` |")
            for k, v in code:
                out.append(f"- **{k}**:")
                out.append(f"```java\n{v}\n```")
    return "\n".join(out)


def render_connectors(conns) -> str:
    if not conns:
        return ""
    out = ["### Flowchart chain", ""]
    out.append("```text")
    for c in conns:
        out.append(f"{c['src']}  →  {c['dst']}")
    out.append("```")
    return "\n".join(out)


def _snip(text, limit):
    """Truncate text for a table cell (turn newlines → spaces, strip pipes to avoid breaking the table)"""
    text = text.replace("|", "\\|").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def render_statechart(states, transitions, initials) -> str:
    if not states and not transitions:
        return ""
    out = ["### Statechart"]
    # Top-level + nested state list (multi-line EntryAction/ExitAction get pushed down into a code block)
    if states:
        out.append("#### States")
        out.append("| State | Parent state | EntryAction | ExitAction |")
        out.append("|---|---|---|---|")
        for s in states.values():
            entry = _snip(s["entry"], 60)
            exit_ = _snip(s["exit"], 60)
            out.append(f"| `{s['name']}` | `{s['parent'] or 'ROOT'}` | `{entry}` | `{exit_}` |")
        # States with actual code in their actions are listed separately (to avoid overstuffing the table)
        code_actions = []
        for s in states.values():
            for tag, text in (("EntryAction", s["entry"]), ("ExitAction", s["exit"])):
                if text and "\n" in text:
                    code_actions.append((s["name"], tag, text))
        if code_actions:
            out.append("##### State action code")
            for (sname, tag, text) in code_actions:
                out.append(f"**{sname}.{tag}**:")
                out.append(f"```java\n{text}\n```")
    if transitions:
        out.append("#### Transitions")
        out.append("| Source → Target | Trigger | Condition/Message | Action summary |")
        out.append("|---|---|---|---|")
        for t in transitions:
            src_name = states.get(t["src"], {}).get("name", t["src"]) if t["src"] else "entry"
            dst_name = states.get(t["dst"], {}).get("name", t["dst"]) if t["dst"] else "?"
            trigger = t["trigger"]
            cond = t["cond"]
            msg = ""
            if t["filter"] and t["equals"]:
                msg = f"{t['filter']} {t['equals']}"
            elif cond:
                msg = f"cond={cond}"
            if t["timeout"] and trigger == "timeout":
                msg = f"timeout {t['timeout']}"
            action = _snip(t["action"], 60)
            out.append(f"| `{src_name}` → `{dst_name}` | `{trigger}` | `{msg}` | `{action}` |")
        # Multi-line actions are shown separately as code blocks
        code_trans = [(t["src"], t["dst"], t["action"]) for t in transitions
                      if t["action"] and "\n" in t["action"]]
        if code_trans:
            out.append("##### Transition action code")
            for (src, dst, text) in code_trans:
                src_name = states.get(src, {}).get("name", src)
                dst_name = states.get(dst, {}).get("name", dst)
                out.append(f"**`{src_name}` → `{dst_name}`**:")
                out.append(f"```java\n{text}\n```")
    if initials:
        out.append("#### Initial pointers")
        for (p, dst) in initials:
            dst_name = states.get(dst, {}).get("name", dst)
            out.append(f"- `{p or 'entry'}` → `{dst_name}`")
    return "\n".join(out)


def render_events(events) -> str:
    if not events:
        return ""
    out = ["### Events"]
    for ev in events:
        out.append(f"#### `{ev['name']}`")
        parts = []
        if ev["trigger"]:
            parts.append(f"- **Trigger**: `{ev['trigger']}`" + (f" / `{ev['mode']}`" if ev["mode"] and ev["trigger"] == "timeout" else ""))
        for label, value in ev["details"]:
            parts.append(f"- **{label}**: `{value}`")
        if parts:
            out.extend(parts)
        if ev["body"]:
            out.append(f"- **Code**:")
            out.append(f"```java\n{ev['body']}\n```")
    return "\n".join(out)


def render_dynamic_events(events) -> str:
    """Render dynamic events: hybrids of Events and Functions — they take
    parameters like a function, but are scheduled to run their body at a later
    time with the bound argument values, like an event"""
    if not events:
        return ""
    out = ["### Dynamic events"]
    for ev in events:
        params = ", ".join(ev["params"]) if ev["params"] else ""
        sig = f"{ev['name']}({params})"
        out.append(f"#### `{sig}`")
        if ev["body"]:
            out.append(f"- **Code**:")
            out.append(f"```java\n{ev['body']}\n```")
    return "\n".join(out)


def render_network(net) -> str:
    if not net or not (net["nodes"] or net["paths"]):
        return ""
    out = ["### Network"]
    nodes = net["nodes"]
    paths = net["paths"]
    if nodes:
        out.append("| Node | Type | Coordinates |")
        out.append("|---|---|---|")
        for n in nodes.values():
            out.append(f"| `{n['name']}` | `{n['type']}` | ({n['x']}, {n['y']}) |")
    if paths:
        out.append("| Path | Connection | Type |")
        out.append("|---|---|---|")
        for p in paths:
            out.append(f"| `{p['name']}` | `{p['src']}` ↔ `{p['dst']}` | `{p['type']}` |")
    return "\n".join(out)


def render_java_class(cls) -> str:
    return f"### `{cls['name']}`\n```java\n{cls['code']}\n```"


def render_java_classes(classes) -> str:
    """Render the global Java classes section (_alp/Classes/): plain Java
    classes/records/enums that belong to the model's global environment rather than
    to any individual agent type"""
    if not classes:
        return ""
    header = "## Java Classes\n\n> Global environment classes (not tied to any specific agent type) — declared under `_alp/Classes/`"
    return "\n\n".join([header] + [render_java_class(cls) for cls in classes])


def render_option_list(ol) -> str:
    out = [f"### `{ol['name']}`"]
    out.extend(f"- `{o}`" for o in ol["options"])
    return "\n".join(out)


def render_option_lists(lists) -> str:
    """Render the model-level option lists section (defined in the .alpx, not
    belonging to any agent type)"""
    if not lists:
        return ""
    header = "## Option Lists\n\n> Model-level option lists (not tied to any specific agent type) — declared in the `.alpx` file"
    return "\n\n".join([header] + [render_option_list(ol) for ol in lists])


def render_agent_section(section, agent):
    """Render the markdown for a single section"""
    if section == "agent-class":
        return render_agent_class(agent["agent_class"])
    if section == "steps":
        return render_steps(agent["steps"])
    if section == "variables":
        return render_variables(agent["variables"], agent["params"], agent["collections"])
    if section == "functions":
        return render_functions(agent["functions"])
    if section == "embedded":
        return render_embedded(agent["embedded"])
    if section == "connectors":
        return render_connectors(agent["connectors"])
    if section == "statechart":
        return render_statechart(agent["statechart"], agent["transitions"], agent["initials"])
    if section == "events":
        return render_events(agent["events"])
    if section == "dynamic-events":
        return render_dynamic_events(agent["dynamic_events"])
    if section == "network":
        return render_network(agent["network"])
    return ""


def render_agent(agent) -> str:
    """Render the full markdown for one agent"""
    name = agent["agent_class"]["name"]
    out = [f"## {name}", ""]
    out.append(render_agent_class(agent["agent_class"]))
    out.append(render_steps(agent["steps"]))
    out.append(render_variables(agent["variables"], agent["params"], agent["collections"]))
    out.append(render_functions(agent["functions"]))
    out.append(render_embedded(agent["embedded"]))
    out.append(render_connectors(agent["connectors"]))
    out.append(render_statechart(agent["statechart"], agent["transitions"], agent["initials"]))
    out.append(render_events(agent["events"]))
    out.append(render_dynamic_events(agent["dynamic_events"]))
    out.append(render_network(agent["network"]))
    return "\n\n".join([x for x in out if x.strip()])


# =====================================================================
# Main flow
# =====================================================================

def discover_agents(model_dir):
    """Discover all agent directories under the model directory"""
    alp_dir = os.path.join(model_dir, "_alp")
    if not os.path.isdir(alp_dir):
        # Compatibility: the model directory itself is already the parent of _alp
        alp_dir = model_dir
    agents_dir = os.path.join(alp_dir, "Agents")
    agents = []
    if os.path.isdir(agents_dir):
        for name in sorted(os.listdir(agents_dir)):
            d = os.path.join(agents_dir, name)
            if os.path.isdir(d) and any(f.startswith("AOC.") for f in os.listdir(d)):
                agents.append(d)
    return agents


def discover_classes(model_dir):
    """Discover global Java class files under _alp/Classes/. These belong to the
    model's global environment (like the agent types themselves), not to any
    individual agent type."""
    alp_dir = os.path.join(model_dir, "_alp")
    if not os.path.isdir(alp_dir):
        alp_dir = model_dir
    classes_dir = os.path.join(alp_dir, "Classes")
    paths = []
    if os.path.isdir(classes_dir):
        for name in sorted(os.listdir(classes_dir)):
            if name.startswith("Class.") and name.endswith(".java"):
                paths.append(os.path.join(classes_dir, name))
    return paths


def discover_alpx(model_dir):
    """Find the model's .alpx file: next to `_alp/` (inside model_dir), or in model_dir's
    parent when model_dir is the `_alp` directory itself"""
    for d in (model_dir, os.path.dirname(os.path.abspath(model_dir))):
        if os.path.isdir(d):
            found = sorted(f for f in os.listdir(d) if f.endswith(".alpx"))
            if found:
                return os.path.join(d, found[0])
    return None


def render_toc(model_dir, agents, class_paths, option_lists=()) -> str:
    """Render the model structure overview"""
    alp_dir = os.path.join(model_dir, "_alp")
    out = [f"# {os.path.basename(model_dir.rstrip('/'))} — Model structure overview", ""]
    out.append("> Source: `_alp/` directory")
    out.append("")
    if option_lists:
        out.append("## Option Lists  (use `--optionlist <name>` to see details)")
        for ol in option_lists:
            out.append(f"- **`{ol['name']}`** — {len(ol['options'])} options: "
                       + ", ".join(f"`{o}`" for o in ol["options"]))
        out.append("")
    if class_paths:
        out.append("## Java Classes Use `--class <class_name>` to see details")
        for p in class_paths:
            cls = parse_java_class(p)
            size = os.path.getsize(p)
            out.append(f"- **`{cls['name']}`** — `{os.path.relpath(p, alp_dir)}` ({size:,} B)")
        out.append("")
    out.append("## Agents (Use `--agent <name>` to see details)")
    for d in agents:
        aoc = [f for f in os.listdir(d) if f.startswith("AOC.")]
        aoc_path = os.path.join(d, aoc[0]) if aoc else None
        info = parse_agent_class(aoc_path) if aoc_path else {"name": "?", "usage": ""}
        usage = f" [{info['usage']}]" if info.get("usage") else ""
        out.append(f"- **`{info['name']}`**{usage} — `{os.path.relpath(d, alp_dir)}`")
        for f in sorted(os.listdir(d)):
            full = os.path.join(d, f)
            if os.path.isfile(full):
                size = os.path.getsize(full)
                out.append(f"  - `{f}` ({size:,} B)")
        code_dir = os.path.join(d, "Code")
        if os.path.isdir(code_dir):
            for f in sorted(os.listdir(code_dir)):
                full = os.path.join(code_dir, f)
                size = os.path.getsize(full)
                out.append(f"  - `Code/{f}` ({size:,} B)")
        levels_dir = os.path.join(d, "Levels")
        if os.path.isdir(levels_dir):
            for f in sorted(os.listdir(levels_dir)):
                full = os.path.join(levels_dir, f)
                size = os.path.getsize(full)
                out.append(f"  - `Levels/{f}` ({size:,} B)")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description="AnyLogic XML → semantic Markdown")
    parser.add_argument("model_dir", help="Model directory (containing _alp/)")
    parser.add_argument("--toc", action="store_true", help="Output only the structure overview")
    parser.add_argument("--agent", help="Parse only the specified agent")
    parser.add_argument("--class", dest="class_name", help="Parse only the specified global Java class (_alp/Classes/)")
    parser.add_argument("--optionlist", help="Show only the specified model-level option list (defined in the .alpx)")
    parser.add_argument("--section", help="Output only a given section (agent-class|steps|variables|functions|embedded|connectors|statechart|events|dynamic-events|network)")
    parser.add_argument("--output", help="Write to a file (produces a persistent reference document)")
    parser.add_argument("--quiet", action="store_true", help="Don't print progress")
    args = parser.parse_args()

    model_dir = args.model_dir
    if not os.path.isdir(model_dir):
        print(f"❌ Directory does not exist: {model_dir}", file=sys.stderr)
        sys.exit(1)

    agents = discover_agents(model_dir)
    classes = discover_classes(model_dir)
    if not agents:
        print(f"❌ No agents found under {model_dir}/_alp/Agents/ (must contain AOC.*.xml)", file=sys.stderr)
        sys.exit(1)

    option_lists = parse_option_lists(discover_alpx(model_dir))

    if args.toc:
        print(render_toc(model_dir, agents, classes, option_lists))
        return

    if args.optionlist:
        target = next((ol for ol in option_lists if ol["name"].lower() == args.optionlist.lower()), None)
        if target is None:
            available = ", ".join(sorted(ol["name"] for ol in option_lists)) or "(none)"
            print(f"❌ Option list `{args.optionlist}` not found. Available: {available}", file=sys.stderr)
            sys.exit(1)
        md = render_option_list(target)
    elif args.class_name:
        target = None
        for p in classes:
            cls = parse_java_class(p)
            if cls["name"].lower() == args.class_name.lower():
                target = cls
                break
        if target is None:
            available = ", ".join(sorted(parse_java_class(p)["name"] for p in classes))
            print(f"❌ Class `{args.class_name}` not found. Available: {available}", file=sys.stderr)
            sys.exit(1)
        md = render_java_class(target)
    elif args.agent:
        # Locate the agent directory
        target = None
        for d in agents:
            aoc = [f for f in os.listdir(d) if f.startswith("AOC.")]
            aoc_path = os.path.join(d, aoc[0]) if aoc else None
            info = parse_agent_class(aoc_path) if aoc_path else {"name": ""}
            if info["name"].lower() == args.agent.lower() or os.path.basename(d).lower() == args.agent.lower():
                target = d
                break
        if target is None:
            available = ", ".join(sorted(
                parse_agent_class(os.path.join(d, [f for f in os.listdir(d) if f.startswith('AOC.')][0]))["name"]
                for d in agents if [f for f in os.listdir(d) if f.startswith('AOC.')]))
            print(f"❌ Agent `{args.agent}` not found. Available: {available}", file=sys.stderr)
            sys.exit(1)
        agent = parse_agent(target)
        if args.section:
            md = render_agent_section(args.section, agent)
        else:
            md = render_agent(agent)
    else:
        # All global Java classes + all agents
        sections = []
        option_lists_section = render_option_lists(option_lists)
        if option_lists_section:
            sections.append(option_lists_section)
        classes_section = render_java_classes([parse_java_class(p) for p in classes])
        if classes_section:
            sections.append(classes_section)
        for d in agents:
            agent = parse_agent(d)
            sections.append(render_agent(agent))
        header = [f"# {os.path.basename(model_dir.rstrip('/'))} — Model semantic reference",
                  f"> Auto-generated: {datetime.date.today().isoformat()}  |  Source: `_alp/` directory  |  "
                  f"Noise such as Id/coordinates/UI config has been stripped out"]
        md = "\n\n".join(header + sections)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md + "\n")
        if not args.quiet:
            print(f"✅ Written to {args.output}")
    else:
        print(md)


if __name__ == "__main__":
    main()