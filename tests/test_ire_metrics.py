"""Tests for IRE Prometheus textfile metrics."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from sim.checks.phase0 import CheckResult
from sim.ire.drift import DriftItem
from sim.ire.metrics import build_prometheus_metrics, write_prometheus_textfile
from sim.ire.safety import SafetyCheck, SafetyReport
from sim.main import app
from sim.orchestrator import HealthReport


def _health_report() -> HealthReport:
    return HealthReport(
        phase0_checks=[
            CheckResult("cpu", "passed", "ok", critical=True),
            CheckResult("storage", "failed", "low space", critical=True),
        ],
        drift=[
            DriftItem(
                component="ssh",
                field="allowed_users",
                severity="warning",
                desired="cybershaman",
                observed="",
                message="missing user",
                auto_repairable=True,
            ),
            DriftItem(
                component="firewall",
                field="services.ssh.tailscale0",
                severity="warning",
                desired="allowed",
                observed="blocked",
                message="ssh zone drift",
                auto_repairable=True,
            ),
        ],
        passed=False,
        message="issues detected",
    )


def test_build_prometheus_metrics_includes_core_gauges():
    health = _health_report()
    safety = SafetyReport(
        checks=[
            SafetyCheck("storage_mount_/mnt/ai", True, "mounted"),
            SafetyCheck("ssh_access_path", True, "active"),
        ]
    )
    text = build_prometheus_metrics(health=health, safety=safety)

    assert "sim_health_passed 0" in text
    assert "sim_ire_safety_passed 1" in text
    assert "sim_phase0_critical_failures_total 1" in text
    assert 'sim_ire_drift_items_total{severity="warning"} 2' in text
    assert "sim_ire_drift_repairable_total 2" in text
    assert text.endswith("\n")


def test_write_prometheus_textfile_is_atomic(tmp_path: Path):
    output = tmp_path / "sim_ire.prom"
    write_prometheus_textfile("sim_health_passed 1\n", output)
    assert output.read_text(encoding="utf-8") == "sim_health_passed 1\n"
    assert not output.with_suffix(".prom.tmp").exists()


def test_cli_ire_metrics_stdout():
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ire", "metrics", "--manifest", "examples/k1_server_manifest.yaml", "--stdout"],
    )
    assert result.exit_code in (0, 1)
    assert "sim_health_passed" in result.stdout
    assert "sim_ire_safety_passed" in result.stdout


def test_cli_ire_metrics_writes_file(tmp_path: Path, monkeypatch):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        f"""
server:
  hostname: k1
  role: production
filesystem:
  root: {tmp_path}
report:
  directory: {tmp_path / "reports"}
infrastructure:
  ssh:
    enabled: true
    port: 22
    allowed_users: [cybershaman]
  storage:
    mounts: []
""".strip()
        + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "metrics" / "sim_ire.prom"

    monkeypatch.setattr(
        "sim.orchestrator.run_phase0_checks",
        lambda **_kwargs: [CheckResult("cpu", "passed", "ok", critical=True)],
    )
    monkeypatch.setattr(
        "sim.orchestrator.collect_observed_state",
        lambda **_kwargs: __import__("sim.ire.models", fromlist=["ObservedState"]).ObservedState(),
    )
    monkeypatch.setattr(
        "sim.ire.safety.collect_observed_state",
        lambda **_kwargs: __import__("sim.ire.models", fromlist=["ObservedState"]).ObservedState(),
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ire", "metrics", "--manifest", str(manifest), "--output", str(output)],
    )
    assert result.exit_code in (0, 1)
    assert output.exists()
    assert "sim_health_passed" in output.read_text(encoding="utf-8")
