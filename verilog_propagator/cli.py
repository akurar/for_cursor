"""Command-line entry for Verilog port propagation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .propagator import ChangeType, PortChange, PropagationConfig, run_from_paths


def _load_changes(path: Path | None, inline: str | None) -> list[PortChange]:
    if path:
        data = json.loads(path.read_text(encoding="utf-8"))
        out: list[PortChange] = []
        for item in data:
            out.append(
                PortChange(
                    change=ChangeType(item["change"]),
                    old_port=item.get("old_port"),
                    new_port=item.get("new_port"),
                    port=item.get("port"),
                    msb=item.get("msb"),
                    lsb=item.get("lsb"),
                    actual_expr=item.get("actual_expr"),
                )
            )
        return out
    if not inline:
        raise SystemExit("Provide --changes or --changes-json")
    item = json.loads(inline)
    return [
        PortChange(
            change=ChangeType(item["change"]),
            old_port=item.get("old_port"),
            new_port=item.get("new_port"),
            port=item.get("port"),
            msb=item.get("msb"),
            lsb=item.get("lsb"),
            actual_expr=item.get("actual_expr"),
        )
    ]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Verilog port change auto-propagator")
    p.add_argument("--vc", type=Path, help="Verilog command file (.vc) listing RTL sources")
    p.add_argument("-f", "--file", type=Path, action="append", default=[], help="Additional .v file")
    p.add_argument("--top", action="append", default=[], help="Top module name(s) (optional)")
    p.add_argument("--source-module", required=True, help="Leaf module where the port change originates")
    p.add_argument(
        "--target-modules",
        default="",
        help="Comma-separated upward boundary: update these modules but do not propagate above them",
    )
    p.add_argument("--changes-json", type=Path, help="JSON file: list of change objects")
    p.add_argument("--changes", help="Single change object as JSON string")
    p.add_argument("--dry-run", action="store_true", help="Parse and plan only; do not write files")
    args = p.parse_args(argv)

    if not args.vc and not args.file:
        p.error("Provide --vc and/or -f/--file")

    targets = {x.strip() for x in args.target_modules.split(",") if x.strip()}
    changes = _load_changes(args.changes_json, args.changes)
    cfg = PropagationConfig(
        source_module=args.source_module,
        changes=changes,
        target_modules=targets,
    )
    report = run_from_paths(
        args.vc,
        list(args.file),
        args.top if args.top else None,
        cfg,
        dry_run=args.dry_run,
    )
    print("modules_touched:", sorted(report.modules_touched))
    print("files_modified:", [str(x) for x in report.files_modified])
    for n in report.notes:
        print("note:", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
