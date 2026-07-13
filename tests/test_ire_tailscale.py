"""Tests for IRE Tailscale identity observer."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from sim.config import InfrastructureDesiredState, TailscaleDesiredState
from sim.ire.desired import SSHDesiredState
from sim.ire.drift import detect_drift
from sim.ire.modules.tailscale import (
    TailscaleObserver,
    build_tailscale_report,
    tailscale_report_exit_code,
)
from sim.ire.models import ObservedState, TailscaleObserved
from sim.main import app

TAILSCALE_STATUS_ONLINE = {
    "Self": {
        "Online": True,
        "DNSName": "k1.example.ts.net.",
        "CertDomains": ["k1.example.ts.net"],
    }
}

TAILSCALE_STATUS_WRONG_TAILNET = {
    "Self": {
        "Online": True,
        "DNSName": "k1.wrong.ts.net.",
        "CertDomains": ["k1.wrong.ts.net"],
    }
}


def _run_command_factory(responses: dict[tuple[str, ...], tuple[bool, str]]):
    def _fake(args: list[str]) -> tuple[bool, str]:
        key = tuple(args)
        if key in responses:
            return responses[key]
        return False, ""
    return _fake


def test_tailscale_observer_online(monkeypatch):
    monkeypatch.setattr(
        "sim.ire.modules.tailscale.run_command",
        _run_command_factory(
            {("tailscale", "status", "--json"): (True, json.dumps(TAILSCALE_STATUS_ONLINE))}
        ),
    )
    observed = TailscaleObserver().observe()
    assert observed.installed is True
    assert observed.online is True
    assert observed.hostname == "k1.example.ts.net"
    assert observed.tailnet == "example.ts.net"


def test_tailscale_observer_not_installed(monkeypatch):
    monkeypatch.setattr(
        "sim.ire.modules.tailscale.run_command",
        _run_command_factory({}),
    )
    monkeypatch.setattr("sim.ire.modules.tailscale.shutil.which", lambda _: None)
    observed = TailscaleObserver().observe()
    assert observed.installed is False
    assert observed.online is False


def test_tailscale_wrong_tailnet_drift():
    desired = InfrastructureDesiredState(
        tailscale=TailscaleDesiredState(enabled=True, tailnet="example.ts.net"),
    )
    observed = ObservedState(
        tailscale=TailscaleObserved(
            installed=True,
            online=True,
            hostname="k1.wrong.ts.net",
            tailnet="wrong.ts.net",
        )
    )
    drift = detect_drift(desired, observed)
    fields = {d.field for d in drift}
    assert "tailnet" in fields
    tailnet_drift = next(d for d in drift if d.field == "tailnet")
    assert tailnet_drift.severity == "critical"
    assert tailnet_drift.auto_repairable is False


def test_tailscale_hostname_mismatch_drift():
    desired = InfrastructureDesiredState(
        tailscale=TailscaleDesiredState(enabled=True, hostname="k1"),
    )
    observed = ObservedState(
        tailscale=TailscaleObserved(
            installed=True,
            online=True,
            hostname="other.example.ts.net",
            tailnet="example.ts.net",
        )
    )
    drift = detect_drift(desired, observed)
    assert any(d.field == "hostname" for d in drift)


def test_tailscale_report_exit_code_offline():
    desired = TailscaleDesiredState(enabled=True)
    from sim.ire.modules.tailscale import TailscaleReport

    report = TailscaleReport(
        installed=True,
        online=False,
        hostname=None,
        tailnet=None,
        drift=[],
    )
    assert tailscale_report_exit_code(report, desired) == 1


def test_tailscale_report_exit_code_healthy():
    desired = TailscaleDesiredState(enabled=True)
    from sim.ire.modules.tailscale import TailscaleReport

    report = TailscaleReport(
        installed=True,
        online=True,
        hostname="k1.example.ts.net",
        tailnet="example.ts.net",
        drift=[],
    )
    assert tailscale_report_exit_code(report, desired) == 0


def test_build_tailscale_report(monkeypatch):
    monkeypatch.setattr(
        "sim.ire.modules.tailscale.run_command",
        _run_command_factory(
            {("tailscale", "status", "--json"): (True, json.dumps(TAILSCALE_STATUS_ONLINE))}
        ),
    )
    desired = InfrastructureDesiredState(
        ssh=SSHDesiredState(),
        tailscale=TailscaleDesiredState(enabled=True, tailnet="ts.net"),
    )
    report = build_tailscale_report(desired)
    assert report.online is True
    assert report.hostname == "k1.example.ts.net"


def test_cli_ire_tailscale_json(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "server:\n  hostname: k1\n  role: production\n"
        "infrastructure:\n  tailscale:\n    enabled: true\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sim.ire.modules.tailscale.run_command",
        _run_command_factory(
            {("tailscale", "status", "--json"): (True, json.dumps(TAILSCALE_STATUS_ONLINE))}
        ),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["ire", "tailscale", "--manifest", str(manifest), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["online"] is True
    assert payload["hostname"] == "k1.example.ts.net"
