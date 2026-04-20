"""Propagate port-level mutations upward through the instance hierarchy."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Iterable

from .hierarchy import HierarchyGraph
from .name_match import MatchKind, PortNameRelation, classify_port_rename, rename_in_actual_expr, rename_signal_identifier
from .text_rewrite import iter_module_spans, replace_module_spans
from .verilog_parser import InstanceDef, ParsedFile, PortDecl, merge_parsed_files_with_paths


class ChangeType(str, Enum):
    RENAME = "rename"
    WIDTH_CHANGE = "width_change"
    ADD = "add"
    DELETE = "delete"


@dataclass
class PortChange:
    change: ChangeType
    old_port: str | None = None
    new_port: str | None = None
    port: str | None = None
    msb: int | None = None
    lsb: int | None = None
    actual_expr: str | None = None


@dataclass
class PropagationConfig:
    source_module: str
    changes: list[PortChange]
    """Module names at the upward boundary: update them, then do not propagate further."""
    target_modules: set[str] = field(default_factory=set)


@dataclass
class PropagationReport:
    files_modified: list[Path] = field(default_factory=list)
    modules_touched: set[str] = field(default_factory=set)
    notes: list[str] = field(default_factory=list)


def _module_file_path(parsed: ParsedFile, mod_name: str) -> Path | None:
    return getattr(parsed, "_module_paths", {}).get(mod_name)


def _port_by_name(mod_ports: list[PortDecl], name: str) -> PortDecl | None:
    for p in mod_ports:
        if p.name == name:
            return p
    return None


def _format_range(msb: int, lsb: int) -> str:
    return f"[{msb}:{lsb}]"


def _extract_simple_ids(expr: str) -> list[str]:
    return list(dict.fromkeys(m.group(1) for m in re.finditer(r"\b([A-Za-z_]\w*)\b", expr)))


def _find_instances(parsed: ParsedFile, parent: str, child_cell: str) -> list[tuple[str, InstanceDef]]:
    return [(i.inst_name, i) for i in parsed.instances_by_module.get(parent, []) if i.cell_type == child_cell]


def _parents_of_module(graph: HierarchyGraph, cell: str) -> list[str]:
    return sorted(set(graph.parents_of_cell.get(cell, [])))


def _find_conn(inst: InstanceDef, formal: str) -> tuple[str | None, str | None]:
    for c in inst.conns:
        if c.formal == formal:
            return c.formal, c.actual_expr
    return None, None


def _balanced_paren(s: str, open_idx: int) -> tuple[str, int]:
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


def _locate_instance_port_list(mod_text: str, inst_name: str, child_cell: str) -> tuple[int, int] | None:
    inst_pat = re.compile(
        rf"\b{re.escape(child_cell)}\s+(?:#\s*\([^;]*\)\s*)?{re.escape(inst_name)}\s*\(",
        re.MULTILINE,
    )
    m = inst_pat.search(mod_text)
    if not m:
        return None
    open_paren = mod_text.find("(", m.end() - 1)
    _, after = _balanced_paren(mod_text, open_paren)
    return open_paren + 1, after - 1


def _replace_dot_port_connection(
    port_list_inner: str,
    formal: str,
    new_formal: str | None,
    new_actual: str | None,
    delete: bool,
) -> str | None:
    token = f".{formal}"
    idx = port_list_inner.find(token)
    if idx < 0:
        return None
    j = idx + len(token)
    j = _skip_ws(port_list_inner, j)
    if j >= len(port_list_inner) or port_list_inner[j] != "(":
        return None
    inner, after_paren = _balanced_paren(port_list_inner, j)
    start = idx
    end = after_paren
    if delete:
        before = port_list_inner[:start].rstrip()
        afterb = port_list_inner[end:].lstrip()
        if before.endswith(","):
            before = before[:-1].rstrip()
        elif afterb.startswith(","):
            afterb = afterb[1:].lstrip()
        return (before + (" " if before and afterb else "") + afterb).strip()
    actual = inner
    if new_actual is not None:
        actual = new_actual
    rhs = new_formal if new_formal is not None else formal
    frag = f".{rhs}({actual})"
    return port_list_inner[:start] + frag + port_list_inner[end:]


def _skip_ws(s: str, i: int) -> int:
    n = len(s)
    while i < n and s[i] in " \t\r\n":
        i += 1
    return i


def _replace_formal_port_in_instance(
    mod_text: str,
    inst_name: str,
    child_cell: str,
    old_formal: str,
    new_formal: str | None,
    new_actual: str | None,
    delete: bool,
) -> str:
    span = _locate_instance_port_list(mod_text, inst_name, child_cell)
    if not span:
        return mod_text
    a, b = span
    inner = mod_text[a:b]
    new_inner = _replace_dot_port_connection(inner, old_formal, new_formal, new_actual, delete)
    if new_inner is None:
        return mod_text
    return mod_text[:a] + new_inner + mod_text[b:]


def _insert_port_after_open(
    mod_text: str,
    inst_name: str,
    child_cell: str,
    new_formal: str,
    actual_expr: str,
) -> str:
    span = _locate_instance_port_list(mod_text, inst_name, child_cell)
    if not span:
        return mod_text
    a, b = span
    inner = mod_text[a:b]
    insert = f".{new_formal}({actual_expr})"
    new_inner = insert + (",\n        " + inner.strip() if inner.strip() else "")
    return mod_text[:a] + new_inner + mod_text[b:]


def _split_module_header_body(mod_text: str) -> tuple[str, str] | None:
    m = re.search(r"\bmodule\s+(\w+)\s*", mod_text)
    if not m:
        return None
    semi = mod_text.find(";", m.end())
    if semi < 0:
        return None
    return mod_text[: semi + 1], mod_text[semi + 1 :]


def _update_header_port(head: str, port_name: str, new_name: str | None, new_range: str | None) -> str:
    def repl_port(mm: re.Match[str]) -> str:
        direction, signed, rng, name = mm.group(1), mm.group(2) or "", mm.group(3) or "", mm.group(4)
        if name != port_name:
            return mm.group(0)
        newn = new_name if new_name is not None else name
        newr = new_range if new_range is not None else rng
        signed_kw = f" {signed}" if signed.strip() else ""
        rng_kw = f" {newr}" if newr else ""
        return f"{direction}{signed_kw}{rng_kw} {newn}"

    return re.sub(
        rf"\b(input|output|inout)\b\s*(signed)?\s*(\[[^\]]+\])?\s*{re.escape(port_name)}\b",
        repl_port,
        head,
        count=1,
    )


def _update_wire_reg(body: str, id_name: str, new_id: str | None, new_range: str | None) -> str:
    def repl_decl(mm: re.Match[str]) -> str:
        kind, signed, rng, names_blob = mm.group(1), mm.group(2) or "", mm.group(3) or "", mm.group(4)
        names = [x.strip() for x in names_blob.split(",")]
        if id_name not in names:
            return mm.group(0)
        new_names = []
        for nm in names:
            new_names.append(new_id if nm == id_name and new_id is not None else nm)
        nr = new_range if new_range is not None else rng
        rng_kw = f" {nr}" if nr else ""
        signed_kw = f" {signed}" if signed.strip() else ""
        return f"{kind}{signed_kw}{rng_kw} {', '.join(new_names)}"

    return re.sub(
        rf"\b(wire|reg|logic)\s*(signed)?\s*(\[[^\]]+\])?\s*((?:\w+\s*(?:,\s*\w+\s*)*));",
        repl_decl,
        body,
        flags=re.MULTILINE,
    )


def _rename_id_everywhere(mod_text: str, old_id: str, new_id: str) -> str:
    return re.sub(rf"\b{re.escape(old_id)}\b", new_id, mod_text)


def _sync_net_decls_for_ids(
    mod_text: str,
    ids_before: list[str],
    rel: PortNameRelation,
    range_str: str | None,
) -> str:
    split = _split_module_header_body(mod_text)
    if not split:
        return mod_text
    head, body = split
    for oid in ids_before:
        nid = rename_signal_identifier(oid, rel) if rel.kind != MatchKind.UNRELATED else oid
        if nid != oid:
            head = _update_header_port(head, oid, nid, None)
            body = _update_wire_reg(body, oid, nid, None)
            head = _rename_id_everywhere(head, oid, nid)
            body = _rename_id_everywhere(body, oid, nid)
        if range_str:
            head = _update_header_port(head, nid, None, range_str)
            body = _update_wire_reg(body, nid, None, range_str)
    return head + body


def _resolve_to_exported_port(parsed: ParsedFile, mod_name: str, sig: str) -> str | None:
    """Map internal net name to a module port via trivial assign (id = id)."""
    mdef = parsed.modules.get(mod_name)
    if not mdef:
        return None
    port_names = {p.name for p in mdef.ports}
    if sig in port_names:
        return sig
    path = _module_file_path(parsed, mod_name)
    if path is None:
        return None
    full = path.read_text(encoding="utf-8", errors="replace")
    spans = iter_module_spans(full)
    mod_text = next((s.text for s in spans if s.name == mod_name), None)
    if not mod_text:
        return None
    split = _split_module_header_body(mod_text)
    if not split:
        return None
    body = split[1]
    for mm in re.finditer(r"\bassign\s+(\w+)\s*=\s*(\w+)\s*;", body):
        a, b = mm.group(1), mm.group(2)
        if a == sig and b in port_names:
            return b
        if b == sig and a in port_names:
            return a
    return None


def _range_for_port(parsed: ParsedFile, mod: str, port: str) -> str | None:
    mdef = parsed.modules.get(mod)
    if not mdef:
        return None
    decl = _port_by_name(mdef.ports, port)
    return decl.range_expr if decl else None


def _read_actual_from_instance(
    mod_text: str,
    inst_name: str,
    child_cell: str,
    formal: str,
) -> str | None:
    span = _locate_instance_port_list(mod_text, inst_name, child_cell)
    if not span:
        return None
    inner = mod_text[span[0] : span[1]]
    token = f".{formal}"
    pos = 0
    while True:
        idx = inner.find(token, pos)
        if idx < 0:
            return None
        j = idx + len(token)
        j = _skip_ws(inner, j)
        if j < len(inner) and inner[j] == "(":
            act_inner, _ = _balanced_paren(inner, j)
            return act_inner.strip()
        pos = idx + 1


class _FileBuffer:
    def __init__(self, paths: Iterable[Path]) -> None:
        self._text: dict[Path, str] = {p.resolve(): p.read_text(encoding="utf-8", errors="replace") for p in paths}

    def module_text(self, parsed: ParsedFile, mod: str) -> str | None:
        path = _module_file_path(parsed, mod)
        if path is None:
            return None
        full = self._text[path.resolve()]
        for sp in iter_module_spans(full):
            if sp.name == mod:
                return sp.text
        return None

    def set_module_text(self, parsed: ParsedFile, mod: str, text: str) -> None:
        path = _module_file_path(parsed, mod)
        if path is None:
            return
        pr = path.resolve()
        full = self._text[pr]
        self._text[pr] = replace_module_spans(full, {mod: text})

    def write_all_if_changed(self) -> list[Path]:
        changed: list[Path] = []
        for path, text in self._text.items():
            orig = path.read_text(encoding="utf-8", errors="replace")
            if text != orig:
                path.write_text(text, encoding="utf-8")
                changed.append(path)
        return changed


def propagate(
    parsed: ParsedFile,
    graph: HierarchyGraph,
    cfg: PropagationConfig,
    *,
    dry_run: bool = False,
) -> PropagationReport:
    report = PropagationReport()
    paths = sorted({p.resolve() for p in getattr(parsed, "_module_paths", {}).values()})
    buf = _FileBuffer(paths)

    for ch in cfg.changes:
        if ch.change == ChangeType.RENAME:
            if not ch.old_port or not ch.new_port:
                report.notes.append("rename requires old_port and new_port")
                continue
            rel = classify_port_rename(ch.old_port, ch.new_port)
            _run_rename_or_width(
                parsed,
                graph,
                cfg.source_module,
                ch.old_port,
                ch.new_port,
                rel,
                None,
                cfg.target_modules,
                buf,
                report,
            )
        elif ch.change == ChangeType.WIDTH_CHANGE:
            if not ch.port or ch.msb is None or ch.lsb is None:
                report.notes.append("width_change requires port, msb, lsb")
                continue
            rng = _format_range(ch.msb, ch.lsb)
            rel = classify_port_rename(ch.port, ch.port)
            _run_rename_or_width(
                parsed,
                graph,
                cfg.source_module,
                ch.port,
                ch.port,
                rel,
                rng,
                cfg.target_modules,
                buf,
                report,
            )
        elif ch.change == ChangeType.ADD:
            if not ch.new_port or not ch.actual_expr:
                report.notes.append("add requires new_port and actual_expr")
                continue
            _run_add(parsed, graph, cfg.source_module, ch.new_port, ch.actual_expr, cfg.target_modules, buf, report)
        elif ch.change == ChangeType.DELETE:
            if not ch.old_port:
                report.notes.append("delete requires old_port")
                continue
            _run_delete(parsed, graph, cfg.source_module, ch.old_port, cfg.target_modules, buf, report)

    if not dry_run:
        report.files_modified = buf.write_all_if_changed()
    return report


def _run_rename_or_width(
    parsed: ParsedFile,
    graph: HierarchyGraph,
    start_cell: str,
    old_port: str,
    new_port: str,
    rel: PortNameRelation,
    forced_range: str | None,
    target_modules: set[str],
    buf: _FileBuffer,
    report: PropagationReport,
) -> None:
    queue: list[tuple[str, str, str, str, str, str | None]] = []
    for parent in _parents_of_module(graph, start_cell):
        for inst_name, inst in _find_instances(parsed, parent, start_cell):
            _, actual = _find_conn(inst, old_port)
            if actual is None:
                continue
            f_old = old_port
            f_new = new_port if rel.kind != MatchKind.UNRELATED else old_port
            rng = forced_range or _range_for_port(
                parsed,
                start_cell,
                new_port if rel.kind != MatchKind.UNRELATED else old_port,
            )
            queue.append((parent, inst_name, start_cell, f_old, f_new, rng))

    seen: set[tuple[str, str, str]] = set()
    while queue:
        parent, inst_name, child_cell, formal_old, formal_new, range_str = queue.pop(0)
        key = (parent, inst_name, formal_old)
        if key in seen:
            continue
        seen.add(key)

        mt = buf.module_text(parsed, parent)
        if mt is None:
            continue
        actual = _read_actual_from_instance(mt, inst_name, child_cell, formal_old)
        if actual is None:
            continue
        new_actual = rename_in_actual_expr(actual, rel) if rel.kind != MatchKind.UNRELATED else actual
        new_mt = _replace_formal_port_in_instance(
            mt,
            inst_name,
            child_cell,
            formal_old,
            formal_new if formal_new != formal_old else None,
            new_actual,
            False,
        )
        ids_before = _extract_simple_ids(actual)
        new_mt = _sync_net_decls_for_ids(new_mt, ids_before, rel, range_str)
        buf.set_module_text(parsed, parent, new_mt)
        report.modules_touched.add(parent)

        stop = target_modules and parent in target_modules
        if stop:
            continue

        pmod = parsed.modules.get(parent)
        if not pmod:
            continue
        for pid in _extract_simple_ids(new_actual):
            port_name = _resolve_to_exported_port(parsed, parent, pid)
            if port_name is None:
                continue
            for gp in _parents_of_module(graph, parent):
                for gin, _ in _find_instances(parsed, gp, parent):
                    gmt = buf.module_text(parsed, gp)
                    if gmt is None:
                        continue
                    aa = _read_actual_from_instance(gmt, gin, parent, port_name)
                    if aa is None:
                        continue
                    queue.append((gp, gin, parent, port_name, port_name, range_str))


def _run_add(
    parsed: ParsedFile,
    graph: HierarchyGraph,
    start_cell: str,
    new_port: str,
    actual_expr: str,
    target_modules: set[str],
    buf: _FileBuffer,
    report: PropagationReport,
) -> None:
    for parent in _parents_of_module(graph, start_cell):
        for inst_name, _ in _find_instances(parsed, parent, start_cell):
            mt = buf.module_text(parsed, parent)
            if mt is None:
                continue
            buf.set_module_text(parsed, parent, _insert_port_after_open(mt, inst_name, start_cell, new_port, actual_expr))
            report.modules_touched.add(parent)
            if target_modules and parent in target_modules:
                continue


def _run_delete(
    parsed: ParsedFile,
    graph: HierarchyGraph,
    start_cell: str,
    old_port: str,
    target_modules: set[str],
    buf: _FileBuffer,
    report: PropagationReport,
) -> None:
    for parent in _parents_of_module(graph, start_cell):
        for inst_name, _ in _find_instances(parsed, parent, start_cell):
            mt = buf.module_text(parsed, parent)
            if mt is None:
                continue
            buf.set_module_text(
                parsed,
                parent,
                _replace_formal_port_in_instance(mt, inst_name, start_cell, old_port, None, None, True),
            )
            report.modules_touched.add(parent)
            if target_modules and parent in target_modules:
                continue


def run_from_paths(
    vc: Path | None,
    verilog_files: list[Path],
    tops: list[str] | None,
    cfg: PropagationConfig,
    *,
    dry_run: bool = False,
) -> PropagationReport:
    from .verilog_parser import parse_verilog_file
    from .vc_loader import iter_verilog_sources, load_vc

    paths: list[Path] = []
    top_list = list(tops) if tops else []
    if vc:
        r = load_vc(vc)
        paths.extend(iter_verilog_sources(r.verilog_files))
        if not top_list and r.tops:
            top_list = r.tops
    paths.extend(verilog_files)
    pfiles = [parse_verilog_file(p) for p in paths]
    parsed = merge_parsed_files_with_paths(pfiles)
    from .hierarchy import build_graph

    g = build_graph(parsed, tops=top_list if top_list else None)
    return propagate(parsed, g, cfg, dry_run=dry_run)
