"""Stage 2b: verify system Python and provision the K1 virtual environment."""

from __future__ import annotations

import os
import re
import shutil
from collections.abc import Callable
from pathlib import Path

from sim.config import ManifestConfig
from sim.modules.base import InstallModule
from sim.phases.lifecycle import CheckResult
from sim.subprocess_util import run_command


def _parse_version(version_str: str) -> tuple[int, ...]:
    parts = tuple(int(part) for part in re.findall(r"\d+", version_str))
    return parts if parts else (0,)


def _version_at_least(installed: str, minimum: str) -> bool:
    got = _parse_version(installed)
    want = _parse_version(minimum)
    size = max(len(got), len(want))
    got += (0,) * (size - len(got))
    want += (0,) * (size - len(want))
    return got >= want


def _extract_version(output: str) -> str | None:
    match = re.search(r"(\d+(?:\.\d+)*)", output)
    return match.group(1) if match else None


def python_candidates(minimum: str) -> list[str]:
    """Return interpreter names to probe, most specific first."""
    parts = _parse_version(minimum)
    if len(parts) >= 2:
        specific = f"python{parts[0]}.{parts[1]}"
        return [specific, "python3", "python"]
    return ["python3", "python"]


def resolve_system_python(minimum: str) -> tuple[Path | None, str | None]:
    """Find a system interpreter that satisfies the manifest minimum."""
    for name in python_candidates(minimum):
        found = shutil.which(name)
        if not found:
            continue
        ok, output = run_command([found, "--version"])
        if not ok:
            continue
        version = _extract_version(output)
        if version and _version_at_least(version, minimum):
            return Path(found), version
    return None, None


def venv_python(venv_path: Path) -> Path:
    return venv_path / "bin" / "python"


def venv_is_valid(venv_path: Path, *, minimum: str) -> tuple[bool, str]:
    """Return whether the venv exists and its interpreter meets the minimum."""
    interpreter = venv_python(venv_path)
    if not venv_path.is_dir():
        return False, f"{venv_path} does not exist"
    if not interpreter.is_file() or not os.access(interpreter, os.X_OK):
        return False, f"{interpreter} is missing or not executable"
    ok, output = run_command([str(interpreter), "--version"])
    if not ok:
        return False, output or f"{interpreter} --version failed"
    version = _extract_version(output)
    if version is None:
        return False, f"Could not parse version from: {output}"
    if not _version_at_least(version, minimum):
        return False, f"venv Python {version} < required {minimum}"
    return True, version


class PythonRuntimeModule(InstallModule):
    """Ensure system Python meets the manifest and create the K1 venv."""

    def __init__(
        self,
        cfg: ManifestConfig,
        *,
        dry_run: bool = False,
        resolve_python: Callable[[str], tuple[Path | None, str | None]] | None = None,
    ) -> None:
        self._cfg = cfg
        self._dry_run = dry_run
        self._resolve_python = resolve_python or resolve_system_python

    @property
    def name(self) -> str:
        return "python_runtime"

    @property
    def minimum_version(self) -> str:
        return self._cfg.python.version

    @property
    def venv_path(self) -> Path:
        return self._cfg.python.venv

    def _check_system_python(self) -> CheckResult:
        interpreter, version = self._resolve_python(self.minimum_version)
        if interpreter is None or version is None:
            return CheckResult(
                "system_python",
                "failed",
                f"No Python {self.minimum_version}+ interpreter found in PATH",
                critical=True,
            )
        return CheckResult(
            "system_python",
            "passed",
            f"{interpreter} ({version})",
            critical=True,
        )

    def _check_venv_parent(self) -> CheckResult | None:
        parent = self.venv_path.parent
        if parent.exists():
            if not parent.is_dir():
                return CheckResult(
                    "venv_parent",
                    "failed",
                    f"{parent} exists but is not a directory",
                    critical=True,
                )
            if not os.access(parent, os.W_OK):
                return CheckResult(
                    "venv_parent",
                    "failed",
                    f"{parent} is not writable",
                    critical=True,
                )
            return CheckResult(
                "venv_parent",
                "passed",
                f"{parent} is writable",
                critical=True,
            )

        grandparent = parent.parent
        if not grandparent.exists() or not os.access(grandparent, os.W_OK):
            return CheckResult(
                "venv_parent",
                "failed",
                f"Cannot create {parent}: ancestor {grandparent} missing or not writable",
                critical=True,
            )
        detail = f"{parent} will be created for venv placement"
        status = "warning" if self._dry_run else "passed"
        return CheckResult("venv_parent", status, detail, critical=not self._dry_run)

    def _check_venv(self) -> CheckResult:
        ok, detail = venv_is_valid(self.venv_path, minimum=self.minimum_version)
        if ok:
            return CheckResult(
                "venv",
                "passed",
                f"{self.venv_path} ready (Python {detail})",
                critical=True,
            )
        if self._dry_run:
            return CheckResult(
                "venv",
                "warning",
                f"{self.venv_path} missing or invalid ({detail}); would be created",
                critical=False,
            )
        return CheckResult(
            "venv",
            "passed",
            f"{self.venv_path} pending creation ({detail})",
            critical=True,
        )

    def detect(self) -> list[CheckResult]:
        checks = [self._check_system_python()]
        if checks[0].status == "failed":
            return checks

        parent_check = self._check_venv_parent()
        if parent_check is not None:
            checks.append(parent_check)
            if parent_check.status == "failed":
                return checks

        checks.append(self._check_venv())
        return checks

    def install(self) -> None:
        ok, _detail = venv_is_valid(self.venv_path, minimum=self.minimum_version)
        if ok:
            return

        interpreter, _version = self._resolve_python(self.minimum_version)
        if interpreter is None:
            raise RuntimeError(
                f"No Python {self.minimum_version}+ interpreter available to create venv"
            )

        self.venv_path.parent.mkdir(parents=True, exist_ok=True)
        ok, output = run_command([str(interpreter), "-m", "venv", str(self.venv_path)])
        if not ok:
            raise RuntimeError(f"venv creation failed: {output}")

    def verify(self) -> list[CheckResult]:
        if self._dry_run:
            return self.detect()

        checks = [self._check_system_python()]
        ok, detail = venv_is_valid(self.venv_path, minimum=self.minimum_version)
        checks.append(
            CheckResult(
                "venv",
                "passed" if ok else "failed",
                f"{self.venv_path} ready (Python {detail})"
                if ok
                else f"{self.venv_path} invalid: {detail}",
                critical=True,
            )
        )
        checks.append(
            CheckResult(
                "runtime",
                "passed" if ok and checks[0].status == "passed" else "failed",
                "Python runtime verified" if ok else "Python runtime verification failed",
                critical=True,
            )
        )
        return checks

    def rollback(self) -> None:
        """Remove the manifest venv only; system Python packages are untouched."""
        if self.venv_path.is_dir():
            shutil.rmtree(self.venv_path)

    def rollback(self) -> None:
        """Remove the manifest venv only; system Python packages are untouched."""
        if self.venv_path.is_dir():
            shutil.rmtree(self.venv_path)
