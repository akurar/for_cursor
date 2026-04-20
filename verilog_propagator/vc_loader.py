"""Load design file lists from Verilog command (.vc) files."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


@dataclass
class VcLoadResult:
    """Resolved paths and preprocessor state from a .vc file."""

    verilog_files: list[Path] = field(default_factory=list)
    incdirs: list[Path] = field(default_factory=list)
    defines: dict[str, str | None] = field(default_factory=dict)
    tops: list[str] = field(default_factory=list)


def _expand_vars(text: str, env: dict[str, str]) -> str:
    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        return env.get(key, m.group(0))

    return re.sub(r"\$\{([^}]+)\}", repl, text)


def _normalize_path(p: str, base_dir: Path) -> Path:
    p = p.strip().strip('"').strip("'")
    if not p:
        return base_dir
    path = Path(p)
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def load_vc(
    vc_path: Path | str,
    *,
    extra_defines: dict[str, str | None] | None = None,
) -> VcLoadResult:
    """
    Parse a .vc (filelist) file: +incdir+, +define+, -y/-v style paths, -f recurse.

    Lines starting with // or # are treated as comments (after trim).
    Optional directive: +top+MODULE (non-standard; used when design has no single default).
    """
    vc_path = Path(vc_path).resolve()
    base = vc_path.parent
    result = VcLoadResult()
    seen_vc: set[Path] = set()
    stack: list[Path] = [vc_path]
    env = dict(os.environ)

    if extra_defines:
        for k, v in extra_defines.items():
            result.defines[k] = v

    def parse_file(path: Path) -> None:
        if path in seen_vc:
            return
        seen_vc.add(path)
        text = path.read_text(encoding="utf-8", errors="replace")
        b = path.parent
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("//") or line.startswith("#"):
                continue
            if line.startswith("+top+"):
                result.tops.append(line[len("+top+") :].strip())
                continue
            if line.startswith("+incdir+"):
                rest = line[len("+incdir+") :].strip()
                for part in rest.split("+"):
                    part = part.strip()
                    if not part:
                        continue
                    result.incdirs.append(_normalize_path(part, b))
                continue
            if line.startswith("+define+"):
                rest = line[len("+define+") :].strip()
                if "=" in rest:
                    k, v = rest.split("=", 1)
                    result.defines[k.strip()] = v.strip()
                else:
                    result.defines[rest.strip()] = None
                continue
            if line.startswith("-f") or line.startswith("-F"):
                sub = line.split(None, 1)[1] if len(line.split()) > 1 else ""
                sub = _expand_vars(sub.strip(), env)
                stack.append(_normalize_path(sub, b))
                continue
            if line.startswith("+libext+"):
                continue
            if line.startswith("-y") or line.startswith("+y"):
                continue
            tokens = line.split()
            if not tokens:
                continue
            first = tokens[0]
            if first.endswith(".v") or first.endswith(".sv"):
                p = _expand_vars(first, env)
                result.verilog_files.append(_normalize_path(p, b))
            elif len(tokens) >= 2 and tokens[0] in ("-v",):
                p = _expand_vars(tokens[1], env)
                result.verilog_files.append(_normalize_path(p, b))

    while stack:
        parse_file(stack.pop())

    uniq: list[Path] = []
    seen: set[Path] = set()
    for p in result.verilog_files:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq.append(rp)
    result.verilog_files = uniq
    return result


def iter_verilog_sources(paths: Iterable[Path]) -> list[Path]:
    out: list[Path] = []
    for p in paths:
        if p.suffix.lower() in (".v", ".sv") and p.is_file():
            out.append(p)
    return out
