from pathlib import Path

import pytest

from sim.config import ManifestConfig
from sim.modules.base import run_install_module
from sim.modules.init_environment import InitEnvironmentModule, STANDARD_SUBDIRS
from sim.phases.lifecycle import CheckResult, run_phase_lifecycle
from sim.state import StateManager


def test_run_install_module_skips_completed(tmp_path: Path):
    db_path = tmp_path / "state.db"
    sm = StateManager(db_path=db_path)
    sm.record_success("init_environment")
    root = tmp_path / "k1"
    for name in STANDARD_SUBDIRS:
        (root / name).mkdir(parents=True)
    (root / "state" / "backups").mkdir(parents=True)
    cfg = ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "filesystem": {"root": str(root)},
        }
    )
    module = InitEnvironmentModule(cfg, dry_run=False)

    install_called = False

    def _install() -> None:
        nonlocal install_called
        install_called = True

    module.install = _install  # type: ignore[method-assign]
    result = run_install_module(module, sm, dry_run=False)

    assert result.passed is True
    assert install_called is False
    assert any(c.name == "resumability" for c in result.checks)
    sm.close()


def test_run_install_module_runs_lifecycle_when_not_completed(tmp_path: Path):
    sm = StateManager(db_path=tmp_path / "state.db")
    root = tmp_path / "k1"
    cfg = ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "filesystem": {"root": str(root)},
        }
    )
    module = InitEnvironmentModule(cfg, dry_run=False)
    result = run_install_module(module, sm, dry_run=False)

    assert result.passed is True
    assert sm.is_completed("init_environment")
    for name in STANDARD_SUBDIRS:
        assert (root / name).is_dir()
    assert (root / "state" / "backups").is_dir()
    sm.close()


def test_init_environment_idempotent(tmp_path: Path):
    root = tmp_path / "k1"
    cfg = ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "filesystem": {"root": str(root)},
        }
    )
    module = InitEnvironmentModule(cfg, dry_run=False)
    module.install()
    first_mtime = (root / "logs").stat().st_mtime_ns
    module.install()
    second_mtime = (root / "logs").stat().st_mtime_ns
    assert first_mtime == second_mtime


def test_init_environment_dry_run_does_not_create_dirs(tmp_path: Path):
    root = tmp_path / "k1"
    cfg = ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "filesystem": {"root": str(root)},
        }
    )
    sm = StateManager(db_path=tmp_path / "state.db")
    module = InitEnvironmentModule(cfg, dry_run=True)
    result = run_install_module(module, sm, dry_run=True)

    assert result.passed is True
    assert not root.exists()
    assert not sm.is_completed("init_environment")
    assert any(c.status == "warning" for c in result.checks)
    sm.close()


def test_init_environment_detect_fails_when_parent_not_writable(tmp_path: Path, monkeypatch):
    root = tmp_path / "locked" / "k1"
    cfg = ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "filesystem": {"root": str(root)},
        }
    )
    module = InitEnvironmentModule(cfg, dry_run=False)
    monkeypatch.setattr("sim.modules.init_environment.os.access", lambda _path, _mode: False)
    checks = module.detect()
    assert any(c.name == "root" and c.status == "failed" for c in checks)


def test_lifecycle_skip_if_completed(tmp_path: Path):
    sm = StateManager(db_path=tmp_path / "state.db")
    sm.record_success("phase0")
    detect_called = False

    def _detect() -> list[CheckResult]:
        nonlocal detect_called
        detect_called = True
        return [CheckResult("cpu", "passed", "ok", critical=True)]

    result = run_phase_lifecycle(
        phase_name="phase0",
        state=sm,
        detect=_detect,
        skip_if_completed=True,
    )
    assert result.passed is True
    assert detect_called is False
    assert any(c.name == "resumability" for c in result.checks)
    sm.close()
