#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
xml2md.py — AnyLogic 模型 XML → 语义化 Markdown 解析器
=====================================================================
用途：
  把 AnyLogic 项目 `_alp/` 目录下充满噪音的 XML 源码，解析成语义化、
  高信息密度的 Markdown，剥离 Id/坐标/Label/颜色/滑块配置等展示性噪音。

特性：
  - 零依赖（仅 Python 标准库 xml.etree）
  - 按 agent 组织：类定义 / 变量 / 参数 / 集合 / 函数 / 嵌入对象 / 流程链 / 状态机 / 事件 / 网络
  - 函数体、事件体通过 `/*ALCODESTART::<Id>*/` 标记从 .java 关联（已验证 experiment3）
  - 嵌入对象只输出「有 Value 的参数」（无 Value 的是默认未设置，属噪音）
  - 状态机 / 网络把 Id 引用解析为名称（Source/Target → 状态名/节点名）

用法：
  python xml2md.py <模型目录> [选项]

选项：
  --toc              只输出模型结构总览（agent 列表 + 文件清单）
  --agent NAME       只解析指定 agent（如 --agent Main）
  --section S        只输出某部分：class|variables|functions|embedded|connectors|statechart|events|network
  --output FILE      把完整结果写入文件（生成持久参考文档）
  --quiet            不打印进度信息
