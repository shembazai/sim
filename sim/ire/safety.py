"""Pre-change safety checks (panic-safe mode)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sim.ire.desired import InfrastructureDesiredState
from sim.ire.observed import ObservedState, collect_observed_state
from sim.subprocess_util import run_command


@dataclass(frozen=True)
class SafetyCheck:
    name: str
    passed: bool
    detail: str
    blocking: bool = True


@dataclass(frozen=True)
class SafetyReport:
    checks: list[SafetyCheck]

    @property
    def passed(self) -> bool:
        return all(c.passed or not c.blocking for c in self.checks)

    @property
    def blocking_failures(self) -> list[SafetyCheck]:
        return [c for c in self.checks if c.blocking and not c.passed]


def run_safety_checks(
    desired: InfrastructureDesiredState,
    *,
    observed: ObservedState | None = None,
    require_ssh_path: bool = True,
) -> SafetyReport:
    """Verify prerequisites before applying critical infrastructure changes."""
    observed = observed or collect_observed_state(
        storage_paths=[m.path for m in desired.storage.mounts],
    )
    checks: list[SafetyCheck] = []

    root_ok, root_detail = run_command(["test", "-d", "/"])
    checks.append(
        SafetyCheck("root_filesystem", root_ok, root_detail or "root filesystem available"),
    )

    for mount in desired.storage.mounts:
        if not mount.required:
            continue
        found = next((m for m in observed.storage.mounts if m.path == str(mount.path)), None)
        mounted = found is not None and found.mounted
        checks.append(
            SafetyCheck(
                f"storage_mount_{mount.path}",
                mounted,
                (
                    f"Required storage mount {mount.path} is available"
                    if mounted
                    else f"Required storage mount {mount.path} unavailable"
                ),
            )
        )

    ssh_active = observed.ssh.service_active
    checks.append(
        SafetyCheck(
            "ssh_access_path",
            ssh_active or not require_ssh_path,
            "sshd is active — remote recovery path available"
            if ssh_active
            else "sshd inactive — SSH recovery path unavailable",
            blocking=require_ssh_path,
        )
    )

    backup_dir = Path("/opt/k1/state/backups")
    backup_available = backup_dir.exists() and backup_dir.is_dir()
    checks.append(
        SafetyCheck(
            "rollback_storage",
            backup_available,
            f"Rollback directory {backup_dir} exists"
            if backup_available
            else f"Rollback directory {backup_dir} missing — config backups may be limited",
            blocking=False,
        )
    )

    return SafetyReport(checks=checks)


def reconcile_privilege_check(repairable_components: set[str]) -> SafetyCheck | None:
    """Block apply when SSH/firewall reconciliation requires root."""
    if not repairable_components.intersection({"ssh", "firewall"}):
        return None
    if os.geteuid() == 0:
        return SafetyCheck(
            "reconcile_privileges",
            True,
            "Running with elevated privileges",
        )
    return SafetyCheck(
        "reconcile_privileges",
        False,
        "Elevated privileges required for SSH/firewall reconciliation "
        "(run: sudo sim ire reconcile --apply)",
    )
