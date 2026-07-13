"""Abstract base for SIM provisioning modules.

Each concrete module implements detect/install/verify hooks and is executed
through the same lifecycle used by phases: Detect -> Validate -> Install ->
Verify -> Complete. Module completion is persisted in SQLite so interrupted
runs can resume without redoing finished work.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from time import perf_counter

from sim.phases.lifecycle import CheckResult, PhaseResult, run_phase_lifecycle
from sim.state import StateManager


class InstallModule(ABC):
    """Contract for a single idempotent provisioning step."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Stable module identifier used as the state DB key."""

    @abstractmethod
    def detect(self) -> list[CheckResult]:
        """Pre-install detection and prerequisite validation."""

    @abstractmethod
    def install(self) -> None:
        """Apply host changes. Must be safe to re-run (idempotent)."""

    def verify(self) -> list[CheckResult]:
        """Post-install verification. Defaults to re-running detect checks."""
        return self.detect()

    def rollback(self) -> None:
        """Best-effort undo. Optional; Stage 7 will wire this into repair flows."""


def run_install_module(
    module: InstallModule,
    state: StateManager,
    *,
    dry_run: bool = False,
    reverify_completed: bool = True,
) -> PhaseResult:
    """Execute a provisioning module with resumability and optional dry-run.

    When ``reverify_completed`` is True (default), a module marked completed in
    state is re-run if ``detect()`` reports critical failures — manual drift or
    partial host changes no longer hide behind ``is_completed``.
    """
    started = perf_counter()
    if state.is_completed(module.name):
        if reverify_completed:
            detected = module.detect()
            failures = [c for c in detected if c.critical and c.status == "failed"]
            if not failures:
                return PhaseResult(
                    phase_name=module.name,
                    passed=True,
                    checks=[
                        CheckResult(
                            "resumability",
                            "passed",
                            f"Module {module.name!r} skipped: already completed and verified",
                            critical=False,
                        )
                    ],
                    duration_seconds=perf_counter() - started,
                )
            if dry_run:
                return PhaseResult(
                    phase_name=module.name,
                    passed=True,
                    checks=detected
                    + [
                        CheckResult(
                            "reverify",
                            "warning",
                            f"Module {module.name!r} drift detected; would re-install",
                            critical=False,
                        )
                    ],
                    duration_seconds=perf_counter() - started,
                )
            state.record_start(module.name)
            module.install()
            final_checks = module.verify()
            verify_failures = [c for c in final_checks if c.critical and c.status == "failed"]
            if verify_failures:
                message = "; ".join(f"{c.name}: {c.detail}" for c in verify_failures)
                state.record_failure(module.name, message)
                return PhaseResult(
                    phase_name=module.name,
                    passed=False,
                    checks=final_checks,
                    duration_seconds=perf_counter() - started,
                )
            state.record_success(
                module.name,
                detail={
                    "checks": [
                        {
                            "name": c.name,
                            "status": c.status,
                            "critical": c.critical,
                            "detail": c.detail,
                        }
                        for c in final_checks
                    ]
                },
            )
            return PhaseResult(
                phase_name=module.name,
                passed=True,
                checks=final_checks
                + [
                    CheckResult(
                        "reverify",
                        "passed",
                        f"Module {module.name!r} re-installed after drift",
                        critical=False,
                    )
                ],
                duration_seconds=perf_counter() - started,
            )
        return PhaseResult(
            phase_name=module.name,
            passed=True,
            checks=[
                CheckResult(
                    "resumability",
                    "passed",
                    f"Module {module.name!r} skipped: already completed",
                    critical=False,
                )
            ],
            duration_seconds=perf_counter() - started,
        )

    return run_phase_lifecycle(
        phase_name=module.name,
        state=state,
        detect=module.detect,
        install=None if dry_run else module.install,
        verify=lambda _checks: module.verify(),
        persist=not dry_run,
    )