"""

import argparse
import datetime
import os
import re
import sys
import xml.etree.ElementTree as ET

# =====================================================================
# 常量
# =====================================================================

# 任何 XML 节点中都属于纯展示/样板噪音的标签（递归剥离）
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
    "OccurrenceAtTime", "OccurrenceDate", "OccurrenceTime", "RecurrenceCode",
    "AutoCreate", "DatasetsCreationProperties", "EnableSteps", "StepDurationCode",
    "ColumnsCountCode", "RowsCountCode", "NeigborhoodType", "LayoutType",
    "NetworkType", "ConnectionsPerAgentCode", "ConnectionsRangeCode",
    "NeighborLinkFractionCode", "MCode", "ScaleRuler", "InheritedFromParentAgentType",
    "ScaleType", "ModelLength", "LengthUnits", "Scale", "EnvironmentProperties",
    "AgentProperties", "EnvironmentDefinesInitialLocation",
    "RotateAnimationTowardsMovement", "RotateAnimationVertically",
    "VelocityCode", "PhysicalLength", "PhysicalWidth", "PhysicalHeight",
    "Properties", "ParameterEditor", "EditorContolType", "MinSliderValue",
    "MaxSliderValue", "DelimeterType", "SdArray", "UnitType", "ModificatorType",
    "PackageName",  # 在 ActiveObjectClass 引用里 ClassName 足够，PackageName 剥离
    "FlowChartsUsage",  # 单独提取，见 parse_class
}

# 直接子级纯噪音（通常与语义子元素共存于同一父节点下）
SKIP_ATTRS = {
    "SaveInSnapshot", "Constant", "AccessType", "StaticVariable", "ModificatorType",
    "Class", "Trigger", "Mode", "Source", "Target", "SourceId", "TargetId",
}

# =====================================================================
# 工具函数
# =====================================================================

def localname(tag: str) -> str:
    """去掉 namespace 前缀，如 '{urn}x' → 'x'"""
    return tag.split('}')[-1] if tag.startswith('{') else tag


def cdata(elem) -> str:
    """返回元素的文本内容（含子元素代码），去掉首尾空白"""
    if elem is None:
        return ""
    # 如果只有 text，直接用
    if elem.text:
        return elem.text.strip()
    return ""


def find_child(elem, name):
    """按本地名查找直接子元素"""
    for c in elem:
        if localname(c.tag) == name:
            return c
    return None


def find_children(elem, name):
    """按本地名查找所有直接子元素"""
    return [c for c in elem if localname(c.tag) == name]


def find_all_children(elem, name):
    """递归查找所有后代中的指定标签（本地名匹配）"""
    out = []
    for c in elem:
        if localname(c.tag) == name:
            out.append(c)
        out.extend(find_all_children(c, name))
    return out


def code_value(elem):
    """从 Value/InitialValue/DefaultValue 的 CodeUnitValue/CodeValue 中提取代码文本
    兼容 EntityCodeValue（newTransporter 等实体引用）：返回内部 ActiveObjectClass.ClassName"""
    if elem is None:
        return ""
    # 直接是 Code 子元素
    code = find_child(elem, "Code")
    if code is not None and code.text:
        return code.text.strip()
    # 实体引用（EntityCodeValue → EntityEmbeddedObject → ActiveObjectClass.ClassName）
    aoc = find_child(elem, "ActiveObjectClass")
    if aoc is not None:
        cls = find_child(aoc, "ClassName")
        if cls is not None and cls.text:
            return cls.text.strip()
    for aoc in find_all_children(elem, "ActiveObjectClass"):
        cls = find_child(aoc, "ClassName")
        if cls is not None and cls.text:
            return cls.text.strip()
    # 可能是 <Code>...</Code> 但空，或 unit value
    return cdata(elem)


def unit_label(elem):
    """从 CodeUnitValue 提取 '值 单位'，如 '1 SECOND'"""
    if elem is None:
        return ""
    code = find_child(elem, "Code")
    unit = find_child(elem, "Unit")
    val = code.text.strip() if (code is not None and code.text) else ""
    u = ""
    if unit is not None:
        unit_child = unit
        # Unit 元素本身有 Class 属性，文本为空；单位名在 Class 里（如 SpeedUnits 或 LengthUnits）
        u = unit_child.get("Class", "")
        # 去掉 Units 后缀：SpeedUnits → MPS? 不，Class=SpeedUnits 值在 Code 里。取 Unit 的文本。
        u = unit_child.text or ""
    return f"{val} {u}".strip() if (val or u) else ""


# =====================================================================
# 各文件解析器
# =====================================================================

def parse_class(aoc_path):
    """解析 AOC.*.xml → agent 类定义（名称、类型、导入、启动代码）"""
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


def parse_variables(xml_path):
    """解析 Variables.xml → 变量 / 参数 / 集合 三类列表"""
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
        # 类型
        type_el = find_child(props, "Type") if props is not None else None
        vtype_name = cdata(type_el)
        # 初始值 / 默认值
        init = ""
        for tag in ("InitialValue", "DefaultValue"):
            el = find_child(props, tag) if props is not None else None
            if el is not None:
                init = code_value(el)
                break
        # 集合
        coll_class = cdata(find_child(props, "CollectionClass")) if props is not None else ""
        elem_class = cdata(find_child(props, "ElementClass")) if props is not None else ""
        val_class = cdata(find_child(props, "ValueElementClass")) if props is not None else ""
        init_code = ""
        init_el = find_child(props, "CollectionInitializer") if props is not None else None
        if init_el is not None:
            init_code = code_value(init_el)

        row = {"name": name, "type": vtype_name, "desc": desc, "init": init}
        if vtype == "Parameter":
            row["editor"] = vtype_name  # 参数类型即类型
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
    """从 .java 提取 /*ALCODESTART::<Id>*/ ... /*ALCODEEND*/ 代码块 → {id: code}
    注意：Functions.java 里一个函数可能带嵌套的命名标记
    （如 /*ALCODESTART::1783922945239*/ 后紧跟 /*ALCODESTART::send_emergency_stop_001*/），
    提取时剥离所有内部 /*ALCODESTART::...*/ 嵌套标记。"""
    bodies = {}
    if not os.path.exists(java_path):
        return bodies
    with open(java_path, "r", encoding="utf-8") as f:
        text = f.read()
    # 正则：ALCODESTART::<数字id> 到 ALCODEEND（非贪婪）
    for m in re.finditer(r"/\*ALCODESTART::(\d+)\*/(.*?)/\*ALCODEEND\*/", text, re.DOTALL):
        fid = m.group(1)
        code = m.group(2)
        # 剥离内部嵌套的命名 ALCODESTART 标记（如 send_emergency_stop_001）
        code = re.sub(r"/\*ALCODESTART::[^*]*?\*/", "", code)
        code = code.strip()
        if code:
            bodies[fid] = code
    return bodies


def parse_functions(xml_path, java_path=None):
    """解析 Functions.xml (+ Functions.java) → 函数列表（签名 + 代码）"""
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
    """解析 EmbeddedObjects.xml → 嵌入对象列表（名称 + 类型 + 有值的参数 + 代码回调）"""
    objects = []
    if not os.path.exists(xml_path):
        return objects
    tree = ET.parse(xml_path)
    root = tree.getroot()
    for obj in find_children(root, "EmbeddedObject"):
        name = cdata(find_child(obj, "Name"))
        # 类型
        aoc = find_child(obj, "ActiveObjectClass")
        cls = cdata(find_child(aoc, "ClassName")) if aoc is not None else ""
        # 泛型实体类型（GenericParameterSubstituteValue）
        gen_val = ""
        for g in find_all_children(obj, "GenericParameterSubstituteValue"):
            gen_val = cdata(g)
            if gen_val:
                break
        # 有 Value 的参数
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
    """解析 Connectors.xml → 流程链连接列表"""
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
        # 数据流方向 = 从 out 端口流到 in 端口。
        # Connector 的 Source=物理连接起点，Target=终点；但流程链语义是
        # "携带 agent 从 out 流到 in"。当 src_port 以 in 结尾、dst_port 以 out 结尾时，
        # 交换使其表示数据流方向（out → in）。
        src = f"{src_obj}.{src_port}" if src_obj else src_port
        dst = f"{dst_obj}.{dst_port}" if dst_obj else dst_port
        # 物理连接: A.out → B.in 即数据流 A→B；若源是 in / 目标是 out，则反转
        if src_port.endswith("in") and not dst_port.endswith("in"):
            src, dst = dst, src
        conns.append({"src": src, "dst": dst})
    return conns


def parse_statechart(aoc_path):
    """解析 AOC.*.xml 的 <StatechartElements> → 状态机（状态、转移、初始指针）"""
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

    # 第一遍：收集所有 State 的名称（用于 id→名称解析）
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

    # 第二遍：解析 Transition / InitialStatePointer / EntryPoint
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
    """解析 Events.xml (+ Events.java) → 事件列表"""
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
        timeout = ""
        if props is not None:
            t_el = find_child(props, "Timeout")
            if t_el is not None:
                timeout = code_value(t_el)
            else:
                # cyclic 事件：RecurrenceCode 是周期
                r_el = find_child(props, "RecurrenceCode")
                if r_el is not None:
                    timeout = code_value(r_el)
        events.append({
            "name": name, "trigger": trigger, "mode": mode, "timeout": timeout,
            "body": bodies.get(eid, ""),
        })
    return events


def parse_network(level_xml_path):
    """解析 Level*.xml 的 <Network> → 节点表 + 路径表"""
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


# =====================================================================
# Agent 聚合
# =====================================================================

def parse_agent(agent_dir):
    """解析单个 agent 目录下的所有语义文件"""
    result = {}
    aoc_candidates = [f for f in os.listdir(agent_dir) if f.startswith("AOC.") and f.endswith(".xml")]
    aoc_path = os.path.join(agent_dir, aoc_candidates[0]) if aoc_candidates else None
    result["class"] = parse_class(aoc_path) if aoc_path else {"name": "", "usage": "", "imports": "", "startup": ""}

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

    # 网络：Levels/Level*.xml 里可能只有其中一个有 Network
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
# Markdown 渲染
# =====================================================================

def render_class(info) -> str:
    lines = [f"### 类定义"]
    name = info.get("name", "")
    usage = info.get("usage", "")
    lines.append(f"- **名称**: `{name}`" + (f"  |  类型: `{usage}`" if usage else ""))
    if info.get("imports"):
        lines.append(f"- **导入**:")
        lines.append(f"```java\n{info['imports'].strip()}\n```")
    if info.get("startup"):
        lines.append(f"- **启动代码**:")
        lines.append(f"```java\n{info['startup'].strip()}\n```")
    return "\n".join(lines)


def render_variables(vars_, params, collections) -> str:
    out = []
    if vars_:
        out.append("### 变量")
        out.append("| 名称 | 类型 | 初始值 | 说明 |")
        out.append("|---|---|---|---|")
        for v in vars_:
            init = v["init"].replace("|", "\\|")
            desc = v["desc"].replace("|", "\\|")
            out.append(f"| `{v['name']}` | `{v['type']}` | `{init}` | {desc} |")
    if params:
        out.append("### 参数")
        out.append("| 名称 | 类型 | 默认值 | 说明 |")
        out.append("|---|---|---|---|")
        for p in params:
            init = p["init"].replace("|", "\\|")
            desc = p["desc"].replace("|", "\\|")
            out.append(f"| `{p['name']}` | `{p['type']}` | `{init}` | {desc} |")
    if collections:
        out.append("### 集合")
        out.append("| 名称 | 集合类型 | 说明 |")
        out.append("|---|---|---|")
        for c in collections:
            desc = c["desc"].replace("|", "\\|")
            typ = c["type"]
            # 集合初始化代码（如 {node0,node1,...}）可能超长，截断并换行展示
            if " = {" in typ and len(typ) > 120:
                typ = typ.split(" = {")[0] + " = {...}"
            out.append(f"| `{c['name']}` | `{typ}` | {desc} |")
    return "\n".join(out)


def render_functions(funcs) -> str:
    if not funcs:
        return ""
    out = ["### 函数"]
    for fn in funcs:
        params = ", ".join(fn["params"]) if fn["params"] else ""
        sig = f"{fn['name']}({params})"
        out.append(f"#### `{sig}`")
        parts = []
        if fn["return_mod"] != "VOID" or fn["return_type"]:
            parts.append(f"- **返回**: `{fn['return_type']}`")
        if fn["desc"]:
            parts.append(f"- **说明**: {fn['desc']}")
        if parts:
            out.extend(parts)
        if fn["body"]:
            out.append(f"- **代码**:")
            out.append(f"```java\n{fn['body']}\n```")
    return "\n".join(out)


def render_embedded(objects) -> str:
    if not objects:
        return ""
    out = ["### 嵌入对象"]
    for obj in objects:
        cls = obj["class"]
        gen = f"<{obj['generic']}>" if obj["generic"] else ""
        type_str = f"{cls}{gen}"
        out.append(f"#### `{obj['name']}` — `{type_str}`")
        if obj["params"]:
            # 分成简单值 和 代码回调
            simple = [(k, v) for k, v in obj["params"] if "\n" not in v and len(v) < 80]
            code = [(k, v) for k, v in obj["params"] if "\n" in v or len(v) >= 80]
            if simple:
                out.append("| 参数 | 值 |")
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
    out = ["### 流程链", ""]
    out.append("```text")
    for c in conns:
        out.append(f"{c['src']}  →  {c['dst']}")
    out.append("```")
    return "\n".join(out)


def _snip(text, limit):
    """截断文本用于表格单元格（处理换行 → 空格，去掉竖线避免破表）"""
    text = text.replace("|", "\\|").replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def render_statechart(states, transitions, initials) -> str:
    if not states and not transitions:
        return ""
    out = ["### 状态机"]
    # 顶层 + 嵌套状态列表（EntryAction/ExitAction 多行则下沉为代码块）
    if states:
        out.append("#### 状态")
        out.append("| 状态 | 父状态 | EntryAction | ExitAction |")
        out.append("|---|---|---|---|")
        for s in states.values():
            entry = _snip(s["entry"], 60)
            exit_ = _snip(s["exit"], 60)
            out.append(f"| `{s['name']}` | `{s['parent'] or 'ROOT'}` | `{entry}` | `{exit_}` |")
        # 有代码的状态动作单独列出（避免塞爆表格）
        code_actions = []
        for s in states.values():
            for tag, text in (("EntryAction", s["entry"]), ("ExitAction", s["exit"])):
                if text and "\n" in text:
                    code_actions.append((s["name"], tag, text))
        if code_actions:
            out.append("##### 状态动作代码")
            for (sname, tag, text) in code_actions:
                out.append(f"**{sname}.{tag}**:")
                out.append(f"```java\n{text}\n```")
    if transitions:
        out.append("#### 转移")
        out.append("| 源 → 目标 | 触发 | 条件/消息 | 动作摘要 |")
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
        # 多行动作单独用代码块展示
        code_trans = [(t["src"], t["dst"], t["action"]) for t in transitions
                      if t["action"] and "\n" in t["action"]]
        if code_trans:
            out.append("##### 转移动作代码")
            for (src, dst, text) in code_trans:
                src_name = states.get(src, {}).get("name", src)
                dst_name = states.get(dst, {}).get("name", dst)
                out.append(f"**`{src_name}` → `{dst_name}`**:")
                out.append(f"```java\n{text}\n```")
    if initials:
        out.append("#### 初始指向")
        for (p, dst) in initials:
            dst_name = states.get(dst, {}).get("name", dst)
            out.append(f"- `{p or 'entry'}` → `{dst_name}`")
    return "\n".join(out)


def render_events(events) -> str:
    if not events:
        return ""
    out = ["### 事件"]
    for ev in events:
        out.append(f"#### `{ev['name']}`")
        parts = []
        if ev["trigger"]:
            parts.append(f"- **触发**: `{ev['trigger']}`" + (f" / `{ev['mode']}`" if ev["mode"] else ""))
        if ev["timeout"]:
            parts.append(f"- **周期/超时**: `{ev['timeout']}`")
        if parts:
            out.extend(parts)
        if ev["body"]:
            out.append(f"- **代码**:")
            out.append(f"```java\n{ev['body']}\n```")
    return "\n".join(out)


def render_network(net) -> str:
    if not net or not (net["nodes"] or net["paths"]):
        return ""
    out = ["### 网络"]
    nodes = net["nodes"]
    paths = net["paths"]
    if nodes:
        out.append("| 节点 | 类型 | 坐标 |")
        out.append("|---|---|---|")
        for n in nodes.values():
            out.append(f"| `{n['name']}` | `{n['type']}` | ({n['x']}, {n['y']}) |")
    if paths:
        out.append("| 路径 | 连接 | 类型 |")
        out.append("|---|---|---|")
        for p in paths:
            out.append(f"| `{p['name']}` | `{p['src']}` ↔ `{p['dst']}` | `{p['type']}` |")
    return "\n".join(out)


def render_agent_section(section, agent):
    """渲染单个 section 的 markdown"""
    if section == "class":
        return render_class(agent["class"])
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
    if section == "network":
        return render_network(agent["network"])
    return ""


def render_agent(agent) -> str:
    """渲染一个 agent 的完整 markdown"""
    name = agent["class"]["name"]
    out = [f"## {name}", ""]
    out.append(render_class(agent["class"]))
    out.append(render_variables(agent["variables"], agent["params"], agent["collections"]))
    out.append(render_functions(agent["functions"]))
    out.append(render_embedded(agent["embedded"]))
    out.append(render_connectors(agent["connectors"]))
    out.append(render_statechart(agent["statechart"], agent["transitions"], agent["initials"]))
    out.append(render_events(agent["events"]))
    out.append(render_network(agent["network"]))
    return "\n\n".join([x for x in out if x.strip()])


# =====================================================================
# 主流程
# =====================================================================

def discover_agents(model_dir):
    """在模型目录下发现所有 agent 目录"""
    alp_dir = os.path.join(model_dir, "_alp")
    if not os.path.isdir(alp_dir):
        # 兼容：模型目录本身就是 _alp 的父级
        alp_dir = model_dir
    agents_dir = os.path.join(alp_dir, "Agents")
    agents = []
    if os.path.isdir(agents_dir):
        for name in sorted(os.listdir(agents_dir)):
            d = os.path.join(agents_dir, name)
            if os.path.isdir(d) and any(f.startswith("AOC.") for f in os.listdir(d)):
                agents.append(d)
    return agents


def render_toc(model_dir, agents) -> str:
    """渲染模型结构总览"""
    alp_dir = os.path.join(model_dir, "_alp")
    out = [f"# {os.path.basename(model_dir.rstrip('/'))} — 模型结构总览", ""]
    out.append("> 来源: `_alp/` 目录  |  用 `--agent <名称>` 查看详情")
    out.append("")
    out.append("## Agents")
    for d in agents:
        aoc = [f for f in os.listdir(d) if f.startswith("AOC.")]
        aoc_path = os.path.join(d, aoc[0]) if aoc else None
        info = parse_class(aoc_path) if aoc_path else {"name": "?", "usage": ""}
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
    parser = argparse.ArgumentParser(description="AnyLogic XML → 语义化 Markdown")
    parser.add_argument("model_dir", help="模型目录（含 _alp/）")
    parser.add_argument("--toc", action="store_true", help="只输出结构总览")
    parser.add_argument("--agent", help="只解析指定 agent")
    parser.add_argument("--section", help="只输出某部分（class|variables|functions|embedded|connectors|statechart|events|network）")
    parser.add_argument("--output", help="写入文件（生成持久参考文档）")
    parser.add_argument("--quiet", action="store_true", help="不打印进度")
    args = parser.parse_args()

    model_dir = args.model_dir
    if not os.path.isdir(model_dir):
        print(f"❌ 目录不存在: {model_dir}", file=sys.stderr)
        sys.exit(1)

    agents = discover_agents(model_dir)
    if not agents:
        print(f"❌ 未在 {model_dir}/_alp/Agents/ 下发现任何 agent（需含 AOC.*.xml）", file=sys.stderr)
        sys.exit(1)

    if args.toc:
        print(render_toc(model_dir, agents))
        return

    if args.agent:
        # 定位 agent 目录
        target = None
        for d in agents:
            aoc = [f for f in os.listdir(d) if f.startswith("AOC.")]
            aoc_path = os.path.join(d, aoc[0]) if aoc else None
            info = parse_class(aoc_path) if aoc_path else {"name": ""}
            if info["name"].lower() == args.agent.lower() or os.path.basename(d).lower() == args.agent.lower():
                target = d
                break
        if target is None:
            available = ", ".join(sorted(
                parse_class(os.path.join(d, [f for f in os.listdir(d) if f.startswith('AOC.')][0]))["name"]
                for d in agents if [f for f in os.listdir(d) if f.startswith('AOC.')]))
            print(f"❌ 未找到 agent `{args.agent}`。可用: {available}", file=sys.stderr)
            sys.exit(1)
        agent = parse_agent(target)
        if args.section:
            md = render_agent_section(args.section, agent)
        else:
            md = render_agent(agent)
    else:
        # 全部 agent
        sections = []
        for d in agents:
            agent = parse_agent(d)
            sections.append(render_agent(agent))
        header = [f"# {os.path.basename(model_dir.rstrip('/'))} — 模型语义参考",
                  f"> 自动生成: {datetime.date.today().isoformat()}  |  来源: `_alp/` 目录  |  剥离了 Id/坐标/UI 配置等噪音"]
        md = "\n\n".join(header + sections)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(md + "\n")
        if not args.quiet:
            print(f"✅ 已写入 {args.output}")
    else:
        print(md)


if __name__ == "__main__":
    main()
