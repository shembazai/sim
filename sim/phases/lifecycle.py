"""Shared phase lifecycle orchestration.

All SIM phases use the same lifecycle:
Detect -> Validate -> Install -> Verify -> Report -> Complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Callable, Literal

from sim.state import StateManager

CheckStatus = Literal["passed", "failed", "warning"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    detail: str
    critical: bool = True


@dataclass(frozen=True)
class PhaseResult:
    phase_name: str
    passed: bool
    checks: list[CheckResult]
    duration_seconds: float


def _critical_failures(checks: list[CheckResult]) -> list[CheckResult]:
    return [c for c in checks if c.critical and c.status == "failed"]


def run_phase_lifecycle(
    *,
    phase_name: str,
    state: StateManager,
    detect: Callable[[], list[CheckResult]],
    install: Callable[[], None] | None = None,
    verify: Callable[[list[CheckResult]], list[CheckResult]] | None = None,
    skip_if_completed: bool = False,
    persist: bool = True,
) -> PhaseResult:
    """Execute a phase with the canonical SIM lifecycle.

    The first production increment allows a no-op install callable for
    validation-only phases such as Phase 0. When ``skip_if_completed`` is
    True and the phase already passed in state, the lifecycle returns
    immediately without re-running detect/install/verify. Set ``persist``
    to False for dry-run invocations that must not update module state.
    """
    started = perf_counter()

    if skip_if_completed and state.is_completed(phase_name):
        return PhaseResult(
            phase_name=phase_name,
            passed=True,
            checks=[
                CheckResult(
                    "resumability",
                    "passed",
                    f"Phase {phase_name!r} skipped: already completed",
                    critical=False,
                )
            ],
            duration_seconds=perf_counter() - started,
        )

    def _record(status, **kwargs) -> None:
        if persist:
            state.record_status(phase_name, status, **kwargs)

    _record("Validated")
    detected = detect()
    failures = _critical_failures(detected)
    if failures:
        message = "; ".join(f"{c.name}: {c.detail}" for c in failures)
        _record("Failed", error=message)
        return PhaseResult(
            phase_name=phase_name,
            passed=False,
            checks=detected,
            duration_seconds=perf_counter() - started,
        )

    _record("Installing")
    if install is not None:
        install()

    _record("Verifying")
    final_checks = verify(detected) if verify is not None else detected
    failures = _critical_failures(final_checks)
    if failures:
        message = "; ".join(f"{c.name}: {c.detail}" for c in failures)
        _record("Failed", error=message)
        return PhaseResult(
            phase_name=phase_name,
            passed=False,
            checks=final_checks,
            duration_seconds=perf_counter() - started,
        )

    _record(
        "Passed",
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
        phase_name=phase_name,
        passed=True,
        checks=final_checks,
        duration_seconds=perf_counter() - started,
    )
