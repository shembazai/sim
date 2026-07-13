from pathlib import Path

import pytest

from sim.state import StateLockedError, StateManager


def test_module_lifecycle_success(tmp_path: Path):
    sm = StateManager(db_path=tmp_path / "state.db")
    assert not sm.is_completed("gpu")

    sm.record_start("gpu")
    rec = sm.get("gpu")
    assert rec is not None
    assert rec.status == "Installing"
    assert not sm.is_completed("gpu")

    sm.record_success("gpu", detail={"driver_version": "550.90"})
    assert sm.is_completed("gpu")
    rec = sm.get("gpu")
    assert rec.detail == {"driver_version": "550.90"}
    sm.close()


def test_module_lifecycle_failure_then_retry(tmp_path: Path):
    sm = StateManager(db_path=tmp_path / "state.db")
    sm.record_start("podman")
    sm.record_failure("podman", "dnf transaction failed: network timeout")
    rec = sm.get("podman")
    assert rec.status == "Failed"
    assert "network timeout" in rec.error
    assert not sm.is_completed("podman")

    # Simulate resume: SIM re-runs the same module.
    sm.record_start("podman")
    sm.record_success("podman")
    assert sm.is_completed("podman")
    sm.close()


def test_resumability_skips_completed_modules(tmp_path: Path):
    db_path = tmp_path / "state.db"
    sm1 = StateManager(db_path=db_path)
    sm1.record_start("ollama")
    sm1.record_success("ollama")
    sm1.close()

    # New process, same DB file -- must see prior completion.
    sm2 = StateManager(db_path=db_path)
    assert sm2.is_completed("ollama")
    sm2.close()


def test_process_lock_prevents_concurrent_runs(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("sim.state._pid_is_alive", lambda pid: True)
    sm = StateManager(db_path=tmp_path / "state.db")
    with sm.process_lock(pid=1234):
        with pytest.raises(StateLockedError):
            with sm.process_lock(pid=5678):
                pass
    # Lock released after context exit -- must be re-acquirable.
    with sm.process_lock(pid=5678):
        pass
    sm.close()


def test_process_lock_recovers_from_stale_pid(tmp_path: Path, monkeypatch):
    sm = StateManager(db_path=tmp_path / "state.db")
    sm._conn.execute(
        "INSERT INTO lock (id, pid, acquired_at) VALUES (1, 999999, 0)",
    )
    monkeypatch.setattr("sim.state._pid_is_alive", lambda pid: False)

    with sm.process_lock(pid=4321):
        row = sm._conn.execute("SELECT pid FROM lock WHERE id = 1").fetchone()
        assert row is not None
        assert row[0] == 4321
    sm.close()


def test_history_ordered_by_start_time(tmp_path: Path):
    sm = StateManager(db_path=tmp_path / "state.db")
    for name in ["hardware", "gpu", "podman"]:
        sm.record_start(name)
        sm.record_success(name)
    names = [r.name for r in sm.history()]
    assert names == ["hardware", "gpu", "podman"]
    sm.close()
