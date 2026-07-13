"""Infrastructure Reconciliation Engine (IRE) for SIM.

Separates desired infrastructure intent from runtime observation and
reconciles through a transactional, rollback-capable lifecycle.
"""

from sim.ire.desired import (
    FirewallDesiredState,
    InfrastructureDesiredState,
    SSHDesiredState,
    StorageDesiredState,
    StorageMountDesired,
    TailscaleDesiredState,
)
from sim.ire.engine import ReconciliationEngine, ReconciliationResult
from sim.ire.observed import ObservedState, collect_observed_state
from sim.ire.transaction import TransactionRecord, TransactionStatus

__all__ = [
    "FirewallDesiredState",
    "InfrastructureDesiredState",
    "ObservedState",
    "ReconciliationEngine",
    "ReconciliationResult",
    "SSHDesiredState",
    "StorageDesiredState",
    "StorageMountDesired",
    "TailscaleDesiredState",
    "TransactionRecord",
    "TransactionStatus",
    "collect_observed_state",
]
