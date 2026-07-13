"""Chaos-style IRE tests — safety gates block mutation, dry-run stays read-only."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sim.ire.desired import InfrastructureDesiredState, SSHDesiredState, StorageDesiredState, StorageMountDesired
from sim.ire.drift import detect_drift
from sim.ire.engine import PlanStep, ReconciliationEngine
from sim.ire.models import ObservedState, SSHObserved, StorageMountObserved, StorageObserved
from sim.ire.safety import reconcile_privilege_check, run_safety_checks
from sim.ire.transaction import TransactionStore


def _desired() -> InfrastructureDesiredState:
    return InfrastructureDesiredState(
        ssh=SSHDesiredState(
            enabled=True,
            port=22,
            root_login=False,
            password_authentication=False,
            allowed_users=["cybershaman"],
        ),
        storage=StorageDesiredState(
            mounts=[StorageMountDesired(path=Path("/mnt/ai"), required=True)],
        ),
    )


def _unmounted_observed(*, ssh_active: bool = True) -> ObservedState:
    return ObservedState(
        hostname="k1",
        ssh=SSHObserved(service_active=ssh_active, listening_ports=[22] if ssh_active else []),
        storage=StorageObserved(
            mounts=[StorageMountObserved(path="/mnt/ai", mounted=False)],
        ),
    )


class _TrackingModule:
    component = "ssh"

    def __init__(self) -> None:
        self.apply_calls = 0
        self.rollback_calls = 0

    def plan(self, drift: list) -> list[PlanStep]:
        return [
            PlanStep(
                component=self.component,
                action="reconcile.test",
                description="test step",
                auto_repairable=True,
            )
        ]

    def apply(self, steps: list[PlanStep], *, backup_dir: Path) -> dict[str, str]:
        self.apply_calls += 1
        return {str(backup_dir / "ssh"): "applied"}

    def verify(self) -> dict[str, str]:
        return {"sshd": "PASS"}

    def rollback(self, backup_dir: Path) -> None:
        self.rollback_calls += 1


@pytest.mark.parametrize("dry_run", [True, False])
def test_unmounted_storage_blocks_reconcile(tmp_path: Path, monkeypatch, dry_run: bool):
    desired = _desired()
    module = _TrackingModule()
    engine = ReconciliationEngine(
        desired,
        transaction_db=tmp_path / "tx.db",
        modules=[module],
    )
    monkeypatch.setattr(engine, "observe", lambda: _unmounted_observed())

    result = engine.reconcile(dry_run=dry_run)

    assert result.transaction is not None
    assert result.transaction.status == "BLOCKED"
    assert "BLOCKED" in result.message
    assert module.apply_calls == 0


def test_inactive_sshd_blocks_apply(tmp_path: Path, monkeypatch):
    desired = _desired()
    module = _TrackingModule()
    engine = ReconciliationEngine(
        desired,
        transaction_db=tmp_path / "tx.db",
        modules=[module],
    )
    monkeypatch.setattr(
        engine,
        "observe",
        lambda: _unmounted_observed(ssh_active=False),
    )

    result = engine.reconcile(dry_run=False)

    assert not result.committed
    assert result.transaction is not None
    assert result.transaction.status == "BLOCKED"
    assert module.apply_calls == 0


def test_dry_run_with_modules_never_applies(tmp_path: Path, monkeypatch):
    desired = _desired()
    module = _TrackingModule()
    engine = ReconciliationEngine(
        desired,
        transaction_db=tmp_path / "tx.db",
        modules=[module],
    )

    def _healthy_observed() -> ObservedState:
        return ObservedState(
            hostname="k1",
            ssh=SSHObserved(
                service_active=True,
                listening_ports=[22],
                allowed_users=[],
            ),
            storage=StorageObserved(
                mounts=[StorageMountObserved(path="/mnt/ai", mounted=True)],
            ),
        )

    monkeypatch.setattr(engine, "observe", _healthy_observed)
    monkeypatch.setattr(
        "sim.ire.engine.run_safety_checks",
        lambda *_args, **_kwargs: MagicMock(passed=True, blocking_failures=[]),
    )

    result = engine.reconcile(dry_run=True)

    assert result.transaction is not None
    assert result.transaction.status == "PLANNED"
    assert module.apply_calls == 0
    assert "Dry-run" in result.message


def test_blocked_transaction_is_persisted_without_apply(tmp_path: Path, monkeypatch):
    desired = _desired()
    tx_db = tmp_path / "tx.db"
    engine = ReconciliationEngine(desired, transaction_db=tx_db)
    monkeypatch.setattr(engine, "observe", lambda: _unmounted_observed())

    engine.reconcile(dry_run=False)

    with TransactionStore(tx_db) as store:
        records = store.history()
    assert len(records) == 1
    assert records[0].status == "BLOCKED"


def test_volatile_ssh_bind_is_critical_drift():
    desired = _desired()
    observed = ObservedState(
        ssh=SSHObserved(
            service_active=True,
            listening_ports=[22],
            bind_addresses=["100.64.0.5"],
            permit_root_login="no",
            password_authentication="no",
            allowed_users=["cybershaman"],
        ),
        storage=StorageObserved(
            mounts=[StorageMountObserved(path="/mnt/ai", mounted=True)],
        ),
    )
    drift = detect_drift(desired, observed)
    bind = next(d for d in drift if d.field == "bind_addresses")
    assert bind.severity == "critical"
    assert "Tailscale" in bind.message


def test_safety_passes_when_optional_ssh_path_not_required():
    desired = _desired()
    observed = ObservedState(
        ssh=SSHObserved(service_active=False),
        storage=StorageObserved(
            mounts=[StorageMountObserved(path="/mnt/ai", mounted=True)],
        ),
    )
    report = run_safety_checks(desired, observed=observed, require_ssh_path=False)
    assert report.passed


def test_reconcile_privilege_check_blocks_non_root(monkeypatch):
    monkeypatch.setattr("sim.ire.safety.os.geteuid", lambda: 1000)
    check = reconcile_privilege_check({"ssh"})
    assert check is not None
    assert not check.passed
    assert "sudo" in check.detail


def test_reconcile_privilege_check_passes_as_root(monkeypatch):
    monkeypatch.setattr("sim.ire.safety.os.geteuid", lambda: 0)
    check = reconcile_privilege_check({"firewall"})
    assert check is not None
    assert check.passed


def test_apply_without_root_is_blocked_before_modules(tmp_path: Path, monkeypatch):
    desired = _desired()
    module = _TrackingModule()
    report_dir = tmp_path / "reports"
    engine = ReconciliationEngine(
        desired,
        transaction_db=tmp_path / "tx.db",
        report_dir=report_dir,
        modules=[module],
    )
    monkeypatch.setattr("sim.ire.safety.os.geteuid", lambda: 1000)
    monkeypatch.setattr(
        engine,
        "observe",
        lambda: ObservedState(
            hostname="k1",
            ssh=SSHObserved(
                service_active=True,
                listening_ports=[22],
                allowed_users=[],
            ),
            storage=StorageObserved(
                mounts=[StorageMountObserved(path="/mnt/ai", mounted=True)],
            ),
        ),
    )
    monkeypatch.setattr(
        "sim.ire.engine.run_safety_checks",
        lambda *_args, **_kwargs: MagicMock(passed=True, blocking_failures=[], checks=[]),
    )

    result = engine.reconcile(dry_run=False)

    assert result.transaction is not None
    assert result.transaction.status == "BLOCKED"
    assert module.apply_calls == 0
    assert (report_dir / f"{result.transaction.transaction_id}.json").exists()
