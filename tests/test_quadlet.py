from pathlib import Path

from sim.config import ManifestConfig
from sim.modules.base import run_install_module
from sim.modules.quadlet import QuadletModule, quadlet_dir_for_manifest, quadlet_supported
from sim.state import StateManager


def _cfg(*, quadlet: bool = True) -> ManifestConfig:
    return ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "container": {"runtime": "podman", "quadlet": quadlet},
        }
    )


def test_quadlet_dir_for_manifest_uses_system_path_when_root(monkeypatch):
    monkeypatch.setattr("sim.modules.quadlet.os.geteuid", lambda: 0)
    assert quadlet_dir_for_manifest(_cfg()) == Path("/etc/containers/systemd")


def test_quadlet_detect_passes_when_enabled_and_ready(monkeypatch, tmp_path: Path):
    quadlet_dir = tmp_path / "systemd"
    quadlet_dir.mkdir()
    monkeypatch.setattr(
        "sim.modules.quadlet.quadlet_supported",
        lambda: (True, "podman quadlet available"),
    )
    monkeypatch.setattr(
        "sim.modules.quadlet.quadlet_dir_for_manifest",
        lambda _cfg: quadlet_dir,
    )
    module = QuadletModule(_cfg(), dry_run=False)
    checks = module.detect()
    assert all(check.status != "failed" for check in checks)


def test_quadlet_detect_skips_when_disabled():
    module = QuadletModule(_cfg(quadlet=False), dry_run=False)
    checks = module.detect()
    assert len(checks) == 1
    assert checks[0].name == "quadlet_policy"
    assert checks[0].status == "passed"


def test_quadlet_install_creates_directory_and_reloads(monkeypatch, tmp_path: Path):
    quadlet_dir = tmp_path / "systemd"
    reloaded = False

    def _reload() -> None:
        nonlocal reloaded
        reloaded = True

    monkeypatch.setattr(
        "sim.modules.quadlet.quadlet_supported",
        lambda: (True, "ok"),
    )
    monkeypatch.setattr(
        "sim.modules.quadlet.quadlet_dir_for_manifest",
        lambda _cfg: quadlet_dir,
    )
    module = QuadletModule(_cfg(), dry_run=False, reload_systemd=_reload)
    module.install()
    assert quadlet_dir.is_dir()
    assert reloaded is True


def test_quadlet_install_noop_when_disabled(tmp_path: Path):
    module = QuadletModule(_cfg(quadlet=False), dry_run=False, reload_systemd=lambda: (_ for _ in ()).throw(AssertionError("reload called")))
    module.install()


def test_quadlet_dry_run_does_not_create_or_persist(tmp_path: Path, monkeypatch):
    quadlet_dir = tmp_path / "systemd"
    monkeypatch.setattr(
        "sim.modules.quadlet.quadlet_supported",
        lambda: (True, "ok"),
    )
    monkeypatch.setattr(
        "sim.modules.quadlet.quadlet_dir_for_manifest",
        lambda _cfg: quadlet_dir,
    )
    sm = StateManager(db_path=tmp_path / "state.db")
    module = QuadletModule(_cfg(), dry_run=True)
    result = run_install_module(module, sm, dry_run=True)

    assert result.passed is True
    assert not quadlet_dir.exists()
    assert not sm.is_completed("quadlet")
    sm.close()


def test_quadlet_run_install_module_records_completion(tmp_path: Path, monkeypatch):
    quadlet_dir = tmp_path / "systemd"
    monkeypatch.setattr(
        "sim.modules.quadlet.quadlet_supported",
        lambda: (True, "ok"),
    )
    monkeypatch.setattr(
        "sim.modules.quadlet.quadlet_dir_for_manifest",
        lambda _cfg: quadlet_dir,
    )
    sm = StateManager(db_path=tmp_path / "state.db")
    module = QuadletModule(_cfg(), dry_run=False, reload_systemd=lambda: None)
    result = run_install_module(module, sm, dry_run=False)

    assert result.passed is True
    assert sm.is_completed("quadlet")
    assert quadlet_dir.is_dir()
    sm.close()


def test_quadlet_supported_uses_podman_subcommand(monkeypatch):
    monkeypatch.setattr(
        "sim.modules.quadlet.run_command",
        lambda args: (True, "quadlet help"),
    )
    ok, detail = quadlet_supported()
    assert ok is True
    assert "available" in detail
