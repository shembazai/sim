"""Phase reporting utilities.

Each phase emits both JSON and Markdown reports.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sim.phases.lifecycle import CheckResult, PhaseResult


def _status_symbol(status: str) -> str:
    if status == "passed":
        return "PASS"
    if status == "failed":
        return "FAIL"
    return "WARN"


def write_phase_reports(result: PhaseResult, report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    base_name = f"{result.phase_name}_{timestamp}"
    json_path = report_dir / f"{base_name}.json"
    md_path = report_dir / f"{base_name}.md"

    payload = {
        "phase": result.phase_name,
        "passed": result.passed,
        "duration_seconds": round(result.duration_seconds, 3),
        "generated_at": datetime.now(UTC).isoformat(),
        "checks": [asdict(c) for c in result.checks],
    }
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        f"# {result.phase_name} report",
        "",
        f"- Passed: {'yes' if result.passed else 'no'}",
        f"- Duration (s): {result.duration_seconds:.3f}",
        "",
        "| Check | Status | Critical | Detail |",
        "| --- | --- | --- | --- |",
    ]
    for check in result.checks:
        lines.append(
            f"| {check.name} | {_status_symbol(check.status)} | {'yes' if check.critical else 'no'} | {check.detail} |"
        )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    return json_path, md_path
