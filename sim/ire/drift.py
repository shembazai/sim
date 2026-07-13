"""Compare desired and observed infrastructure state."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from sim.ire.desired import InfrastructureDesiredState, StorageMountDesired
from sim.ire.models import ObservedState, StorageMountObserved

DriftSeverity = Literal["info", "warning", "critical"]


@dataclass(frozen=True)
class DriftItem:
    component: str
    field: str
    severity: DriftSeverity
    desired: str
    observed: str
    message: str
    auto_repairable: bool = False


def _find_mount(observed: ObservedState, path: str) -> StorageMountObserved | None:
    for mount in observed.storage.mounts:
        if mount.path == path:
            return mount
    return None


def _compare_storage(
    desired_mount: StorageMountDesired,
    observed: ObservedState,
) -> list[DriftItem]:
    items: list[DriftItem] = []
    path = str(desired_mount.path)
    mount = _find_mount(observed, path)
    if mount is None or not mount.mounted:
        items.append(
            DriftItem(
                component="storage",
                field=f"mounts.{path}",
                severity="warning" if desired_mount.required else "info",
                desired="mounted",
                observed="missing",
                message=(
                    f"Expected storage mount {path} unavailable. "
                    "No destructive action will be taken."
                ),
                auto_repairable=False,
            )
        )
        return items
    if desired_mount.uuid and mount.uuid and desired_mount.uuid != mount.uuid:
        items.append(
            DriftItem(
                component="storage",
                field=f"mounts.{path}.uuid",
                severity="critical",
                desired=desired_mount.uuid,
                observed=mount.uuid or "unknown",
                message=f"Mount {path} UUID mismatch — possible wrong filesystem attached.",
                auto_repairable=False,
            )
        )
    if desired_mount.fstype and mount.fstype and desired_mount.fstype != mount.fstype:
        items.append(
            DriftItem(
                component="storage",
                field=f"mounts.{path}.fstype",
                severity="warning",
                desired=desired_mount.fstype,
                observed=mount.fstype,
                message=f"Mount {path} filesystem type differs from desired.",
                auto_repairable=False,
            )
        )
    if (
        desired_mount.min_free_gib is not None
        and mount.free_gib is not None
        and mount.free_gib < desired_mount.min_free_gib
    ):
        items.append(
            DriftItem(
                component="storage",
                field=f"mounts.{path}.free_space",
                severity="warning",
                desired=f">= {desired_mount.min_free_gib} GiB",
                observed=f"{mount.free_gib} GiB",
                message=f"Low free space on {path}.",
                auto_repairable=False,
            )
        )
    if mount.btrfs_healthy is False:
        items.append(
            DriftItem(
                component="storage",
                field=f"mounts.{path}.btrfs_health",
                severity="critical",
                desired="healthy",
                observed="degraded",
                message=f"Btrfs health check failed on {path}.",
                auto_repairable=False,
            )
        )
    if mount.smart_healthy is False:
        items.append(
            DriftItem(
                component="storage",
                field=f"mounts.{path}.smart_health",
                severity="critical",
                desired="healthy",
                observed="failed",
                message=f"SMART health check failed on device backing {path}.",
                auto_repairable=False,
            )
        )
    if mount.mount_sources_agree is False:
        items.append(
            DriftItem(
                component="storage",
                field=f"mounts.{path}.source_agreement",
                severity="warning",
                desired="proc_mounts agrees with findmnt",
                observed=(
                    f"proc={mount.source or 'none'} findmnt={mount.findmnt_source or 'none'}"
                ),
                message=(
                    f"/proc/mounts and findmnt disagree for {path} — "
                    "infrastructure state may be ambiguous."
                ),
                auto_repairable=False,
            )
        )
    return items


def _compare_ssh(desired: InfrastructureDesiredState, observed: ObservedState) -> list[DriftItem]:
    items: list[DriftItem] = []
    ssh_d = desired.ssh
    ssh_o = observed.ssh

    if ssh_d.enabled and not ssh_o.service_active:
        items.append(
            DriftItem(
                component="ssh",
                field="service_active",
                severity="critical",
                desired="active",
                observed="inactive",
                message="sshd is not active.",
                auto_repairable=True,
            )
        )
    if ssh_d.port not in ssh_o.listening_ports and ssh_o.listening_ports:
        items.append(
            DriftItem(
                component="ssh",
                field="port",
                severity="critical",
                desired=str(ssh_d.port),
                observed=",".join(str(p) for p in ssh_o.listening_ports),
                message="SSH is not listening on the desired port.",
                auto_repairable=True,
            )
        )
    volatile_binds = [
        addr for addr in ssh_o.bind_addresses
        if addr not in ("*", "0.0.0.0", "::", "[::]")
        and not addr.startswith("127.")
    ]
    if volatile_binds:
        items.append(
            DriftItem(
                component="ssh",
                field="bind_addresses",
                severity="critical",
                desired="0.0.0.0 or :: (all interfaces)",
                observed=",".join(volatile_binds),
                message=(
                    "SSH binds to specific addresses. Avoid Tailscale or DHCP IPs "
                    "in ListenAddress — use firewall rules for access control."
                ),
                auto_repairable=True,
            )
        )
    if ssh_d.root_login is False and (ssh_o.permit_root_login or "").lower() not in ("no", "prohibit-password", "without-password"):
        if ssh_o.permit_root_login:
            items.append(
                DriftItem(
                    component="ssh",
                    field="permit_root_login",
                    severity="critical",
                    desired="no",
                    observed=ssh_o.permit_root_login,
                    message="Root SSH login should be disabled.",
                    auto_repairable=True,
                )
            )
    if ssh_d.password_authentication is False and (ssh_o.password_authentication or "").lower() == "yes":
        items.append(
            DriftItem(
                component="ssh",
                field="password_authentication",
                severity="critical",
                desired="no",
                observed=ssh_o.password_authentication or "yes",
                message="Password authentication should be disabled.",
                auto_repairable=True,
            )
        )
    if ssh_d.allowed_users:
        missing = sorted(set(ssh_d.allowed_users) - set(ssh_o.allowed_users))
        if missing:
            items.append(
                DriftItem(
                    component="ssh",
                    field="allowed_users",
                    severity="warning",
                    desired=",".join(ssh_d.allowed_users),
                    observed=",".join(ssh_o.allowed_users) or "(none configured)",
                    message=f"AllowUsers missing: {', '.join(missing)}",
                    auto_repairable=True,
                )
            )
    return items


def _compare_firewall(desired: InfrastructureDesiredState, observed: ObservedState) -> list[DriftItem]:
    items: list[DriftItem] = []
    if not observed.firewall.active:
        items.append(
            DriftItem(
                component="firewall",
                field="active",
                severity="critical",
                desired="active",
                observed="inactive",
                message="firewalld is not active.",
                auto_repairable=True,
            )
        )
        return items
    for iface, iface_desired in desired.firewall.interfaces.items():
        observed_zone = observed.firewall.interface_zones.get(iface)
        if observed_zone != iface_desired.zone:
            items.append(
                DriftItem(
                    component="firewall",
                    field=f"interfaces.{iface}.zone",
                    severity="warning",
                    desired=iface_desired.zone,
                    observed=observed_zone or "unassigned",
                    message=f"Interface {iface} is not in zone {iface_desired.zone}.",
                    auto_repairable=True,
                )
            )
    ssh_access = desired.firewall.services.get("ssh")
    if ssh_access:
        for iface in ssh_access.allowed_interfaces:
            zone = observed.firewall.interface_zones.get(iface)
            if zone and zone not in observed.firewall.ssh_allowed_zones and "ssh" not in observed.firewall.ssh_allowed_zones:
                items.append(
                    DriftItem(
                        component="firewall",
                        field=f"services.ssh.{iface}",
                        severity="warning",
                        desired="ssh permitted",
                        observed=f"zone={zone}",
                        message=f"SSH may not be reachable via interface {iface}.",
                        auto_repairable=True,
                    )
                )
    return items


def _compare_tailscale(desired: InfrastructureDesiredState, observed: ObservedState) -> list[DriftItem]:
    items: list[DriftItem] = []
    if not desired.tailscale.enabled:
        return items
    if not observed.tailscale.installed:
        items.append(
            DriftItem(
                component="tailscale",
                field="installed",
                severity="warning",
                desired="installed",
                observed="missing",
                message="Tailscale is not installed.",
                auto_repairable=False,
            )
        )
        return items
    if not observed.tailscale.online:
        items.append(
            DriftItem(
                component="tailscale",
                field="online",
                severity="warning",
                desired="online",
                observed="offline",
                message="Tailscale is installed but not online.",
                auto_repairable=False,
            )
        )
    if desired.tailscale.hostname and observed.tailscale.hostname:
        expected = desired.tailscale.hostname.rstrip(".")
        actual = observed.tailscale.hostname.rstrip(".")
        if not actual.startswith(expected):
            items.append(
                DriftItem(
                    component="tailscale",
                    field="hostname",
                    severity="warning",
                    desired=expected,
                    observed=actual,
                    message=f"Tailscale hostname mismatch (expected prefix {expected!r}).",
                    auto_repairable=False,
                )
            )
    if desired.tailscale.tailnet and observed.tailscale.tailnet:
        if observed.tailscale.tailnet != desired.tailscale.tailnet:
            items.append(
                DriftItem(
                    component="tailscale",
                    field="tailnet",
                    severity="critical",
                    desired=desired.tailscale.tailnet,
                    observed=observed.tailscale.tailnet,
                    message=(
                        f"Tailscale tailnet mismatch "
                        f"(expected {desired.tailscale.tailnet!r}, "
                        f"observed {observed.tailscale.tailnet!r})."
                    ),
                    auto_repairable=False,
                )
            )
    return items


def detect_drift(desired: InfrastructureDesiredState, observed: ObservedState) -> list[DriftItem]:
    """Return all drift items between desired and observed state."""
    items: list[DriftItem] = []
    items.extend(_compare_ssh(desired, observed))
    for mount in desired.storage.mounts:
        items.extend(_compare_storage(mount, observed))
    items.extend(_compare_firewall(desired, observed))
    items.extend(_compare_tailscale(desired, observed))
    return items
