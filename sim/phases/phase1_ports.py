"""Phase 1 port management.

Detects used ports, proposes defaults, validates assignments, and persists
resolved ports to the infrastructure manifest.
"""

from __future__ import annotations

from pathlib import Path

from sim.config import ManifestConfig
from sim.phases.lifecycle import CheckResult
from sim.subprocess_util import run_command
from sim.ui import choose_port, confirm, console, port_table

SERVICE_LABELS: dict[str, str] = {
    "open_webui": "Open WebUI",
    "grafana": "Grafana",
    "prometheus": "Prometheus",
    "ollama": "Ollama",
    "k1": "K1 API",
}

RECOMMENDED_PORTS: dict[str, list[int]] = {
    "open_webui": [3000, 8080, 8888],
    "grafana": [3001, 3100, 9091],
    "prometheus": [9090, 9092, 9190],
    "ollama": [11434],
    "k1": [8000],
}

SERVICE_ORDER = ["open_webui", "grafana", "prometheus", "ollama", "k1"]


def detect_used_tcp_ports() -> set[int]:
    ok, output = run_command(["ss", "-lntH"])
    if not ok:
        raise RuntimeError(f"Failed to detect used TCP ports via ss: {output}")

    ports: set[int] = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        local_addr = parts[3]
        if ":" not in local_addr:
            continue
        port_raw = local_addr.rsplit(":", 1)[-1]
        if port_raw.isdigit():
            ports.add(int(port_raw))
    return ports


def _service_enabled(cfg: ManifestConfig, service_name: str) -> bool:
    if not hasattr(cfg.services, service_name):
        raise ValueError(f"Unknown service in phase1 configuration: {service_name}")
    service_cfg = getattr(cfg.services, service_name)
    return bool(service_cfg.enabled)


def choose_ports_non_interactive(cfg: ManifestConfig, used_ports: set[int]) -> dict[str, int]:
    assignments: dict[str, int] = {}

    for service_name in SERVICE_ORDER:
        if not _service_enabled(cfg, service_name):
            continue

        selected: int | None = None
        for candidate in RECOMMENDED_PORTS[service_name]:
            if candidate in used_ports or candidate in assignments.values():
                continue
            selected = candidate
            break

        if selected is None:
            raise ValueError(
                f"No available recommended ports for {service_name}. "
                f"Run in interactive mode and provide a custom value."
            )

        assignments[service_name] = selected
    return assignments


def choose_ports_interactive(cfg: ManifestConfig, used_ports: set[int]) -> dict[str, int]:
    assignments: dict[str, int] = {}

    for service_name in SERVICE_ORDER:
        if not _service_enabled(cfg, service_name):
            continue

        display = SERVICE_LABELS[service_name]
        while True:
            selected = choose_port(display, RECOMMENDED_PORTS[service_name])
            if selected in assignments.values():
                console.print(f"[yellow]Port {selected} is already assigned in this phase. Choose another.[/yellow]")
                continue
            if selected in used_ports:
                console.print(f"[yellow]Port {selected} is currently in use. Choose another.[/yellow]")
                continue
            if not confirm(f"Use port {selected} for {display}?", default=True):
                continue
            assignments[service_name] = selected
            break

    return assignments


def assignment_checks(assignments: dict[str, int], used_ports: set[int]) -> list[CheckResult]:
    checks: list[CheckResult] = []
    seen: dict[int, str] = {}

    for service_name, port in assignments.items():
        if port in seen:
            checks.append(
                CheckResult(
                    name=f"port_{service_name}",
                    status="failed",
                    detail=f"Duplicate port {port} also assigned to {seen[port]}",
                    critical=True,
                )
            )
            continue

        if port in used_ports:
            checks.append(
                CheckResult(
                    name=f"port_{service_name}",
                    status="failed",
                    detail=f"Port {port} is in use",
                    critical=True,
                )
            )
            seen[port] = service_name
            continue

        checks.append(
            CheckResult(
                name=f"port_{service_name}",
                status="passed",
                detail=f"Port {port} available",
                critical=True,
            )
        )
        seen[port] = service_name

    return checks


def verify_assignments(
    assignments: dict[str, int],
    used_ports: set[int],
    *,
    cfg: ManifestConfig,
    manifest_path: Path,
    dry_run: bool,
) -> list[CheckResult]:
    """Verify port availability and, when not dry-run, manifest persistence."""
    checks = assignment_checks(assignments, used_ports)
    if dry_run:
        return checks

    if not manifest_path.exists():
        checks.append(
            CheckResult(
                name="manifest_persisted",
                status="failed",
                detail=f"Manifest not found at {manifest_path}",
                critical=True,
            )
        )
        return checks

    reloaded = ManifestConfig.load(manifest_path)
    for service_name, expected_port in assignments.items():
        actual_port = getattr(reloaded.services, service_name).port
        if actual_port != expected_port:
            checks.append(
                CheckResult(
                    name=f"manifest_{service_name}",
                    status="failed",
                    detail=(
                        f"Expected port {expected_port} in manifest, found {actual_port}"
                    ),
                    critical=True,
                )
            )
            continue
        checks.append(
            CheckResult(
                name=f"manifest_{service_name}",
                status="passed",
                detail=f"Manifest records port {actual_port}",
                critical=True,
            )
        )
    return checks


def apply_assignments(cfg: ManifestConfig, assignments: dict[str, int], manifest_path: Path) -> None:
    for service_name, port in assignments.items():
        getattr(cfg.services, service_name).port = port
    cfg.dump(manifest_path)


def render_assignment_table(assignments: dict[str, int], used_ports: set[int]):
    rows = [
        (SERVICE_LABELS[name], port, port not in used_ports)
        for name, port in assignments.items()
    ]
    return port_table(rows)
