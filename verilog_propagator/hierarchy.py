"""Build module instance graph from parsed Verilog and optional .vc top."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .verilog_parser import InstanceDef, ParsedFile, merge_parsed_files_with_paths


@dataclass(frozen=True)
class InstPathEdge:
    parent_module: str
    instance_name: str
    cell_type: str


@dataclass
class HierarchyGraph:
    """parent_module -> list of (inst_name, cell_type)."""

    children: dict[str, list[tuple[str, str]]] = field(default_factory=dict)
    """cell_type -> list of parent modules that instantiate it."""
    parents_of_cell: dict[str, list[str]] = field(default_factory=dict)
    tops: list[str] = field(default_factory=list)

    def add_instance(self, parent: str, inst: InstanceDef) -> None:
        lst = self.children.setdefault(parent, [])
        lst.append((inst.inst_name, inst.cell_type))
        self.parents_of_cell.setdefault(inst.cell_type, []).append(parent)


def build_graph(parsed: ParsedFile, tops: Iterable[str] | None = None) -> HierarchyGraph:
    g = HierarchyGraph()
    for parent, insts in parsed.instances_by_module.items():
        for inst in insts:
            g.add_instance(parent, inst)
    if tops:
        g.tops = list(tops)
    else:
        # Heuristic: modules that are never instantiated as cells are tops
        all_cells = set(parsed.modules.keys())
        instantiated = {c for kids in g.children.values() for _, c in kids}
        g.tops = sorted(all_cells - instantiated)
    return g


def load_design(vc_path: Path | None, verilog_paths: list[Path], tops: list[str] | None) -> tuple[ParsedFile, HierarchyGraph]:
    from .vc_loader import load_vc, iter_verilog_sources

    paths: list[Path] = []
    top_list = list(tops) if tops else []
    if vc_path:
        r = load_vc(vc_path)
        paths.extend(r.verilog_files)
        if not top_list and r.tops:
            top_list = r.tops
    paths.extend(verilog_paths)
    paths = iter_verilog_sources(paths)
    pfiles = []
    for p in paths:
        from .verilog_parser import parse_verilog_file

        pfiles.append(parse_verilog_file(p))
    merged = merge_parsed_files_with_paths(pfiles)
    g = build_graph(merged, tops=top_list if top_list else None)
    return merged, g
