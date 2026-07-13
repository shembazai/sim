from pathlib import Path

from sim.config import ManifestConfig
from sim.modules.base import run_install_module
from sim.modules.cuda import CudaModule
from sim.modules.gpu_util import CUDA_PACKAGES, NVIDIA_DRIVER_PACKAGES, NVIDIA_CONTAINER_PACKAGES
from sim.modules.nvidia_container import NvidiaContainerModule
from sim.modules.nvidia_driver import NvidiaDriverModule
from sim.state import StateManager


def _cfg(
    *,
    vendor: str = "nvidia",
    install_driver: bool = True,
    install_cuda: bool = True,
    runtime: str = "podman",
) -> ManifestConfig:
    return ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "gpu": {
                "vendor": vendor,
                "install_driver": install_driver,
                "install_cuda": install_cuda,
            },
            "container": {"runtime": runtime},
        }
    )


def test_nvidia_driver_detect_skips_when_vendor_none():
    module = NvidiaDriverModule(_cfg(vendor="none"), dry_run=False)
    checks = module.detect()
    assert len(checks) == 1
    assert checks[0].name == "gpu_policy"
    assert "skipped" in checks[0].detail.lower()


def test_nvidia_driver_detect_skips_when_install_disabled():
    module = NvidiaDriverModule(_cfg(install_driver=False), dry_run=False)
    checks = module.detect()
    assert len(checks) == 1
    assert "disabled" in checks[0].detail.lower()


def test_nvidia_driver_detect_passes_when_smi_works(monkeypatch):
    monkeypatch.setattr(
        "sim.modules.nvidia_driver.nvidia_smi_works",
        lambda: (True, "NVIDIA-SMI 550.90"),
    )
    module = NvidiaDriverModule(_cfg(), dry_run=False)
    checks = module.detect()
    assert all(check.status != "failed" for check in checks)


def test_nvidia_driver_install_runs_dnf_when_smi_missing(monkeypatch):
    installed: list[tuple[str, ...]] = []

    def _fake_install(packages: tuple[str, ...]) -> None:
        installed.append(packages)

    monkeypatch.setattr(
        "sim.modules.nvidia_driver.nvidia_smi_works",
        lambda: (False, "missing"),
    )
    monkeypatch.setattr("sim.modules.nvidia_driver.shutil.which", lambda name: "/usr/bin/dnf" if name == "dnf" else None)
    module = NvidiaDriverModule(_cfg(), dry_run=False, run_install=_fake_install)
    module.install()
    assert installed == [NVIDIA_DRIVER_PACKAGES]


def test_nvidia_driver_install_is_idempotent(monkeypatch):
    install_calls = 0

    def _fake_install(_packages: tuple[str, ...]) -> None:
        nonlocal install_calls
        install_calls += 1

    monkeypatch.setattr(
        "sim.modules.nvidia_driver.nvidia_smi_works",
        lambda: (True, "NVIDIA-SMI 550.90"),
    )
    module = NvidiaDriverModule(_cfg(), dry_run=False, run_install=_fake_install)
    module.install()
    module.install()
    assert install_calls == 0


def test_nvidia_driver_dry_run_does_not_persist(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "sim.modules.nvidia_driver.nvidia_smi_works",
        lambda: (False, "missing"),
    )
    sm = StateManager(db_path=tmp_path / "state.db")
    module = NvidiaDriverModule(_cfg(), dry_run=True)
    result = run_install_module(module, sm, dry_run=True)

    assert result.passed is True
    assert not sm.is_completed("nvidia_driver")
    assert any(check.status == "warning" for check in result.checks)
    sm.close()


def test_cuda_detect_skips_when_vendor_none():
    module = CudaModule(_cfg(vendor="none"), dry_run=False)
    checks = module.detect()
    assert len(checks) == 1
    assert "skipped" in checks[0].detail.lower()


def test_cuda_detect_skips_when_install_disabled():
    module = CudaModule(_cfg(install_cuda=False), dry_run=False)
    checks = module.detect()
    assert len(checks) == 1
    assert "disabled" in checks[0].detail.lower()


def test_cuda_detect_fails_without_driver(monkeypatch):
    monkeypatch.setattr("sim.modules.cuda.nvidia_smi_works", lambda: (False, "missing"))
    module = CudaModule(_cfg(), dry_run=False)
    checks = module.detect()
    assert any(check.name == "nvidia_driver" and check.status == "failed" for check in checks)


def test_cuda_install_runs_dnf_when_nvcc_missing(monkeypatch):
    installed: list[tuple[str, ...]] = []

    def _fake_install(packages: tuple[str, ...]) -> None:
        installed.append(packages)

    monkeypatch.setattr("sim.modules.cuda.nvidia_smi_works", lambda: (True, "driver ok"))
    monkeypatch.setattr("sim.modules.cuda.nvcc_works", lambda: (False, "missing"))
    monkeypatch.setattr("sim.modules.cuda.shutil.which", lambda name: "/usr/bin/dnf" if name == "dnf" else None)
    module = CudaModule(_cfg(), dry_run=False, run_install=_fake_install)
    module.install()
    assert installed == [CUDA_PACKAGES]


