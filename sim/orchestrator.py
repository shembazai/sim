"""Unified provisioning orchestration with IRE preflight and repair flows."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Literal

from sim.checks.phase0 import run_phase0_checks
from sim.config import ManifestConfig
from sim.ire.desired import InfrastructureDesiredState
from sim.ire.drift import DriftItem, detect_drift
from sim.ire.engine import ReconciliationEngine, ReconciliationResult
from sim.ire.observed import collect_observed_state
from sim.ire.safety import SafetyReport, run_safety_checks
from sim.modules import (
    CudaModule,
    DependenciesModule,
    InitEnvironmentModule,
    InstallModule,
    NvidiaContainerModule,
    NvidiaDriverModule,
    PodmanModule,
    PythonRuntimeModule,
    QuadletModule,
    RegistryModule,
    run_install_module,
)
from sim.modules.gpu_util import nvidia_gpu_enabled
from sim.phases.lifecycle import CheckResult, PhaseResult, run_phase_lifecycle
from sim.phases.phase1_ports import (
    apply_assignments,
    assignment_checks,
    choose_ports_non_interactive,
    detect_used_tcp_ports,
    verify_assignments,
)
from sim.state import StateManager

InstallStageKind = Literal["phase0", "phase1", "module"]
RepairTargetKind = Literal["install", "ire"]

INSTALL_MODULE_ORDER: tuple[str, ...] = (
    "init_environment",
    "python_runtime",
    "dependencies",
    "podman",
    "quadlet",
    "registry",
    "nvidia_driver",
    "cuda",
    "nvidia_container",
)

IRE_REPAIR_TARGETS: frozenset[str] = frozenset({"ssh", "firewall"})


@dataclass(frozen=True)
class InstallStage:
    name: str
    kind: InstallStageKind
    run: Callable[[StateManager], PhaseResult]


@dataclass(frozen=True)
class PreflightResult:
    safety: SafetyReport
    drift: list[DriftItem]
    passed: bool
    message: str


@dataclass
class InstallPipelineResult:
    stages: list[PhaseResult] = field(default_factory=list)
    preflight: PreflightResult | None = None
    passed: bool = True
    message: str = ""


@dataclass(frozen=True)
class HealthReport:
    phase0_checks: list[CheckResult]
    drift: list[DriftItem]
    passed: bool
    message: str

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "message": self.message,
            "phase0": [
                {
                    "name": c.name,
                    "status": c.status,
                    "detail": c.detail,
                    "critical": c.critical,
                }
                for c in self.phase0_checks
            ],
            "drift": [
                {
                    "component": d.component,
                    "field": d.field,
                    "severity": d.severity,
                    "message": d.message,
                    "auto_repairable": d.auto_repairable,
                }
                for d in self.drift
            ],
        }


@dataclass(frozen=True)
class RepairResult:
    target: str
    kind: RepairTargetKind
    passed: bool
    message: str
    reconciliation: ReconciliationResult | None = None
    module_result: PhaseResult | None = None


def build_install_modules(cfg: ManifestConfig, *, dry_run: bool = False) -> dict[str, InstallModule]:
    """Construct install modules keyed by stable module name."""
    modules: dict[str, InstallModule] = {
        "init_environment": InitEnvironmentModule(cfg, dry_run=dry_run),
        "python_runtime": PythonRuntimeModule(cfg, dry_run=dry_run),
        "dependencies": DependenciesModule(cfg, dry_run=dry_run),
        "podman": PodmanModule(cfg, dry_run=dry_run),
        "quadlet": QuadletModule(cfg, dry_run=dry_run),
        "registry": RegistryModule(cfg, dry_run=dry_run),
    }
    if nvidia_gpu_enabled(cfg):
        modules["nvidia_driver"] = NvidiaDriverModule(cfg, dry_run=dry_run)
        modules["cuda"] = CudaModule(cfg, dry_run=dry_run)
        modules["nvidia_container"] = NvidiaContainerModule(cfg, dry_run=dry_run)
    return modules


def known_repair_targets(cfg: ManifestConfig) -> list[str]:
    """Return repairable install and IRE module names for this manifest."""
    targets = list(INSTALL_MODULE_ORDER)
    if not nvidia_gpu_enabled(cfg):
        targets = [name for name in targets if not name.startswith("nvidia") and name != "cuda"]
    targets.extend(sorted(IRE_REPAIR_TARGETS))
    return targets


def run_ire_preflight(
    desired: InfrastructureDesiredState,
    *,
    strict_drift: bool = False,
) -> PreflightResult:
    """Run panic-safe checks and optional drift gate before provisioning."""
    safety = run_safety_checks(desired)
    observed = collect_observed_state(storage_paths=[m.path for m in desired.storage.mounts])
    drift = detect_drift(desired, observed)

    if not safety.passed:
        failures = "; ".join(c.detail for c in safety.blocking_failures)
        return PreflightResult(
            safety=safety,
            drift=drift,
            passed=False,
            message=f"IRE safety blocked install: {failures}",
        )
    if strict_drift and drift:
        return PreflightResult(
            safety=safety,
            drift=drift,
            passed=False,
            message=f"IRE drift blocked install: {len(drift)} item(s) detected",
        )
    if drift:
        return PreflightResult(
            safety=safety,
            drift=drift,
            passed=True,
            message=f"IRE preflight passed with {len(drift)} drift warning(s)",
        )
    return PreflightResult(
        safety=safety,
        drift=drift,
        passed=True,
        message="IRE preflight passed",
    )


def run_health_check(cfg: ManifestConfig) -> HealthReport:
    """Combine Phase 0 critical checks with IRE drift for operator health view."""
    phase0 = run_phase0_checks(
        root=cfg.filesystem.root,
        min_free_gib=cfg.requirements.min_free_disk_gib,
    )
    critical_failures = [c for c in phase0 if c.critical and c.status == "failed"]
    observed = collect_observed_state(
        storage_paths=[m.path for m in cfg.infrastructure.storage.mounts],
    )
    drift = detect_drift(cfg.infrastructure, observed)
    critical_drift = [d for d in drift if d.severity == "critical"]

    if critical_failures and critical_drift:
        message = (
            f"{len(critical_failures)} critical Phase 0 failure(s), "
            f"{len(critical_drift)} critical drift item(s)"
        )
    elif critical_failures:
        message = f"{len(critical_failures)} critical Phase 0 failure(s)"
    elif critical_drift:
        message = f"{len(critical_drift)} critical drift item(s)"
    elif drift:
        message = f"No critical issues; {len(drift)} drift warning(s)"
    else:
        message = "Host healthy — no critical Phase 0 failures or drift"

    return HealthReport(
        phase0_checks=phase0,
        drift=drift,
        passed=not critical_failures and not critical_drift,
        message=message,
    )


def build_install_stages(
    cfg: ManifestConfig,
    *,
    manifest_path: Path,
    dry_run: bool = False,
    skip_phase1: bool = False,
) -> list[InstallStage]:
    """Ordered install stages: Phase 0, Phase 1, then provisioning modules."""
    modules = build_install_modules(cfg, dry_run=dry_run)
    stages: list[InstallStage] = []

    def _phase0(state: StateManager) -> PhaseResult:
        return run_phase_lifecycle(
            phase_name="phase0",
            state=state,
            detect=lambda: run_phase0_checks(
                root=cfg.filesystem.root,
                min_free_gib=cfg.requirements.min_free_disk_gib,
            ),
            skip_if_completed=True,
            persist=not dry_run,
        )

    stages.append(InstallStage("phase0", "phase0", _phase0))

    if not skip_phase1:
        def _phase1(state: StateManager) -> PhaseResult:
            used_ports = detect_used_tcp_ports()
            assignments = choose_ports_non_interactive(cfg, used_ports)
            return run_phase_lifecycle(
                phase_name="phase1_ports",
                state=state,
                detect=lambda: assignment_checks(assignments, used_ports),
                install=None
                if dry_run
                else lambda: apply_assignments(cfg, assignments, manifest_path),
                verify=lambda _checks: verify_assignments(
                    assignments,
                    detect_used_tcp_ports(),
                    cfg=cfg,
                    manifest_path=manifest_path,
                    dry_run=dry_run,
                ),
                skip_if_completed=True,
                persist=not dry_run,
            )

        stages.append(InstallStage("phase1_ports", "phase1", _phase1))

    for name in INSTALL_MODULE_ORDER:
        module = modules.get(name)
        if module is None:
            continue

        def _make_runner(mod: InstallModule) -> Callable[[StateManager], PhaseResult]:
            def _run(state: StateManager) -> PhaseResult:
                return run_install_module(mod, state, dry_run=dry_run)
            return _run

        stages.append(InstallStage(name, "module", _make_runner(module)))
    return stages


def run_install_pipeline(
    cfg: ManifestConfig,
    state: StateManager,
    *,
    manifest_path: Path,
    dry_run: bool = False,
    skip_ire_preflight: bool = False,
    strict_drift: bool = False,
    skip_phase1: bool = False,
    from_stage: str | None = None,
) -> InstallPipelineResult:
    """Run the full provisioning pipeline with optional IRE preflight."""
    result = InstallPipelineResult()
    if not skip_ire_preflight:
        preflight = run_ire_preflight(cfg.infrastructure, strict_drift=strict_drift)
        result.preflight = preflight
        if not preflight.passed:
            result.passed = False
            result.message = preflight.message
            return result

    stages = build_install_stages(
        cfg,
        manifest_path=manifest_path,
        dry_run=dry_run,
        skip_phase1=skip_phase1,
    )
    stage_names = [s.name for s in stages]
    start = from_stage is None
    if from_stage is not None:
        if from_stage not in stage_names:
            result.passed = False
            result.message = f"Unknown stage {from_stage!r}; choose from: {', '.join(stage_names)}"
            return result

    for stage in stages:
        if not start:
            if stage.name == from_stage:
                start = True
            else:
                continue
        phase_result = stage.run(state)
        result.stages.append(phase_result)
        if not phase_result.passed:
            result.passed = False
            result.message = f"Install failed at {stage.name}"
            return result

    result.message = "Install pipeline completed successfully"
    return result


def _ire_engine_for_target(
    cfg: ManifestConfig,
    target: str,
    *,
    transaction_db: Path,
    backup_dir: Path,
) -> ReconciliationEngine | None:
    if target == "ssh":
        from sim.ire.modules.ssh import SSHReconciliationModule

        modules = [SSHReconciliationModule(cfg.infrastructure.ssh)]
    elif target == "firewall":
        from sim.ire.modules.firewall import FirewallReconciliationModule

        modules = [FirewallReconciliationModule(cfg.infrastructure.firewall)]
    else:
        return None
    return ReconciliationEngine(
        desired=cfg.infrastructure,
        transaction_db=transaction_db,
        backup_dir=backup_dir,
        report_dir=cfg.report.directory,
        modules=modules,
    )


def repair_target(
    target: str,
    cfg: ManifestConfig,
    state: StateManager,
    *,
    dry_run: bool = False,
    transaction_db: Path = Path("/opt/k1/state/ire_transactions.db"),
    backup_dir: Path = Path("/opt/k1/state/backups"),
) -> RepairResult:
    """Rollback and re-apply a single install or IRE module."""
    if target in IRE_REPAIR_TARGETS:
        engine = _ire_engine_for_target(
            cfg,
            target,
            transaction_db=transaction_db,
            backup_dir=backup_dir,
        )
        if engine is None:
            return RepairResult(target, "ire", False, f"Unknown IRE target {target!r}")
        if dry_run:
            reconcile = engine.reconcile(dry_run=True)
            return RepairResult(
                target=target,
                kind="ire",
                passed=reconcile.safety.passed,
                message=f"Dry-run repair plan for {target}",
                reconciliation=reconcile,
            )
        for module in engine.modules:
            module.rollback(backup_dir)
        reconcile = engine.reconcile(dry_run=False, require_ssh_path=False)
        return RepairResult(
            target=target,
            kind="ire",
            passed=reconcile.committed,
            message=reconcile.message,
            reconciliation=reconcile,
        )

    modules = build_install_modules(cfg, dry_run=dry_run)
    module = modules.get(target)
    if module is None:
        available = ", ".join(known_repair_targets(cfg))
        return RepairResult(
            target,
            "install",
            False,
            f"Unknown repair target {target!r}. Available: {available}",
        )

    module.rollback()
    state.record_rollback(target)
    module_result = run_install_module(module, state, dry_run=dry_run)
    return RepairResult(
        target=target,
        kind="install",
        passed=module_result.passed,
        message=f"Repair {'succeeded' if module_result.passed else 'failed'} for {target}",
        module_result=module_result,
    )
