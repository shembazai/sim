"""Rollback behavior for install modules."""

from __future__ import annotations

from pathlib import Path

from sim.config import ManifestConfig
from sim.modules.dependencies import DependenciesModule
from sim.modules.python_runtime import PythonRuntimeModule
from sim.modules.registry import RegistryModule, expected_registry_config
from sim.modules.rollback_util import remove_sim_managed_file


def _cfg(tmp_path: Path) -> ManifestConfig:
    return ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "filesystem": {"root": str(tmp_path / "k1")},
            "python": {"version": "3.12", "venv": str(tmp_path / "k1" / ".venv")},
            "container": {
                "runtime": "podman",
                "quadlet": True,
                "registry": {"enabled": True, "endpoint": "localhost:5000"},
            },
        }
    )


def test_python_runtime_rollback_removes_venv(tmp_path: Path):
    cfg = _cfg(tmp_path)
    venv = cfg.python.venv
    venv.mkdir(parents=True)
    (venv / "bin").mkdir()
    (venv / "bin" / "python").write_text("", encoding="utf-8")

    PythonRuntimeModule(cfg).rollback()

    assert not venv.exists()


def test_dependencies_rollback_is_noop():
    DependenciesModule(_cfg(Path("/tmp"))).rollback()


def test_registry_rollback_removes_managed_dropin(tmp_path: Path):
    cfg = _cfg(tmp_path)
    module = RegistryModule(cfg)
    dropin = module.dropin_path
    dropin.parent.mkdir(parents=True, exist_ok=True)
    dropin.write_text(expected_registry_config(cfg), encoding="utf-8")

    module.rollback()

    assert not dropin.exists()


def test_remove_sim_managed_file_skips_unmarked(tmp_path: Path):
    path = tmp_path / "other.conf"
    path.write_text("# third party\n", encoding="utf-8")
    assert remove_sim_managed_file(path) is False
    assert path.exists()
