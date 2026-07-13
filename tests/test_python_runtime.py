from pathlib import Path

from sim.config import ManifestConfig
from sim.modules.base import run_install_module
from sim.modules.python_runtime import (
    PythonRuntimeModule,
    _version_at_least,
    python_candidates,
    resolve_system_python,
    venv_is_valid,
)
from sim.state import StateManager


def test_version_at_least():
    assert _version_at_least("3.12.1", "3.12") is True
    assert _version_at_least("3.11.9", "3.12") is False
    assert _version_at_least("3.12", "3.12.0") is True


def test_python_candidates():
    assert python_candidates("3.12") == ["python3.12", "python3", "python"]


def test_resolve_system_python_uses_current_interpreter():
    interpreter, version = resolve_system_python("3.12")
    assert interpreter is not None
    assert version is not None
    assert _version_at_least(version, "3.12")


def test_python_runtime_detect_passes_with_valid_venv(tmp_path: Path):
    root = tmp_path / "k1"
    venv = root / ".venv"
    cfg = ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "filesystem": {"root": str(root)},
            "python": {"version": "3.12", "venv": str(venv)},
        }
    )
    module = PythonRuntimeModule(cfg, dry_run=False)
    module.install()

    checks = module.detect()
    assert all(c.status != "failed" for c in checks)
    assert any(c.name == "system_python" and c.status == "passed" for c in checks)
    assert any(c.name == "venv" and c.status == "passed" for c in checks)


def test_python_runtime_install_is_idempotent(tmp_path: Path):
    root = tmp_path / "k1"
    venv = root / ".venv"
    cfg = ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "python": {"version": "3.12", "venv": str(venv)},
        }
    )
    module = PythonRuntimeModule(cfg, dry_run=False)
    module.install()
    first_mtime = venv_python_mtime(venv)
    module.install()
    assert venv_python_mtime(venv) == first_mtime


def venv_python_mtime(venv: Path) -> int:
    return (venv / "bin" / "python").stat().st_mtime_ns


def test_python_runtime_dry_run_does_not_create_venv(tmp_path: Path):
    venv = tmp_path / "k1" / ".venv"
    cfg = ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "python": {"version": "3.12", "venv": str(venv)},
        }
    )
    sm = StateManager(db_path=tmp_path / "state.db")
    module = PythonRuntimeModule(cfg, dry_run=True)
    result = run_install_module(module, sm, dry_run=True)

    assert result.passed is True
    assert not venv.exists()
    assert not sm.is_completed("python_runtime")
    assert any(c.name == "venv" and c.status == "warning" for c in result.checks)
    sm.close()


def test_python_runtime_detect_fails_without_system_python(tmp_path: Path):
    cfg = ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "python": {"version": "99.99", "venv": str(tmp_path / ".venv")},
        }
    )
    module = PythonRuntimeModule(
        cfg,
        dry_run=False,
        resolve_python=lambda _minimum: (None, None),
    )
    checks = module.detect()
    assert any(c.name == "system_python" and c.status == "failed" for c in checks)


def test_python_runtime_run_install_module_records_completion(tmp_path: Path):
    venv = tmp_path / ".venv"
    cfg = ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "python": {"version": "3.12", "venv": str(venv)},
        }
    )
    sm = StateManager(db_path=tmp_path / "state.db")
    module = PythonRuntimeModule(cfg, dry_run=False)
    result = run_install_module(module, sm, dry_run=False)

    assert result.passed is True
    assert sm.is_completed("python_runtime")
    ok, detail = venv_is_valid(venv, minimum="3.12")
    assert ok, detail
    sm.close()


def test_python_runtime_skips_when_already_completed(tmp_path: Path):
    sm = StateManager(db_path=tmp_path / "state.db")
    sm.record_success("python_runtime")
    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    module = PythonRuntimeModule(cfg, dry_run=False)
    result = run_install_module(module, sm, dry_run=False)

    assert result.passed is True
    assert any(c.name == "resumability" for c in result.checks)
    sm.close()


def test_python_runtime_detect_fails_when_parent_not_writable(tmp_path: Path, monkeypatch):
    venv = tmp_path / "locked" / ".venv"
    cfg = ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "python": {"version": "3.12", "venv": str(venv)},
        }
    )
    module = PythonRuntimeModule(
        cfg,
        dry_run=False,
        resolve_python=lambda _minimum: (Path("/usr/bin/python3"), "3.12.0"),
    )
    monkeypatch.setattr("sim.modules.python_runtime.os.access", lambda _path, _mode: False)
    checks = module.detect()
    assert any(c.name == "venv_parent" and c.status == "failed" for c in checks)
