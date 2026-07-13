"""Stage 4b: install and verify the CUDA toolkit when requested."""

from __future__ import annotations

import shutil
from collections.abc import Callable

from sim.config import ManifestConfig
from sim.modules.base import InstallModule
from sim.modules.gpu_util import (
    CUDA_PACKAGES,
    install_packages,
    nvidia_gpu_enabled,
    nvcc_works,
    nvidia_smi_works,
)
from sim.phases.lifecycle import CheckResult


class CudaModule(InstallModule):
    """Ensure the CUDA toolkit is installed when requested by the manifest."""

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
        return "cuda"

    def _disabled_checks(self, *, detail: str) -> list[CheckResult]:
        return [
            CheckResult(
                "cuda_policy",
                "passed",
                detail,
                critical=False,
            )
        ]

    def _stage_enabled(self) -> bool:
        return nvidia_gpu_enabled(self._cfg) and self._cfg.gpu.install_cuda

    def _check_policy(self) -> CheckResult:
        if not nvidia_gpu_enabled(self._cfg):
            return CheckResult(
                "cuda_policy",
                "passed",
                "GPU vendor is none; CUDA stage skipped",
                critical=False,
            )
        if not self._cfg.gpu.install_cuda:
            return CheckResult(
                "cuda_policy",
                "passed",
                "CUDA installation disabled in manifest",
                critical=False,
            )
        return CheckResult(
            "cuda_policy",
            "passed",
            "Manifest requests CUDA toolkit installation",
            critical=True,
        )

    def _check_driver_prerequisite(self) -> CheckResult:
        ok, detail = nvidia_smi_works()
        if ok:
            return CheckResult("nvidia_driver", "passed", detail, critical=True)
        if self._dry_run:
            return CheckResult(
                "nvidia_driver",
                "warning",
                f"{detail}; CUDA requires a working NVIDIA driver",
                critical=False,
            )
        return CheckResult(
            "nvidia_driver",
            "failed",
            f"{detail}; install Stage 4a before CUDA",
            critical=True,
        )

    def _check_nvcc(self) -> CheckResult:
        ok, detail = nvcc_works()
        if ok:
            return CheckResult("nvcc", "passed", detail, critical=True)
        if self._dry_run:
            package_list = ", ".join(CUDA_PACKAGES)
            return CheckResult(
                "nvcc",
                "warning",
                f"{detail}; would install {package_list}",
                critical=False,
            )
        return CheckResult(
            "nvcc",
            "passed",
            f"{detail}; CUDA package(s) pending installation",
            critical=True,
        )

    def detect(self) -> list[CheckResult]:
        if not self._stage_enabled():
            if not nvidia_gpu_enabled(self._cfg):
                return self._disabled_checks(detail="GPU vendor is none; CUDA stage skipped")
            return self._disabled_checks(detail="CUDA installation disabled in manifest")

        checks = [self._check_policy(), self._check_driver_prerequisite()]
        if checks[1].status == "failed":
            return checks
        checks.append(self._check_nvcc())
        return checks

    def install(self) -> None:
        if not self._stage_enabled():
            return
        if not nvidia_smi_works()[0]:
            raise RuntimeError("CUDA installation requires a working NVIDIA driver (Stage 4a)")
        if nvcc_works()[0]:
            return
        if not shutil.which("dnf"):
            raise RuntimeError("dnf is required to install CUDA")
        self._run_install(CUDA_PACKAGES)

    def verify(self) -> list[CheckResult]:
        if not self._stage_enabled():
            if not nvidia_gpu_enabled(self._cfg):
                return self._disabled_checks(detail="GPU vendor is none; CUDA stage skipped")
            return self._disabled_checks(detail="CUDA installation disabled in manifest")
        if self._dry_run:
            return self.detect()

        driver_ok, driver_detail = nvidia_smi_works()
        nvcc_ok, nvcc_detail = nvcc_works()
        checks = [
            self._check_policy(),
            CheckResult(
                "nvidia_driver",
                "passed" if driver_ok else "failed",
                driver_detail,
                critical=True,
            ),
            CheckResult(
                "nvcc",
                "passed" if nvcc_ok else "failed",
                nvcc_detail,
                critical=True,
            ),
            CheckResult(
                "cuda",
                "passed" if driver_ok and nvcc_ok else "failed",
                "CUDA toolkit verified" if driver_ok and nvcc_ok else "CUDA verification failed",
                critical=True,
            ),
        ]
        return checks

    def rollback(self) -> None:
        """CUDA packages are intentionally not removed — uninstall is operator-driven."""
