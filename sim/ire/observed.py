"""Runtime observation of host infrastructure state."""

from __future__ import annotations

import socket
from pathlib import Path

from sim.ire.models import (
    FirewallObserved,
    NetworkInterfaceObserved,
    NetworkObserved,
    ObservedState,
    SSHObserved,
    StorageObserved,
    TailscaleObserved,
)
from sim.ire.modules.storage import observe_storage
from sim.subprocess_util import run_command

__all__ = [
    "FirewallObserved",
    "NetworkInterfaceObserved",
    "NetworkObserved",
    "ObservedState",
    "SSHObserved",
    "StorageMountObserved",
    "StorageObserved",
    "TailscaleObserved",
    "collect_observed_state",
]

from sim.ire.models import StorageMountObserved  # re-export


def _parse_sshd_config(path: Path = Path("/etc/ssh/sshd_config")) -> dict[str, str]:
    """Parse sshd effective settings from main config plus included drop-ins.

    SIM writes managed settings to ``sshd_config.d/``; observing only the main
    file misses AllowUsers / PasswordAuthentication and breaks reconcile verify.
    """
    values: dict[str, str] = {}
    paths: list[Path] = [path]
    drop_in_dir = path.parent / "sshd_config.d"
    if drop_in_dir.is_dir():
        paths.extend(sorted(drop_in_dir.glob("*.conf")))

    for cfg_path in paths:
        if not cfg_path.exists():
            continue
        try:
            content = cfg_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw in content.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or line.lower().startswith("match"):
                continue
            if line.lower().startswith("include"):
                continue
            parts = line.split(None, 1)
            if len(parts) == 2:
                # Later drop-ins override earlier keys (sshd last-wins for most).
                values[parts[0].lower()] = parts[1].strip()
    return values


def _ssh_ports_from_config(cfg: dict[str, str]) -> list[int]:
    ports: list[int] = []
    for token in cfg.get("port", "22").split():
        if token.isdigit():
            ports.append(int(token))
    return ports or [22]


def _parse_ss_listeners(output: str) -> list[tuple[str, int]]:
    """Parse ``ss -lntH`` lines into (bind_host, port) pairs."""
    listeners: list[tuple[str, int]] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        local_addr = parts[3]
        if ":" not in local_addr:
            continue
        host, port_raw = local_addr.rsplit(":", 1)
        if not port_raw.isdigit():
            continue
        host = host.strip("[]") or "*"
        listeners.append((host, int(port_raw)))
    return listeners


def _parse_ip_addr_show(output: str) -> dict[str, NetworkInterfaceObserved]:
    """Parse ``ip -o addr show`` output into interface records."""
    interfaces: dict[str, NetworkInterfaceObserved] = {}
    for line in output.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        name = parts[1]
        ip = None
        for idx, token in enumerate(parts):
            if token in ("inet", "inet6") and idx + 1 < len(parts):
                ip = parts[idx + 1].split("/", 1)[0]
                break
        interfaces[name] = NetworkInterfaceObserved(name=name, ip=ip, state=None)
    return interfaces


def _parse_firewall_active_zones(output: str) -> dict[str, str]:
    """Parse ``firewall-cmd --get-active-zones`` into interface → zone mapping."""
    interface_zones: dict[str, str] = {}
    current_zone: str | None = None
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("interfaces:"):
            if current_zone is None:
                continue
            zone_name = current_zone.split("(", 1)[0].strip()
            ifaces = stripped.split(":", 1)[1].strip().split()
            for iface in ifaces:
                interface_zones[iface] = zone_name
        else:
            current_zone = stripped
    return interface_zones


def _observe_ssh() -> SSHObserved:
    active = False
    ok, output = run_command(["systemctl", "is-active", "sshd"])
    if ok and output.strip() == "active":
        active = True

    cfg = _parse_sshd_config()
    ssh_ports = set(_ssh_ports_from_config(cfg))
    ports: list[int] = []
    bind_addresses: list[str] = []
    ok, output = run_command(["ss", "-lntH"])
    if ok:
        for host, port in _parse_ss_listeners(output):
            if port in ssh_ports:
                ports.append(port)
                bind_addresses.append(host)

    allow_users = cfg.get("allowusers", "").split()
    return SSHObserved(
        service_active=active,
        listening_ports=sorted(set(ports)),
        bind_addresses=sorted(set(bind_addresses)),
        permit_root_login=cfg.get("permitrootlogin"),
        password_authentication=cfg.get("passwordauthentication"),
        allowed_users=allow_users,
    )


def _observe_storage(paths: list[Path] | None = None) -> StorageObserved:
    return observe_storage(paths)


def _observe_network() -> NetworkObserved:
    ok, output = run_command(["ip", "-o", "addr", "show"])
    if not ok:
        return NetworkObserved()
    return NetworkObserved(interfaces=_parse_ip_addr_show(output))


def _observe_firewall() -> FirewallObserved:
    ok, output = run_command(["systemctl", "is-active", "firewalld"])
    active = ok and output.strip() == "active"
    interface_zones: dict[str, str] = {}
    ssh_zones: list[str] = []
    ssh_services_observable = False
    if not active:
        return FirewallObserved(active=False)
    ok, output = run_command(["firewall-cmd", "--get-active-zones"])
    if ok:
        interface_zones = _parse_firewall_active_zones(output)
    ok, output = run_command(["firewall-cmd", "--list-services", "--zone=trusted"])
    if ok:
        ssh_services_observable = True
        if "ssh" in output.split():
            ssh_zones.append("trusted")
    return FirewallObserved(
        active=active,
        interface_zones=interface_zones,
        ssh_allowed_zones=ssh_zones,
        ssh_services_observable=ssh_services_observable,
    )


def _observe_tailscale() -> TailscaleObserved:
    from sim.ire.modules.tailscale import observe_tailscale

    return observe_tailscale()


def collect_observed_state(*, storage_paths: list[Path] | None = None) -> ObservedState:
    """Discover current host infrastructure state without mutating anything."""
    return ObservedState(
        hostname=socket.gethostname(),
        network=_observe_network(),
        ssh=_observe_ssh(),
        storage=_observe_storage(storage_paths),
        firewall=_observe_firewall(),
        tailscale=_observe_tailscale(),
    )
