"""Stage 3a: install and configure Podman for K1 container workloads."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable

from sim.config import ManifestConfig
from sim.modules.base import InstallModule
from sim.phases.lifecycle import CheckResult
from sim.subprocess_util import run_command

PODMAN_SOCKET_UNIT = "podman.socket"
PODMAN_PACKAGE = "podman"


def podman_command_available() -> tuple[bool, str]:
    path = shutil.which("podman")
    if not path:
        return False, "podman not found in PATH"
    ok, output = run_command([path, "--version"])
    if not ok:
        return False, output or "podman --version failed"
    detail = output.splitlines()[0] if output else "podman available"
    return True, detail


def podman_info_works() -> tuple[bool, str]:
    ok, output = run_command(["podman", "info", "--format", "{{.Host.Os}}"])
    if not ok:
        return False, output or "podman info failed"
    runtime = output.strip() or "unknown"
    return True, f"podman info ok (host OS: {runtime})"


def systemd_unit_active(unit: str) -> tuple[bool, str]:
    ok, output = run_command(["systemctl", "is-active", unit])
    state = output.strip()
    if ok and state == "active":
        return True, f"{unit} is active"
    return False, state or f"{unit} is not active"


def _enable_systemd_unit(unit: str) -> None:
    ok, output = run_command(["systemctl", "enable", "--now", unit])
    if not ok:
        raise RuntimeError(f"Failed to enable {unit}: {output}")


def _install_podman_package() -> None:
    ok, output = run_command(["dnf", "install", "-y", PODMAN_PACKAGE])
    if not ok:
        raise RuntimeError(f"dnf install {PODMAN_PACKAGE} failed: {output}")


class PodmanModule(InstallModule):
    """Ensure Podman is installed, functional, and socket-activated."""

    def __init__(
        self,
        cfg: ManifestConfig,
        *,
        dry_run: bool = False,
        install_package: Callable[[], None] | None = None,
        enable_socket: Callable[[], None] | None = None,
    ) -> None:
        self._cfg = cfg
        self._dry_run = dry_run
        self._install_package = install_package or _install_podman_package
        self._enable_socket = enable_socket or (lambda: _enable_systemd_unit(PODMAN_SOCKET_UNIT))

    @property
    def name(self) -> str:
        return "podman"

    def _runtime_scope_check(self) -> CheckResult:
        if self._cfg.container.runtime != "podman":
            return CheckResult(
                "runtime_policy",
                "failed",
                f"Unsupported container runtime {self._cfg.container.runtime!r}",
                critical=True,
            )
        return CheckResult(
            "runtime_policy",
            "passed",
            "Manifest requests podman runtime",
            critical=True,
        )

    def _rootless_check(self) -> CheckResult:
        if os.geteuid() == 0:
            return CheckResult(
                "rootless",
                "passed",
                "Running as root; system Podman will be used",
                critical=True,
            )
        ok, detail = podman_info_works()
        if ok:
            return CheckResult(
                "rootless",
                "passed",
                f"Rootless Podman available ({detail})",
                critical=True,
            )
        return CheckResult(
            "rootless",
            "failed",
            f"Rootless Podman unavailable: {detail}",
            critical=True,
        )

    def _check_command(self) -> CheckResult:
        ok, detail = podman_command_available()
        if ok:
            return CheckResult("podman_cli", "passed", detail, critical=True)
        if self._dry_run:
            return CheckResult(
                "podman_cli",
                "warning",
                f"{detail}; would install package {PODMAN_PACKAGE!r}",
                critical=False,
            )
        return CheckResult(
            "podman_cli",
            "passed",
            f"{detail}; package {PODMAN_PACKAGE!r} pending installation",
            critical=True,
        )

    def _check_info(self) -> CheckResult:
        ok, detail = podman_info_works()
        if ok:
            return CheckResult("podman_info", "passed", detail, critical=True)
        if self._dry_run:
            return CheckResult(
                "podman_info",
                "warning",
                f"{detail}; would configure Podman after install",
                critical=False,
            )
        return CheckResult(
            "podman_info",
            "passed",
            f"{detail}; pending Podman configuration",
            critical=True,
        )

    def _check_socket(self) -> CheckResult:
        ok, detail = systemd_unit_active(PODMAN_SOCKET_UNIT)
        if ok:
            return CheckResult("podman_socket", "passed", detail, critical=True)
        if self._dry_run:
            return CheckResult(
                "podman_socket",
                "warning",
                f"{detail}; would enable {PODMAN_SOCKET_UNIT}",
                critical=False,
            )
        return CheckResult(
            "podman_socket",
            "passed",
            f"{detail}; {PODMAN_SOCKET_UNIT} pending enablement",
            critical=True,
        )

    def detect(self) -> list[CheckResult]:
        checks = [self._runtime_scope_check()]
        if checks[0].status == "failed":
            return checks
        checks.extend(
            [
                self._check_command(),
                self._check_info(),
                self._check_socket(),
                self._rootless_check(),
            ]
        )
        return checks

    def install(self) -> None:
        if not podman_command_available()[0]:
            if not shutil.which("dnf"):
                raise RuntimeError("dnf is required to install Podman")
            self._install_package()
        if not systemd_unit_active(PODMAN_SOCKET_UNIT)[0]:
            self._enable_socket()

    def verify(self) -> list[CheckResult]:
        if self._dry_run:
            return self.detect()

        cli_ok, cli_detail = podman_command_available()
        info_ok, info_detail = podman_info_works()
        socket_ok, socket_detail = systemd_unit_active(PODMAN_SOCKET_UNIT)
        checks = [
            self._runtime_scope_check(),
            CheckResult(
                "podman_cli",
                "passed" if cli_ok else "failed",
                cli_detail,
                critical=True,
            ),
            CheckResult(
                "podman_info",
                "passed" if info_ok else "failed",
                info_detail,
                critical=True,
            ),
            CheckResult(
                "podman_socket",
                "passed" if socket_ok else "failed",
                socket_detail,
                critical=True,
            ),
            self._rootless_check(),
        ]
        all_ok = all(check.status == "passed" for check in checks)
        checks.append(
            CheckResult(
                "podman",
                "passed" if all_ok else "failed",
                "Podman runtime verified" if all_ok else "Podman runtime verification failed",
                critical=True,
            )
        )
        return checks

    def rollback(self) -> None:
        """Disable socket activation; leave the Podman package installed."""
        ok, output = run_command(["systemctl", "disable", "--now", PODMAN_SOCKET_UNIT])
        if not ok and output.strip() not in ("", "not-found"):
            raise RuntimeError(f"Failed to disable {PODMAN_SOCKET_UNIT}: {output}")
