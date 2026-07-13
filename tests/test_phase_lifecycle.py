from pathlib import Path

from sim.phases.lifecycle import CheckResult, run_phase_lifecycle
from sim.report import write_phase_reports
from sim.state import StateManager


def test_lifecycle_stops_on_critical_failure(tmp_path: Path):
    sm = StateManager(db_path=tmp_path / "state.db")
    result = run_phase_lifecycle(
        phase_name="phase0",
        state=sm,
        detect=lambda: [CheckResult("dnf", "failed", "dnf missing", critical=True)],
    )
    rec = sm.get("phase0")
    assert result.passed is False
    assert rec is not None
    assert rec.status == "Failed"
    sm.close()


def test_lifecycle_passes_and_writes_reports(tmp_path: Path):
    sm = StateManager(db_path=tmp_path / "state.db")
    result = run_phase_lifecycle(
        phase_name="phase0",
        state=sm,
        detect=lambda: [
            CheckResult("cpu", "passed", "ok", critical=True),
            CheckResult("gpu", "warning", "not found", critical=False),
        ],
    )
    rec = sm.get("phase0")
    assert result.passed is True
    assert rec is not None
    assert rec.status == "Passed"

    json_path, md_path = write_phase_reports(result, tmp_path / "reports")
    assert json_path.exists()
    assert md_path.exists()
    assert "phase0" in json_path.read_text(encoding="utf-8")
    assert "| cpu | PASS |" in md_path.read_text(encoding="utf-8")
    sm.close()
