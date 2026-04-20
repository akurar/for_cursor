"""Port / signal name matching rules for propagation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class MatchKind(Enum):
    EXACT = "exact"
    PREFIX = "prefix"  # new_port == old_port + tail
    SUFFIX = "suffix"  # new_port == head + old_port
    CONTAINS = "contains"  # old_port strictly inside new_port (not only prefix/suffix)
    UNRELATED = "unrelated"


@dataclass
class PortNameRelation:
    kind: MatchKind
    old_port: str
    new_port: str


def classify_port_rename(old_port: str, new_port: str) -> PortNameRelation:
    if old_port == new_port:
        return PortNameRelation(MatchKind.EXACT, old_port, new_port)
    if new_port.startswith(old_port) and len(old_port) > 0:
        return PortNameRelation(MatchKind.PREFIX, old_port, new_port)
    if new_port.endswith(old_port) and len(old_port) > 0:
        return PortNameRelation(MatchKind.SUFFIX, old_port, new_port)
    if old_port in new_port:
        return PortNameRelation(MatchKind.CONTAINS, old_port, new_port)
    return PortNameRelation(MatchKind.UNRELATED, old_port, new_port)


def rename_signal_identifier(ident: str, rel: PortNameRelation) -> str:
    """Rename a bare identifier (no hierarchical dots) according to port relation."""
    old, new = rel.old_port, rel.new_port
    if rel.kind == MatchKind.UNRELATED:
        return ident
    if rel.kind == MatchKind.EXACT:
        return new if ident == old else ident
    if rel.kind == MatchKind.PREFIX:
        if ident == old:
            return new
        if ident.startswith(old) and len(ident) > len(old) and ident[len(old)] in "_$[":
            return new + ident[len(old) :]
        return ident
    if rel.kind == MatchKind.SUFFIX:
        if ident == old:
            return new
        if ident.endswith(old) and len(ident) > len(old) and ident[-len(old) - 1] in "_$]":
            return ident[: -len(old)] + new
        return ident
    if rel.kind == MatchKind.CONTAINS:
        if old in ident:
            return ident.replace(old, new, 1)
        return ident
    return ident


def rename_in_actual_expr(expr: str, rel: PortNameRelation) -> str:
    """
    Rename identifiers in a port actual expression (best-effort).
    Handles simple hierarchical a.b.c by renaming leaf and intermediate when exact.
    """
    if rel.kind == MatchKind.UNRELATED:
        return expr

    def repl_leaf(token: str) -> str:
        if "." in token:
            parts = token.split(".")
            parts[-1] = rename_signal_identifier(parts[-1], rel)
            return ".".join(parts)
        return rename_signal_identifier(token, rel)

    def sub_hier(m: re.Match[str]) -> str:
        return repl_leaf(m.group(0))

    return re.sub(r"\b[\w$][\w$.]*\b", sub_hier, expr)
