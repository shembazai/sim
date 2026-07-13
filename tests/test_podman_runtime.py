from pathlib import Path

from sim.config import ManifestConfig
from sim.modules.base import run_install_module
from sim.modules.podman_runtime import (
    PODMAN_SOCKET_UNIT,
    PodmanModule,
    podman_command_available,
    systemd_unit_active,
)
from sim.phases.lifecycle import CheckResult
from sim.state import StateManager


def _cfg() -> ManifestConfig:
    return ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})


def test_podman_detect_passes_when_runtime_ready(monkeypatch):
    monkeypatch.setattr(
        "sim.modules.podman_runtime.podman_command_available",
        lambda: (True, "podman version 5.0.0"),
    )
    monkeypatch.setattr(
        "sim.modules.podman_runtime.podman_info_works",
        lambda: (True, "podman info ok"),
    )
    monkeypatch.setattr(
        "sim.modules.podman_runtime.systemd_unit_active",
        lambda unit: (True, f"{unit} active"),
    )
    module = PodmanModule(_cfg(), dry_run=False)
    checks = module.detect()
    assert all(check.status != "failed" for check in checks)


def test_podman_install_enables_socket_when_inactive(monkeypatch):
    enabled = False

    def _fake_enable() -> None:
        nonlocal enabled
        enabled = True

    monkeypatch.setattr(
        "sim.modules.podman_runtime.podman_command_available",
        lambda: (True, "podman version 5.0.0"),
    )
    monkeypatch.setattr(
        "sim.modules.podman_runtime.systemd_unit_active",
        lambda unit: (False, "inactive"),
    )
    module = PodmanModule(_cfg(), dry_run=False, enable_socket=_fake_enable)
    module.install()
    assert enabled is True


def test_podman_install_installs_package_when_missing(monkeypatch):
    installed = False

    def _fake_install() -> None:
        nonlocal installed
        installed = True

    monkeypatch.setattr(
        "sim.modules.podman_runtime.podman_command_available",
        lambda: (False, "missing"),
    )
    monkeypatch.setattr("sim.modules.podman_runtime.shutil.which", lambda name: "/usr/bin/dnf" if name == "dnf" else None)
    monkeypatch.setattr(
        "sim.modules.podman_runtime.systemd_unit_active",
        lambda unit: (True, "active"),
    )
    module = PodmanModule(_cfg(), dry_run=False, install_package=_fake_install)
    module.install()
    assert installed is True


def test_podman_dry_run_warns_without_changes(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "sim.modules.podman_runtime.podman_command_available",
        lambda: (True, "podman version 5.0.0"),
    )
    monkeypatch.setattr(
        "sim.modules.podman_runtime.podman_info_works",
        lambda: (True, "podman info ok"),
    )
    monkeypatch.setattr(
        "sim.modules.podman_runtime.systemd_unit_active",
        lambda unit: (False, "inactive"),
    )
    sm = StateManager(db_path=tmp_path / "state.db")
    module = PodmanModule(_cfg(), dry_run=True)
    result = run_install_module(module, sm, dry_run=True)

    assert result.passed is True
    assert not sm.is_completed("podman")
    assert any(check.status == "warning" for check in result.checks)
    sm.close()


def test_podman_run_install_module_records_completion(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "sim.modules.podman_runtime.podman_command_available",
        lambda: (True, "podman version 5.0.0"),
    )
    monkeypatch.setattr(
        "sim.modules.podman_runtime.podman_info_works",
        lambda: (True, "podman info ok"),
    )
    monkeypatch.setattr(
        "sim.modules.podman_runtime.systemd_unit_active",
        lambda unit: (True, "active"),
    )
    sm = StateManager(db_path=tmp_path / "state.db")
    module = PodmanModule(_cfg(), dry_run=False, enable_socket=lambda: None)
    result = run_install_module(module, sm, dry_run=False)

    assert result.passed is True
    assert sm.is_completed("podman")
    sm.close()


def test_podman_skips_when_already_completed(tmp_path: Path, monkeypatch):
    sm = StateManager(db_path=tmp_path / "state.db")
    sm.record_success("podman")
    module = PodmanModule(_cfg(), dry_run=False)
    monkeypatch.setattr(
        module,
        "detect",
        lambda: [CheckResult("podman", "passed", "already installed", critical=True)],
    )
    result = run_install_module(module, sm, dry_run=False)

    assert result.passed is True
    assert any(check.name == "resumability" for check in result.checks)
    sm.close()


def test_systemd_unit_active_parses_output(monkeypatch):
    monkeypatch.setattr(
        "sim.modules.podman_runtime.run_command",
        lambda args: (True, "active"),
    )
    ok, detail = systemd_unit_active(PODMAN_SOCKET_UNIT)
    assert ok is True
    assert "active" in detail


def test_podman_command_available_uses_path(monkeypatch):
    monkeypatch.setattr(
        "sim.modules.podman_runtime.shutil.which",
        lambda name: "/usr/bin/podman" if name == "podman" else None,
    )
    monkeypatch.setattr(
        "sim.modules.podman_runtime.run_command",
        lambda args: (True, "podman version 5.0.0"),
    )
    ok, detail = podman_command_available()
    assert ok is True
    assert "podman version" in detail


def test_podman_rollback_disables_socket(monkeypatch):
    commands: list[list[str]] = []

    def _fake_run(args: list[str]) -> tuple[bool, str]:
        commands.append(args)
        return True, ""

    monkeypatch.setattr("sim.modules.podman_runtime.run_command", _fake_run)
    PodmanModule(_cfg(), dry_run=False).rollback()
    assert commands == [["systemctl", "disable", "--now", PODMAN_SOCKET_UNIT]]
