"""Tests for verilog_propagator (copy sample RTL to temp dir to avoid mutating repo)."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from verilog_propagator.propagator import (
    ChangeType,
    PortChange,
    PropagationConfig,
    run_from_paths,
)


@pytest.fixture()
def sample_rtl(tmp_path: Path) -> Path:
    src = Path(__file__).resolve().parent.parent / "examples" / "hier_rtl"
    dst = tmp_path / "hier_rtl"
    shutil.copytree(src, dst)
    return dst


def test_width_propagation_upward(sample_rtl: Path) -> None:
    cfg = PropagationConfig(
        source_module="leaf",
        changes=[
            PortChange(change=ChangeType.WIDTH_CHANGE, port="in_a", msb=7, lsb=0),
        ],
        target_modules=set(),
    )
    report = run_from_paths(
        sample_rtl / "files.vc",
        [],
        None,
        cfg,
        dry_run=False,
    )
    assert "sub" in report.modules_touched
    sub_text = (sample_rtl / "sub.v").read_text()
    assert "[7:0]" in sub_text
    assert "wire [7:0] bus" in sub_text


def test_rename_prefix_propagation(sample_rtl: Path) -> None:
    cfg = PropagationConfig(
        source_module="leaf",
        changes=[
            PortChange(change=ChangeType.RENAME, old_port="in_a", new_port="in_a_wide"),
        ],
        target_modules=set(),
    )
    run_from_paths(sample_rtl / "files.vc", [], None, cfg, dry_run=False)
    sub_text = (sample_rtl / "sub.v").read_text()
    assert ".in_a_wide(" in sub_text


def test_target_boundary_stops_at_mid(sample_rtl: Path) -> None:
    cfg = PropagationConfig(
        source_module="leaf",
        changes=[
            PortChange(change=ChangeType.WIDTH_CHANGE, port="in_a", msb=7, lsb=0),
        ],
        target_modules={"mid"},
    )
    report = run_from_paths(sample_rtl / "files.vc", [], None, cfg, dry_run=False)
    assert "mid" in report.modules_touched
    assert "top" not in report.modules_touched
    top_text = (sample_rtl / "top.v").read_text()
    assert "[3:0]" in top_text
