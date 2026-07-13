"""Infrastructure inventory generation for SIM."""

from __future__ import annotations

import json
import platform
import shutil
import socket
from datetime import UTC, datetime
from pathlib import Path

from sim.ire.models import ObservedState
from sim.ire.observed import collect_observed_state
from sim.subprocess_util import run_command


def _version_from_command(args: list[str]) -> str:
    ok, output = run_command(args)
    if not ok:
        return "not_detected"
    return output.splitlines()[0] if output else "detected"


def _read_first_matching_line(path: Path, prefix: str) -> str:
    if not path.exists():
        return "not_detected"
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.lower().startswith(prefix.lower()):
            return line.split(":", 1)[1].strip() if ":" in line else line.strip()
    return "not_detected"


def _open_ports() -> list[int]:
    ok, output = run_command(["ss", "-lntH"])
    if not ok:
        return []
    ports: set[int] = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        local_addr = parts[3]
        if ":" not in local_addr:
            continue
        port_raw = local_addr.rsplit(":", 1)[-1]
        if port_raw.isdigit():
            ports.add(int(port_raw))
    return sorted(ports)


def _os_release() -> tuple[str, str]:
    values: dict[str, str] = {}
    path = Path("/etc/os-release")
    if not path.exists():
        return "not_detected", "not_detected"
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"')
    return values.get("ID", "not_detected"), values.get("VERSION_ID", "not_detected")


def _infrastructure_section(observed: ObservedState) -> dict[str, object]:
    """Summarize IRE-observed infrastructure for inventory snapshots."""
    return {
        "ssh": observed.ssh.model_dump(),
        "firewall": observed.firewall.model_dump(),
        "storage": observed.storage.model_dump(),
        "tailscale": observed.tailscale.model_dump(),
        "network_interfaces": {
            name: iface.model_dump()
            for name, iface in observed.network.interfaces.items()
        },
    }


def collect_inventory(
    filesystem_root: Path,
    *,
    storage_paths: list[Path] | None = None,
    include_infrastructure: bool = True,
) -> dict[str, object]:
    distro, os_version = _os_release()
    kernel = platform.release() or "not_detected"
    cpu = _read_first_matching_line(Path("/proc/cpuinfo"), "model name")
    ram = _read_first_matching_line(Path("/proc/meminfo"), "MemTotal")
    disk_total_gib = "not_detected"
    try:
        usage = shutil.disk_usage(filesystem_root)
        disk_total_gib = f"{usage.total / (1024**3):.2f}"
    except OSError:
        pass

    versions = {
        "python": platform.python_version(),
        "podman": _version_from_command(["podman", "--version"]),
        "nvidia": _version_from_command(["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"]),
        "cuda": _version_from_command(["nvcc", "--version"]),
        "ollama": _version_from_command(["ollama", "--version"]),
        "open_webui": "not_detected",
        "grafana": _version_from_command(["grafana-server", "-v"]),
        "prometheus": _version_from_command(["prometheus", "--version"]),
    }

    inventory = {
        "generated_at": datetime.now(UTC).isoformat(),
        "operating_system": f"{distro} {os_version}",
        "kernel": kernel,
        "cpu": cpu,
        "gpu": "detected" if versions["nvidia"] != "not_detected" else "not_detected",
        "ram": ram,
        "storage": {"root": str(filesystem_root), "total_gib": disk_total_gib},
        "installed_packages": [],
        "installed_services": [],
        "open_ports": _open_ports(),
        "python_version": versions["python"],
        "podman_version": versions["podman"],
        "nvidia_version": versions["nvidia"],
        "cuda_version": versions["cuda"],
        "ollama_version": versions["ollama"],
        "open_webui_version": versions["open_webui"],
        "grafana_version": versions["grafana"],
        "prometheus_version": versions["prometheus"],
        "hostname": socket.gethostname(),
    }
    if include_infrastructure:
        observed = collect_observed_state(storage_paths=storage_paths)
        inventory["infrastructure"] = _infrastructure_section(observed)
    return inventory


def write_inventory(inventory: dict[str, object], output_file: Path) -> Path:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(json.dumps(inventory, indent=2) + "\n", encoding="utf-8")
    return output_file
