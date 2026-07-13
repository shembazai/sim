from pathlib import Path

from sim.config import ManifestConfig
from sim.modules.base import run_install_module
from sim.modules.registry import (
    REGISTRY_DROPIN_NAME,
    RegistryModule,
    expected_registry_config,
    registries_dropin_dir_for_manifest,
    registries_dropin_path_for_manifest,
    registry_config_matches,
)
from sim.state import StateManager


def _cfg(*, registry_enabled: bool = True, endpoint: str = "localhost:5000") -> ManifestConfig:
    return ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "container": {
                "runtime": "podman",
                "quadlet": True,
                "registry": {
                    "enabled": registry_enabled,
                    "endpoint": endpoint,
                    "data_dir": "/opt/k1/data/registry",
                },
            },
        }
    )


def test_manifest_registry_defaults_disabled():
    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    assert cfg.container.registry.enabled is False
    assert cfg.container.registry.endpoint == "localhost:5000"
    assert cfg.container.registry.data_dir == Path("/opt/k1/data/registry")


def test_expected_registry_config_includes_endpoint_and_mirror():
    content = expected_registry_config(_cfg(endpoint="127.0.0.1:5001"))
    assert 'location = "127.0.0.1:5001"' in content
    assert 'prefix = "docker.io"' in content
    assert "[[registry.mirror]]" in content
    assert content.endswith("\n")


def test_registries_dropin_dir_for_manifest_uses_system_path_when_root(monkeypatch):
    monkeypatch.setattr("sim.modules.registry.os.geteuid", lambda: 0)
    assert registries_dropin_dir_for_manifest(_cfg()) == Path("/etc/containers/registries.conf.d")


def test_registries_dropin_path_for_manifest():
    cfg = _cfg()
    assert registries_dropin_path_for_manifest(cfg).name == REGISTRY_DROPIN_NAME


def test_registry_detect_skips_when_disabled():
    module = RegistryModule(_cfg(registry_enabled=False), dry_run=False)
    checks = module.detect()
    assert len(checks) == 1
    assert checks[0].name == "registry_policy"
    assert checks[0].status == "passed"


def test_registry_detect_passes_when_ready(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "registry-data"
    data_dir.mkdir()
    dropin_dir = tmp_path / "registries.conf.d"
    dropin_dir.mkdir()
    dropin_path = dropin_dir / REGISTRY_DROPIN_NAME
    cfg = _cfg()
    dropin_path.write_text(expected_registry_config(cfg), encoding="utf-8")
    dropin_path.chmod(0o644)

    monkeypatch.setattr("sim.modules.registry.podman_available", lambda: (True, "podman version 5.0.0"))
    monkeypatch.setattr(
        "sim.modules.registry.registries_dropin_path_for_manifest",
        lambda _cfg: dropin_path,
    )
    monkeypatch.setattr(
        RegistryModule,
        "data_dir",
        property(lambda self: data_dir),
    )

    module = RegistryModule(cfg, dry_run=False)
    checks = module.detect()
    assert all(check.status != "failed" for check in checks)


def test_registry_install_creates_data_dir_and_dropin(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "registry-data"
    dropin_dir = tmp_path / "registries.conf.d"
    dropin_path = dropin_dir / REGISTRY_DROPIN_NAME
    cfg = _cfg()
    written: list[tuple[Path, str]] = []

    def _write(path: Path, content: str) -> None:
        written.append((path, content))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    monkeypatch.setattr(
        "sim.modules.registry.registries_dropin_path_for_manifest",
        lambda _cfg: dropin_path,
    )
    monkeypatch.setattr(
        RegistryModule,
        "data_dir",
        property(lambda self: data_dir),
    )

    module = RegistryModule(cfg, dry_run=False, write_config=_write)
    module.install()
    assert data_dir.is_dir()
    assert len(written) == 1
    assert written[0][0] == dropin_path
    assert written[0][1] == expected_registry_config(cfg)


def test_registry_install_is_idempotent(monkeypatch, tmp_path: Path):
    data_dir = tmp_path / "registry-data"
    data_dir.mkdir()
    dropin_dir = tmp_path / "registries.conf.d"
    dropin_dir.mkdir()
    dropin_path = dropin_dir / REGISTRY_DROPIN_NAME
    cfg = _cfg()
    dropin_path.write_text(expected_registry_config(cfg), encoding="utf-8")
    dropin_path.chmod(0o644)
    write_calls = 0

    def _write(path: Path, content: str) -> None:
        nonlocal write_calls
        write_calls += 1
        path.write_text(content, encoding="utf-8")

    monkeypatch.setattr(
        "sim.modules.registry.registries_dropin_path_for_manifest",
        lambda _cfg: dropin_path,
    )
    monkeypatch.setattr(
        RegistryModule,
        "data_dir",
        property(lambda self: data_dir),
    )

    module = RegistryModule(cfg, dry_run=False, write_config=_write)
    module.install()
    module.install()
    assert write_calls == 0


def test_registry_install_noop_when_disabled(tmp_path: Path):
    module = RegistryModule(
        _cfg(registry_enabled=False),
        dry_run=False,
        write_config=lambda _path, _content: (_ for _ in ()).throw(AssertionError("write called")),
    )
    module.install()


def test_registry_dry_run_does_not_create_or_persist(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "registry-data"
    dropin_dir = tmp_path / "registries.conf.d"
    dropin_path = dropin_dir / REGISTRY_DROPIN_NAME

    monkeypatch.setattr("sim.modules.registry.podman_available", lambda: (True, "podman version 5.0.0"))
    monkeypatch.setattr(
        "sim.modules.registry.registries_dropin_path_for_manifest",
        lambda _cfg: dropin_path,
    )
    monkeypatch.setattr(
        RegistryModule,
        "data_dir",
        property(lambda self: data_dir),
    )

    sm = StateManager(db_path=tmp_path / "state.db")
    module = RegistryModule(_cfg(), dry_run=True)
    result = run_install_module(module, sm, dry_run=True)

    assert result.passed is True
    assert not data_dir.exists()
    assert not dropin_path.exists()
    assert not sm.is_completed("registry")
    sm.close()


def test_registry_run_install_module_records_completion(tmp_path: Path, monkeypatch):
    data_dir = tmp_path / "registry-data"
    dropin_dir = tmp_path / "registries.conf.d"
    dropin_path = dropin_dir / REGISTRY_DROPIN_NAME
    cfg = _cfg()

    monkeypatch.setattr("sim.modules.registry.podman_available", lambda: (True, "podman version 5.0.0"))
    monkeypatch.setattr(
        "sim.modules.registry.registries_dropin_path_for_manifest",
        lambda _cfg: dropin_path,
    )
    monkeypatch.setattr(
        RegistryModule,
        "data_dir",
        property(lambda self: data_dir),
    )

    sm = StateManager(db_path=tmp_path / "state.db")
    module = RegistryModule(cfg, dry_run=False)
    result = run_install_module(module, sm, dry_run=False)

    assert result.passed is True
    assert sm.is_completed("registry")
    assert data_dir.is_dir()
    assert dropin_path.read_text(encoding="utf-8") == expected_registry_config(cfg)
    sm.close()


def test_registry_config_matches_detects_drift(tmp_path: Path):
    cfg = _cfg()
    dropin_path = tmp_path / REGISTRY_DROPIN_NAME
    dropin_path.write_text("stale content\n", encoding="utf-8")
    ok, detail = registry_config_matches(cfg, dropin_path)
    assert ok is False
    assert "differs" in detail
