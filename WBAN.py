

"""
WBAN.py

Nimmt eine JSON-Datei (global_parameters, nodes, connections, optional queries)
und erzeugt:
  - WBAN.profeat  (ProFeat-Grundmodell embedded im Skript; nur Konstanten werden ersetzt)
  - WBAN.ctl      (Storm properties aus "queries")
  - WBAN.prism    (Übersetztes PRISM-Modell)
  - results.json  (Ausgabe der Ergebnisse)

Usage:
  python WBAN.py --input model.json
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Set, Sequence, Union, Iterable
import subprocess
import re
from decimal import Decimal
from datetime import datetime
import matplotlib.pyplot as plt
import math
from switss.model import MDP, ReachabilityForm
from switss.problem import QSHeur, MILPExact
import inspect

WORKDIR_HOST_FS = r"C:\Users\linus\profeat\wban_work"
WORKDIR_DOCKER_MOUNT = r"C:\Users\linus\profeat\wban_work"
CONTAINER_DIR = "/data"
PROFEAT_DOCKER_IMAGE = "profeat"
STORM_DOCKER_IMAGE = "movesrwth/storm:stable"
PRISM_CMD = [r"C:\Program Files\prism-4.9\bin\prism.bat"]



_STUTTER_RE = re.compile(
    r"""^\s*
        \[([^\]]+)\]
        \s+!(_[A-Za-z0-9_]+_active)
        \s*->\s*true;\s*$
    """,
    re.VERBOSE,
)
_MODULE_START_RE = re.compile(r"^\s*module\b")
_MODULE_END_RE = re.compile(r"^\s*endmodule\b")

_RE_STORM_VERSION = re.compile(r"^\s*Storm\s+([0-9]+\.[0-9]+\.[0-9]+)\s*$", re.MULTILINE)
_RE_DATE_LINE = re.compile(r"^\s*Date:\s*(.+)\s*$", re.MULTILINE)
_RE_CMDLINE = re.compile(r"^\s*Command line arguments:\s*(.+)\s*$", re.MULTILINE)

_RE_TIME_INPUT = re.compile(r"^\s*Time for model input parsing:\s*([0-9]*\.?[0-9]+)s\.\s*$", re.MULTILINE)
_RE_TIME_CONSTR = re.compile(r"^\s*Time for model construction:\s*([0-9]*\.?[0-9]+)s\.\s*$", re.MULTILINE)

_RE_MODEL_TYPE = re.compile(r"^\s*Model type:\s*(.+)\s*$", re.MULTILINE)
_RE_STATES = re.compile(r"^\s*States:\s*([0-9]+)\s*$", re.MULTILINE)
_RE_TRANSITIONS = re.compile(r"^\s*Transitions:\s*([0-9]+)\s*$", re.MULTILINE)
_RE_CHOICES = re.compile(r"^\s*Choices:\s*([0-9]+)\s*$", re.MULTILINE)

_RE_PROP_START = re.compile(r'^\s*Model checking property\s+"(\d+)"\s*:\s*(.+?)\s*\.\.\.\s*$', re.MULTILINE)
_RE_RESULT = re.compile(r"^\s*Result\s*\(for initial states\)\s*:\s*([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)\s*$",
                        re.MULTILINE)
_RE_TIME_CHECK = re.compile(r"^\s*Time for model checking:\s*([0-9]*\.?[0-9]+)s\.\s*$", re.MULTILINE)

_RE_PARETO_HEADER = re.compile(r"^\s*\d+\s+Pareto optimal points found:\s*$", re.MULTILINE)
_RE_PARETO_POINT = re.compile(
    r"^\s*\(\s*([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)\s*,\s*([+-]?[0-9]*\.?[0-9]+(?:[eE][+-]?[0-9]+)?)\s*\)\s*$",
    re.MULTILINE
)

_REWARD_START_RE = re.compile(r'^\s*rewards\s+"([^"]+)"\s*$', re.MULTILINE)
_LABEL_DEF_RE = re.compile(r'^\s*label\s+"([^"]+)"\s*=', re.MULTILINE)

LABEL_KINDS = {"buffer_full", "batt_empty", "sensor_failure", "node_failed"}
REWARD_BASES = {
    "energy_used",
    "batt_empty_time",
    "sensor_failed_time",
    "successful_transmissions",
    "generated_data"
}

PROFEAT_PREFIX = """//Modell, bei dem Sensoren in source, relay und target Sensoren unterteilt werden
//Dabei kommen bei der numerischen Auflistung immer zuerst die sources, dann die relays und dann die targets
//Alle Funktionen sind in einzelne Features modular verpackt
//Die Features energy und energy_mode sind auf sources und relays begrenzt
"""


PROFEAT_BODY = r"""
formula linkname(sensor1, sensor2) = (sensor1+1)*pow(10, ceil(log(10,num_sources+num_relay+num_targets+1)))+ sensor2+1;
formula sensor_fail (batt) = p_fail*(4-batt);
formula dynamic_p_fail (batt, mode) = p_fail*(4-batt)/mode;
formula last_buffer (sens) = for i in [0..num_sources+num_relay-1] (sens=i?data[i].buffer[buffer_size[i]-1]:0) + ... endfor + (sens=num_sources+num_relay?-1:0);
formula first_buffer(sens) = for i in [0..num_sources+num_relay-1] (sens=i?data[i].buffer[0]:0) + ... endfor + (sens=num_sources+num_relay?-1:0);
formula buffer_count(sens) = for i in [0..buffer_size[sens]-1] (data[sens].buffer[i]!=-1?1:0) + ... endfor;
formula buffer_free(sens, connection) = for i in [0..num_sources+num_relay-1] (sens=i?for j in [0..buffer_size[i]-1] (data[i].buffer[j]=-1?1:0) + ... endfor:0) + ... endfor + (sens=num_sources+num_relay?max(num_packets[sender[connection]], buffer_count(sender[connection])):0);
formula sending_energy(sens) = energy_consumption_connection[sens] * (sensor_mode(sens)=2?power_energy_increase[sens]:1);
formula idle_energy(sens) = energy_consumption_idle[sens] * (sensor_mode(sens)=2?power_energy_increase[sens]:1);
formula idle_fail(sens) = p_fail_idle[sens]/ (sensor_mode(sens)=2?power_fail_decrease[sens]:1) * (sensor_batt(sens)=1?low_energy_factor[sens]:1);
formula sending_fail(sens) = p_fail[sens]/ (sensor_mode(sens)=2?power_fail_decrease[sens]:1) * (sensor_batt(sens)=1?low_energy_factor[sens]:1);
formula sensor_mode(sens) = for i in [0..num_sources+num_relay-1] (sens=i?energy_mode[i].mode:0) + ... endfor + (sens>num_sources+num_relay-1?1:0);
formula sensor_batt(sens) = for i in [0..num_sources+num_relay-1] (sens=i?energy[i].batt:0) + ... endfor + (sens>num_sources+num_relay-1?2:0);
formula connection_fail(con) = connect_fail[con]/(sensor_mode(sender[con])=2?power_energy_increase[sender[con]]:1);

label "system_failure" = for i in [0..num_sources+num_relay-1] state[i].s=1 & ... endfor;
label "all_buffer_full" = for i in [0..num_sources+num_relay-1] data[i].buffer[buffer_size[i]-1]!=-1 & ... endfor;



root feature
	all of connectivity[num_sources+num_relay+num_targets], optional state[num_sources+num_relay+num_targets], data[num_sources+num_relay], optional energy[num_sources+num_relay], optional energy_mode[num_sources+num_relay];
	initial constraint for i in [0..num_sources+num_relay+num_targets-1] active(state[i])=state_feature[i] & ... endfor & for i in [0..num_sources+num_relay-1] active(energy[i])=energy_feature[i] & active(energy_mode[i])=(energy_feature[i]&energy_mode_feature[i]) & ... endfor;
	rewards "steps"
		true : 1;
	endrewards

	rewards "successful_transmissions"
		for i in [0..num_connections]
			[link[linkname(receiver[i], sender[i])]] receiver[i]>num_sources+num_relay-1 & state[receiver[i]].s!=1: 1-connection_fail(i);
		endfor
	endrewards

	rewards "lost_data"
		for i in [0..num_connections]
			[link[linkname(receiver[i], sender[i])]] true : (state[receiver[i]].s=1 | last_buffer(receiver[i])!=-1)?(1/retry_num):connection_fail(i);
		endfor
	endrewards
