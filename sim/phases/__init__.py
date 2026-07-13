"""Phase execution primitives for SIM."""

from sim.phases.lifecycle import CheckResult, CheckStatus, PhaseResult, run_phase_lifecycle

__all__ = [
    "CheckResult",
    "CheckStatus",
    "PhaseResult",
    "run_phase_lifecycle",
]
