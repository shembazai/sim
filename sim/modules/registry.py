"""Stage 3c: configure Podman local image cache / mirror policy."""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from pathlib import Path

from sim.config import ManifestConfig
from sim.modules.base import InstallModule
from sim.modules.rollback_util import remove_sim_managed_file
from sim.phases.lifecycle import CheckResult
from sim.subprocess_util import run_command

SYSTEM_REGISTRIES_DIR = Path("/etc/containers/registries.conf.d")
REGISTRY_DROPIN_NAME = "001-k1-registry.conf"
DEFAULT_DIR_MODE = 0o755
DEFAULT_FILE_MODE = 0o644
DOCKER_IO_PREFIX = "docker.io"


def registries_dropin_dir_for_manifest(_cfg: ManifestConfig) -> Path:
    """Return the Podman registries drop-in directory for the current scope."""
    if os.geteuid() == 0:
        return SYSTEM_REGISTRIES_DIR
    return Path.home() / ".config" / "containers" / "registries.conf.d"


def registries_dropin_path_for_manifest(cfg: ManifestConfig) -> Path:
    """Return the managed registries drop-in path for this manifest."""
    return registries_dropin_dir_for_manifest(cfg) / REGISTRY_DROPIN_NAME


def expected_registry_config(cfg: ManifestConfig) -> str:
    """Render the deterministic registries.conf.d content for the manifest."""
    endpoint = cfg.container.registry.endpoint.strip()
    lines = [
        "# Managed by SIM (registry module). Do not edit manually.",
        f'unqualified-search-registries = ["{endpoint}"]',
        "",
        "[[registry]]",
        f'location = "{endpoint}"',
        "insecure = true",
        "",
        "[[registry]]",
        f'prefix = "{DOCKER_IO_PREFIX}"',
        f'location = "{DOCKER_IO_PREFIX}"',
        "",
        "[[registry.mirror]]",
        f'location = "{endpoint}"',
        "insecure = true",
        "",
    ]
    return "\n".join(lines)


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


def registry_config_matches(cfg: ManifestConfig, path: Path) -> tuple[bool, str]:
    """Return whether the managed drop-in matches the manifest policy."""
    expected = expected_registry_config(cfg)
    if not path.exists():
        return False, f"{path} does not exist"
    if not path.is_file():
        return False, f"{path} exists but is not a file"
    current = path.read_text(encoding="utf-8")
    if current != expected:
        return False, f"{path} content differs from manifest policy"
    current_mode = stat.S_IMODE(path.stat().st_mode)
    if current_mode != DEFAULT_FILE_MODE:
        return (
            False,
            f"{path} mode is {_format_mode(current_mode)}, expected {_format_mode(DEFAULT_FILE_MODE)}",
        )
    return True, f"{path} matches manifest policy"


def podman_available() -> tuple[bool, str]:
    ok, output = run_command(["podman", "--version"])
    if ok:
        detail = output.splitlines()[0] if output else "podman available"
        return True, detail
    return False, output or "podman not available"


