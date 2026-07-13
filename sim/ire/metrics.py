"""Prometheus textfile metrics for IRE health and drift status."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from sim.ire.safety import SafetyReport
from sim.orchestrator import HealthReport


def build_prometheus_metrics(*, health: HealthReport, safety: SafetyReport) -> str:
    """Render node_exporter-compatible textfile metrics."""
    phase0_critical = sum(
        1 for check in health.phase0_checks if check.critical and check.status == "failed"
    )
    drift_counts = Counter(item.severity for item in health.drift)
    repairable = sum(1 for item in health.drift if item.auto_repairable)

    lines = [
        "# HELP sim_health_passed Whether sim health passed (1=yes, 0=no).",
        "# TYPE sim_health_passed gauge",
        f"sim_health_passed {1 if health.passed else 0}",
        "# HELP sim_ire_safety_passed Whether IRE safety checks passed (1=yes, 0=no).",
        "# TYPE sim_ire_safety_passed gauge",
        f"sim_ire_safety_passed {1 if safety.passed else 0}",
        "# HELP sim_phase0_critical_failures_total Critical Phase 0 check failures.",
        "# TYPE sim_phase0_critical_failures_total gauge",
        f"sim_phase0_critical_failures_total {phase0_critical}",
        "# HELP sim_ire_drift_items_total IRE drift items by severity.",
        "# TYPE sim_ire_drift_items_total gauge",
    ]
    for severity in ("info", "warning", "critical"):
        lines.append(f'sim_ire_drift_items_total{{severity="{severity}"}} {drift_counts.get(severity, 0)}')
    lines.extend(
        [
            "# HELP sim_ire_drift_repairable_total Repairable IRE drift items.",
            "# TYPE sim_ire_drift_repairable_total gauge",
            f"sim_ire_drift_repairable_total {repairable}",
        ]
    )
    return "\n".join(lines) + "\n"


def write_prometheus_textfile(content: str, output_file: Path) -> Path:
    """Atomically write metrics for node_exporter textfile collector."""
    output_file.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_file.with_suffix(output_file.suffix + ".tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(output_file)
    return output_file
