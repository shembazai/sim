"""Stage 3b: prepare Quadlet systemd integration for Podman services."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path

from sim.config import ManifestConfig
from sim.modules.base import InstallModule
from sim.phases.lifecycle import CheckResult
from sim.subprocess_util import run_command

SYSTEM_QUADLET_DIR = Path("/etc/containers/systemd")
DEFAULT_DIR_MODE = 0o755


def quadlet_dir_for_manifest(cfg: ManifestConfig) -> Path:
    """Return the Quadlet unit directory for the current provisioning scope."""
    if os.geteuid() == 0:
        return SYSTEM_QUADLET_DIR
    return Path.home() / ".config" / "containers" / "systemd"


def quadlet_supported() -> tuple[bool, str]:
    ok, output = run_command(["podman", "quadlet", "--help"])
    if ok:
        return True, "podman quadlet available"
    return False, output or "podman quadlet subcommand unavailable"


def _format_mode(mode: int) -> str:
    return oct(mode & 0o777)


def _check_directory(path: Path, *, expected_mode: int) -> tuple[bool, str]:
    if not path.exists():
        return False, f"{path} does not exist"
    if not path.is_dir():
        return False, f"{path} exists but is not a directory"
    if not os.access(path, os.W_OK):
        return False, f"{path} is not writable"
    current_mode = stat.S_IMODE(path.stat().st_mode)
    if current_mode != expected_mode:
        return (
            False,
            f"{path} mode is {_format_mode(current_mode)}, expected {_format_mode(expected_mode)}",
        )
    return True, f"{path} ready ({_format_mode(current_mode)}, writable)"


def _reload_systemd() -> None:
    ok, output = run_command(["systemctl", "daemon-reload"])
    if not ok:
        raise RuntimeError(f"systemctl daemon-reload failed: {output}")


class QuadletModule(InstallModule):
    """Ensure the Quadlet unit directory exists and systemd is reloaded."""

    def __init__(
        self,
        cfg: ManifestConfig,
        *,
        dry_run: bool = False,
        dir_mode: int = DEFAULT_DIR_MODE,
        reload_systemd: Callable[[], None] | None = None,
    ) -> None:
        self._cfg = cfg
        self._dry_run = dry_run
        self._dir_mode = dir_mode
        self._reload_systemd = reload_systemd or _reload_systemd

    @property
    def name(self) -> str:
        return "quadlet"

    @property
    def quadlet_dir(self) -> Path:
        return quadlet_dir_for_manifest(self._cfg)

    def _disabled_checks(self) -> list[CheckResult]:
        return [
            CheckResult(
                "quadlet_policy",
                "passed",
                "Quadlet disabled in manifest",
                critical=False,
            )
        ]

    def _check_policy(self) -> CheckResult:
        if not self._cfg.container.quadlet:
            return CheckResult(
                "quadlet_policy",
                "passed",
                "Quadlet disabled in manifest",
                critical=False,
            )
        return CheckResult(
            "quadlet_policy",
            "passed",
            "Quadlet enabled in manifest",
            critical=True,
        )

    def _check_podman_quadlet(self) -> CheckResult:
        ok, detail = quadlet_supported()
        if ok:
            return CheckResult("quadlet_cli", "passed", detail, critical=True)
        if self._dry_run:
            return CheckResult(
                "quadlet_cli",
                "warning",
                f"{detail}; would require Podman with Quadlet support",
                critical=False,
            )
        return CheckResult("quadlet_cli", "failed", detail, critical=True)

    def _check_directory(self) -> CheckResult:
        ok, detail = _check_directory(self.quadlet_dir, expected_mode=self._dir_mode)
        if ok:
            return CheckResult("quadlet_dir", "passed", detail, critical=True)
        if self._dry_run:
            return CheckResult(
                "quadlet_dir",
                "warning",
                f"{detail}; would be created",
                critical=False,
            )
        return CheckResult(
            "quadlet_dir",
            "passed",
            f"{detail}; pending creation",
            critical=True,
        )

    def detect(self) -> list[CheckResult]:
        if not self._cfg.container.quadlet:
            return self._disabled_checks()

        checks = [
            self._check_policy(),
            self._check_podman_quadlet(),
            self._check_directory(),
        ]
        if checks[1].status == "failed":
            return checks
        return checks

    def install(self) -> None:
        if not self._cfg.container.quadlet:
            return

        ok, _detail = _check_directory(self.quadlet_dir, expected_mode=self._dir_mode)
        if not ok:
            self.quadlet_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.quadlet_dir, self._dir_mode)
        self._reload_systemd()

    def verify(self) -> list[CheckResult]:
        if not self._cfg.container.quadlet:
            return self._disabled_checks()
        if self._dry_run:
            return self.detect()

        ok, detail = _check_directory(self.quadlet_dir, expected_mode=self._dir_mode)
        quadlet_ok, quadlet_detail = quadlet_supported()
        checks = [
            self._check_policy(),
            CheckResult(
                "quadlet_cli",
                "passed" if quadlet_ok else "failed",
                quadlet_detail,
                critical=True,
            ),
            CheckResult(
                "quadlet_dir",
                "passed" if ok else "failed",
                detail if ok else f"{self.quadlet_dir} invalid: {detail}",
                critical=True,
            ),
            CheckResult(
                "quadlet",
                "passed" if ok and quadlet_ok else "failed",
                "Quadlet integration verified"
                if ok and quadlet_ok
                else "Quadlet integration verification failed",
                critical=True,
            ),
        ]
        return checks

    def rollback(self) -> None:
        """Quadlet directories are shared infrastructure — not removed on rollback."""
