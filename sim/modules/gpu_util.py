"""Shared helpers for Stage 4 GPU provisioning modules."""

from __future__ import annotations

import shutil
from pathlib import Path

from sim.config import ManifestConfig
from sim.subprocess_util import run_command

NVIDIA_DRIVER_PACKAGES = ("cuda-drivers",)
CUDA_PACKAGES = ("cuda-toolkit",)
NVIDIA_CONTAINER_PACKAGES = ("nvidia-container-toolkit",)


def nvidia_gpu_enabled(cfg: ManifestConfig) -> bool:
    return cfg.gpu.vendor == "nvidia"


def nvidia_smi_works() -> tuple[bool, str]:
    ok, output = run_command(["nvidia-smi"])
    if ok:
        line = output.splitlines()[0] if output else "nvidia-smi succeeded"
        return True, line
    return False, output or "nvidia-smi unavailable"


def nvcc_works() -> tuple[bool, str]:
    path = shutil.which("nvcc")
    if not path:
        return False, "nvcc not found in PATH"
    ok, output = run_command([path, "--version"])
    if not ok:
        return False, output or "nvcc --version failed"
    for line in output.splitlines():
        if "release" in line.lower():
            return True, line.strip()
    detail = output.splitlines()[0] if output else "nvcc available"
    return True, detail.strip()


def nvidia_ctk_available() -> tuple[bool, str]:
    path = shutil.which("nvidia-ctk")
    if not path:
        return False, "nvidia-ctk not found in PATH"
    ok, output = run_command([path, "--version"])
    if not ok:
        return False, output or "nvidia-ctk --version failed"
    detail = output.splitlines()[0] if output else "nvidia-ctk available"
    return True, detail


def podman_nvidia_runtime_ready() -> tuple[bool, str]:
    """Return whether Podman appears configured for NVIDIA GPUs."""
    ok, output = run_command(["podman", "info", "--format", "{{.Host.Devices}}"])
    if ok and "nvidia" in output.lower():
        return True, "Podman NVIDIA devices available"

    for path in (
        Path("/etc/containers/containers.conf"),
        Path("/etc/containers/containers.conf.d/nvidia.toml"),
    ):
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "nvidia" in text.lower():
            return True, f"NVIDIA runtime referenced in {path}"

    return False, "Podman NVIDIA runtime not detected"


def install_packages(packages: tuple[str, ...]) -> None:
    ok, output = run_command(["dnf", "install", "-y", *packages])
    if not ok:
        raise RuntimeError(f"dnf install failed: {output}")


def configure_podman_nvidia_runtime() -> None:
    ok, output = run_command(["nvidia-ctk", "runtime", "configure", "--runtime=podman"])
    if not ok:
        raise RuntimeError(f"nvidia-ctk runtime configure failed: {output}")