endfeature

feature state
	modules state_impl;
	
	rewards "sensor_failed_time"
		s=1:1;
	endrewards
endfeature


feature energy
	modules energy_impl;
	
	rewards "batt_empty_time"
		batt=0:1;
	endrewards

	rewards "energy_used"
		for i in [0..num_connections]
			[link[linkname(receiver[i], sender[i])]] state[id].s!=1 & energy[id].batt!=0 : (receiver[i]=id | sender[i]=id?sending_energy(id):idle_energy(id));
		endfor
		[generate_data] state[id].s!=1 & energy[id].batt!=0 : idle_energy(id);
	endrewards
endfeature

feature energy_mode
	modules energy_mode_impl;
endfeature


feature data
	modules data_impl;

	rewards "generated_data"
		[generate_data] state[id].s!=1 : generate_prob[id];
		for i in [0..num_connections]
			[link[linkname(receiver[i], sender[i])]] receiver[i]!=id & sender[i]!=id & state[id].s!=1 : generate_prob[id];
		endfor
	endrewards
		
	rewards "lost_data"
		[generate_data] state[id].s!=1 & data[id].buffer[buffer_size[id]-1]!=-1 : id>num_sources-1?0:generate_prob[id];
		for i in [0..num_connections]
			[link[linkname(receiver[i], sender[i])]] receiver[i]!=id & sender[i]!=id & state[id].s!=1 & data[id].buffer[buffer_size[id]-1]!=-1: id>num_sources-1?0:generate_prob[id];
		endfor
	endrewards
endfeature

feature connectivity
	modules connectivity_impl;
endfeature



module connectivity_impl
	for i in [0..num_connections]
		[link[id=sender[i]?linkname(receiver[i], sender[i]):-1]] id=sender[i] & state[id].s=0 & first_buffer(id)!=-1 & energy[sender[i]].batt!=0-> true;
		[link[id=receiver[i]?linkname(receiver[i], sender[i]):-1]] id=receiver[i] -> true;
	endfor
endmodule

//represents the battery for all sensors (except targets)
//decreases the energy dependent on the energy_mode of the sensor and the general energy consumption
//refills the energy with a constant probability if the battery is low or empty
module energy_impl
	batt : [0..2] init 2;

	for i in [0..num_connections]
		[link[linkname(receiver[i], sender[i])]] active(this) & batt > 0 -> ((id=receiver[i] | id=sender[i])?sending_energy(id):idle_energy(id)):(batt'=max(0, batt-1)) + (1-((id=receiver[i] | id=sender[i])?sending_energy(id):idle_energy(id))):(batt'=batt);
		[link[linkname(receiver[i], sender[i])]] active(this) & batt<2 -> energy_refill[id]:(batt'=2) + 1-energy_refill[id]:(batt'=batt);
	endfor
	[generate_data] active(this) & batt<2 -> energy_refill[id]:(batt'=2) + 1-energy_refill[id]:(batt'=batt);
	[generate_data] active(this) & batt=2 -> true;

endmodule


