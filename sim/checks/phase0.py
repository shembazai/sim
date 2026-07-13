"""Phase 0 validation checks.

Nothing is installed before Phase 0 passes.
"""

from __future__ import annotations

import platform
import shutil
import socket
import sys
from pathlib import Path

from sim.config import detect_host_os, os_recommendation_warning
from sim.phases.lifecycle import CheckResult
from sim.subprocess_util import run_command


def _check_cpu() -> CheckResult:
    model = ""
    cpuinfo = Path("/proc/cpuinfo")
    if cpuinfo.exists():
        for line in cpuinfo.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.lower().startswith("model name"):
                _, _, model = line.partition(":")
                model = model.strip()
                break
    if model:
        return CheckResult("cpu", "passed", f"Detected {model}", critical=True)
    return CheckResult("cpu", "failed", "Unable to detect CPU model", critical=True)


def _check_ram() -> CheckResult:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return CheckResult("ram", "failed", "/proc/meminfo missing", critical=True)
    total_kib = 0
    for line in meminfo.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("MemTotal:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                total_kib = int(parts[1])
            break
    if total_kib <= 0:
        return CheckResult("ram", "failed", "Unable to parse MemTotal", critical=True)
    total_gib = total_kib / (1024 * 1024)
    return CheckResult("ram", "passed", f"{total_gib:.2f} GiB detected", critical=True)


def _check_storage(root: Path) -> CheckResult:
    try:
        usage = shutil.disk_usage(root)
    except OSError as exc:
        return CheckResult("storage", "failed", str(exc), critical=True)
    total_gib = usage.total / (1024**3)
    return CheckResult("storage", "passed", f"{total_gib:.2f} GiB total on {root}", critical=True)


def _check_filesystem_root() -> CheckResult:
    mounts = Path("/proc/mounts")
    if not mounts.exists():
        return CheckResult("filesystem", "failed", "/proc/mounts missing", critical=True)
    for line in mounts.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[1] == "/":
            return CheckResult("filesystem", "passed", f"Root filesystem type: {parts[2]}", critical=True)
    return CheckResult("filesystem", "failed", "Root filesystem entry not found", critical=True)


def _check_uefi() -> CheckResult:
    if Path("/sys/firmware/efi").exists():
        return CheckResult("uefi", "passed", "UEFI firmware detected", critical=True)
    return CheckResult("uefi", "failed", "UEFI firmware path not found", critical=True)


def _check_secure_boot() -> CheckResult:
    efivars = Path("/sys/firmware/efi/efivars")
    if not efivars.exists():
        return CheckResult("secure_boot", "warning", "Unable to verify Secure Boot state", critical=False)
    matches = list(efivars.glob("SecureBoot-*"))
    if not matches:
        return CheckResult("secure_boot", "warning", "SecureBoot EFI variable not found", critical=False)
    data = matches[0].read_bytes()
    enabled = len(data) > 4 and data[4] == 1
    return CheckResult(
        "secure_boot",
        "passed" if enabled else "warning",
        "Enabled" if enabled else "Disabled",
        critical=False,
    )


def _check_tpm() -> CheckResult:
    if Path("/dev/tpm0").exists() or Path("/sys/class/tpm").exists():
        return CheckResult("tpm", "passed", "TPM detected", critical=False)
    return CheckResult("tpm", "warning", "TPM not detected", critical=False)


def _check_os() -> CheckResult:
    try:
        distro, version = detect_host_os()
    except FileNotFoundError as exc:
        return CheckResult("operating_system", "failed", str(exc), critical=True)
    warning = os_recommendation_warning(distro, version)
    if warning is None:
        return CheckResult("operating_system", "passed", f"{distro} {version}", critical=True)
    return CheckResult("operating_system", "warning", warning, critical=False)


def _check_kernel() -> CheckResult:
    release = platform.release().strip()
    if release:
        return CheckResult("kernel", "passed", release, critical=True)
    return CheckResult("kernel", "failed", "Kernel release empty", critical=True)


def _check_python() -> CheckResult:
    ver = sys.version_info
    if (ver.major, ver.minor) >= (3, 12):
        return CheckResult("python", "passed", platform.python_version(), critical=True)
    return CheckResult("python", "failed", f"Python {platform.python_version()} < 3.12", critical=True)


def _check_dnf() -> CheckResult:
    if shutil.which("dnf"):
        return CheckResult("dnf", "passed", "dnf found in PATH", critical=True)
    return CheckResult("dnf", "failed", "dnf command not found", critical=True)


def _check_selinux() -> CheckResult:
    ok, output = run_command(["getenforce"])
    if not ok:
        return CheckResult("selinux", "failed", output or "getenforce failed", critical=True)
    mode = output.strip().lower()
    if mode == "enforcing":
        return CheckResult("selinux", "passed", "SELinux enforcing", critical=True)
    return CheckResult("selinux", "failed", f"SELinux mode is {output}", critical=True)


def _check_firewall() -> CheckResult:
    ok, output = run_command(["systemctl", "is-active", "firewalld"])
    if ok and output.strip() == "active":
        return CheckResult("firewall", "passed", "firewalld active", critical=True)
    return CheckResult("firewall", "failed", output or "firewalld inactive", critical=True)


def _check_ssh() -> CheckResult:
    ok, output = run_command(["systemctl", "is-active", "sshd"])
    if ok and output.strip() == "active":
        return CheckResult("ssh", "passed", "sshd active", critical=True)
    return CheckResult("ssh", "failed", output or "sshd inactive", critical=True)


def _check_internet() -> CheckResult:
    try:
        with socket.create_connection(("1.1.1.1", 53), timeout=2):
            return CheckResult("internet", "passed", "Outbound connectivity available", critical=True)
    except OSError as exc:
        return CheckResult("internet", "failed", str(exc), critical=True)


def _check_dns() -> CheckResult:
    try:
        addr = socket.gethostbyname("github.com")
        return CheckResult("dns", "passed", f"github.com -> {addr}", critical=True)
    except OSError as exc:
        return CheckResult("dns", "failed", str(exc), critical=True)


def _check_ntp() -> CheckResult:
    ok, output = run_command(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    if ok and output.strip().lower() in ("yes", "true", "1"):
        return CheckResult("ntp", "passed", "NTP synchronized", critical=True)
    return CheckResult("ntp", "failed", output or "NTP not synchronized", critical=True)


def _check_repositories() -> CheckResult:
    ok, output = run_command(["dnf", "repolist", "--enabled"])
    if not ok:
        return CheckResult("repositories", "failed", output or "dnf repolist failed", critical=True)
    if "repo id" in output.lower():
        return CheckResult("repositories", "passed", "Enabled repositories detected", critical=True)
    return CheckResult("repositories", "failed", "No enabled repositories found", critical=True)


def _check_available_disk_space(root: Path, min_free_gib: int) -> CheckResult:
    try:
        usage = shutil.disk_usage(root)
    except OSError as exc:
        return CheckResult("available_disk_space", "failed", str(exc), critical=True)
    free_gib = usage.free / (1024**3)
    if free_gib >= min_free_gib:
        return CheckResult(
            "available_disk_space",
            "passed",
            f"{free_gib:.2f} GiB free on {root} (min {min_free_gib} GiB)",
            critical=True,
        )
    return CheckResult(
        "available_disk_space",
        "failed",
        f"{free_gib:.2f} GiB free on {root}, need at least {min_free_gib} GiB",
        critical=True,
    )


def _check_gpu() -> CheckResult:
    ok, output = run_command(["nvidia-smi"])
    if ok:
        first_line = output.splitlines()[0] if output else "nvidia-smi succeeded"
        return CheckResult("gpu", "passed", first_line, critical=False)
    return CheckResult("gpu", "warning", "No NVIDIA GPU runtime detected", critical=False)


def run_phase0_checks(root: Path, min_free_gib: int = 20) -> list[CheckResult]:
    return [
        _check_cpu(),
        _check_gpu(),
        _check_ram(),
        _check_storage(root),
        _check_filesystem_root(),
        _check_uefi(),
        _check_secure_boot(),
        _check_tpm(),
        _check_os(),
        _check_kernel(),
        _check_python(),
        _check_dnf(),
        _check_selinux(),
        _check_firewall(),
        _check_ssh(),
        _check_internet(),
        _check_dns(),
        _check_ntp(),
        _check_repositories(),
        _check_available_disk_space(root, min_free_gib=min_free_gib),
    ]
