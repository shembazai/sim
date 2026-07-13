"""Reconciliation engine lifecycle orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from sim.ire.desired import InfrastructureDesiredState
from sim.ire.drift import DriftItem, detect_drift
from sim.ire.models import ObservedState
from sim.ire.observed import collect_observed_state
from sim.ire.safety import SafetyReport, reconcile_privilege_check, run_safety_checks
from sim.ire.transaction import (
    TransactionRecord,
    TransactionStore,
    generate_transaction_id,
    write_transaction_reports,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PlanStep:
    component: str
    action: str
    description: str
    auto_repairable: bool


@dataclass
class ReconciliationPlan:
    drift: list[DriftItem] = field(default_factory=list)
    steps: list[PlanStep] = field(default_factory=list)

    @property
    def has_drift(self) -> bool:
        return bool(self.drift)

    @property
    def repairable_steps(self) -> list[PlanStep]:
        return [s for s in self.steps if s.auto_repairable]


class ReconciliationModule(Protocol):
    """Component-specific reconcile implementation."""

    component: str

    def plan(self, drift: list[DriftItem]) -> list[PlanStep]:
        ...

    def apply(self, steps: list[PlanStep], *, backup_dir: Path) -> dict[str, str]:
        ...

    def verify(self) -> dict[str, str]:
        ...

    def rollback(self, backup_dir: Path) -> None:
        ...


@dataclass
class ReconciliationResult:
    plan: ReconciliationPlan
    safety: SafetyReport
    transaction: TransactionRecord | None
    observed: ObservedState
    committed: bool
    message: str


def build_plan(desired: InfrastructureDesiredState, observed: ObservedState) -> ReconciliationPlan:
    drift = detect_drift(desired, observed)
    steps: list[PlanStep] = []
    for item in drift:
        if not item.auto_repairable:
            continue
        steps.append(
            PlanStep(
                component=item.component,
                action=f"reconcile.{item.field}",
                description=item.message,
                auto_repairable=True,
            )
        )
    return ReconciliationPlan(drift=drift, steps=steps)


class ReconciliationEngine:
    """Observe → compare → plan → validate → backup → execute → verify → commit/rollback."""

    def __init__(
        self,
        desired: InfrastructureDesiredState,
        *,
        transaction_db: Path = Path("/opt/k1/state/ire_transactions.db"),
        backup_dir: Path = Path("/opt/k1/state/backups"),
        report_dir: Path | None = None,
        modules: list[ReconciliationModule] | None = None,
    ) -> None:
        self.desired = desired
        self.transaction_db = transaction_db
        self.backup_dir = backup_dir
        self.report_dir = report_dir
        self.modules = modules or []

    def _persist_transaction(self, tx: TransactionRecord) -> None:
        with TransactionStore(self.transaction_db) as store:
            store.save(tx)
        if self.report_dir is not None:
            write_transaction_reports(tx, self.report_dir)

    def observe(self) -> ObservedState:
        return collect_observed_state(
            storage_paths=[m.path for m in self.desired.storage.mounts],
        )

    def plan(self, observed: ObservedState | None = None) -> ReconciliationPlan:
        observed = observed or self.observe()
        base = build_plan(self.desired, observed)
        extra_steps: list[PlanStep] = []
        for module in self.modules:
            component_drift = [d for d in base.drift if d.component == module.component]
            extra_steps.extend(module.plan(component_drift))
        return ReconciliationPlan(drift=base.drift, steps=base.steps + extra_steps)

    def reconcile(
        self,
        *,
        dry_run: bool = True,
        require_ssh_path: bool = True,
        component: str = "IRE",
    ) -> ReconciliationResult:
        observed = self.observe()
        plan = self.plan(observed)
        safety = run_safety_checks(
            self.desired,
            observed=observed,
            require_ssh_path=require_ssh_path,
        )

        if not plan.has_drift:
            return ReconciliationResult(
                plan=plan,
                safety=safety,
                transaction=None,
                observed=observed,
                committed=False,
                message="No drift detected — host matches desired state.",
            )

        tx = TransactionRecord(
            transaction_id=generate_transaction_id(component),
            timestamp=datetime.now(UTC).isoformat(),
            target_host=observed.hostname,
            status="PLANNED",
            detail=f"{len(plan.drift)} drift item(s), {len(plan.repairable_steps)} repairable step(s)",
        )

        if not safety.passed:
            failures = "; ".join(c.detail for c in safety.blocking_failures)
            tx.status = "BLOCKED"
            tx.detail = failures
            self._persist_transaction(tx)
            return ReconciliationResult(
                plan=plan,
                safety=safety,
                transaction=tx,
                observed=observed,
                committed=False,
                message=f"SIM BLOCKED CHANGE: {failures}",
            )

        if not dry_run:
            privilege = reconcile_privilege_check(
                {step.component for step in plan.repairable_steps},
            )
            if privilege is not None:
                safety = SafetyReport(checks=[*safety.checks, privilege])
                if not safety.passed:
                    tx.status = "BLOCKED"
                    tx.detail = privilege.detail
                    self._persist_transaction(tx)
                    return ReconciliationResult(
                        plan=plan,
                        safety=safety,
                        transaction=tx,
                        observed=observed,
                        committed=False,
                        message=f"SIM BLOCKED CHANGE: {privilege.detail}",
                    )

        if dry_run:
            tx.status = "PLANNED"
            self._persist_transaction(tx)
            return ReconciliationResult(
                plan=plan,
                safety=safety,
                transaction=tx,
                observed=observed,
                committed=False,
                message="Dry-run complete — no changes applied.",
            )

        self.backup_dir.mkdir(parents=True, exist_ok=True)
        validation: dict[str, str] = {}
        changed: list[str] = []
        try:
            for module in self.modules:
                component_steps = [s for s in plan.repairable_steps if s.component == module.component]
                if not component_steps:
                    continue
                results = module.apply(component_steps, backup_dir=self.backup_dir)
                validation.update(results)
                changed.extend(results.keys())
            post_verify: dict[str, str] = {}
            for module in self.modules:
                post_verify.update(module.verify())
            validation.update(post_verify)
            post_observed = self.observe()
            remaining = detect_drift(self.desired, post_observed)
            if remaining:
                raise RuntimeError(
                    f"Verification failed — {len(remaining)} drift item(s) remain after apply"
                )
            tx.changed_resources = changed
            tx.validation_results = validation
            tx.rollback_available = True
            tx.status = "COMMITTED"
            self._persist_transaction(tx)
            return ReconciliationResult(
                plan=plan,
                safety=safety,
                transaction=tx,
                observed=post_observed,
                committed=True,
                message=f"Reconciliation committed: {tx.transaction_id}",
            )
        except Exception as exc:
            if isinstance(exc, PermissionError):
                logger.error("Reconciliation failed: %s", exc)
            else:
                logger.exception("Reconciliation failed; attempting rollback")
            for module in reversed(self.modules):
                try:
                    module.rollback(self.backup_dir)
                except Exception:
                    logger.exception("Rollback failed for module %s", module.component)
            tx.status = "FAILED"
            tx.detail = str(exc)
            self._persist_transaction(tx)
            return ReconciliationResult(
                plan=plan,
                safety=safety,
                transaction=tx,
                observed=observed,
                committed=False,
                message=f"Reconciliation failed and was rolled back: {exc}",
            )
