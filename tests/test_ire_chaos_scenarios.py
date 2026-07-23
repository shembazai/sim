"""Chaos scenarios from ROADMAP — unit coverage plus optional VM gate."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sim.ire.desired import (
    FirewallDesiredState,
    FirewallInterfaceDesired,
    FirewallServiceDesired,
    InfrastructureDesiredState,
    SSHDesiredState,
    StorageDesiredState,
    StorageMountDesired,
)
from sim.ire.drift import detect_drift
from sim.ire.engine import PlanStep, ReconciliationEngine
from sim.ire.models import (
    FirewallObserved,
    ObservedState,
    SSHObserved,
    StorageMountObserved,
    StorageObserved,
)
from sim.ire.modules.firewall import FirewallReconciliationModule


def _desired() -> InfrastructureDesiredState:
    return InfrastructureDesiredState(
        ssh=SSHDesiredState(
            enabled=True,
            port=22,
            allowed_users=["cybershaman"],
        ),
        firewall=FirewallDesiredState(
            interfaces={"tailscale0": FirewallInterfaceDesired(zone="trusted")},
            services={"ssh": FirewallServiceDesired(allowed_interfaces=["tailscale0"])},
        ),
        storage=StorageDesiredState(
            mounts=[StorageMountDesired(path=Path("/mnt/ai"), required=True)],
        ),
    )


def test_chaos_unmounted_storage_blocks_apply(tmp_path: Path, monkeypatch):
    """ROADMAP: unmount /mnt/ai → sim ire safety BLOCKED, no mutation."""
    engine = ReconciliationEngine(_desired(), transaction_db=tmp_path / "tx.db")
    monkeypatch.setattr(
        engine,
        "observe",
        lambda: ObservedState(
            storage=StorageObserved(
                mounts=[StorageMountObserved(path="/mnt/ai", mounted=False)],
            ),
            ssh=SSHObserved(service_active=True, listening_ports=[22]),
        ),
    )
    result = engine.reconcile(dry_run=False)
    assert result.transaction is not None
    assert result.transaction.status == "BLOCKED"
    assert not result.committed


def test_chaos_volatile_ssh_bind_is_critical_drift():
    """ROADMAP: break SSH ListenAddress bind → critical drift flagged."""
    observed = ObservedState(
        ssh=SSHObserved(
            service_active=True,
            listening_ports=[22],
            bind_addresses=["100.64.0.5"],
            allowed_users=["cybershaman"],
        ),
        storage=StorageObserved(
            mounts=[StorageMountObserved(path="/mnt/ai", mounted=True)],
        ),
    )
    drift = detect_drift(_desired(), observed)
    bind = next(d for d in drift if d.field == "bind_addresses")
    assert bind.severity == "critical"
    assert "Tailscale" in bind.message
    assert bind.auto_repairable is False


def test_chaos_firewall_zone_drift_plans_repairable_step():
    """ROADMAP: firewall zone drift → IRE plan includes repairable firewall step."""
    desired = _desired()
    observed = ObservedState(
        firewall=FirewallObserved(
            active=True,
            interface_zones={"tailscale0": "public"},
            ssh_allowed_zones=[],
        ),
        storage=StorageObserved(
            mounts=[StorageMountObserved(path="/mnt/ai", mounted=True)],
        ),
        ssh=SSHObserved(service_active=True, listening_ports=[22]),
    )
    drift = detect_drift(desired, observed)
    zone_drift = [d for d in drift if "tailscale0" in d.field]
    assert zone_drift
    assert any(d.auto_repairable for d in zone_drift)

    module = FirewallReconciliationModule(desired.firewall)
    steps = module.plan(zone_drift)
    assert steps
    assert all(step.component == "firewall" for step in steps)
    assert all(isinstance(step, PlanStep) for step in steps)


@pytest.mark.skipif(
    os.environ.get("SIM_VM_INTEGRATION") != "1",
    reason="Set SIM_VM_INTEGRATION=1 on a Rocky Linux 10 VM with snapshot revert",
)
def test_vm_integration_observe_only_pipeline():
    """Optional VM gate: run observe-only IRE commands on a live Rocky Linux host."""
    from sim.config import ManifestConfig
    from sim.ire.safety import run_safety_checks
    from sim.orchestrator import run_health_check

    manifest = Path(os.environ.get("SIM_VM_MANIFEST", "examples/k1_server_manifest.yaml"))
    cfg = ManifestConfig.load(manifest)
    health = run_health_check(cfg)
    safety = run_safety_checks(cfg.infrastructure)
    assert safety.passed
    assert health.phase0_checks
