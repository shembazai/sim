"""Tests for Infrastructure Reconciliation Engine (IRE)."""

from __future__ import annotations

from pathlib import Path

import pytest

from sim.config import InfrastructureDesiredState, ManifestConfig, StorageDesiredState, StorageMountDesired
from sim.ire.desired import SSHDesiredState
from sim.ire.drift import detect_drift
from sim.ire.engine import ReconciliationEngine, build_plan
from sim.ire.observed import (
    FirewallObserved,
    ObservedState,
    SSHObserved,
    StorageMountObserved,
    StorageObserved,
    TailscaleObserved,
)
from sim.ire.safety import run_safety_checks
from sim.ire.transaction import TransactionRecord, TransactionStore, generate_transaction_id


def _minimal_desired(**overrides) -> InfrastructureDesiredState:
    base = InfrastructureDesiredState(
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
    if overrides:
        return base.model_copy(update=overrides)
    return base


def test_manifest_infrastructure_defaults():
    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    assert cfg.infrastructure.ssh.port == 22
    assert cfg.infrastructure.tailscale.enabled is True


def test_detect_ssh_volatile_bind_address():
    desired = _minimal_desired()
    observed = ObservedState(
        ssh=SSHObserved(
            service_active=True,
            listening_ports=[22],
            bind_addresses=["100.64.0.5"],
            permit_root_login="no",
            password_authentication="no",
            allowed_users=["cybershaman"],
        )
    )
    drift = detect_drift(desired, observed)
    fields = {d.field for d in drift}
    assert "bind_addresses" in fields


def test_storage_missing_mount_is_warning_not_destructive():
    desired = _minimal_desired()
    observed = ObservedState(
        storage=StorageObserved(
            mounts=[StorageMountObserved(path="/mnt/ai", mounted=False)],
        )
    )
    drift = detect_drift(desired, observed)
    mount_drift = next(d for d in drift if d.field == "mounts./mnt/ai")
    assert mount_drift.severity == "warning"
    assert mount_drift.auto_repairable is False
    assert "No destructive action" in mount_drift.message


def test_build_plan_skips_non_repairable_drift():
    desired = _minimal_desired()
    observed = ObservedState(
        ssh=SSHObserved(
            service_active=True,
            listening_ports=[22],
            permit_root_login="no",
            password_authentication="no",
            allowed_users=["cybershaman"],
        ),
        storage=StorageObserved(
            mounts=[StorageMountObserved(path="/mnt/ai", mounted=False)],
        ),
        firewall=FirewallObserved(active=True),
    )
    plan = build_plan(desired, observed)
    assert plan.has_drift
    assert plan.steps == []


def test_safety_blocks_when_required_mount_missing(monkeypatch):
    desired = _minimal_desired()

    def fake_collect(**_kwargs):
        return ObservedState(
            ssh=SSHObserved(service_active=True, listening_ports=[22]),
            storage=StorageObserved(
                mounts=[StorageMountObserved(path="/mnt/ai", mounted=False)],
            ),
        )

    monkeypatch.setattr("sim.ire.safety.collect_observed_state", fake_collect)
    report = run_safety_checks(desired)
    assert not report.passed
    assert any("storage_mount" in c.name for c in report.blocking_failures)


def test_reconciliation_engine_dry_run_blocked(tmp_path: Path, monkeypatch):
    desired = _minimal_desired()
    tx_db = tmp_path / "ire.db"

    def fake_observe(self):
        return ObservedState(
            hostname="k1",
            ssh=SSHObserved(service_active=False),
            storage=StorageObserved(
                mounts=[StorageMountObserved(path="/mnt/ai", mounted=False)],
            ),
        )

    monkeypatch.setattr(ReconciliationEngine, "observe", fake_observe)
    engine = ReconciliationEngine(desired, transaction_db=tx_db)
    result = engine.reconcile(dry_run=True)
    assert result.transaction is not None
    assert result.transaction.status == "BLOCKED"
    assert "BLOCKED" in result.message


def test_transaction_store_round_trip(tmp_path: Path):
    db = tmp_path / "tx.db"
    record = TransactionRecord(
        transaction_id=generate_transaction_id("ssh"),
        timestamp="2026-07-10T12:00:00+00:00",
        target_host="k1",
        status="COMMITTED",
        changed_resources=["/etc/ssh/sshd_config"],
        validation_results={"sshd -t": "PASS"},
        rollback_available=True,
    )
    with TransactionStore(db) as store:
        store.save(record)
        loaded = store.get(record.transaction_id)
    assert loaded is not None
    assert loaded.status == "COMMITTED"
    assert loaded.changed_resources == ["/etc/ssh/sshd_config"]
