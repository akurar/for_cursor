"""Extract and rewrite Verilog module regions in source files."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class ModuleSpan:
    name: str
    start: int
    end: int  # exclusive, position after endmodule
    text: str


def iter_module_spans(full_text: str) -> list[ModuleSpan]:
    """Split Verilog source into module spans (no nested modules)."""
    spans: list[ModuleSpan] = []
    idx = 0
    n = len(full_text)
    mod_re = re.compile(r"\bmodule\s+(\w+)\b")
    while True:
        m = mod_re.search(full_text, idx)
        if not m:
            break
        name = m.group(1)
        start = m.start()
        endm = full_text.find("endmodule", m.end())
        if endm < 0:
            break
        end = endm + len("endmodule")
        spans.append(ModuleSpan(name=name, start=start, end=end, text=full_text[start:end]))
        idx = end
    return spans


def replace_module_spans(full_text: str, replacements: dict[str, str]) -> str:
    spans = iter_module_spans(full_text)
    if not spans:
        return full_text
    out: list[str] = []
    pos = 0
    for sp in spans:
        if pos < sp.start:
            out.append(full_text[pos:sp.start])
        new_body = replacements.get(sp.name, sp.text)
        out.append(new_body)
        pos = sp.end
    out.append(full_text[pos:])
    return "".join(out)