//implements an eco and power mode for all sensors (except targets) and switches nondeterministic between the modes
module energy_mode_impl
	mode : [1..2] init 1;
	for i in [0..num_connections]
		[link[linkname(receiver[i], sender[i])]] true -> (mode'=1);
		[link[linkname(receiver[i], sender[i])]] true -> (mode'=2);
	endfor
	[generate_data] true -> (mode'=1);
	[generate_data] true -> (mode'=2);
endmodule


//represents the state of each sensor
//s=0 is ready and s=1 is the fail state
//a sensor failing is dependent on the battery status, the energy mode and wether it is sending/receiving or not
module state_impl
	s : [0..1] init 0;
	for i in [0..num_connections]
		[link[linkname(receiver[i], sender[i])]] s=0 -> (((id=receiver[i]) | (id=sender[i]))?sending_fail(id):idle_fail(id)):(s'=1) + 1-((id=receiver[i] | id=sender[i])?sending_fail(id):idle_fail(id)):(s'=0);
		[link[linkname(receiver[i], sender[i])]] s=1 -> p_repair[id]:(s'=0) + 1-p_repair[id]:(s'=1);
	endfor
	[generate_data] s=0 -> idle_fail(id):(s'=1) + 1-idle_fail(id):(s'=0);
	[generate_data] s=1 -> p_repair[id]:(s'=0) + 1-p_repair[id]:(s'=1);
	//[repair_all] for i in [0..num_connections] (state[receiver[i]].s=1 | state[sender[i]].s=1) & ... endfor -> p_repair[id]:(s'=0) + 1-p_repair[id]:(s'=1);
endmodule


//represents the memory of all sensors (except targets)
//each sensor may have a different memory capacity
//only the sources can generate data, the generation happens parallel to connections if one is possible, otherwise it's an separate action
module data_impl
	buffer : array [0..buffer_size[id]-1] of [-1..data_size-1] init -1;

	[generate_data] (for i in [0..num_sources+num_relay-1] (data[i].buffer[0]=-1 | state[i].s=1 | energy[i].batt=0) & ... endfor) | generate_data_always -> (id>num_sources-1 | state[id].s=1?1:(1-generate_prob[id])):(buffer[0]'=buffer[0]) + for k in [0..data_size-1] (id>num_sources-1 | state[id].s=1?0:generate_prob[id]*(1/data_size)): for j in [1..buffer_size[id]-1] (buffer[j]'= (buffer[j]=-1 & buffer[j-1]!=-1)?k:buffer[j]) endfor & (buffer[0]'= buffer[0]=-1?k:buffer[0]) endfor;

	//sending and receiving for each connection
	for i in [0..num_connections]
		[link[id=sender[i]?linkname(receiver[i], sender[i]):-1]] id=sender[i] -> ((state[receiver[i]].s=1 | last_buffer(receiver[i])!=-1 | sensor_batt(receiver[i])=0)?(1/retry_num):1-connection_fail(i)):(buffer[buffer_size[id]-1]'=-1) & for j in [0..(buffer_size[id]-2)] (buffer[j]'=buffer[j+1]) endfor + ((state[receiver[i]].s=1 | last_buffer(receiver[i])!=-1 | sensor_batt(receiver[i])=0)?1-(1/retry_num):connection_fail(i)):(buffer[0]'=buffer[0]);

		[link[id=receiver[i]?linkname(receiver[i], sender[i]):-1]] id=receiver[i] -> (state[receiver[i]].s=1 | buffer[buffer_size[id]-1]!=-1)?1-(1/retry_num):(connection_fail(i)):(buffer[0]'=buffer[0]) + (state[receiver[i]].s=0 & buffer[buffer_size[id]-1]=-1)?(1-connection_fail(i)):(1/retry_num): for j in [1..buffer_size[id]-1] (buffer[j]'= (buffer[j]=-1 & buffer[j-1]!=-1)?data[sender[i]].buffer[0]:buffer[j]) endfor & (buffer[0]'= buffer[0]=-1?data[sender[i]].buffer[0]:buffer[0]);
	endfor

	//data generation parallel to each connection
	for i in [0..num_connections]
		[link[linkname(receiver[i], sender[i])]] id!=sender[i] & id!=receiver[i] -> (id>num_sources-1 | state[id].s=1?1:(1-generate_prob[id])):(buffer[0]'=buffer[0]) + for k in [0..data_size-1] (id>num_sources-1 | state[id].s=1?0:generate_prob[id]*(1/data_size)): for j in [1..buffer_size[id]-1] (buffer[j]'= (buffer[j]=-1 & buffer[j-1]!=-1)?k:buffer[j]) endfor & (buffer[0]'= buffer[0]=-1?k:buffer[0]) endfor;
	endfor

endmodule
""".lstrip("\n")


# Defaults (init values)
INIT_GLOBALS: Dict[str, Any] = {
    "retry_num": 1,
    "data_size": 1,
    "buffer_size": 1,
    "p_repair": 1.0,
    "p_fail_idle": 0.0,
    "p_fail": 0.0,
    "low_energy_factor": 2.0,
    "power_fail_decrease": 2.0,
    "power_energy_increase": 2.0,
    "energy_refill": 1.0,
    "energy_consumption_idle": 0.0,
    "energy_consumption_connection": 0.0,
    "num_packets": 1,
    "connect_fail": 0.0,
    "generate_prob": 0.0,
    "state_feature": True,
    "energy_feature": True,
    "energy_mode_feature": True,
    "generate_data_always": "false"
}

NODE_TYPES = {"source", "relay", "target"}


@dataclass(frozen=True)
class Node:
    name: str
    type: str
    params: Dict[str, Any]


@dataclass(frozen=True)
class Connection:
    sender: str
    receiver: str
    params: Dict[str, Any]


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def get_with_default(d: Dict[str, Any], key: str, default: Any) -> Any:
    return d[key] if key in d else default


def bool_to_profeat(b: bool) -> str:
    return "true" if b else "false"


def fmt_num(x: Any) -> str:
    if isinstance(x, bool):
        return bool_to_profeat(x)
    if isinstance(x, int):
        return str(x)
    if isinstance(x, float):
        d = Decimal(str(x))
        s = format(d, "f")
        return s
    return str(x)


def profeat_array(values: List[Any]) -> str:
    return "{%s}" % ", ".join(fmt_num(v) for v in values)


def parse_nodes(raw_nodes: List[Dict[str, Any]]) -> List[Node]:
    nodes: List[Node] = []
    names = set()
    for obj in raw_nodes:
        if "name" not in obj or "type" not in obj:
            raise ValueError("Each node must contain fields: name, type.")
        name = obj["name"]
        ntype = obj["type"]
        if ntype not in NODE_TYPES:
            raise ValueError(f'Node "{name}": invalid type "{ntype}".')
        if name in names:
            raise ValueError(f'Duplicate node name: "{name}".')
        names.add(name)
        params = {k: v for k, v in obj.items() if k not in ("name", "type")}
        nodes.append(Node(name=name, type=ntype, params=params))
    return nodes


def parse_connections(raw_conns: List[Dict[str, Any]], node_names: set) -> List[Connection]:
    conns: List[Connection] = []
    for i, obj in enumerate(raw_conns):
        if "sender" not in obj or "receiver" not in obj:
            raise ValueError("Each connection must contain fields: sender, receiver.")
        s = obj["sender"]
        r = obj["receiver"]
        if s not in node_names:
            raise ValueError(f'Connection {i}: sender "{s}" not found in nodes.')
        if r not in node_names:
            raise ValueError(f'Connection {i}: receiver "{r}" not found in nodes.')
        params = {k: v for k, v in obj.items() if k not in ("sender", "receiver")}
        conns.append(Connection(sender=s, receiver=r, params=params))
    return conns


def build_indexing(nodes: List[Node]) -> Tuple[List[Node], Dict[str, int]]:
    sources = [n for n in nodes if n.type == "source"]
    relays = [n for n in nodes if n.type == "relay"]
    targets = [n for n in nodes if n.type == "target"]
    ordered = sources + relays + targets
    name_to_idx = {n.name: i for i, n in enumerate(ordered)}
    return ordered, name_to_idx


def effective_param(global_params: Dict[str, Any], local_params: Dict[str, Any], key: str) -> Any:
    if key in local_params:
        return local_params[key]
    if key in global_params:
        return global_params[key]
    return INIT_GLOBALS[key]


def build_constants_block(
    global_params: Dict[str, Any],
    ordered_nodes: List[Node],
    conns: List[Connection],
    name_to_idx: Dict[str, int],
) -> str:

    """
    Builds the constants for the ProFeat model according to the input.
    """
    
    ns = sum(1 for n in ordered_nodes if n.type == "source")
    nr = sum(1 for n in ordered_nodes if n.type == "relay")
    nt = sum(1 for n in ordered_nodes if n.type == "target")

    if len(conns) < 1:
        raise ValueError("At least one connection is required.")
    num_connections = len(conns) - 1

    sender_idx = [name_to_idx[c.sender] for c in conns]
    receiver_idx = [name_to_idx[c.receiver] for c in conns]

    generate_prob = []
    connect_fail = []
    num_packets = []
    energy_cons_conn = []
    energy_cons_idle = []
    energy_refill = []
    power_energy_increase = []
    power_fail_decrease = []
    energy_feature = []
    energy_mode_feature = []
    state_feature = []
    low_energy_factor = []
    p_fail = []
    p_fail_idle = []
    p_repair = []
    buffer_size = []
    # Connection-level array
    for c in conns:
        connect_fail.append(effective_param(global_params, c.params, "connect_fail"))

    # Node-level arrays
    for n in ordered_nodes:
        generate_prob.append(effective_param(global_params, n.params, "generate_prob"))
        num_packets.append(effective_param(global_params, n.params, "num_packets"))
        energy_cons_conn.append(effective_param(global_params, n.params, "energy_consumption_connection"))
        energy_cons_idle.append(effective_param(global_params, n.params, "energy_consumption_idle"))
        energy_refill.append(effective_param(global_params, n.params, "energy_refill"))

        power_energy_increase.append(effective_param(global_params, n.params, "power_energy_increase"))
        power_fail_decrease.append(effective_param(global_params, n.params, "power_fail_decrease"))

        energy_feature.append(bool(effective_param(global_params, n.params, "energy_feature")))
        energy_mode_feature.append(bool(effective_param(global_params, n.params, "energy_mode_feature")))
        state_feature.append(bool(effective_param(global_params, n.params, "state_feature")))

        low_energy_factor.append(effective_param(global_params, n.params, "low_energy_factor"))
        p_fail.append(effective_param(global_params, n.params, "p_fail"))
        p_fail_idle.append(effective_param(global_params, n.params, "p_fail_idle"))
        p_repair.append(effective_param(global_params, n.params, "p_repair"))
        buffer_size.append(effective_param(global_params, n.params, "buffer_size"))

    retry_num = int(get_with_default(global_params, "retry_num", INIT_GLOBALS["retry_num"]))
    data_size = int(get_with_default(global_params, "data_size", INIT_GLOBALS["data_size"]))
    generate_data_always = str(get_with_default(global_params, "generate_data_always", INIT_GLOBALS["generate_data_always"]))

    lines = []
    lines.append(f"const int num_sources = {ns};")
    lines.append(f"const int num_relay = {nr};")
    lines.append(f"const int num_targets = {nt};")
    lines.append(f"const int num_connections = {num_connections}; //Anzahl minus 1, wegen 0")
    lines.append(f"const int sender = {profeat_array(sender_idx)}; //Nummer -1")
    lines.append(f"const int receiver = {profeat_array(receiver_idx)};")
    lines.append(f"const double generate_prob={profeat_array(generate_prob)};")
    lines.append(f"const double connect_fail={profeat_array(connect_fail)};")
    lines.append(f"const int num_packets={profeat_array(num_packets)};")
    lines.append(f"const double energy_consumption_connection={profeat_array(energy_cons_conn)};")
    lines.append(f"const double energy_consumption_idle={profeat_array(energy_cons_idle)};")
    lines.append(f"const double energy_refill={profeat_array(energy_refill)};")
    lines.append(f"const double power_energy_increase={profeat_array(power_energy_increase)};")
    lines.append(f"const double power_fail_decrease={profeat_array(power_fail_decrease)};")
    lines.append(f"const bool energy_feature = {profeat_array(energy_feature)};")
    lines.append(f"const bool energy_mode_feature = {profeat_array(energy_mode_feature)};")
    lines.append(f"const bool state_feature = {profeat_array(state_feature)};")
    lines.append(f"const double low_energy_factor = {profeat_array(low_energy_factor)};")
    lines.append(f"const double p_fail = {profeat_array(p_fail)};")
    lines.append(f"const double p_fail_idle = {profeat_array(p_fail_idle)};")
    lines.append(f"const double p_repair = {profeat_array(p_repair)};")
    lines.append(f"const int buffer_size = {profeat_array(buffer_size)};")
    lines.append(f"const int retry_num = {retry_num};")
    lines.append(f"const int data_size = {data_size};")
    lines.append(f"const bool generate_data_always = {generate_data_always};")

    return "\n".join(lines) + "\n"


def build_predicate_expr(pred: Dict[str, Any]) -> str:
    """
    Turns a structured predicate into a label name for a label in PRISM.
    """
    if "expr" in pred:
        return pred["expr"]

    if "node" in pred and pred.get("predicate") in LABEL_KINDS:
        node = pred["node"]
        kind = pred["predicate"]
        return f'"{kind}_{node}"'

    if "sensor_label" in pred:
        sl = pred["sensor_label"]
        node = sl["node"]
        kind = sl["kind"]
        if kind not in LABEL_KINDS:
            raise ValueError(f"Unsupported sensor label kind '{kind}'.")
        return f'"{kind}_{node}"'

    p = pred.get("predicate")
    if not p:
        raise ValueError("Predicate object must contain either 'predicate' or 'expr'.")

    if p in ("all_buffer_full", "system_failure"):
        return f'"{p}"'

    raise ValueError(f"Unsupported predicate '{p}'. Provide pred as {{\"expr\":\"...\"}} or extend mapping.")


def query_to_ctl_line(q: Dict[str, Any]) -> str:
    qtype = q.get("type")
    if not qtype:
        raise ValueError("Each query must have a 'type'.")

    opt = q.get("opt")  
    if opt is not None and opt not in ("min", "max"):
        raise ValueError(f"Query '{q.get('name','?')}': opt must be 'min' or 'max'.")

    if qtype == "reachability_probability":
        target = build_predicate_expr(q["target"])
        prefix = "P"
        suffix = "=?"
        if opt:
            prefix = f"P{opt}"
        return f'{prefix}{suffix} [ F {target} ];'

    if qtype == "bounded_reachability_probability":
        target = build_predicate_expr(q["target"])
        k = int(q["bound_steps"])
        prefix = "P"
        suffix = "=?"
        if opt:
            prefix = f"P{opt}"
        return f'{prefix}{suffix} [ F<={k} {target} ];'

    if qtype == "expected_reward_until":
        reward = _reward_name_for_ctl(q["reward"])
        target = build_predicate_expr(q["until"])
        prefix = f'R{{"{reward}"}}'
        if opt:
            prefix += opt
        return f'{prefix}=? [ F {target} ];'

    if qtype == "lra_reward":
        reward = _reward_name_for_ctl(q["reward"])
        prefix = f'R{{"{reward}"}}'
        if opt:
            prefix += opt
        return f"{prefix}=? [ LRA ];"

    if qtype == "pareto":
        objs = []
        for obj in q["objectives"]:
            otype = obj["type"]
            oopt = obj.get("opt")
            if otype != "lra_reward":
                raise ValueError("Currently pareto supports only objectives of type 'lra_reward'.")
            rname = _reward_name_for_ctl(obj["reward"])
            part = f'R{{"{rname}"}}'
            if oopt:
                part += oopt
            part += "=? [ LRA ]"
            objs.append(part)
        return "multi(" + ", ".join(objs) + ");"

    raise ValueError(f"Unsupported query type '{qtype}'.")

def _reward_name_for_ctl(reward_obj: Dict[str, Any]) -> str:
    base = reward_obj["name"]
    node = reward_obj.get("node")
    if node:
        return f"{base}_{node}"
    return base


def write_ctl(queries: List[Dict[str, Any]], out_path: Path) -> None:
    lines = []
    for q in queries:
        name = q.get("name")
        if name:
            lines.append(f"// {name}")
        lines.append(query_to_ctl_line(q))
        lines.append("")  # blank line
    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def ensure_workspace_dir() -> Path:
    """
    Ensures the internal workspace directory exists and returns it as a Path.
    """
    wd = Path(WORKDIR_HOST_FS)
    wd.mkdir(parents=True, exist_ok=True)
    return wd


def run_profeat_translation(workdir: Path, profeat_name: str = "WBAN.profeat", prism_name: str = "WBAN.prism") -> Path:
    """
    Runs ProFeat via Docker to translate `workdir/profeat_name` -> `workdir/prism_name`.
    Returns the Path to the generated PRISM file on the host.
    """
    profeat_host = workdir / profeat_name
    prism_host = workdir / prism_name

    if not profeat_host.exists():
        raise FileNotFoundError(f"Missing ProFeat input file: {profeat_host}")

    profeat_in_container = f"{CONTAINER_DIR}/{profeat_name}"
    prism_out_container = f"{CONTAINER_DIR}/{prism_name}"

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{WORKDIR_DOCKER_MOUNT}:{CONTAINER_DIR}",
        PROFEAT_DOCKER_IMAGE,
        profeat_in_container,
        "-t",
        "-o", prism_out_container,
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(
            "ProFeat translation (docker) failed.\n"
            f"Command: {' '.join(cmd)}\n"
            f"STDOUT:\n{res.stdout}\n"
            f"STDERR:\n{res.stderr}\n"
        )

    if not prism_host.exists():
        raise RuntimeError(
            "ProFeat docker run succeeded but output PRISM file was not created.\n"
            f"Expected: {prism_host}\n"
            f"Command: {' '.join(cmd)}\n"
            f"STDOUT:\n{res.stdout}\n"
            f"STDERR:\n{res.stderr}\n"
        )

    return prism_host

def cleanup_duplicate_feature_commands(prism_path: Path) -> None:
    """
    Removes duplicate feature-disabled commands of the form:
        [label] !_feature_active -> true;
    within each module of the PRISM file.
    Works in-place. No return value.
    """
    lines = prism_path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)

    out_lines = []
    in_module = False
    seen_keys = set()

    for line in lines:
        if _MODULE_START_RE.match(line):
            in_module = True
            seen_keys.clear()
            out_lines.append(line)
            continue

        if _MODULE_END_RE.match(line):
            in_module = False
            seen_keys.clear()
            out_lines.append(line)
            continue

        if in_module:
            m = _STUTTER_RE.match(line)
            if m:
                label = m.group(1).strip()
                feature_flag = m.group(2).strip()
                key = (label, feature_flag)

                if key in seen_keys:
                    continue

                seen_keys.add(key)

        out_lines.append(line)

    prism_path.write_text("".join(out_lines), encoding="utf-8")


def run_storm_model_checking(
    workdir: Path,
    prism_name: str = "WBAN.prism",
    prop_name: str = "WBAN.ctl",
    *,
    extra_args: Optional[list[str]] = None,
    timeout_sec: Optional[int] = None,
    export_scheduler_cfg: Optional[dict] = None,   # <-- NEU
) -> str:
    """
    Runs Storm via Docker on the given PRISM model + property file (both expected in `workdir`).
    Returns Storm's stdout as a string.
    """
    prism_host = workdir / prism_name
    prop_host = workdir / prop_name

    if not prism_host.exists():
        raise FileNotFoundError(f"Missing PRISM model for Storm: {prism_host}")
    if not prop_host.exists():
        raise FileNotFoundError(f"Missing property file for Storm: {prop_host}")

    prism_in_container = f"{CONTAINER_DIR}/{prism_name}"
    prop_in_container = f"{CONTAINER_DIR}/{prop_name}"

    cmd = [
        "docker", "run", "--rm",
        "-v", f"{WORKDIR_DOCKER_MOUNT}:{CONTAINER_DIR}",
        STORM_DOCKER_IMAGE,
        "storm",
        "--prism", prism_in_container,
        "--prop", prop_in_container,
    ]

    if export_scheduler_cfg is not None:
        export_path = export_scheduler_cfg.get("path", "scheduler.json")

        export_path_container = f"{CONTAINER_DIR}/{export_path}"


        cmd.extend(["--exportscheduler", export_path_container])

        if export_scheduler_cfg.get("build_state_valuations", False):
            cmd.append("--buildstateval")

        if export_scheduler_cfg.get("build_choice_origins", False):
            cmd.append("--buildchoiceorig")

        if "path" not in export_scheduler_cfg:
            raise ValueError("export_scheduler requires a 'path' field.")

    if extra_args:
        cmd.extend(extra_args)

    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )

    if res.returncode != 0:
        raise RuntimeError(
            "Storm model checking (docker) failed.\n"
            f"Command: {' '.join(cmd)}\n"
            f"STDOUT:\n{res.stdout}\n"
            f"STDERR:\n{res.stderr}\n"
        )

    return res.stdout

@dataclass
class StormPropertyResult:
    index: int
    property_str: str
    value: Optional[float] = None
    time_check_s: Optional[float] = None
    name: Optional[str] = None  # from your input queries, if available
    qtype: Optional[str] = None
    opt: Optional[str] = None


def _first_match_float(rx: re.Pattern, text: str) -> Optional[float]:
    m = rx.search(text)
    return float(m.group(1)) if m else None


def _first_match_int(rx: re.Pattern, text: str) -> Optional[int]:
    m = rx.search(text)
    return int(m.group(1)) if m else None


def parse_storm_output(storm_out: str) -> Dict[str, Any]:
    """
    Parse Storm's stdout into a structured dict.
    Extracts:
      - meta: version, date line, command line
      - statistics: model type, states, transitions, choices, input parsing time, construction time
      - properties: list of property results (index, property_str, value, checking time)
      - pareto_points: list of (x,y) if present (from Storm's pareto output)
    """
    meta: Dict[str, Any] = {
        "storm_version": None,
        "storm_date_line": None,
        "command_line": None,
        "parsed_at_iso": datetime.now().isoformat(timespec="seconds"),
    }

    m = _RE_STORM_VERSION.search(storm_out)
    if m:
        meta["storm_version"] = m.group(1)

    m = _RE_DATE_LINE.search(storm_out)
    if m:
        meta["storm_date_line"] = m.group(1).strip()

    m = _RE_CMDLINE.search(storm_out)
    if m:
        meta["command_line"] = m.group(1).strip()

    statistics: Dict[str, Any] = {
        "model_type": None,
        "states": None,
        "transitions": None,
        "choices": None,
        "time_input_parsing_s": _first_match_float(_RE_TIME_INPUT, storm_out),
        "time_model_construction_s": _first_match_float(_RE_TIME_CONSTR, storm_out),
    }

    mt = _RE_MODEL_TYPE.search(storm_out)
    if mt:
        statistics["model_type"] = mt.group(1).strip()

    statistics["states"] = _first_match_int(_RE_STATES, storm_out)
    statistics["transitions"] = _first_match_int(_RE_TRANSITIONS, storm_out)
    statistics["choices"] = _first_match_int(_RE_CHOICES, storm_out)

    prop_headers = list(_RE_PROP_START.finditer(storm_out))

    properties: List[StormPropertyResult] = []
    pareto_sets: List[Dict[str, Any]] = []
    pareto_points_agg: List[Tuple[float, float]] = []

    for i, ph in enumerate(prop_headers):
        idx = int(ph.group(1))
        prop_str = ph.group(2).strip()

        start = ph.end()
        end = prop_headers[i + 1].start() if i + 1 < len(prop_headers) else len(storm_out)
        block = storm_out[start:end]

        val = _first_match_float(_RE_RESULT, block)
        tcheck = _first_match_float(_RE_TIME_CHECK, block)

        properties.append(
            StormPropertyResult(index=idx, property_str=prop_str, value=val, time_check_s=tcheck)
        )

        if _RE_PARETO_HEADER.search(block):
            pts: List[Tuple[float, float]] = []
            for pm in _RE_PARETO_POINT.finditer(block):
                x = float(pm.group(1))
                y = float(pm.group(2))
                pts.append((x, y))
                pareto_points_agg.append((x, y))

            pareto_sets.append(
                {
                    "property_index": idx,
                    "property_str": prop_str,
                    "points": pts,
                }
            )

    return {
        "meta": meta,
        "statistics": statistics,
        "properties": [p.__dict__ for p in properties],
        "pareto_sets": pareto_sets,
        "pareto_points": pareto_points_agg,
    }


def attach_query_metadata(parsed: Dict[str, Any], queries: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Attach name/type/opt from the input query list to the parsed property results by index order.
    """
    props = parsed.get("properties", [])
    for i, p in enumerate(props):
        if i >= len(queries):
            break
        q = queries[i]
        p["name"] = q.get("name")
        p["qtype"] = q.get("type")
        p["opt"] = q.get("opt")
    return parsed


def format_console_report(parsed: Dict[str, Any]) -> str:
    """
    Create a clean, readable console report.
    Output format:
      === Modellstatistik ===
      ...
      === Analyseergebnisse ===
      [Q1] ...
    """
    stat = parsed.get("statistics", {})
    props = parsed.get("properties", [])

    def fmt_int(x: Any) -> str:
        return str(x) if isinstance(x, int) else "n/a"

    def fmt_time(x: Any) -> str:
        if isinstance(x, (int, float)):
            # keep short but stable
            return f"{x:.3f} s"
        return "n/a"

    def fmt_val(x: Any) -> str:
        if isinstance(x, (int, float)):
            return f"{x:.10g}"
        return "n/a"

    lines: List[str] = []
    lines.append("=== Modellstatistik ===")
    if stat.get("model_type"):
        lines.append(f"Typ:          {stat['model_type']}")
    lines.append(f"States:       {fmt_int(stat.get('states'))}")
    lines.append(f"Transitions:  {fmt_int(stat.get('transitions'))}")
    lines.append(f"Choices:      {fmt_int(stat.get('choices'))}")
    lines.append(f"Parse time:   {fmt_time(stat.get('time_input_parsing_s'))}")
    lines.append(f"Build time:   {fmt_time(stat.get('time_model_construction_s'))}")
    lines.append("")
    lines.append("=== Analyseergebnisse ===")
    lines.append("")

    for p in props:
        idx = p.get("index")
        pname = p.get("name")
        prop_str = p.get("property_str", "")
        qtype = p.get("qtype")
        opt = p.get("opt")
        val = p.get("value")
        tcheck = p.get("time_check_s")

        header = f"[Q{idx}]"
        if pname:
            header += f" {pname}"
        lines.append(header)

        if qtype:
            desc = qtype
            if opt:
                desc += f" ({opt})"
            lines.append(f"     Typ: {desc}")
            lines.append(f"     Property: {prop_str}")
        else:
            lines.append(f"     {prop_str}")

        lines.append(f"     Wert: {fmt_val(val)}")
        if tcheck is not None:
            lines.append(f"     Check time: {fmt_time(tcheck)}")
        lines.append("")

    pareto_pts = parsed.get("pareto_points") or []
    if pareto_pts:
        lines.append(f"Pareto-Punkte erkannt: {len(pareto_pts)}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_results_json(
    parsed: Dict[str, Any],
    queries: List[Dict[str, Any]],
    out_path: Path,
    *,
    include_raw_stdout: bool = False,
    raw_stdout: Optional[str] = None,
) -> None:
    """
    Write a structured results.json file.
    Includes:
      - meta/statistics/properties (+ attached query metadata)
      - original queries (for reproducibility)
      - raw Storm stdout (useful for debugging)
    """
    payload: Dict[str, Any] = {
        "meta": parsed.get("meta", {}),
        "statistics": parsed.get("statistics", {}),
        "properties": parsed.get("properties", []),
        "pareto_points": parsed.get("pareto_points", []),
        "pareto_sets": parsed.get("pareto_sets", []),
        "queries": queries,
    }
    if include_raw_stdout:
        payload["raw_stdout"] = raw_stdout if raw_stdout is not None else ""

    out_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

def _obj_label(obj: Dict[str, Any], fallback: str) -> str:
    otype = obj.get("type", "")
    opt = obj.get("opt", "")
    r = obj.get("reward", {}) or {}
    rname = r.get("name")
    if rname:
        if otype == "lra_reward" and opt:
            return f'LRA R{{"{rname}"}}{opt}'
        if otype == "lra_reward":
            return f'LRA R{{"{rname}"}}'
        return f'{otype}("{rname}")'
    return fallback


def _infer_axis_labels_for_pareto_query(pareto_q: Dict[str, Any]) -> Tuple[str, str, str]:
    """
    Returns: (title, xlabel, ylabel) from one pareto-query object.
    """
    title = pareto_q.get("name") or "Pareto-Front"
    objs = pareto_q.get("objectives", []) or []
    xlabel = _obj_label(objs[0], "Objective 1") if len(objs) >= 1 else "Objective 1"
    ylabel = _obj_label(objs[1], "Objective 2") if len(objs) >= 2 else "Objective 2"
    return title, xlabel, ylabel


def plot_pareto_fronts_png(
    parsed: Dict[str, Any],
    queries: List[Dict[str, Any]],
    out_dir: Path,
    *,
    filename_prefix: str = "pareto",
    connect_points: bool = True,
    sort_by_x: bool = True,
) -> List[Path]:
    """
    Creates one PNG per Pareto query.
    Returns:
      List of created PNG paths. Empty list if no pareto points exist.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    created: List[Path] = []

    pareto_queries = [q for q in queries if q.get("type") == "pareto"]

    pareto_sets = parsed.get("pareto_sets")
    if isinstance(pareto_sets, list) and pareto_sets:
        for j, ps in enumerate(pareto_sets):
            pts = ps.get("points") or []
            if not pts:
                continue
            pq = pareto_queries[j] if j < len(pareto_queries) else {"name": "Pareto-Front", "objectives": []}
            title, xlabel, ylabel = _infer_axis_labels_for_pareto_query(pq)
            prop_idx = ps.get("property_index")
            if isinstance(prop_idx, int):
                fname = f"{filename_prefix}_Q{prop_idx}.png"
            else:
                fname = f"{filename_prefix}_{j+1}.png"

            out_path = out_dir / fname

            points: List[Tuple[float, float]] = [(float(x), float(y)) for (x, y) in pts]
            if sort_by_x:
                points.sort(key=lambda t: t[0])

            xs = [p[0] for p in points]
            ys = [p[1] for p in points]

            plt.figure()
            plt.scatter(xs, ys, label="Pareto points")
            if connect_points and len(points) >= 2:
                plt.plot(xs, ys, linewidth=1)

            plt.title(title)
            plt.xlabel(xlabel)
            plt.ylabel(ylabel)
            plt.grid(True, linestyle="--", linewidth=0.5, alpha=0.6)
            plt.tight_layout()
            plt.savefig(out_path, dpi=300)
            plt.close()

            created.append(out_path)

        return created

    pts = parsed.get("pareto_points") or []
    if not pts:
        return []
    pq = pareto_queries[0] if pareto_queries else {"name": "Pareto-Front", "objectives": []}
    title, xlabel, ylabel = _infer_axis_labels_for_pareto_query(pq)

    out_path = out_dir / f"{filename_prefix}.png"

    points2: List[Tuple[float, float]] = [(float(x), float(y)) for (x, y) in pts]
    if sort_by_x:
        points2.sort(key=lambda t: t[0])

    xs = [p[0] for p in points2]
    ys = [p[1] for p in points2]

    plt.figure()
    plt.scatter(xs, ys, label="Pareto points")
    if connect_points and len(points2) >= 2:
        plt.plot(xs, ys, linewidth=1)

    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.grid(True, linestyle="--", linewidth=0.5, alpha=0.6)
    plt.tight_layout()
    plt.savefig(out_path, dpi=300)
    plt.close()

    created.append(out_path)
    return created

def _link_width(num_nodes: int) -> int:
    return max(1, int(math.ceil(math.log10(num_nodes + 1))))

def make_link_action_label(receiver_idx: int, sender_idx: int, num_nodes: int) -> str:
    w = _link_width(num_nodes)
    label_num = (receiver_idx + 1) * (10 ** w) + (sender_idx + 1)
    return f"link_{label_num}"

@dataclass(frozen=True)
class SensorLabelRef:
    node: str
    kind: str

@dataclass(frozen=True)
class SensorRewardRef:
    node: str
    base: str

def _collect_predicate_refs(pred: Dict[str, Any], out_labels: Set[SensorLabelRef]) -> None:
    """
    Collect sensor_label references inside a predicate object.
    """
    if not isinstance(pred, dict):
        return

    if "node" in pred and pred.get("predicate") in LABEL_KINDS:
        out_labels.add(SensorLabelRef(node=pred["node"], kind=pred["predicate"]))
        return
    
    sl = pred.get("sensor_label")
    if sl:
        node = sl["node"]
        kind = sl["kind"]
        if kind not in LABEL_KINDS:
            raise ValueError(f"Unsupported sensor label kind '{kind}'.")
        out_labels.add(SensorLabelRef(node=node, kind=kind))

def _collect_reward_refs(rew: Dict[str, Any], out_rewards: Set[SensorRewardRef]) -> None:
    """
    Collect sensor-specific reward references.
    """
    if not isinstance(rew, dict):
        return
    base = rew.get("name")
    node = rew.get("node")
    if base and node:
        if base not in REWARD_BASES:
            raise ValueError(f"Unsupported reward base '{base}'.")
        out_rewards.add(SensorRewardRef(node=node, base=base))

def collect_needed_sensors_from_spec(
    queries: List[Dict[str, Any]],
    switss_spec: Optional[Dict[str, Any]] = None,
) -> Tuple[Set[SensorLabelRef], Set[SensorRewardRef]]:
    """
    Walks through queries and optional switss spec, collecting all referenced
    sensor labels and sensor rewards that must be materialized in the PRISM model.
    """
    needed_labels: Set[SensorLabelRef] = set()
    needed_rewards: Set[SensorRewardRef] = set()

    for q in queries:
        if "target" in q:
            _collect_predicate_refs(q["target"], needed_labels)
        if "until" in q:
            _collect_predicate_refs(q["until"], needed_labels)
        if "reward" in q:
            _collect_reward_refs(q["reward"], needed_rewards)
        if q.get("type") == "pareto":
            for obj in q.get("objectives", []):
                if "reward" in obj:
                    _collect_reward_refs(obj["reward"], needed_rewards)
    if switss_spec:
        if "target" in switss_spec:
            _collect_predicate_refs(switss_spec["target"], needed_labels)
        if "fail" in switss_spec:
            _collect_predicate_refs(switss_spec["fail"], needed_labels)

    return needed_labels, needed_rewards

def run_prism_export_explicit(
    workdir: Path,
    prism_model_name: str = "WBAN.prism",
    export_basename: str = "WBAN",
    *,
    export_parts: str = "tra,lab,sta",
    prism_cmd: Optional[list[str]] = None,
    timeout_sec: Optional[int] = None,
) -> tuple[Path, Path, Optional[Path]]:
    """
    Runs PRISM to export an explicit model representation from a .prism model.
    Returns:
        (tra_path, lab_path, sta_path_or_none)
    """
    prism_cmd = prism_cmd or PRISM_CMD

    model_path = workdir / prism_model_name
    if not model_path.exists():
        raise FileNotFoundError(f"Missing PRISM model file: {model_path}")

    export_arg = f"{export_basename}.{export_parts}"

    cmd = [*prism_cmd, str(model_path), "-exportmodel", export_arg]

    res = subprocess.run(
        cmd,
        cwd=str(workdir),
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    if res.returncode != 0:
        raise RuntimeError(
            "PRISM exportmodel failed.\n"
            f"Command: {' '.join(cmd)}\n"
            f"CWD: {workdir}\n"
            f"STDOUT:\n{res.stdout}\n"
            f"STDERR:\n{res.stderr}\n"
        )

    tra_path = workdir / f"{export_basename}.tra"
    lab_path = workdir / f"{export_basename}.lab"
    sta_path = workdir / f"{export_basename}.sta"

    if not tra_path.exists() or not lab_path.exists():
        raise RuntimeError(
            "PRISM exportmodel succeeded but .tra/.lab not found.\n"
            f"Expected: {tra_path} and {lab_path}\n"
            f"STDOUT:\n{res.stdout}\n"
            f"STDERR:\n{res.stderr}\n"
        )
    return tra_path, lab_path, (sta_path if sta_path.exists() else None)

def ensure_sensor_labels(
    prism_path: Path,
    needed_labels: Set[SensorLabelRef],
    name_to_idx: Dict[str, int],
    ordered_nodes: List[Node],
    global_params: Dict[str, Any],
) -> None:
    """
    Ensures all requested sensor labels exist in the PRISM file.
    Adds missing ones at the end of the file.
    """
    if not needed_labels:
        return

    text = prism_path.read_text(encoding="utf-8", errors="replace")
    existing_labels: Set[str] = {m.group(1) for m in _LABEL_DEF_RE.finditer(text)}

    to_append: List[Tuple[str, str]] = []

    for ref in sorted(needed_labels, key=lambda x: (x.node, x.kind)):
        if ref.node not in name_to_idx:
            raise ValueError(f"Unknown node '{ref.node}' in sensor label reference.")
        idx = name_to_idx[ref.node]

        label_name = f"{ref.kind}_{ref.node}"
        if label_name in existing_labels:
            continue

        if ref.kind == "buffer_full":
            buf_size = effective_param(global_params, ordered_nodes[idx].params, "buffer_size")
            expr = f"_data_{idx}_buffer_{buf_size -1} != -1"
        elif ref.kind == "batt_empty":
            expr = f"_energy_{idx}_batt = 0"
        elif ref.kind == "sensor_failure"or ref.kind == "node_failed":
            expr = f"_state_{idx}_s = 1"
        else:
            raise ValueError(f"Unsupported kind '{ref.kind}'.")

        existing_labels.add(label_name)
        to_append.append((label_name, expr))

    if not to_append:
        return

    with prism_path.open("a", encoding="utf-8") as f:
        for label_name, expr in to_append:
            f.write("\n")
            f.write(f'label "{label_name}" = {expr};\n')


def ensure_sensor_rewards(
    prism_path: Path,
    needed_rewards: Set[SensorRewardRef],
    name_to_idx: Dict[str, int],
    ordered_nodes: List[Node],
    conns: List[Connection],
) -> None:
    """
    Ensures sensor-specific reward structures exist.
    Appends missing ones at the end of the file.
    """
    if not needed_rewards:
        return

    text = prism_path.read_text(encoding="utf-8", errors="replace")
    existing_rewards: Set[str] = {m.group(1) for m in _REWARD_START_RE.finditer(text)}

    num_nodes = len(ordered_nodes)
    conn_triplets: List[Tuple[int, int, int]] = []
    for ci, c in enumerate(conns):
        s_idx = name_to_idx[c.sender]
        r_idx = name_to_idx[c.receiver]
        conn_triplets.append((ci, s_idx, r_idx))

    blocks: List[Tuple[str, List[str]]] = []

    for ref in sorted(needed_rewards, key=lambda x: (x.node, x.base)):
        if ref.node not in name_to_idx:
            raise ValueError(f"Unknown node '{ref.node}' in reward reference.")

        idx = name_to_idx[ref.node]
        reward_name = f"{ref.base}_{ref.node}"

        if reward_name in existing_rewards:
            continue

        lines: List[str] = []

        if ref.base == "batt_empty_time":
            lines.append(f"_energy_{idx}_batt=0 : 1;")

        elif ref.base == "sensor_failed_time":
            lines.append(f"_state_{idx}_s=1 : 1;")

        elif ref.base == "generated_data":
            lines.append(f"[generate_data] _state_{idx}_s!=1 : generate_prob_{idx};")
            for (ci, s_idx, r_idx) in conn_triplets:
                if not (s_idx == idx or r_idx == idx):
                    action = make_link_action_label(r_idx, s_idx, num_nodes)
                    lines.append(f'[{action}] _state_{idx}_s!=1 : generate_prob_{idx};')

        elif ref.base == "successful_transmissions":
            for (ci, s_idx, r_idx) in conn_triplets:
                if s_idx == idx or r_idx == idx:
                    action = make_link_action_label(r_idx, s_idx, num_nodes)
                    lines.append(f'[{action}] true : 1-connect_fail_{ci};')

        elif ref.base == "energy_used":
            lines.append(f"[generate_data] true : energy_consumption_idle_{idx};")
            for (ci, s_idx, r_idx) in conn_triplets:
                action = make_link_action_label(r_idx, s_idx, num_nodes)
                if s_idx == idx or r_idx == idx:                    
                    lines.append(f'[{action}] true : energy_consumption_connection_{idx};')
                else:
                    lines.append(f'[{action}] true : energy_consumption_idle_{idx};')

        else:
            raise ValueError(f"Unsupported reward base '{ref.base}'.")

        existing_rewards.add(reward_name)
        blocks.append((reward_name, lines))

    if not blocks:
        return

    with prism_path.open("a", encoding="utf-8") as f:
        for reward_name, lines in blocks:
            f.write("\n")
            f.write(f'rewards "{reward_name}"\n')
            for ln in lines:
                f.write(f"  {ln}\n")
            f.write("endrewards\n")

def _resolve_predicate_to_states_switss(mdp, pred: Dict[str, Any]) -> Set[int]:
    """
    Resolve a predicate spec to a set of SWITSS state indices.
    """
    if not isinstance(pred, dict):
        raise ValueError("Predicate must be a dict.")

    if "expr" in pred:
        raise ValueError(
            "pred['expr'] is not supported for SWITSS-from-.lab/.tra loading. "
            "Use 'sensor_label' (preferred) or 'predicate' referring to an existing label."
        )

    if "sensor_label" in pred:
        sl = pred["sensor_label"]
        node = sl["node"]
        kind = sl["kind"]
        label = f"{kind}_{node}"
        return switss_states_for_label(mdp, label)

    p = pred.get("predicate")
    if not p:
        raise ValueError("Predicate must contain 'sensor_label' or 'predicate' (or 'expr').")
    
    return switss_states_for_label(mdp, p)

def switss_states_for_label(mdp: Any, label: str) -> Set[int]:
    """
    Returns the set of states carrying 'label' across SWITSS versions.
    """
    sbl = getattr(mdp, "states_by_label", None)

    if callable(sbl):
        try:
            return set(sbl(label))
        except Exception:
            return set()

    if sbl is not None and hasattr(sbl, "__getitem__"):
        try:
            return set(sbl[label])
        except Exception:
            pass

    labels = getattr(mdp, "labels", None)
    if labels is not None and hasattr(labels, "__getitem__"):
        try:
            return set(labels[label])
        except Exception:
            return set()

    return set()


def switss_add_label_states(mdp: Any, label: str, states: Iterable[int]) -> None:
    """
    Adds 'label' to all given states across SWITSS versions.
    """
    st: Set[int] = {int(s) for s in states}
    if not st:
        return

    try:
        mdp.add_label(label, st)
        return
    except Exception:
        pass

    for s in st:
        try:
            mdp.add_label(label, int(s))
        except TypeError:
            mdp.add_label(int(s), label)

def switss_ensure_init_label(mdp: Any, init_label: str = "init", initial_state: int = 0) -> None:
    """
    Ensures that 'initial_state' carries init_label.
    """
    s0 = int(initial_state)
    if s0 in switss_states_for_label(mdp, init_label):
        return
    switss_add_label_states(mdp, init_label, {s0})

def _resolve_target_label_name(switss_target: dict) -> str:
    if "sensor_label" in switss_target:
        sl = switss_target["sensor_label"]
        return f'{sl["kind"]}_{sl["node"]}'

    if "predicate" in switss_target:
        return str(switss_target["predicate"])

    raise ValueError("switss.target must contain either 'sensor_label' or 'predicate'.")

def reduce_reachability_form_compat(
    mdp: Any,
    init_label: str,
    target_label: str,
) -> Tuple[Any, Dict[int, int], Dict[Tuple[int, int], Tuple[int, int]]]:
    try:
        return ReachabilityForm.reduce(
            mdp,
            init_label,
            target_label,
            new_init_label="init",
            new_target_label="target",
            new_fail_label="fail",
        )
    except TypeError:
        return ReachabilityForm.reduce(mdp, init_label, target_label)

def to_switss_reachability_form(
    workdir: Path,
    switss_spec: Dict[str, Any],
    *,
    prism_model_name: str = "WBAN.prism",
) -> tuple[Any, Dict[int, int], Dict[int, int], Path, Path]:
    """
    Builds a SWITSS ReachabilityForm from PRISM-exported explicit files (.tra/.lab),
    then reduces it via ReachabilityForm.reduce (wrapped by reduce_reachability_form_compat).
    Returns: (rf, state_map, state_action_map, tra_path, lab_path)
    """
    tra_path, lab_path, _ = run_prism_export_explicit(
        workdir,
        prism_model_name=prism_model_name,
        export_basename="WBAN",
        export_parts="tra,lab",
    )

    mdp = MDP.from_file(str(lab_path), str(tra_path))

    switss_ensure_init_label(mdp, init_label="init", initial_state=0)

    target_label = _resolve_target_label_name(switss_spec["target"])

    if not switss_states_for_label(mdp, "init"):
        raise RuntimeError("No 'init' states found in exported model (label missing or not exported).")

    if not switss_states_for_label(mdp, target_label):
        raise RuntimeError(f"No states found for target label '{target_label}' in exported model.")

    rf, state_map, state_action_map = reduce_reachability_form_compat(mdp, "init", target_label)

    return rf, state_map, state_action_map, tra_path, lab_path

@dataclass(frozen=True)
class WitnessingSubsystemOutputs:
    result: Any 
    json_path: Path
    dot_path: Path
    graph_path: Optional[Path]


def build_witnessing_subsystem(
    rf: Any,
    threshold: float,
    out_dir: Union[str, Path],
    *,
    method: str = "qsheur", 
    mode: str = "min",               
    labels: Optional[Sequence[str]] = None, 
    timeout: Optional[float] = None,          
    iterations: int = 5,
    solver: str = "cbc",             
    basename: str = "witness_subsystem",
    graph_format: str = "pdf",
    state_map: Optional[Dict[int, int]] = None,
) -> WitnessingSubsystemOutputs:
    """
    Builds a witnessing subsystem for a ReachabilityForm
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if method not in ("qsheur", "milpexact"):
        raise ValueError("method must be 'qsheur' or 'milpexact'")
    if mode not in ("min", "max"):
        raise ValueError("mode must be 'min' or 'max'")

    if method == "qsheur":
        pf = QSHeur(iterations=iterations, solver=solver)
    else:
        pf = MILPExact(mode, solver=solver)

    result = pf.solve(
        rf,
        threshold,
        mode,
        labels=list(labels) if labels else None,
        timeout=timeout
    )

    subsys = result.subsystem  
    subsys_mask = subsys.subsystem_mask  
    cert = subsys.certificate
    certform = subsys.certform

    included_states: List[int] = [i for i, keep in enumerate(subsys_mask) if bool(keep)]
    excluded_states: List[int] = [i for i, keep in enumerate(subsys_mask) if not bool(keep)]

    payload: Dict[str, Any] = {
        "status": getattr(result, "status", None),
        "value": getattr(result, "value", None),
        "certform": certform,
        "threshold": threshold,
        "method": method,
        "mode": mode,
        "labels": list(labels) if labels else [],
        "num_states_total": int(len(subsys_mask)),
        "included_states": included_states,
        "excluded_states": excluded_states,
        "subsystem_mask": [bool(x) for x in subsys_mask],
        "certificate": [float(x) for x in cert],
    }

    if state_map is not None:
        payload["state_map"] = {str(k): int(v) for k, v in state_map.items()}

    json_path = out_dir / f"{basename}.json"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    dot = subsys.digraph()
    dot_path = out_dir / f"{basename}.dot"
    dot.save(str(dot_path))

    graph_path: Optional[Path] = None
    try:
        rendered = dot.render(filename=str(out_dir / basename), format=graph_format, cleanup=True)
        graph_path = Path(rendered)
    except Exception:
        graph_path = None

    return WitnessingSubsystemOutputs(
        result=result,
        json_path=json_path,
        dot_path=dot_path,
        graph_path=graph_path,
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, type=Path, help="JSON input file (parameters + nodes + connections + queries).")
    ap.add_argument("--profeat-name", default="WBAN.profeat", help="Output ProFeat filename.")
    ap.add_argument("--prop-name", default="WBAN.ctl", help="Output properties filename.")
    args = ap.parse_args()

    data = load_json(args.input)
    global_params = data.get("global_parameters", {})
    raw_nodes = data.get("nodes", [])
    raw_conns = data.get("connections", [])
    queries = data.get("queries", [])
    export_scheduler_cfg = data.get("export_scheduler", None)

    nodes = parse_nodes(raw_nodes)
    ordered_nodes, name_to_idx = build_indexing(nodes)
    conns = parse_connections(raw_conns, set(n.name for n in nodes))

    constants = build_constants_block(global_params, ordered_nodes, conns, name_to_idx)
    profeat_text = PROFEAT_PREFIX + constants + PROFEAT_BODY

    workdir = ensure_workspace_dir()

    profeat_path = workdir / args.profeat_name
    profeat_path.write_text(profeat_text, encoding="utf-8")

    prism_path = run_profeat_translation(workdir, profeat_name=args.profeat_name, prism_name="WBAN.prism")
    cleanup_duplicate_feature_commands(prism_path)
    switss_spec = data.get("switss")
    needed_labels, needed_rewards = collect_needed_sensors_from_spec(queries, switss_spec)

    ensure_sensor_labels(prism_path, needed_labels, name_to_idx, ordered_nodes, global_params)
    ensure_sensor_rewards(prism_path, needed_rewards, name_to_idx, ordered_nodes, conns)

    if switss_spec and "target" in switss_spec:
        rf, state_map, state_action_map, tra_path, lab_path = to_switss_reachability_form(
        workdir,
        switss_spec,
        prism_model_name="WBAN.prism",
    )
        print("SWITSS ReachabilityForm erstellt.")
        outs = build_witnessing_subsystem(
        rf,
        threshold=0.1,
        out_dir=workdir,
        method="qsheur",
        mode="min",
        iterations=5,
        solver="cbc",
        basename="witness_subsystem",
        graph_format="pdf",
        state_map=state_map,
    )

    ctl_path = workdir / args.prop_name
    write_ctl(queries, ctl_path)
    storm_out = run_storm_model_checking(
        workdir,
        prism_name="WBAN.prism",
        prop_name=args.prop_name,
        export_scheduler_cfg=export_scheduler_cfg
    )
    parsed = parse_storm_output(storm_out)
    parsed = attach_query_metadata(parsed, queries)
    report = format_console_report(parsed)
    print(report)
    write_results_json(parsed, queries, workdir / "results.json", include_raw_stdout=False)
    pareto_paths = plot_pareto_fronts_png(parsed, queries, workdir, filename_prefix="pareto")


if __name__ == "__main__":
    main()
