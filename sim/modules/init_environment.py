"""Stage 2a: create the /opt/k1 filesystem layout."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from sim.config import ManifestConfig
from sim.modules.base import InstallModule
from sim.phases.lifecycle import CheckResult

STANDARD_SUBDIRS = ("logs", "state", "reports", "data", "config")
# Nested paths under filesystem root required by IRE (rollback storage, etc.).
NESTED_SUBDIRS = ("state/backups",)
DEFAULT_DIR_MODE = 0o755


def _format_mode(mode: int) -> str:
    return oct(mode & 0o777)


def _check_directory(path: Path, *, expected_mode: int) -> CheckResult:
    label = str(path)
    if not path.exists():
        return CheckResult(
            f"dir:{path.name}",
            "failed",
            f"{label} does not exist",
            critical=True,
        )
    if not path.is_dir():
        return CheckResult(
            f"dir:{path.name}",
            "failed",
            f"{label} exists but is not a directory",
            critical=True,
        )
    if not os.access(path, os.W_OK):
        return CheckResult(
            f"dir:{path.name}",
            "failed",
            f"{label} is not writable",
            critical=True,
        )
    current_mode = stat.S_IMODE(path.stat().st_mode)
    if current_mode != expected_mode:
        return CheckResult(
            f"dir:{path.name}",
            "failed",
            f"{label} mode is {_format_mode(current_mode)}, expected {_format_mode(expected_mode)}",
            critical=True,
        )
    return CheckResult(
        f"dir:{path.name}",
        "passed",
        f"{label} exists ({_format_mode(current_mode)}, writable)",
        critical=True,
    )


class InitEnvironmentModule(InstallModule):
    """Create manifest-defined K1 directories with consistent permissions."""

    def __init__(
        self,
        cfg: ManifestConfig,
        *,
        dry_run: bool = False,
        dir_mode: int = DEFAULT_DIR_MODE,
    ) -> None:
        self._cfg = cfg
        self._dry_run = dry_run
        self._dir_mode = dir_mode

    @property
    def name(self) -> str:
        return "init_environment"

    @property
    def root(self) -> Path:
        return self._cfg.filesystem.root

    def required_paths(self) -> list[Path]:
        return [self.root / name for name in STANDARD_SUBDIRS]

    def nested_paths(self) -> list[Path]:
        return [self.root / rel for rel in NESTED_SUBDIRS]

    def detect(self) -> list[CheckResult]:
        checks: list[CheckResult] = []
        root = self.root

        if root.exists():
            if not root.is_dir():
                checks.append(
                    CheckResult(
                        "root",
                        "failed",
                        f"{root} exists but is not a directory",
                        critical=True,
                    )
                )
                return checks
            if not os.access(root, os.W_OK):
                checks.append(
                    CheckResult(
                        "root",
                        "failed",
                        f"{root} is not writable",
                        critical=True,
                    )
                )
                return checks
            checks.append(
                CheckResult(
                    "root",
                    "passed",
                    f"{root} exists and is writable",
                    critical=True,
                )
            )
        else:
            parent = root.parent
            if not parent.exists() or not os.access(parent, os.W_OK):
                checks.append(
                    CheckResult(
                        "root",
                        "failed",
                        f"Cannot create {root}: parent {parent} is missing or not writable",
                        critical=True,
                    )
                )
                return checks
            detail = f"{root} will be created"
            status = "warning" if self._dry_run else "passed"
            checks.append(
                CheckResult("root", status, detail, critical=not self._dry_run)
            )

        missing = [path for path in self.required_paths() if not path.exists()]
        for path in self.required_paths():
            if path.exists():
                checks.append(_check_directory(path, expected_mode=self._dir_mode))
            elif self._dry_run:
                checks.append(
                    CheckResult(
                        f"dir:{path.name}",
                        "warning",
                        f"{path} missing (would be created)",
                        critical=False,
                    )
                )
            else:
                checks.append(
                    CheckResult(
                        f"dir:{path.name}",
                        "passed",
                        f"{path} missing (will be created)",
                        critical=True,
                    )
                )

        for path in self.nested_paths():
            if path.exists():
                checks.append(_check_directory(path, expected_mode=self._dir_mode))
            elif self._dry_run:
                checks.append(
                    CheckResult(
                        f"dir:{path.relative_to(self.root)}",
                        "warning",
                        f"{path} missing (would be created)",
                        critical=False,
                    )
                )
            else:
                checks.append(
                    CheckResult(
                        f"dir:{path.relative_to(self.root)}",
                        "passed",
                        f"{path} missing (will be created)",
                        critical=True,
                    )
                )

        if missing and not self._dry_run:
            checks.append(
                CheckResult(
                    "layout",
                    "passed",
                    f"{len(missing)} director{'y' if len(missing) == 1 else 'ies'} pending creation",
                    critical=True,
                )
            )
        elif not missing:
            checks.append(
                CheckResult(
                    "layout",
                    "passed",
                    "All standard directories present",
                    critical=True,
                )
            )
        return checks

    def install(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, self._dir_mode)
        for path in self.required_paths():
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(path, self._dir_mode)
        for path in self.nested_paths():
            path.mkdir(parents=True, exist_ok=True)
            os.chmod(path, self._dir_mode)

    def verify(self) -> list[CheckResult]:
        if self._dry_run:
            return self.detect()

        checks = [
            CheckResult(
                "root",
                "passed" if self.root.is_dir() and os.access(self.root, os.W_OK) else "failed",
                f"{self.root} ready" if self.root.is_dir() else f"{self.root} missing",
                critical=True,
            )
        ]
        checks.extend(
            _check_directory(path, expected_mode=self._dir_mode)
            for path in self.required_paths()
        )
        checks.extend(
            _check_directory(path, expected_mode=self._dir_mode)
            for path in self.nested_paths()
        )
        checks.append(
            CheckResult(
                "layout",
                "passed",
                "Filesystem layout verified",
                critical=True,
            )
        )
        return checks

    def rollback(self) -> None:
        """Best-effort undo — directories are intentionally not removed."""