def test_cuda_run_install_module_records_completion(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("sim.modules.cuda.nvidia_smi_works", lambda: (True, "driver ok"))
    monkeypatch.setattr("sim.modules.cuda.nvcc_works", lambda: (True, "Cuda compilation tools, release 12.4"))
    sm = StateManager(db_path=tmp_path / "state.db")
    module = CudaModule(_cfg(), dry_run=False, run_install=lambda _packages: None)
    result = run_install_module(module, sm, dry_run=False)

    assert result.passed is True
    assert sm.is_completed("cuda")
    sm.close()


def test_nvidia_container_detect_skips_when_vendor_none():
    module = NvidiaContainerModule(_cfg(vendor="none"), dry_run=False)
    checks = module.detect()
    assert len(checks) == 1
    assert "skipped" in checks[0].detail.lower()


def test_nvidia_container_detect_passes_when_ready(monkeypatch):
    monkeypatch.setattr("sim.modules.nvidia_container.nvidia_smi_works", lambda: (True, "driver ok"))
    monkeypatch.setattr("sim.modules.nvidia_container.podman_available", lambda: (True, "podman version 5.0.0"))
    monkeypatch.setattr("sim.modules.nvidia_container.nvidia_ctk_available", lambda: (True, "nvidia-ctk 1.16"))
    monkeypatch.setattr(
        "sim.modules.nvidia_container.podman_nvidia_runtime_ready",
        lambda: (True, "Podman NVIDIA devices available"),
    )
    module = NvidiaContainerModule(_cfg(), dry_run=False)
    checks = module.detect()
    assert all(check.status != "failed" for check in checks)


def test_nvidia_container_install_installs_and_configures(monkeypatch):
    installed: list[tuple[str, ...]] = []
    configured = False

    def _fake_install(packages: tuple[str, ...]) -> None:
        installed.append(packages)

    def _fake_configure() -> None:
        nonlocal configured
        configured = True

    monkeypatch.setattr("sim.modules.nvidia_container.nvidia_smi_works", lambda: (True, "driver ok"))
    monkeypatch.setattr("sim.modules.nvidia_container.podman_available", lambda: (True, "podman ok"))
    monkeypatch.setattr("sim.modules.nvidia_container.nvidia_ctk_available", lambda: (False, "missing"))
    monkeypatch.setattr("sim.modules.nvidia_container.podman_nvidia_runtime_ready", lambda: (False, "missing"))
    monkeypatch.setattr("sim.modules.nvidia_container.shutil.which", lambda name: "/usr/bin/dnf" if name == "dnf" else None)

    module = NvidiaContainerModule(
        _cfg(),
        dry_run=False,
        run_install=_fake_install,
        configure_runtime=_fake_configure,
    )
    module.install()
    assert installed == [NVIDIA_CONTAINER_PACKAGES]
    assert configured is True


def test_nvidia_container_install_is_idempotent(monkeypatch):
    install_calls = 0
    configure_calls = 0

    def _fake_install(_packages: tuple[str, ...]) -> None:
        nonlocal install_calls
        install_calls += 1

    def _fake_configure() -> None:
        nonlocal configure_calls
        configure_calls += 1

    monkeypatch.setattr("sim.modules.nvidia_container.nvidia_smi_works", lambda: (True, "driver ok"))
    monkeypatch.setattr("sim.modules.nvidia_container.podman_available", lambda: (True, "podman ok"))
    monkeypatch.setattr("sim.modules.nvidia_container.nvidia_ctk_available", lambda: (True, "nvidia-ctk ok"))
    monkeypatch.setattr(
        "sim.modules.nvidia_container.podman_nvidia_runtime_ready",
        lambda: (True, "runtime ready"),
    )

    module = NvidiaContainerModule(
        _cfg(),
        dry_run=False,
        run_install=_fake_install,
        configure_runtime=_fake_configure,
    )
    module.install()
    module.install()
    assert install_calls == 0
    assert configure_calls == 0


def test_nvidia_container_dry_run_does_not_persist(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("sim.modules.nvidia_container.nvidia_smi_works", lambda: (True, "driver ok"))
    monkeypatch.setattr("sim.modules.nvidia_container.podman_available", lambda: (True, "podman ok"))
    monkeypatch.setattr("sim.modules.nvidia_container.nvidia_ctk_available", lambda: (False, "missing"))
    monkeypatch.setattr("sim.modules.nvidia_container.podman_nvidia_runtime_ready", lambda: (False, "missing"))

    sm = StateManager(db_path=tmp_path / "state.db")
    module = NvidiaContainerModule(_cfg(), dry_run=True)
    result = run_install_module(module, sm, dry_run=True)

    assert result.passed is True
    assert not sm.is_completed("nvidia_container")
    assert any(check.status == "warning" for check in result.checks)
    sm.close()
