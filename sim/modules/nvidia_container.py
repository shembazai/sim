"""Stage 4c: install NVIDIA Container Toolkit and configure Podman."""

from __future__ import annotations

import shutil
from collections.abc import Callable

from sim.config import ManifestConfig
from sim.modules.base import InstallModule
from sim.modules.gpu_util import (
    NVIDIA_CONTAINER_PACKAGES,
    configure_podman_nvidia_runtime,
    install_packages,
    nvidia_ctk_available,
    nvidia_gpu_enabled,
    nvidia_smi_works,
    podman_nvidia_runtime_ready,
)
from sim.phases.lifecycle import CheckResult
from sim.subprocess_util import run_command


def podman_available() -> tuple[bool, str]:
    ok, output = run_command(["podman", "--version"])
    if ok:
        detail = output.splitlines()[0] if output else "podman available"
        return True, detail
    return False, output or "podman not available"


class NvidiaContainerModule(InstallModule):
    """Ensure Podman can use NVIDIA GPUs via the container toolkit."""

    def __init__(
        self,
        cfg: ManifestConfig,
        *,
        dry_run: bool = False,
        run_install: Callable[[tuple[str, ...]], None] | None = None,
        configure_runtime: Callable[[], None] | None = None,
    ) -> None:
        self._cfg = cfg
        self._dry_run = dry_run
        self._run_install = run_install or install_packages
        self._configure_runtime = configure_runtime or configure_podman_nvidia_runtime

    @property
    def name(self) -> str:
        return "nvidia_container"

    def _stage_enabled(self) -> bool:
        return nvidia_gpu_enabled(self._cfg) and self._cfg.container.runtime == "podman"

    def _disabled_checks(self, *, detail: str) -> list[CheckResult]:
        return [
            CheckResult(
                "nvidia_container_policy",
                "passed",
                detail,
                critical=False,
            )
        ]

    def _check_policy(self) -> CheckResult:
        if not nvidia_gpu_enabled(self._cfg):
            return CheckResult(
                "nvidia_container_policy",
                "passed",
                "GPU vendor is none; NVIDIA container stage skipped",
                critical=False,
            )
        if self._cfg.container.runtime != "podman":
            return CheckResult(
                "nvidia_container_policy",
                "passed",
                f"Container runtime {self._cfg.container.runtime!r} does not use NVIDIA container toolkit",
                critical=False,
            )
        return CheckResult(
            "nvidia_container_policy",
            "passed",
            "Manifest requests NVIDIA container support for Podman",
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
                f"{detail}; GPU containers require a working NVIDIA driver",
                critical=False,
            )
        return CheckResult(
            "nvidia_driver",
            "failed",
            f"{detail}; install Stage 4a before NVIDIA container toolkit",
            critical=True,
        )

    def _check_podman(self) -> CheckResult:
        ok, detail = podman_available()
        if ok:
            return CheckResult("podman_cli", "passed", detail, critical=True)
        if self._dry_run:
            return CheckResult(
                "podman_cli",
                "warning",
                f"{detail}; Podman required for NVIDIA container integration",
                critical=False,
            )
        return CheckResult("podman_cli", "failed", detail, critical=True)

    def _check_nvidia_ctk(self) -> CheckResult:
        ok, detail = nvidia_ctk_available()
        if ok:
            return CheckResult("nvidia_ctk", "passed", detail, critical=True)
        if self._dry_run:
            package_list = ", ".join(NVIDIA_CONTAINER_PACKAGES)
            return CheckResult(
                "nvidia_ctk",
                "warning",
                f"{detail}; would install {package_list}",
                critical=False,
            )
        return CheckResult(
            "nvidia_ctk",
            "passed",
            f"{detail}; NVIDIA container package(s) pending installation",
            critical=True,
        )

    def _check_runtime(self) -> CheckResult:
        ok, detail = podman_nvidia_runtime_ready()
        if ok:
            return CheckResult("podman_nvidia_runtime", "passed", detail, critical=True)
        if self._dry_run:
            return CheckResult(
                "podman_nvidia_runtime",
                "warning",
                f"{detail}; would run nvidia-ctk runtime configure --runtime=podman",
                critical=False,
            )
        return CheckResult(
            "podman_nvidia_runtime",
            "passed",
            f"{detail}; runtime configuration pending",
            critical=True,
        )

    def detect(self) -> list[CheckResult]:
        if not self._stage_enabled():
            if not nvidia_gpu_enabled(self._cfg):
                return self._disabled_checks(detail="GPU vendor is none; NVIDIA container stage skipped")
            return self._disabled_checks(
                detail=(
                    f"Container runtime {self._cfg.container.runtime!r}; "
                    "NVIDIA container stage skipped"
                ),
            )

        checks = [
            self._check_policy(),
            self._check_driver_prerequisite(),
            self._check_podman(),
        ]
        if checks[1].status == "failed" or checks[2].status == "failed":
            return checks
        checks.extend([self._check_nvidia_ctk(), self._check_runtime()])
        return checks

    def install(self) -> None:
        if not self._stage_enabled():
            return
        if not nvidia_smi_works()[0]:
            raise RuntimeError(
                "NVIDIA container toolkit requires a working NVIDIA driver (Stage 4a)"
            )
        if not podman_available()[0]:
            raise RuntimeError("Podman is required for NVIDIA container integration (Stage 3a)")

        if not nvidia_ctk_available()[0]:
            if not shutil.which("dnf"):
                raise RuntimeError("dnf is required to install NVIDIA Container Toolkit")
            self._run_install(NVIDIA_CONTAINER_PACKAGES)

        if not podman_nvidia_runtime_ready()[0]:
            self._configure_runtime()

    def verify(self) -> list[CheckResult]:
        if not self._stage_enabled():
            if not nvidia_gpu_enabled(self._cfg):
                return self._disabled_checks(detail="GPU vendor is none; NVIDIA container stage skipped")
            return self._disabled_checks(
                detail=(
                    f"Container runtime {self._cfg.container.runtime!r}; "
                    "NVIDIA container stage skipped"
                ),
            )
        if self._dry_run:
            return self.detect()

        driver_ok, driver_detail = nvidia_smi_works()
        podman_ok, podman_detail = podman_available()
        ctk_ok, ctk_detail = nvidia_ctk_available()
        runtime_ok, runtime_detail = podman_nvidia_runtime_ready()
        all_ok = driver_ok and podman_ok and ctk_ok and runtime_ok
        return [
            self._check_policy(),
            CheckResult(
                "nvidia_driver",
                "passed" if driver_ok else "failed",
                driver_detail,
                critical=True,
            ),
            CheckResult(
                "podman_cli",
                "passed" if podman_ok else "failed",
                podman_detail,
                critical=True,
            ),
            CheckResult(
                "nvidia_ctk",
                "passed" if ctk_ok else "failed",
                ctk_detail,
                critical=True,
            ),
            CheckResult(
                "podman_nvidia_runtime",
                "passed" if runtime_ok else "failed",
                runtime_detail,
                critical=True,
            ),
            CheckResult(
                "nvidia_container",
                "passed" if all_ok else "failed",
                "NVIDIA container integration verified"
                if all_ok
                else "NVIDIA container integration verification failed",
                critical=True,
            ),
        ]

    def rollback(self) -> None:
        """Container toolkit packages are intentionally not removed."""
