"""Stage 2c: verify and install host tooling required by later SIM stages."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import dataclass

from sim.config import ManifestConfig
from sim.modules.base import InstallModule
from sim.phases.lifecycle import CheckResult
from sim.subprocess_util import run_command


@dataclass(frozen=True)
class HostDependency:
    """A host command backed by an RPM package."""

    command: str
    package: str
    verify_args: tuple[str, ...] = ()
    description: str = ""


CORE_DEPENDENCIES: tuple[HostDependency, ...] = (
    HostDependency("ss", "iproute", ("-V",), "socket statistics (Phase 1 ports)"),
    HostDependency("dnf", "dnf", ("--version",), "package manager"),
)

CONTAINER_DEPENDENCIES: tuple[HostDependency, ...] = (
    HostDependency("podman", "podman", ("--version",), "container runtime"),
)

FIREWALL_DEPENDENCIES: tuple[HostDependency, ...] = (
    HostDependency("firewall-cmd", "firewalld", ("--version",), "firewalld CLI"),
)


def required_dependencies(cfg: ManifestConfig) -> tuple[HostDependency, ...]:
    """Return manifest-scoped host dependencies for Stage 2c."""
    deps = list(CORE_DEPENDENCIES)
    if cfg.container.runtime == "podman":
        deps.extend(CONTAINER_DEPENDENCIES)
    if cfg.security.firewall == "firewalld":
        deps.extend(FIREWALL_DEPENDENCIES)
    return tuple(deps)


def command_is_ready(dep: HostDependency) -> tuple[bool, str]:
    """Return whether the command exists and responds to a basic probe."""
    path = shutil.which(dep.command)
    if not path:
        return False, f"{dep.command} not found in PATH"
    if dep.verify_args:
        ok, output = run_command([path, *dep.verify_args])
        if not ok:
            return False, output or f"{dep.command} probe failed"
        detail = output.splitlines()[0] if output else f"{dep.command} available"
        return True, detail
    return True, f"{dep.command} available at {path}"


def missing_packages(deps: tuple[HostDependency, ...]) -> list[str]:
    return [dep.package for dep in deps if not command_is_ready(dep)[0]]


def _check_dependency(dep: HostDependency, *, dry_run: bool) -> CheckResult:
    ok, detail = command_is_ready(dep)
    if ok:
        return CheckResult(dep.command, "passed", detail, critical=True)
    if dry_run:
        return CheckResult(
            dep.command,
            "warning",
            f"{detail}; would install package {dep.package!r}",
            critical=False,
        )
    if dep.command == "dnf":
        return CheckResult(
            dep.command,
            "failed",
            f"{detail}; cannot install packages without dnf",
            critical=True,
        )
    return CheckResult(
        dep.command,
        "passed",
        f"{detail}; package {dep.package!r} pending installation",
        critical=True,
    )


class DependenciesModule(InstallModule):
    """Ensure core host commands exist, installing RPMs when needed."""

    def __init__(
        self,
        cfg: ManifestConfig,
        *,
        dry_run: bool = False,
        run_install: Callable[[list[str]], None] | None = None,
    ) -> None:
        self._cfg = cfg
        self._dry_run = dry_run
        self._run_install = run_install or _install_packages

    @property
    def name(self) -> str:
        return "dependencies"

    @property
    def dependencies(self) -> tuple[HostDependency, ...]:
        return required_dependencies(self._cfg)

    def detect(self) -> list[CheckResult]:
        checks = [_check_dependency(dep, dry_run=self._dry_run) for dep in self.dependencies]
        pending = missing_packages(self.dependencies)
        if pending and not self._dry_run:
            label = ", ".join(pending)
            checks.append(
                CheckResult(
                    "packages",
                    "passed",
                    f"{len(pending)} package(s) pending installation: {label}",
                    critical=True,
                )
            )
        elif not pending:
            checks.append(
                CheckResult(
                    "packages",
                    "passed",
                    "All required host tools present",
                    critical=True,
                )
            )
        return checks

    def install(self) -> None:
        packages = missing_packages(self.dependencies)
        if not packages:
            return
        if not shutil.which("dnf"):
            raise RuntimeError("dnf is required to install missing host dependencies")
        self._run_install(packages)

    def verify(self) -> list[CheckResult]:
        if self._dry_run:
            return self.detect()

        checks = []
        for dep in self.dependencies:
            ok, detail = command_is_ready(dep)
            checks.append(
                CheckResult(
                    dep.command,
                    "passed" if ok else "failed",
                    detail if ok else f"{dep.command} unavailable after install: {detail}",
                    critical=True,
                )
            )
        all_ok = all(check.status == "passed" for check in checks)
        checks.append(
            CheckResult(
                "packages",
                "passed" if all_ok else "failed",
                "Host dependencies verified" if all_ok else "One or more dependencies missing",
                critical=True,
            )
        )
        return checks

    def rollback(self) -> None:
        """Packages are intentionally left installed — removal is operator-driven."""


def _install_packages(packages: list[str]) -> None:
    ok, output = run_command(["dnf", "install", "-y", *packages])
    if not ok:
        raise RuntimeError(f"dnf install failed: {output}")
