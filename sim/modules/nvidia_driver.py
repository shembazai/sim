"""Stage 4a: install and verify the NVIDIA driver stack."""

from __future__ import annotations

import shutil
from collections.abc import Callable

from sim.config import ManifestConfig
from sim.modules.base import InstallModule
from sim.modules.gpu_util import (
    NVIDIA_DRIVER_PACKAGES,
    install_packages,
    nvidia_gpu_enabled,
    nvidia_smi_works,
)
from sim.phases.lifecycle import CheckResult


class NvidiaDriverModule(InstallModule):
    """Ensure the NVIDIA driver is installed when requested by the manifest."""

    def __init__(
        self,
        cfg: ManifestConfig,
        *,
        dry_run: bool = False,
        run_install: Callable[[tuple[str, ...]], None] | None = None,
    ) -> None:
        self._cfg = cfg
        self._dry_run = dry_run
        self._run_install = run_install or install_packages

    @property
    def name(self) -> str:
        return "nvidia_driver"

    def _disabled_checks(self, *, detail: str) -> list[CheckResult]:
        return [
            CheckResult(
                "gpu_policy",
                "passed",
                detail,
                critical=False,
            )
        ]

    def _check_policy(self) -> CheckResult:
        if not nvidia_gpu_enabled(self._cfg):
            return CheckResult(
                "gpu_policy",
                "passed",
                "GPU vendor is none; NVIDIA driver stage skipped",
                critical=False,
            )
        if not self._cfg.gpu.install_driver:
            return CheckResult(
                "gpu_policy",
                "passed",
                "NVIDIA driver installation disabled in manifest",
                critical=False,
            )
        return CheckResult(
            "gpu_policy",
            "passed",
            "Manifest requests NVIDIA driver installation",
            critical=True,
        )

    def _check_nvidia_smi(self) -> CheckResult:
        ok, detail = nvidia_smi_works()
        if ok:
            return CheckResult("nvidia_smi", "passed", detail, critical=True)
        if self._dry_run:
            package_list = ", ".join(NVIDIA_DRIVER_PACKAGES)
            return CheckResult(
                "nvidia_smi",
                "warning",
                f"{detail}; would install {package_list}",
                critical=False,
            )
        return CheckResult(
            "nvidia_smi",
            "passed",
            f"{detail}; driver package(s) pending installation",
            critical=True,
        )

    def detect(self) -> list[CheckResult]:
        if not nvidia_gpu_enabled(self._cfg):
            return self._disabled_checks(detail="GPU vendor is none; NVIDIA driver stage skipped")
        if not self._cfg.gpu.install_driver:
            return self._disabled_checks(detail="NVIDIA driver installation disabled in manifest")

        return [self._check_policy(), self._check_nvidia_smi()]

    def install(self) -> None:
        if not nvidia_gpu_enabled(self._cfg) or not self._cfg.gpu.install_driver:
            return
        if nvidia_smi_works()[0]:
            return
        if not shutil.which("dnf"):
            raise RuntimeError("dnf is required to install NVIDIA drivers")
        self._run_install(NVIDIA_DRIVER_PACKAGES)

    def verify(self) -> list[CheckResult]:
        if not nvidia_gpu_enabled(self._cfg):
            return self._disabled_checks(detail="GPU vendor is none; NVIDIA driver stage skipped")
        if not self._cfg.gpu.install_driver:
            return self._disabled_checks(detail="NVIDIA driver installation disabled in manifest")
        if self._dry_run:
            return self.detect()

        ok, detail = nvidia_smi_works()
        checks = [
            self._check_policy(),
            CheckResult(
                "nvidia_smi",
                "passed" if ok else "failed",
                detail,
                critical=True,
            ),
            CheckResult(
                "nvidia_driver",
                "passed" if ok else "failed",
                "NVIDIA driver verified" if ok else "NVIDIA driver verification failed",
                critical=True,
            ),
        ]
        return checks

    def rollback(self) -> None:
        """Driver packages are intentionally not removed — GPU uninstall is operator-driven."""