def _write_registry_config(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    os.chmod(path, DEFAULT_FILE_MODE)


class RegistryModule(InstallModule):
    """Ensure local registry cache policy is configured for Podman."""

    def __init__(
        self,
        cfg: ManifestConfig,
        *,
        dry_run: bool = False,
        dir_mode: int = DEFAULT_DIR_MODE,
        write_config: Callable[[Path, str], None] | None = None,
    ) -> None:
        self._cfg = cfg
        self._dry_run = dry_run
        self._dir_mode = dir_mode
        self._write_config = write_config or _write_registry_config

    @property
    def name(self) -> str:
        return "registry"

    @property
    def registry_enabled(self) -> bool:
        return self._cfg.container.registry.enabled

    @property
    def data_dir(self) -> Path:
        return self._cfg.container.registry.data_dir

    @property
    def dropin_path(self) -> Path:
        return registries_dropin_path_for_manifest(self._cfg)

    def _disabled_checks(self) -> list[CheckResult]:
        return [
            CheckResult(
                "registry_policy",
                "passed",
                "Local registry policy disabled in manifest",
                critical=False,
            )
        ]

    def _check_policy(self) -> CheckResult:
        if not self.registry_enabled:
            return CheckResult(
                "registry_policy",
                "passed",
                "Local registry policy disabled in manifest",
                critical=False,
            )
        endpoint = self._cfg.container.registry.endpoint.strip()
        return CheckResult(
            "registry_policy",
            "passed",
            f"Local registry policy enabled ({endpoint})",
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
                f"{detail}; Podman required before registry policy can be applied",
                critical=False,
            )
        return CheckResult("podman_cli", "failed", detail, critical=True)

    def _check_data_dir(self) -> CheckResult:
        ok, detail = _check_directory(self.data_dir, expected_mode=self._dir_mode)
        if ok:
            return CheckResult("registry_data_dir", "passed", detail, critical=True)
        if self._dry_run:
            return CheckResult(
                "registry_data_dir",
                "warning",
                f"{detail}; would be created",
                critical=False,
            )
        return CheckResult(
            "registry_data_dir",
            "passed",
            f"{detail}; pending creation",
            critical=True,
        )

    def _check_dropin(self) -> CheckResult:
        ok, detail = registry_config_matches(self._cfg, self.dropin_path)
        if ok:
            return CheckResult("registry_dropin", "passed", detail, critical=True)
        if self._dry_run:
            return CheckResult(
                "registry_dropin",
                "warning",
                f"{detail}; would write {self.dropin_path}",
                critical=False,
            )
        return CheckResult(
            "registry_dropin",
            "passed",
            f"{detail}; pending write",
            critical=True,
        )

    def detect(self) -> list[CheckResult]:
        if not self.registry_enabled:
            return self._disabled_checks()

        checks = [
            self._check_policy(),
            self._check_podman(),
            self._check_data_dir(),
            self._check_dropin(),
        ]
        if checks[1].status == "failed":
            return checks
        return checks

    def install(self) -> None:
        if not self.registry_enabled:
            return

        ok, _detail = _check_directory(self.data_dir, expected_mode=self._dir_mode)
        if not ok:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(self.data_dir, self._dir_mode)

        config_ok, _detail = registry_config_matches(self._cfg, self.dropin_path)
        if not config_ok:
            self._write_config(self.dropin_path, expected_registry_config(self._cfg))

    def verify(self) -> list[CheckResult]:
        if not self.registry_enabled:
            return self._disabled_checks()
        if self._dry_run:
            return self.detect()

        data_ok, data_detail = _check_directory(self.data_dir, expected_mode=self._dir_mode)
        dropin_ok, dropin_detail = registry_config_matches(self._cfg, self.dropin_path)
        podman_ok, podman_detail = podman_available()
        checks = [
            self._check_policy(),
            CheckResult(
                "podman_cli",
                "passed" if podman_ok else "failed",
                podman_detail,
                critical=True,
            ),
            CheckResult(
                "registry_data_dir",
                "passed" if data_ok else "failed",
                data_detail if data_ok else f"{self.data_dir} invalid: {data_detail}",
                critical=True,
            ),
            CheckResult(
                "registry_dropin",
                "passed" if dropin_ok else "failed",
                dropin_detail if dropin_ok else f"{self.dropin_path} invalid: {dropin_detail}",
                critical=True,
            ),
            CheckResult(
                "registry",
                "passed" if data_ok and dropin_ok and podman_ok else "failed",
                "Local registry policy verified"
                if data_ok and dropin_ok and podman_ok
                else "Local registry policy verification failed",
                critical=True,
            ),
        ]
        return checks

    def rollback(self) -> None:
        """Remove the SIM-managed registries drop-in only."""
        remove_sim_managed_file(self.dropin_path)
