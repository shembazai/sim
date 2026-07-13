"""Firewalld reconciliation module — declarative zone and service management."""

from __future__ import annotations

import logging
from pathlib import Path

from sim.ire.desired import FirewallDesiredState
from sim.ire.drift import DriftItem
from sim.ire.engine import PlanStep
from sim.subprocess_util import run_command

logger = logging.getLogger(__name__)


class FirewallReconciliationModule:
    """Reconcile firewalld interface zones and service exposure."""

    component = "firewall"

    def __init__(self, desired: FirewallDesiredState) -> None:
        self._desired = desired
        self._applied_zones: list[str] = []

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

    def _ensure_active(self) -> None:
        ok, output = run_command(["systemctl", "is-active", "firewalld"])
        if not ok or output.strip() != "active":
            ok, output = run_command(["systemctl", "start", "firewalld"])
            if not ok:
                raise RuntimeError(f"Failed to start firewalld: {output}")

    def apply(self, steps: list[PlanStep], *, backup_dir: Path) -> dict[str, str]:
        if not steps:
            return {}
        del backup_dir  # firewalld changes are reversible via firewall-cmd
        self._ensure_active()
        changed: dict[str, str] = {}

        for iface, iface_desired in self._desired.interfaces.items():
            ok, output = run_command(
                [
                    "firewall-cmd",
                    "--permanent",
                    f"--zone={iface_desired.zone}",
                    f"--change-interface={iface}",
                ]
            )
            if not ok:
                raise RuntimeError(
                    f"Failed to assign {iface} to zone {iface_desired.zone}: {output}"
                )
            changed[f"interface:{iface}"] = f"zone={iface_desired.zone}"
            self._applied_zones.append(iface_desired.zone)

        ssh_service = self._desired.services.get("ssh")
        if ssh_service:
            zones_needed: set[str] = set()
            for iface in ssh_service.allowed_interfaces:
                zone = self._desired.interfaces.get(iface)
                if zone:
                    zones_needed.add(zone.zone)
            for zone in zones_needed:
                ok, output = run_command(
                    ["firewall-cmd", "--permanent", f"--zone={zone}", "--add-service=ssh"]
                )
                if not ok and "ALREADY_ENABLED" not in output.upper():
                    raise RuntimeError(f"Failed to add ssh to zone {zone}: {output}")
                changed[f"service:ssh@{zone}"] = "added"

        ok, output = run_command(["firewall-cmd", "--reload"])
        if not ok:
            raise RuntimeError(f"firewalld reload failed: {output}")
        changed["firewalld"] = "reloaded"
        return changed

    def verify(self) -> dict[str, str]:
        results: dict[str, str] = {}
        ok, output = run_command(["systemctl", "is-active", "firewalld"])
        results["firewalld active"] = "PASS" if ok and output.strip() == "active" else "FAIL"

        ok, output = run_command(["firewall-cmd", "--get-active-zones"])
        for iface, iface_desired in self._desired.interfaces.items():
            in_zone = f"{iface_desired.zone}\n  {iface}" in output or (
                iface in output and iface_desired.zone in output
            )
            results[f"zone:{iface}"] = "PASS" if in_zone else "FAIL"

        return results

    def rollback(self, backup_dir: Path) -> None:
        del backup_dir
        logger.info("Firewalld rollback: manual review of firewall-cmd --list-all may be required")
