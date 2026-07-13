"""SSH reconciliation module — safe configuration with backup and rollback.

Never generates ListenAddress for Tailscale or other volatile IPs.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from pathlib import Path

from sim.ire.desired import SSHDesiredState
from sim.ire.drift import DriftItem
from sim.ire.engine import PlanStep
from sim.ire.observed import SSHObserved
from sim.subprocess_util import run_command

logger = logging.getLogger(__name__)

SSHD_CONFIG = Path("/etc/ssh/sshd_config")
SSHD_CONFIG_D = Path("/etc/ssh/sshd_config.d")
SIM_DROP_IN = SSHD_CONFIG_D / "99-sim-ire.conf"


def _safe_exists(path: Path) -> bool:
    """Return whether *path* exists without raising on permission errors."""
    try:
        return path.exists()
    except PermissionError:
        return True
    except OSError:
        return False


def _require_elevation(path: Path) -> None:
    """Fail fast when system paths are not writable by the current user."""
    parent = path.parent
    if not parent.exists() or not os.access(parent, os.W_OK):
        raise PermissionError(
            f"Elevated privileges required to manage SSH config under {parent} "
            "(run: sudo sim ire reconcile --apply)"
        )
    if _safe_exists(path):
        try:
            if path.exists() and not os.access(path, os.W_OK):
                raise PermissionError(
                    f"Elevated privileges required to update {path} "
                    "(run: sudo sim ire reconcile --apply)"
                )
        except PermissionError:
            raise
        except OSError as exc:
            raise PermissionError(
                f"Elevated privileges required to access {path} "
                "(run: sudo sim ire reconcile --apply)"
            ) from exc


class SSHReconciliationModule:
    """Reconcile SSH configuration toward desired state."""

    component = "ssh"

    def __init__(
        self,
        desired: SSHDesiredState,
        *,
        sshd_config: Path = SSHD_CONFIG,
        drop_in: Path = SIM_DROP_IN,
    ) -> None:
        self._desired = desired
        self._sshd_config = sshd_config
        self._drop_in = drop_in
        self._backup_path: Path | None = None

    def plan(self, drift: list[DriftItem]) -> list[PlanStep]:
        return [
            PlanStep(
                component=self.component,
                action=f"reconcile.{item.field}",
                description=item.message,
                auto_repairable=True,
            )
            for item in drift
            if item.auto_repairable
        ]

    def _generate_drop_in(self) -> str:
        lines = [
            "# Managed by SIM IRE — do not edit manually",
            f"Port {self._desired.port}",
            f"PermitRootLogin {'yes' if self._desired.root_login else 'no'}",
            f"PasswordAuthentication {'yes' if self._desired.password_authentication else 'no'}",
            "PubkeyAuthentication yes",
        ]
        if self._desired.allowed_users:
            lines.append(f"AllowUsers {' '.join(self._desired.allowed_users)}")
        # Deliberately omit ListenAddress — access control via firewall + auth.
        return "\n".join(lines) + "\n"

    def _backup(self, backup_dir: Path) -> Path:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        target = backup_dir / "ssh" / stamp
        target.mkdir(parents=True, exist_ok=True)
        if _safe_exists(self._drop_in):
            try:
                shutil.copy2(self._drop_in, target / self._drop_in.name)
            except OSError as exc:
                logger.warning("Could not back up %s: %s", self._drop_in, exc)
        if _safe_exists(self._sshd_config):
            try:
                shutil.copy2(self._sshd_config, target / self._sshd_config.name)
            except OSError as exc:
                logger.warning("Could not back up %s: %s", self._sshd_config, exc)
        self._backup_path = target
        return target

    def apply(self, steps: list[PlanStep], *, backup_dir: Path) -> dict[str, str]:
        if not steps:
            return {}
        _require_elevation(self._drop_in)
        self._backup(backup_dir)
        SSHD_CONFIG_D.mkdir(parents=True, exist_ok=True)
        content = self._generate_drop_in()
        self._drop_in.write_text(content, encoding="utf-8")

        ok, output = run_command(["sshd", "-t"])
        if not ok:
            self.rollback(backup_dir)
            raise RuntimeError(f"sshd -t failed after config write: {output}")

        ok, output = run_command(["systemctl", "restart", "sshd"])
        if not ok:
            self.rollback(backup_dir)
            raise RuntimeError(f"sshd restart failed: {output}")

        return {str(self._drop_in): "updated"}

    def verify(self) -> dict[str, str]:
        results: dict[str, str] = {}
        ok, output = run_command(["sshd", "-t"])
        results["sshd -t"] = "PASS" if ok else f"FAIL: {output}"

        ok, output = run_command(["systemctl", "is-active", "sshd"])
        results["sshd active"] = "PASS" if ok and output.strip() == "active" else "FAIL"

        ok, output = run_command(["ss", "-lntH"])
        port_ok = False
        if ok:
            for line in output.splitlines():
                if "sshd" in line and f":{self._desired.port}" in line:
                    port_ok = True
                    break
        results["sshd listening"] = "PASS" if port_ok else "FAIL"

        return results

    def rollback(self, backup_dir: Path) -> None:
        if self._backup_path is None:
            backup_ssh = backup_dir / "ssh"
            if backup_ssh.exists():
                candidates = sorted(backup_ssh.iterdir(), reverse=True)
                if candidates:
                    self._backup_path = candidates[0]
        if self._backup_path is None or not self._backup_path.exists():
            logger.warning("No SSH backup available for rollback")
            return
        drop_backup = self._backup_path / self._drop_in.name
        if drop_backup.exists():
            shutil.copy2(drop_backup, self._drop_in)
        elif _safe_exists(self._drop_in):
            try:
                self._drop_in.unlink()
            except OSError as exc:
                logger.warning("Could not remove %s during rollback: %s", self._drop_in, exc)
        run_command(["sshd", "-t"])
        run_command(["systemctl", "restart", "sshd"])


def observe_ssh_for_module() -> SSHObserved:
    """Re-export observation for tests."""
    from sim.ire.observed import _observe_ssh

    return _observe_ssh()
