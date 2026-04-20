"""Lightweight Verilog (.v) parsing for modules, ports, instances, and net declarations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


def strip_comments(text: str) -> str:
    """Remove // and /* */ comments (best-effort for typical RTL)."""
    out: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        if i + 1 < n and text[i : i + 2] == "//":
            j = text.find("\n", i)
            if j < 0:
                break
            out.append("\n")
            i = j + 1
            continue
        if i + 1 < n and text[i : i + 2] == "/*":
            j = text.find("*/", i + 2)
            if j < 0:
                break
            out.append(" " * (j - i + 2))
            i = j + 2
            continue
        out.append(text[i])
        i += 1
    return "".join(out)


def _split_ports_paren(inner: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    for ch in inner:
        if ch == "(":
            depth += 1
            cur.append(ch)
        elif ch == ")":
            depth = max(0, depth - 1)
            cur.append(ch)
        elif ch == "," and depth == 0:
            s = "".join(cur).strip()
            if s:
                parts.append(s)
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        parts.append(tail)
    return parts


def parse_range(expr: str | None) -> tuple[int, int] | None:
    """
    Parse [msb:lsb] or [width-1:0] style into (msb, lsb) integers when fully numeric.
    Returns None if non-numeric or absent.
    """
    if not expr:
        return None
    m = re.match(r"^\s*\[\s*([^:]+)\s*:\s*([^\]]+)\s*\]\s*$", expr)
    if not m:
        return None
    a, b = m.group(1).strip(), m.group(2).strip()

    def to_int(x: str) -> int | None:
        if re.fullmatch(r"-?\d+", x):
            return int(x)
        mm = re.fullmatch(r"(\d+)\s*-\s*1", x)
        if mm:
            return int(mm.group(1)) - 1
        return None

    ia, ib = to_int(a), to_int(b)
    if ia is None or ib is None:
        return None
    return ia, ib


def range_width_msb_lsb(msb: int, lsb: int) -> int:
    return abs(msb - lsb) + 1


@dataclass
class PortDecl:
    direction: str  # input, output, inout
    signed: bool
    range_expr: str | None  # raw [ ... ]
    name: str


@dataclass
class ModuleDef:
    name: str
    ports: list[PortDecl] = field(default_factory=list)
    port_order: list[str] = field(default_factory=list)


@dataclass
class InstancePortConn:
    formal: str | None  # .name( or .* or empty for ordered
    actual_expr: str


@dataclass
class InstanceDef:
    cell_type: str
    inst_name: str
    conns: list[InstancePortConn] = field(default_factory=list)


@dataclass
class NetDecl:
    kind: str  # wire or reg or logic
    signed: bool
    range_expr: str | None
    names: list[str]


@dataclass
class ParsedFile:
    path: Path
    modules: dict[str, ModuleDef] = field(default_factory=dict)
    instances_by_module: dict[str, list[InstanceDef]] = field(default_factory=dict)
    nets_by_module: dict[str, list[NetDecl]] = field(default_factory=dict)


_PORT_DIR_RE = re.compile(
    r"\b(input|output|inout)\b\s*(?:signed|wire|wand|wor|tri|triand|trior|tri0|tri1|supply0|supply1|reg|logic)?\s*"
    r"(\[[^\]]+\])?\s*(\w+)\s*(?:,|;|\)|$)"
)


def _parse_module_header(header: str) -> tuple[list[PortDecl], list[str]]:
    ports: list[PortDecl] = []
    order: list[str] = []
    for m in _PORT_DIR_RE.finditer(header):
        direction = m.group(1)
        rng = m.group(2)
        name = m.group(3)
        ports.append(PortDecl(direction, False, rng, name))
        order.append(name)
    return ports, order


_MODULE_START = re.compile(
    r"\bmodule\s+(\w+)\s*(?:#\s*\([^;]*\))?\s*(?:\(([^)]*)\))?\s*;",
    re.DOTALL,
)


def parse_verilog_file(path: Path) -> ParsedFile:
    raw = path.read_text(encoding="utf-8", errors="replace")
    text = strip_comments(raw)
    pf = ParsedFile(path=path.resolve())

    for m in _MODULE_START.finditer(text):
        mod_name = m.group(1)
        inner = m.group(2) or ""
        ports, order = _parse_module_header(inner)
        mod = ModuleDef(name=mod_name, ports=ports, port_order=order)
        pf.modules[mod_name] = mod

        start = m.end()
        end_idx = text.find("endmodule", start)
        if end_idx < 0:
            body = text[start:]
        else:
            body = text[start:end_idx]

        pf.instances_by_module[mod_name] = _parse_instances(body)
        pf.nets_by_module[mod_name] = _parse_net_decls(body)

    return pf


def _skip_ws(s: str, i: int) -> int:
    n = len(s)
    while i < n and s[i] in " \t\r\n":
        i += 1
    return i


def _read_ident(s: str, i: int) -> tuple[str | None, int]:
    i = _skip_ws(s, i)
    if i >= len(s) or not (s[i].isalpha() or s[i] == "_"):
        return None, i
    j = i + 1
    while j < len(s) and (s[j].isalnum() or s[j] in "_$"):
        j += 1
    return s[i:j], j


def _balanced_paren(s: str, open_idx: int) -> tuple[str, int]:
    """Return (inner, index_after_closing_paren). open_idx points at '('."""
    depth = 0
    i = open_idx
    n = len(s)
    start_content = open_idx + 1
    while i < n:
        ch = s[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return s[start_content:i], i + 1
        elif ch == '"':
            i += 1
            while i < n and s[i] != '"':
                if s[i] == "\\":
                    i += 1
                i += 1
        i += 1
    return s[start_content:n], n


def _after_optional_params(s: str, i: int) -> int:
    i = _skip_ws(s, i)
    if i < len(s) and s[i] == "#":
        i += 1
        i = _skip_ws(s, i)
        if i < len(s) and s[i] == "(":
            _, i = _balanced_paren(s, i)
    return _skip_ws(s, i)


def _parse_instances(body: str) -> list[InstanceDef]:
    instances: list[InstanceDef] = []
    keywords = {
        "if",
        "else",
        "for",
        "while",
        "case",
        "casex",
        "casez",
        "always",
        "assign",
        "initial",
        "module",
        "endmodule",
        "begin",
        "end",
        "function",
        "task",
        "wire",
        "reg",
        "logic",
        "input",
        "output",
        "inout",
    }
    i = 0
    n = len(body)
    while i < n:
        cell, j = _read_ident(body, i)
        if cell is None:
            i += 1
            continue
        if cell in keywords:
            i = j
            continue
        inst, k = _read_ident(body, j)
        if inst is None:
            i = j
            continue
        k2 = _after_optional_params(body, k)
        if k2 >= len(body) or body[k2] != "(":
            i = j
            continue
        inner, after = _balanced_paren(body, k2)
        semi = body.find(";", after)
        if semi < 0:
            break
        if inst == cell:
            i = j
            continue
        conns = _parse_instance_ports(inner)
        instances.append(InstanceDef(cell_type=cell, inst_name=inst, conns=conns))
        i = semi + 1
    return instances


def _parse_instance_ports(inner: str) -> list[InstancePortConn]:
    inner = inner.strip()
    if not inner:
        return []
    if inner.startswith(".*"):
        return [InstancePortConn(formal=".*", actual_expr=".*")]
    parts = _split_ports_paren(inner)
    out: list[InstancePortConn] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        mm = re.match(r"^\.(\w+)\s*\(\s*([\s\S]*)\s*\)\s*$", p)
        if mm:
            out.append(InstancePortConn(formal=mm.group(1), actual_expr=mm.group(2).strip()))
        else:
            out.append(InstancePortConn(formal=None, actual_expr=p))
    return out


_NET_RE = re.compile(
    r"\b(wire|reg|logic)\s*(signed)?\s*(\[[^\]]+\])?\s*((?:\w+\s*(?:,\s*\w+\s*)*));",
    re.MULTILINE,
)


def _parse_net_decls(body: str) -> list[NetDecl]:
    nets: list[NetDecl] = []
    for m in _NET_RE.finditer(body):
        kind = m.group(1)
        signed = bool(m.group(2))
        rng = m.group(3)
        names_blob = m.group(4)
        names = [x.strip() for x in names_blob.split(",") if x.strip()]
        if names:
            nets.append(NetDecl(kind=kind, signed=signed, range_expr=rng, names=names))
    return nets


def merge_parsed_files(files: list[ParsedFile]) -> ParsedFile:
    merged = ParsedFile(path=Path("."))
    for pf in files:
        for k, v in pf.modules.items():
            merged.modules[k] = v
        for mod, insts in pf.instances_by_module.items():
            merged.instances_by_module.setdefault(mod, []).extend(insts)
        for mod, nets in pf.nets_by_module.items():
            merged.nets_by_module.setdefault(mod, []).extend(nets)
    return merged


def merge_parsed_files_with_paths(files: list[ParsedFile]) -> ParsedFile:
    merged = merge_parsed_files(files)
    paths: dict[str, Path] = {}
    for pf in files:
        for mn in pf.modules:
            paths.setdefault(mn, pf.path)
    setattr(merged, "_module_paths", paths)
    return merged
