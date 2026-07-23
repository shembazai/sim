"""SIM CLI entrypoint."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import typer

from sim import __version__
from sim.checks.phase0 import run_phase0_checks
from sim.config import detect_host_os, os_recommendation_warning
from sim.inventory import collect_inventory, write_inventory
from sim.logger import configure_logging
from sim.phases.phase1_ports import (
    apply_assignments,
    assignment_checks,
    choose_ports_interactive,
    choose_ports_non_interactive,
    detect_used_tcp_ports,
    render_assignment_table,
    verify_assignments,
)
from sim.modules import (
    CudaModule,
    DependenciesModule,
    InitEnvironmentModule,
    NvidiaContainerModule,
    NvidiaDriverModule,
    PodmanModule,
    PythonRuntimeModule,
    QuadletModule,
    RegistryModule,
    run_install_module,
)
from sim.phases.lifecycle import run_phase_lifecycle
from sim.report import write_phase_reports
from sim.state import StateLockedError, StateManager
from sim.ui import console
from sim.ire.engine import ReconciliationEngine

app = typer.Typer(
    name="sim",
    help="Shembazai Infrastructure Manager - deterministic provisioning for Rocky Linux 10.",
    no_args_is_help=True,
)

ire_app = typer.Typer(
    name="ire",
    help="Infrastructure Reconciliation Engine — observe, compare, and reconcile.",
    no_args_is_help=True,
)
app.add_typer(ire_app)


def _version_callback(value: bool) -> None:
    if value:
        console.print(f"SIM (Shembazai Infrastructure Manager) v{__version__}")
        raise typer.Exit()


def _load_manifest(manifest: Path):
    from sim.config import ManifestConfig

    if manifest.exists():
        return ManifestConfig.load(manifest)
    return ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})


def _infrastructure_from_manifest(manifest: Path):
    cfg = _load_manifest(manifest)
    return cfg.infrastructure


def _print_check_results(result) -> None:
    for check in result.checks:
        color = "green" if check.status == "passed" else "red" if check.status == "failed" else "yellow"
        crit = "critical" if check.critical else "advisory"
        console.print(f"[{color}]\u2022 {check.name} ({crit}): {check.status} - {check.detail}[/{color}]")


def _print_state_report(state: StateManager) -> None:
    records = state.history()
    if not records:
        console.print("[yellow]No module history recorded yet.[/yellow]")
        return
    console.print("[bold]Module state history[/bold]")
    for rec in records:
        color = "green" if rec.status in ("Passed", "completed") else "red" if rec.status in ("Failed", "failed") else "yellow"
        error = f" — {rec.error}" if rec.error else ""
        console.print(f"[{color}]{rec.name}: {rec.status}{error}[/{color}]")


@app.callback()
def main(
    ctx: typer.Context,
    version: Optional[bool] = typer.Option(
        None, "--version", callback=_version_callback, is_eager=True,
        help="Show SIM's version and exit.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Run detection/validation only; make no changes to the host.",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable DEBUG logging."),
    log_dir: Path = typer.Option(Path("/opt/k1/logs"), help="Directory for sim.log."),
) -> None:
    level = "DEBUG" if verbose else "INFO"
    logger = configure_logging(level=level, log_dir=log_dir)
    logger.debug("SIM starting: dry_run=%s verbose=%s", dry_run, verbose)
    ctx.obj = {
        "dry_run": dry_run,
        "verbose": verbose,
        "log_dir": log_dir,
    }


@app.command()
def check_os(
    strict: bool = typer.Option(
        False, "--strict",
        help="Exit non-zero if the host deviates from the recommended OS "
             "(Rocky Linux 10+). Default is advisory-only: warn and continue.",
    ),
) -> None:
    """Report the host OS against SIM's recommended target.

    Rocky Linux 10+ is the recommended and tested target. Off-recommendation
    hosts are untested, not blocked -- this prints a warning and exits 0
    unless --strict is passed.
    """
    try:
        distro, version = detect_host_os()
    except FileNotFoundError as exc:
        console.print(f"[red]\u2717 {exc}[/red]")
        raise typer.Exit(code=1)

    warning = os_recommendation_warning(distro, version)
    if warning is None:
        console.print(f"[green]\u2713 Host OS: {distro} {version} (matches recommendation)[/green]")
        raise typer.Exit(code=0)

    console.print(f"[yellow]\u26a0 {warning}[/yellow]")
    if strict:
        raise typer.Exit(code=1)
    raise typer.Exit(code=0)


@app.command()
def phase0(
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    state_db: Path = typer.Option(
        Path("/opt/k1/state/sim_state.db"),
        "--state-db",
        help="Path to the SIM state database.",
    ),
) -> None:
    """Run mandatory Phase 0 host validation.

    Phase 0 executes before installation phases. It validates host readiness,
    records state transitions, and emits Markdown/JSON reports.
    """
    cfg = _load_manifest(manifest)

    with StateManager(db_path=state_db) as state:
        try:
            with state.process_lock(pid=os.getpid()):
                result = run_phase_lifecycle(
                    phase_name="phase0",
                    state=state,
                    detect=lambda: run_phase0_checks(
                        root=cfg.filesystem.root,
                        min_free_gib=cfg.requirements.min_free_disk_gib,
                    ),
                )
        except StateLockedError as exc:
            console.print(f"[red]State lock error:[/red] {exc}")
            raise typer.Exit(code=1)

    json_path, md_path = write_phase_reports(result, cfg.report.directory)
    inventory_path: Path | None = None
    if result.passed:
        storage_paths = [Path(m.path) for m in cfg.infrastructure.storage.mounts]
        inventory = collect_inventory(
            cfg.filesystem.root,
            storage_paths=storage_paths or None,
        )
        inventory_path = write_inventory(inventory, cfg.inventory.file)

    _print_check_results(result)
    console.print(f"JSON report: {json_path}")
    console.print(f"Markdown report: {md_path}")
    if inventory_path is not None:
        console.print(f"Inventory snapshot: {inventory_path}")
    raise typer.Exit(code=0 if result.passed else 1)


@app.command("phase1-ports")
def phase1_ports(
    ctx: typer.Context,
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    state_db: Path = typer.Option(
        Path("/opt/k1/state/sim_state.db"),
        "--state-db",
        help="Path to the SIM state database.",
    ),
    interactive: bool = typer.Option(
        True,
        "--interactive/--no-interactive",
        help="Use interactive prompts to choose service ports.",
    ),
) -> None:
    """Run Phase 1 port management and persist validated assignments."""
    cfg = _load_manifest(manifest)
    dry_run = bool((ctx.obj or {}).get("dry_run", False))

    try:
        with StateManager(db_path=state_db) as state:
            with state.process_lock(pid=os.getpid()):
                used_ports = detect_used_tcp_ports()
                assignments = (
                    choose_ports_interactive(cfg, used_ports)
                    if interactive
                    else choose_ports_non_interactive(cfg, used_ports)
                )

                result = run_phase_lifecycle(
                    phase_name="phase1_ports",
                    state=state,
                    detect=lambda: assignment_checks(assignments, used_ports),
                    install=lambda: _phase1_install(
                        cfg=cfg,
                        assignments=assignments,
                        manifest=manifest,
                        dry_run=dry_run,
                    ),
                    verify=lambda _checks: verify_assignments(
                        assignments,
                        detect_used_tcp_ports(),
                        cfg=cfg,
                        manifest_path=manifest,
                        dry_run=dry_run,
                    ),
                )
    except (RuntimeError, ValueError, StateLockedError) as exc:
        console.print(f"[red]Phase 1 failed:[/red] {exc}")
        raise typer.Exit(code=1)

    json_path, md_path = write_phase_reports(result, cfg.report.directory)
    console.print(render_assignment_table(assignments, detect_used_tcp_ports()))
    _print_check_results(result)
    console.print(f"JSON report: {json_path}")
    console.print(f"Markdown report: {md_path}")

    if dry_run:
        console.print("[yellow]Dry-run enabled:[/yellow] no manifest changes were applied.")

    raise typer.Exit(code=0 if result.passed else 1)


def _phase1_install(
    *,
    cfg,
    assignments: dict[str, int],
    manifest: Path,
    dry_run: bool,
) -> None:
    if dry_run:
        return
    apply_assignments(cfg, assignments, manifest)


@app.command("phase2-init")
def phase2_init(
    ctx: typer.Context,
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    state_db: Path = typer.Option(
        Path("/opt/k1/state/sim_state.db"),
        "--state-db",
        help="Path to the SIM state database.",
    ),
) -> None:
    """Run Stage 2a environment initialization (filesystem layout)."""
    cfg = _load_manifest(manifest)
    dry_run = bool((ctx.obj or {}).get("dry_run", False))
    module = InitEnvironmentModule(cfg, dry_run=dry_run)

    try:
        with StateManager(db_path=state_db) as state:
            with state.process_lock(pid=os.getpid()):
                result = run_install_module(module, state, dry_run=dry_run)
    except StateLockedError as exc:
        console.print(f"[red]State lock error:[/red] {exc}")
        raise typer.Exit(code=1)

    json_path, md_path = write_phase_reports(result, cfg.report.directory)
    _print_check_results(result)
    console.print(f"JSON report: {json_path}")
    console.print(f"Markdown report: {md_path}")
    if dry_run:
        console.print("[yellow]Dry-run enabled:[/yellow] no directories were created.")
    raise typer.Exit(code=0 if result.passed else 1)


@app.command("phase2-python")
def phase2_python(
    ctx: typer.Context,
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    state_db: Path = typer.Option(
        Path("/opt/k1/state/sim_state.db"),
        "--state-db",
        help="Path to the SIM state database.",
    ),
) -> None:
    """Run Stage 2b Python runtime validation and venv provisioning."""
    cfg = _load_manifest(manifest)
    dry_run = bool((ctx.obj or {}).get("dry_run", False))
    module = PythonRuntimeModule(cfg, dry_run=dry_run)

    try:
        with StateManager(db_path=state_db) as state:
            with state.process_lock(pid=os.getpid()):
                result = run_install_module(module, state, dry_run=dry_run)
    except StateLockedError as exc:
        console.print(f"[red]State lock error:[/red] {exc}")
        raise typer.Exit(code=1)

    json_path, md_path = write_phase_reports(result, cfg.report.directory)
    _print_check_results(result)
    console.print(f"JSON report: {json_path}")
    console.print(f"Markdown report: {md_path}")
    if dry_run:
        console.print("[yellow]Dry-run enabled:[/yellow] no venv was created.")
    raise typer.Exit(code=0 if result.passed else 1)


@app.command("phase2-deps")
def phase2_deps(
    ctx: typer.Context,
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    state_db: Path = typer.Option(
        Path("/opt/k1/state/sim_state.db"),
        "--state-db",
        help="Path to the SIM state database.",
    ),
) -> None:
    """Run Stage 2c host dependency verification and installation."""
    cfg = _load_manifest(manifest)
    dry_run = bool((ctx.obj or {}).get("dry_run", False))
    module = DependenciesModule(cfg, dry_run=dry_run)

    try:
        with StateManager(db_path=state_db) as state:
            with state.process_lock(pid=os.getpid()):
                result = run_install_module(module, state, dry_run=dry_run)
    except (StateLockedError, RuntimeError) as exc:
        console.print(f"[red]Stage 2c failed:[/red] {exc}")
        raise typer.Exit(code=1)

    json_path, md_path = write_phase_reports(result, cfg.report.directory)
    _print_check_results(result)
    console.print(f"JSON report: {json_path}")
    console.print(f"Markdown report: {md_path}")
    if dry_run:
        console.print("[yellow]Dry-run enabled:[/yellow] no packages were installed.")
    raise typer.Exit(code=0 if result.passed else 1)


def _run_install_module_command(
    *,
    module,
    cfg,
    state_db: Path,
    dry_run: bool,
    dry_run_message: str,
    failure_label: str,
) -> None:
    try:
        with StateManager(db_path=state_db) as state:
            with state.process_lock(pid=os.getpid()):
                result = run_install_module(module, state, dry_run=dry_run)
    except (StateLockedError, RuntimeError) as exc:
        console.print(f"[red]{failure_label}:[/red] {exc}")
        raise typer.Exit(code=1)

    json_path, md_path = write_phase_reports(result, cfg.report.directory)
    _print_check_results(result)
    console.print(f"JSON report: {json_path}")
    console.print(f"Markdown report: {md_path}")
    if dry_run:
        console.print(f"[yellow]Dry-run enabled:[/yellow] {dry_run_message}")
    raise typer.Exit(code=0 if result.passed else 1)


@app.command("phase3-podman")
def phase3_podman(
    ctx: typer.Context,
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    state_db: Path = typer.Option(
        Path("/opt/k1/state/sim_state.db"),
        "--state-db",
        help="Path to the SIM state database.",
    ),
) -> None:
    """Run Stage 3a Podman runtime installation and configuration."""
    cfg = _load_manifest(manifest)
    dry_run = bool((ctx.obj or {}).get("dry_run", False))
    _run_install_module_command(
        module=PodmanModule(cfg, dry_run=dry_run),
        cfg=cfg,
        state_db=state_db,
        dry_run=dry_run,
        dry_run_message="no Podman configuration was applied.",
        failure_label="Stage 3a failed",
    )


@app.command("phase3-quadlet")
def phase3_quadlet(
    ctx: typer.Context,
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    state_db: Path = typer.Option(
        Path("/opt/k1/state/sim_state.db"),
        "--state-db",
        help="Path to the SIM state database.",
    ),
) -> None:
    """Run Stage 3b Quadlet directory preparation and systemd reload."""
    cfg = _load_manifest(manifest)
    dry_run = bool((ctx.obj or {}).get("dry_run", False))
    _run_install_module_command(
        module=QuadletModule(cfg, dry_run=dry_run),
        cfg=cfg,
        state_db=state_db,
        dry_run=dry_run,
        dry_run_message="no Quadlet directories were created.",
        failure_label="Stage 3b failed",
    )


@app.command("phase3-registry")
def phase3_registry(
    ctx: typer.Context,
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    state_db: Path = typer.Option(
        Path("/opt/k1/state/sim_state.db"),
        "--state-db",
        help="Path to the SIM state database.",
    ),
) -> None:
    """Run Stage 3c local registry cache policy configuration."""
    cfg = _load_manifest(manifest)
    dry_run = bool((ctx.obj or {}).get("dry_run", False))
    _run_install_module_command(
        module=RegistryModule(cfg, dry_run=dry_run),
        cfg=cfg,
        state_db=state_db,
        dry_run=dry_run,
        dry_run_message="no registry policy files were written.",
        failure_label="Stage 3c failed",
    )


@app.command("phase4-nvidia-driver")
def phase4_nvidia_driver(
    ctx: typer.Context,
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    state_db: Path = typer.Option(
        Path("/opt/k1/state/sim_state.db"),
        "--state-db",
        help="Path to the SIM state database.",
    ),
) -> None:
    """Run Stage 4a NVIDIA driver installation and verification."""
    cfg = _load_manifest(manifest)
    dry_run = bool((ctx.obj or {}).get("dry_run", False))
    _run_install_module_command(
        module=NvidiaDriverModule(cfg, dry_run=dry_run),
        cfg=cfg,
        state_db=state_db,
        dry_run=dry_run,
        dry_run_message="no NVIDIA driver packages were installed.",
        failure_label="Stage 4a failed",
    )


@app.command("phase4-cuda")
def phase4_cuda(
    ctx: typer.Context,
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    state_db: Path = typer.Option(
        Path("/opt/k1/state/sim_state.db"),
        "--state-db",
        help="Path to the SIM state database.",
    ),
) -> None:
    """Run Stage 4b CUDA toolkit installation when enabled in the manifest."""
    cfg = _load_manifest(manifest)
    dry_run = bool((ctx.obj or {}).get("dry_run", False))
    _run_install_module_command(
        module=CudaModule(cfg, dry_run=dry_run),
        cfg=cfg,
        state_db=state_db,
        dry_run=dry_run,
        dry_run_message="no CUDA packages were installed.",
        failure_label="Stage 4b failed",
    )


@app.command("phase4-nvidia-container")
def phase4_nvidia_container(
    ctx: typer.Context,
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    state_db: Path = typer.Option(
        Path("/opt/k1/state/sim_state.db"),
        "--state-db",
        help="Path to the SIM state database.",
    ),
) -> None:
    """Run Stage 4c NVIDIA Container Toolkit configuration for Podman."""
    cfg = _load_manifest(manifest)
    dry_run = bool((ctx.obj or {}).get("dry_run", False))
    _run_install_module_command(
        module=NvidiaContainerModule(cfg, dry_run=dry_run),
        cfg=cfg,
        state_db=state_db,
        dry_run=dry_run,
        dry_run_message="no NVIDIA container toolkit changes were applied.",
        failure_label="Stage 4c failed",
    )


@app.command("state-report")
def state_report(
    state_db: Path = typer.Option(
        Path("/opt/k1/state/sim_state.db"),
        "--state-db",
        help="Path to the SIM state database.",
    ),
) -> None:
    """Print module completion history from the SIM state database."""
    with StateManager(db_path=state_db) as state:
        _print_state_report(state)


@app.command("health")
def health(
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON to stdout.",
    ),
) -> None:
    """Operator health dashboard: Phase 0 critical checks plus IRE drift."""
    from sim.orchestrator import run_health_check

    cfg = _load_manifest(manifest)
    report = run_health_check(cfg)
    if as_json:
        typer.echo(json.dumps(report.to_dict(), indent=2))
    else:
        console.print(f"[bold]Health:[/bold] {report.message}")
        console.print("[bold]Phase 0[/bold]")
        for check in report.phase0_checks:
            if not check.critical:
                continue
            color = "green" if check.status == "passed" else "red" if check.status == "failed" else "yellow"
            console.print(f"  [{color}]{check.name}: {check.detail}[/{color}]")
        if report.drift:
            console.print("[bold]IRE drift[/bold]")
            _print_drift(report.drift)
        elif report.passed:
            console.print("[green]No IRE drift detected.[/green]")
    raise typer.Exit(code=0 if report.passed else 1)


@app.command("install")
def install(
    ctx: typer.Context,
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    state_db: Path = typer.Option(
        Path("/opt/k1/state/sim_state.db"),
        "--state-db",
        help="Path to the SIM state database.",
    ),
    skip_ire_preflight: bool = typer.Option(
        False,
        "--skip-ire-preflight",
        help="Skip IRE safety/drift checks before provisioning.",
    ),
    strict_drift: bool = typer.Option(
        False,
        "--strict-drift",
        help="Block install when IRE drift is detected.",
    ),
    skip_phase1: bool = typer.Option(
        False,
        "--skip-phase1",
        help="Skip Phase 1 port assignment during install.",
    ),
    from_stage: str | None = typer.Option(
        None,
        "--from-stage",
        help="Resume install from this stage name (e.g. podman).",
    ),
) -> None:
    """Run the full provisioning pipeline with IRE preflight."""
    from sim.orchestrator import run_install_pipeline

    cfg = _load_manifest(manifest)
    dry_run = bool((ctx.obj or {}).get("dry_run", False))

    try:
        with StateManager(db_path=state_db) as state:
            with state.process_lock(pid=os.getpid()):
                result = run_install_pipeline(
                    cfg,
                    state,
                    manifest_path=manifest,
                    dry_run=dry_run,
                    skip_ire_preflight=skip_ire_preflight,
                    strict_drift=strict_drift,
                    skip_phase1=skip_phase1,
                    from_stage=from_stage,
                )
    except StateLockedError as exc:
        console.print(f"[red]State lock error:[/red] {exc}")
        raise typer.Exit(code=1)

    if result.preflight is not None:
        console.print(f"[bold]IRE preflight:[/bold] {result.preflight.message}")
        if result.preflight.drift:
            _print_drift(result.preflight.drift)

    if not result.passed and not result.stages:
        console.print(f"[red]{result.message}[/red]")
        raise typer.Exit(code=2)

    for stage_result in result.stages:
        color = "green" if stage_result.passed else "red"
        console.print(
            f"[{color}]{stage_result.phase_name}: "
            f"{'passed' if stage_result.passed else 'failed'}[/{color}]"
        )
        if not stage_result.passed:
            _print_check_results(stage_result)
            json_path, md_path = write_phase_reports(stage_result, cfg.report.directory)
            console.print(f"JSON report: {json_path}")
            console.print(f"Markdown report: {md_path}")
            raise typer.Exit(code=1)

    console.print(f"[green]{result.message}[/green]")
    if dry_run:
        console.print("[yellow]Dry-run enabled:[/yellow] no host changes were applied.")
    raise typer.Exit(code=0)


@app.command("repair")
def repair(
    ctx: typer.Context,
    target: str = typer.Argument(..., help="Module to repair (e.g. ssh, podman, init_environment)."),
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    state_db: Path = typer.Option(
        Path("/opt/k1/state/sim_state.db"),
        "--state-db",
        help="Path to the SIM state database.",
    ),
    tx_db: Path = typer.Option(
        Path("/opt/k1/state/ire_transactions.db"),
        "--tx-db",
        help="IRE transaction evidence database.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply repair (default is plan-only / dry-run).",
    ),
    allow_blocked_ssh: bool = typer.Option(
        False,
        "--allow-blocked-ssh",
        help="Allow IRE repair when SSH recovery path safety would otherwise block.",
    ),
) -> None:
    """Rollback and re-apply a single install or IRE module.

    Dry-run by default (SIM constitution §3.3). Pass ``--apply`` to mutate.
    """
    from sim.orchestrator import known_repair_targets, repair_target

    cfg = _load_manifest(manifest)
    # Explicit --apply wins; otherwise honor global --dry-run / default dry-run.
    dry_run = not apply
    if apply is False and bool((ctx.obj or {}).get("dry_run", False)):
        dry_run = True
    available = known_repair_targets(cfg)
    if target not in available:
        console.print(
            f"[red]Unknown repair target {target!r}.[/red] "
            f"Available: {', '.join(available)}"
        )
        raise typer.Exit(code=2)

    try:
        with StateManager(db_path=state_db) as state:
            with state.process_lock(pid=os.getpid()):
                result = repair_target(
                    target,
                    cfg,
                    state,
                    dry_run=dry_run,
                    transaction_db=tx_db,
                    require_ssh_path=not allow_blocked_ssh,
                )
    except StateLockedError as exc:
        console.print(f"[red]State lock error:[/red] {exc}")
        raise typer.Exit(code=1)

    color = "green" if result.passed else "red"
    console.print(f"[{color}]{result.message}[/{color}]")
    if dry_run:
        console.print("[yellow]Dry-run enabled:[/yellow] no host changes were applied. Re-run with --apply.")
    if result.module_result is not None:
        _print_check_results(result.module_result)
    if result.reconciliation is not None and result.reconciliation.plan.drift:
        _print_drift(result.reconciliation.plan.drift)
    raise typer.Exit(code=0 if result.passed else 1)


def _print_drift(drift: list) -> None:
    if not drift:
        console.print("[green]No drift detected.[/green]")
        return
    for item in drift:
        color = "red" if item.severity == "critical" else "yellow" if item.severity == "warning" else "white"
        repair = "repairable" if item.auto_repairable else "observe-only"
        console.print(
            f"[{color}]{item.component}.{item.field} ({item.severity}, {repair}): "
            f"{item.message}[/{color}]"
        )


def _ire_engine(manifest: Path, tx_db: Path, *, with_modules: bool = False) -> ReconciliationEngine:
    cfg = _load_manifest(manifest)
    modules = []
    if with_modules:
        from sim.ire.modules.firewall import FirewallReconciliationModule
        from sim.ire.modules.ssh import SSHReconciliationModule

        modules = [
            SSHReconciliationModule(cfg.infrastructure.ssh),
            FirewallReconciliationModule(cfg.infrastructure.firewall),
        ]
    return ReconciliationEngine(
        desired=cfg.infrastructure,
        transaction_db=tx_db,
        report_dir=cfg.report.directory,
        modules=modules,
    )


@ire_app.command("storage")
def ire_storage(
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Write storage report JSON to this path.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON to stdout.",
    ),
) -> None:
    """Report storage mount health for desired infrastructure mounts."""
    from sim.ire.modules.storage import (
        StorageObserver,
        build_storage_report,
        storage_report_exit_code,
    )

    cfg = _load_manifest(manifest)
    observer = StorageObserver()
    observed = observer.observe_mounts([m.path for m in cfg.infrastructure.storage.mounts])
    reports = build_storage_report(cfg.infrastructure, observed)

    payload = {
        "mounts": [
            {
                "path": r.path,
                "mounted": r.mounted,
                "uuid": r.uuid,
                "fstype": r.fstype,
                "source": r.source,
                "findmnt_source": r.findmnt_source,
                "findmnt_fstype": r.findmnt_fstype,
                "mount_sources_agree": r.mount_sources_agree,
                "free_gib": r.free_gib,
                "total_gib": r.total_gib,
                "btrfs_healthy": r.btrfs_healthy,
                "snapshot_count": r.snapshot_count,
                "smart_healthy": r.smart_healthy,
                "drift": [
                    {
                        "field": d.field,
                        "severity": d.severity,
                        "message": d.message,
                        "auto_repairable": d.auto_repairable,
                    }
                    for d in r.drift
                ],
            }
            for r in reports
        ]
    }

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        console.print(f"Storage report written to {output}")

    if as_json:
        typer.echo(json.dumps(payload, indent=2))
    elif output is None:
        for report in reports:
            status = "mounted" if report.mounted else "MISSING"
            color = "green" if report.mounted else "yellow"
            console.print(f"[bold]{report.path}[/bold]: [{color}]{status}[/{color}]")
            if report.uuid:
                console.print(f"  uuid: {report.uuid}")
            if report.fstype:
                console.print(f"  fstype: {report.fstype}")
            if report.free_gib is not None:
                console.print(f"  free: {report.free_gib} GiB / {report.total_gib} GiB")
            if report.mount_sources_agree is False:
                console.print("  [yellow]proc_mounts / findmnt mismatch[/yellow]")
            if report.btrfs_healthy is False:
                console.print("  [red]btrfs: degraded[/red]")
            if report.smart_healthy is False:
                console.print("  [red]smart: failed[/red]")
            for item in report.drift:
                color = "red" if item.severity == "critical" else "yellow"
                console.print(f"  [{color}]drift: {item.message}[/{color}]")

    code = storage_report_exit_code(reports, cfg.infrastructure.storage.mounts)
    raise typer.Exit(code=code)


@ire_app.command("tailscale")
def ire_tailscale(
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Write Tailscale report JSON to this path.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON to stdout.",
    ),
) -> None:
    """Report Tailscale identity and connectivity health."""
    from sim.ire.modules.tailscale import build_tailscale_report, tailscale_report_exit_code

    cfg = _load_manifest(manifest)
    report = build_tailscale_report(cfg.infrastructure)
    payload = {
        "installed": report.installed,
        "online": report.online,
        "hostname": report.hostname,
        "tailnet": report.tailnet,
        "drift": [
            {
                "field": d.field,
                "severity": d.severity,
                "message": d.message,
                "auto_repairable": d.auto_repairable,
            }
            for d in report.drift
        ],
    }

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        console.print(f"Tailscale report written to {output}")

    if as_json:
        typer.echo(json.dumps(payload, indent=2))
    elif output is None:
        status = "online" if report.online else "offline" if report.installed else "not installed"
        color = "green" if report.online else "yellow"
        console.print(f"[bold]Tailscale[/bold]: [{color}]{status}[/{color}]")
        if report.hostname:
            console.print(f"  hostname: {report.hostname}")
        if report.tailnet:
            console.print(f"  tailnet: {report.tailnet}")
        for item in report.drift:
            color = "red" if item.severity == "critical" else "yellow"
            console.print(f"  [{color}]drift: {item.message}[/{color}]")

    code = tailscale_report_exit_code(report, cfg.infrastructure.tailscale)
    raise typer.Exit(code=code)


@ire_app.command("transactions")
def ire_transactions(
    tx_db: Path = typer.Option(
        Path("/opt/k1/state/ire_transactions.db"),
        "--tx-db",
        help="IRE transaction evidence database.",
    ),
    limit: int = typer.Option(
        50,
        "--limit",
        help="Maximum number of transactions to list.",
    ),
    status: str | None = typer.Option(
        None,
        "--status",
        help="Filter by status (PLANNED, BLOCKED, COMMITTED, ROLLED_BACK, FAILED).",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON to stdout.",
    ),
) -> None:
    """List IRE transaction history."""
    from sim.ire.transaction import TransactionStore

    valid_statuses = {"PLANNED", "BLOCKED", "COMMITTED", "ROLLED_BACK", "FAILED"}
    if status is not None and status not in valid_statuses:
        console.print(f"[red]Invalid status {status!r}. Choose from: {', '.join(sorted(valid_statuses))}[/red]")
        raise typer.Exit(code=2)

    with TransactionStore(tx_db) as store:
        records = store.history(limit=limit, status=status)  # type: ignore[arg-type]

    if as_json:
        typer.echo(json.dumps([r.to_dict() for r in records], indent=2))
    elif not records:
        console.print("No transactions found.")
    else:
        for record in records:
            color = {
                "COMMITTED": "green",
                "BLOCKED": "yellow",
                "FAILED": "red",
                "ROLLED_BACK": "yellow",
            }.get(record.status, "white")
            console.print(
                f"[{color}]{record.transaction_id}[/{color}] "
                f"{record.timestamp} — {record.status}"
            )
            if record.detail:
                console.print(f"  {record.detail}")
    raise typer.Exit(code=0)


@ire_app.command("show")
def ire_show(
    transaction_id: str = typer.Argument(..., help="Transaction ID (e.g. TX-20260710-SSH-001)."),
    tx_db: Path = typer.Option(
        Path("/opt/k1/state/ire_transactions.db"),
        "--tx-db",
        help="IRE transaction evidence database.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Write evidence report to this path.",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Emit machine-readable JSON to stdout.",
    ),
    as_markdown: bool = typer.Option(
        False,
        "--markdown",
        help="Emit Markdown evidence report to stdout.",
    ),
) -> None:
    """Show full evidence report for a transaction."""
    from sim.ire.transaction import TransactionStore, format_transaction_markdown

    with TransactionStore(tx_db) as store:
        record = store.get(transaction_id)
    if record is None:
        console.print(f"[red]Transaction not found: {transaction_id}[/red]")
        raise typer.Exit(code=1)

    if as_json:
        content = json.dumps(record.to_dict(), indent=2)
    elif as_markdown:
        content = format_transaction_markdown(record)
    else:
        content = format_transaction_markdown(record)

    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content + ("\n" if not content.endswith("\n") else ""), encoding="utf-8")
        console.print(f"Evidence report written to {output}")
    elif as_json:
        typer.echo(content)
    else:
        console.print(content)
    raise typer.Exit(code=0)


@ire_app.command("observe")
def ire_observe(
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Write observed state JSON to this path.",
    ),
) -> None:
    """Collect and display runtime infrastructure state."""
    engine = _ire_engine(manifest, Path("/opt/k1/state/ire_transactions.db"))
    observed = engine.observe()
    payload = observed.model_dump(mode="json")
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        console.print(f"Observed state written to {output}")
    else:
        # Machine-readable channel: typer.echo (SIM constitution §5)
        typer.echo(json.dumps(payload, indent=2))
    raise typer.Exit(code=0)


@ire_app.command("drift")
def ire_drift(
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
) -> None:
    """Compare desired state with observed state and report drift."""
    engine = _ire_engine(manifest, Path("/opt/k1/state/ire_transactions.db"))
    observed = engine.observe()
    plan = engine.plan(observed)
    _print_drift(plan.drift)
    raise typer.Exit(code=1 if plan.has_drift else 0)


@ire_app.command("plan")
def ire_plan(
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
) -> None:
    """Generate a reconciliation execution plan without applying changes."""
    engine = _ire_engine(manifest, Path("/opt/k1/state/ire_transactions.db"))
    result = engine.reconcile(dry_run=True)
    _print_drift(result.plan.drift)
    if result.plan.steps:
        console.print("[bold]Planned steps[/bold]")
        for step in result.plan.steps:
            console.print(f"- [{step.component}] {step.action}: {step.description}")
    if result.transaction:
        console.print(f"Transaction: {result.transaction.transaction_id} ({result.transaction.status})")
    console.print(result.message)
    raise typer.Exit(code=0)


@ire_app.command("reconcile")
def ire_reconcile(
    ctx: typer.Context,
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    tx_db: Path = typer.Option(
        Path("/opt/k1/state/ire_transactions.db"),
        "--tx-db",
        help="IRE transaction evidence database.",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help="Apply repairable changes (default is plan-only).",
    ),
    allow_blocked_ssh: bool = typer.Option(
        False,
        "--allow-blocked-ssh",
        help="Do not block when SSH recovery path is unavailable.",
    ),
) -> None:
    """Run the IRE lifecycle: observe, compare, validate safety, plan or apply."""
    dry_run = not apply
    if (ctx.obj or {}).get("dry_run"):
        dry_run = True
    engine = _ire_engine(manifest, tx_db, with_modules=apply)
    result = engine.reconcile(
        dry_run=dry_run,
        require_ssh_path=not allow_blocked_ssh,
    )
    _print_drift(result.plan.drift)
    if not result.safety.passed:
        for check in result.safety.blocking_failures:
            console.print(f"[red]BLOCKED: {check.name} — {check.detail}[/red]")
    if result.transaction:
        console.print(
            f"Transaction {result.transaction.transaction_id}: {result.transaction.status}"
        )
        cfg = _load_manifest(manifest)
        evidence_json = cfg.report.directory / f"{result.transaction.transaction_id}.json"
        evidence_md = cfg.report.directory / f"{result.transaction.transaction_id}.md"
        if evidence_json.exists():
            console.print(f"Evidence JSON: {evidence_json}")
            console.print(f"Evidence report: {evidence_md}")
    console.print(result.message)
    code = 0
    if result.transaction and result.transaction.status == "BLOCKED":
        code = 2
    elif result.plan.has_drift and not result.committed and dry_run:
        code = 1
    elif result.transaction and result.transaction.status == "FAILED":
        code = 1
    raise typer.Exit(code=code)


@ire_app.command("safety")
def ire_safety(
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
) -> None:
    """Run panic-safe pre-change checks."""
    from sim.ire.safety import run_safety_checks

    cfg = _load_manifest(manifest)
    report = run_safety_checks(cfg.infrastructure)
    for check in report.checks:
        color = "green" if check.passed else "red" if check.blocking else "yellow"
        console.print(f"[{color}]{check.name}: {check.detail}[/{color}]")
    raise typer.Exit(code=0 if report.passed else 2)


@ire_app.command("metrics")
def ire_metrics(
    manifest: Path = typer.Option(
        Path("k1_server_manifest.yaml"),
        "--manifest",
        help="Path to the infrastructure manifest.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        help="Write Prometheus textfile metrics to this path.",
    ),
    stdout: bool = typer.Option(
        False,
        "--stdout",
        help="Print metrics to stdout instead of writing a file.",
    ),
) -> None:
    """Export IRE health and drift gauges for Prometheus textfile collector."""
    from sim.ire.metrics import build_prometheus_metrics, write_prometheus_textfile
    from sim.ire.safety import run_safety_checks
    from sim.orchestrator import run_health_check

    cfg = _load_manifest(manifest)
    health = run_health_check(cfg)
    safety = run_safety_checks(cfg.infrastructure)
    content = build_prometheus_metrics(health=health, safety=safety)

    if stdout:
        typer.echo(content, nl=False)
        raise typer.Exit(code=0 if health.passed and safety.passed else 1)

    target = output or cfg.monitoring.prometheus_textfile or (cfg.report.directory / "sim_ire.prom")
    path = write_prometheus_textfile(content, target)
    console.print(f"Prometheus metrics: {path}")
    raise typer.Exit(code=0 if health.passed and safety.passed else 1)


if __name__ == "__main__":
    app()
