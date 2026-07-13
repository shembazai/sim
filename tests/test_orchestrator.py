"""Tests for unified provisioning orchestration (Phase 7)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sim.config import ManifestConfig
from sim.ire.models import (
    FirewallObserved,
    ObservedState,
    SSHObserved,
    StorageMountObserved,
    StorageObserved,
    TailscaleObserved,
)
from sim.modules.base import InstallModule, run_install_module
from sim.modules.init_environment import InitEnvironmentModule
from sim.orchestrator import (
    build_install_modules,
    known_repair_targets,
    repair_target,
    run_health_check,
    run_install_pipeline,
    run_ire_preflight,
)
from sim.phases.lifecycle import CheckResult, PhaseResult
from sim.state import StateManager
from sim.main import app


def test_run_install_module_reverifies_completed_on_detect_failure(tmp_path: Path):
    db = tmp_path / "state.db"
    sm = StateManager(db_path=db)
    sm.record_success("fake_module")

    class FakeModule(InstallModule):
        @property
        def name(self) -> str:
            return "fake_module"

        def detect(self) -> list[CheckResult]:
            return [CheckResult("broken", "failed", "manual drift", critical=True)]

        def install(self) -> None:
            pass

        def verify(self) -> list[CheckResult]:
            return [CheckResult("broken", "passed", "fixed", critical=True)]

    install_called = False
    module = FakeModule()

    def _install() -> None:
        nonlocal install_called
        install_called = True

    module.install = _install  # type: ignore[method-assign]
    result = run_install_module(module, sm, dry_run=False, reverify_completed=True)

    assert install_called is True
    assert result.passed is True
    assert any(c.name == "reverify" for c in result.checks)
    sm.close()


def test_run_install_module_skips_when_completed_and_detect_passes(tmp_path: Path):
    db = tmp_path / "state.db"
    sm = StateManager(db_path=db)
    sm.record_success("fake_module")

    class FakeModule(InstallModule):
        @property
        def name(self) -> str:
            return "fake_module"

        def detect(self) -> list[CheckResult]:
            return [CheckResult("ok", "passed", "still good", critical=True)]

        def install(self) -> None:
            raise AssertionError("install should not run")

    result = run_install_module(FakeModule(), sm, dry_run=False)
    assert result.passed is True
    assert any(c.name == "resumability" for c in result.checks)
    sm.close()


def test_run_ire_preflight_blocks_on_safety_failure(monkeypatch):
    from sim.ire.safety import SafetyCheck, SafetyReport

    monkeypatch.setattr(
        "sim.orchestrator.run_safety_checks",
        lambda *_args, **_kwargs: SafetyReport(
            checks=[SafetyCheck("ssh_path", False, "blocked", blocking=True)]
        ),
    )
    monkeypatch.setattr(
        "sim.orchestrator.collect_observed_state",
        lambda **_kwargs: ObservedState(),
    )
    monkeypatch.setattr("sim.orchestrator.detect_drift", lambda *_args, **_kwargs: [])

    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    result = run_ire_preflight(cfg.infrastructure)
    assert result.passed is False
    assert "safety" in result.message.lower()


def test_run_ire_preflight_strict_drift_blocks(monkeypatch):
    from sim.ire.drift import DriftItem
    from sim.ire.safety import SafetyReport

    monkeypatch.setattr(
        "sim.orchestrator.run_safety_checks",
        lambda *_args, **_kwargs: SafetyReport(checks=[]),
    )
    monkeypatch.setattr(
        "sim.orchestrator.collect_observed_state",
        lambda **_kwargs: ObservedState(),
    )
    monkeypatch.setattr(
        "sim.orchestrator.detect_drift",
        lambda *_args, **_kwargs: [
            DriftItem(
                component="ssh",
                field="port",
                severity="warning",
                desired="22",
                observed="2222",
                message="port mismatch",
            )
        ],
    )

    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    loose = run_ire_preflight(cfg.infrastructure, strict_drift=False)
    strict = run_ire_preflight(cfg.infrastructure, strict_drift=True)
    assert loose.passed is True
    assert strict.passed is False


def test_run_health_check_combines_phase0_and_drift(monkeypatch):
    from sim.ire.drift import DriftItem

    monkeypatch.setattr(
        "sim.orchestrator.run_phase0_checks",
        lambda **_kwargs: [
            CheckResult("ssh", "failed", "sshd inactive", critical=True),
            CheckResult("tpm", "warning", "missing", critical=False),
        ],
    )
    monkeypatch.setattr(
        "sim.orchestrator.collect_observed_state",
        lambda **_kwargs: ObservedState(),
    )
    monkeypatch.setattr(
        "sim.orchestrator.detect_drift",
        lambda *_args, **_kwargs: [
            DriftItem(
                component="storage",
                field="mounts./mnt/ai",
                severity="warning",
                desired="mounted",
                observed="missing",
                message="missing mount",
            )
        ],
    )

    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    report = run_health_check(cfg)
    assert report.passed is False
    assert any(c.name == "ssh" for c in report.phase0_checks)
    assert len(report.drift) == 1


def test_build_install_modules_excludes_gpu_when_disabled():
    cfg = ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "gpu": {"vendor": "none"},
        }
    )
    modules = build_install_modules(cfg)
    assert "podman" in modules
    assert "nvidia_driver" not in modules


def test_known_repair_targets_includes_ire_modules():
    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    targets = known_repair_targets(cfg)
    assert "ssh" in targets
    assert "firewall" in targets
    assert "init_environment" in targets


def test_run_install_pipeline_respects_from_stage(tmp_path: Path, monkeypatch):
    cfg = ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "filesystem": {"root": str(tmp_path / "k1")},
        }
    )
    sm = StateManager(db_path=tmp_path / "state.db")
    calls: list[str] = []

    def _fake_build_stages(*_args, **_kwargs):
        from sim.orchestrator import InstallStage

        def _ok(_state: StateManager) -> PhaseResult:
            return PhaseResult("x", True, [], 0.0)

        def _record(name: str):
            def _run(_state: StateManager) -> PhaseResult:
                calls.append(name)
                return PhaseResult(name, True, [], 0.0)
            return _run

        return [
            InstallStage("phase0", "phase0", _record("phase0")),
            InstallStage("init_environment", "module", _record("init_environment")),
            InstallStage("podman", "module", _record("podman")),
        ]

    monkeypatch.setattr("sim.orchestrator.build_install_stages", _fake_build_stages)
    monkeypatch.setattr(
        "sim.orchestrator.run_ire_preflight",
        lambda *_args, **_kwargs: type("P", (), {"passed": True, "message": "ok", "drift": []})(),
    )

    result = run_install_pipeline(
        cfg,
        sm,
        manifest_path=tmp_path / "manifest.yaml",
        skip_ire_preflight=True,
        from_stage="podman",
    )
    assert result.passed is True
    assert calls == ["podman"]
    sm.close()


def test_repair_install_module_reruns_after_rollback(tmp_path: Path):
    root = tmp_path / "k1"
    cfg = ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "filesystem": {"root": str(root)},
        }
    )
    sm = StateManager(db_path=tmp_path / "state.db")
    module = InitEnvironmentModule(cfg, dry_run=False)
    run_install_module(module, sm)
    assert sm.is_completed("init_environment")

    result = repair_target("init_environment", cfg, sm, dry_run=False)
    assert result.passed is True
    assert sm.is_completed("init_environment")
    sm.close()


def test_cli_health_json(tmp_path: Path, monkeypatch):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "server:\n  hostname: k1\n  role: production\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "sim.orchestrator.run_phase0_checks",
        lambda **_kwargs: [CheckResult("cpu", "passed", "ok", critical=True)],
    )
    monkeypatch.setattr(
        "sim.orchestrator.collect_observed_state",
        lambda **_kwargs: ObservedState(
            ssh=SSHObserved(
                service_active=True,
                listening_ports=[22],
                permit_root_login="no",
                password_authentication="no",
            ),
            storage=StorageObserved(
                mounts=[StorageMountObserved(path="/mnt/ai", mounted=True)],
            ),
            tailscale=TailscaleObserved(installed=True, online=True),
            firewall=FirewallObserved(active=True),
        ),
    )

    runner = CliRunner()
    result = runner.invoke(app, ["health", "--manifest", str(manifest), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["passed"] is True
