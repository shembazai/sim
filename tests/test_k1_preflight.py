"""Tests for sim k1 preflight integration."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sim.k1.preflight import run_k1_preflight
from sim.main import app


def test_k1_preflight_report_shape():
    k1_root = Path(__file__).resolve().parents[2]
    report = run_k1_preflight(k1_root=k1_root)
    payload = report.to_dict()
    assert "k1_root" in payload
    assert "checks" in payload
    assert any(c["name"] == "k1_root" for c in payload["checks"])


def test_cli_k1_preflight_json():
    k1_root = Path(__file__).resolve().parents[2]
    runner = CliRunner()
    result = runner.invoke(app, ["k1", "preflight", "--k1-root", str(k1_root), "--json"])
    assert result.exit_code in (0, 1)
    payload = json.loads(result.stdout)
    assert payload["k1_root"] == str(k1_root.resolve())
