from pathlib import Path

from sim.config import ManifestConfig
from sim.modules.base import run_install_module
from sim.modules.dependencies import (
    DependenciesModule,
    HostDependency,
    command_is_ready,
    missing_packages,
    required_dependencies,
)
from sim.state import StateManager


def test_required_dependencies_respects_manifest():
    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    names = {dep.command for dep in required_dependencies(cfg)}
    assert names == {"ss", "dnf", "podman", "firewall-cmd"}


def test_required_dependencies_includes_core_tools():
    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    names = {dep.command for dep in required_dependencies(cfg)}
    assert {"ss", "dnf"}.issubset(names)


def test_dependencies_detect_passes_when_tools_present():
    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    module = DependenciesModule(cfg, dry_run=False)
    checks = module.detect()
    assert all(check.status != "failed" for check in checks)
    assert any(check.name == "packages" and check.status == "passed" for check in checks)


def test_dependencies_detect_warns_on_missing_tool_in_dry_run(monkeypatch):
    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    module = DependenciesModule(cfg, dry_run=True)

    def _fake_ready(dep: HostDependency):
        if dep.command == "podman":
            return False, "podman not found in PATH"
        return command_is_ready(dep)

    monkeypatch.setattr("sim.modules.dependencies.command_is_ready", _fake_ready)
    checks = module.detect()
    assert any(check.name == "podman" and check.status == "warning" for check in checks)


def test_dependencies_install_calls_dnf_for_missing_packages(monkeypatch):
    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    installed: list[list[str]] = []

    def _fake_install(packages: list[str]) -> None:
        installed.append(packages)

    def _fake_ready(dep: HostDependency):
        if dep.command == "podman":
            return False, "podman not found in PATH"
        return command_is_ready(dep)

    monkeypatch.setattr("sim.modules.dependencies.command_is_ready", _fake_ready)
    module = DependenciesModule(cfg, dry_run=False, run_install=_fake_install)
    module.install()

    assert installed == [["podman"]]


def test_dependencies_install_is_idempotent(monkeypatch):
    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    install_calls = 0

    def _fake_install(_packages: list[str]) -> None:
        nonlocal install_calls
        install_calls += 1

    monkeypatch.setattr(
        "sim.modules.dependencies.command_is_ready",
        lambda dep: (True, f"{dep.command} available"),
    )
    module = DependenciesModule(cfg, dry_run=False, run_install=_fake_install)
    module.install()
    module.install()
    assert install_calls == 0


def test_dependencies_dry_run_does_not_install_or_persist(tmp_path: Path, monkeypatch):
    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    sm = StateManager(db_path=tmp_path / "state.db")
    install_calls = 0

    def _fake_install(_packages: list[str]) -> None:
        nonlocal install_calls
        install_calls += 1

    def _fake_ready(dep: HostDependency):
        if dep.command == "podman":
            return False, "podman not found in PATH"
        return command_is_ready(dep)

    monkeypatch.setattr("sim.modules.dependencies.command_is_ready", _fake_ready)
    module = DependenciesModule(cfg, dry_run=True, run_install=_fake_install)
    result = run_install_module(module, sm, dry_run=True)

    assert result.passed is True
    assert install_calls == 0
    assert not sm.is_completed("dependencies")
    sm.close()


def test_dependencies_detect_fails_without_dnf(monkeypatch):
    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})

    def _fake_ready(dep: HostDependency):
        if dep.command == "dnf":
            return False, "dnf not found in PATH"
        return True, "ok"

    monkeypatch.setattr("sim.modules.dependencies.command_is_ready", _fake_ready)
    module = DependenciesModule(cfg, dry_run=False)
    checks = module.detect()
    assert any(check.name == "dnf" and check.status == "failed" for check in checks)


def test_missing_packages_lists_only_absent_tools(monkeypatch):
    deps = (
        HostDependency("ss", "iproute"),
        HostDependency("podman", "podman"),
    )

    def _fake_ready(dep: HostDependency):
        if dep.command == "podman":
            return False, "missing"
        return True, "ok"

    monkeypatch.setattr("sim.modules.dependencies.command_is_ready", _fake_ready)
    assert missing_packages(deps) == ["podman"]


def test_dependencies_run_install_module_records_completion(tmp_path: Path, monkeypatch):
    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    sm = StateManager(db_path=tmp_path / "state.db")
    monkeypatch.setattr(
        "sim.modules.dependencies.command_is_ready",
        lambda dep: (True, f"{dep.command} available"),
    )
    module = DependenciesModule(cfg, dry_run=False, run_install=lambda _packages: None)
    result = run_install_module(module, sm, dry_run=False)

    assert result.passed is True
    assert sm.is_completed("dependencies")
    sm.close()


def test_dependencies_skips_when_already_completed(tmp_path: Path):
    sm = StateManager(db_path=tmp_path / "state.db")
    sm.record_success("dependencies")
    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    module = DependenciesModule(cfg, dry_run=False)
    result = run_install_module(module, sm, dry_run=False)

    assert result.passed is True
    assert any(check.name == "resumability" for check in result.checks)
    sm.close()
