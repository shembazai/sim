"""Shared IRE data models for observed runtime state."""

from __future__ import annotations

from pydantic import BaseModel, Field


class NetworkInterfaceObserved(BaseModel):
    name: str
    ip: str | None = None
    state: str | None = None

    model_config = {"extra": "forbid"}


class NetworkObserved(BaseModel):
    interfaces: dict[str, NetworkInterfaceObserved] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class SSHObserved(BaseModel):
    service_active: bool = False
    listening_ports: list[int] = Field(default_factory=list)
    bind_addresses: list[str] = Field(default_factory=list)
    permit_root_login: str | None = None
    password_authentication: str | None = None
    allowed_users: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class StorageMountObserved(BaseModel):
    path: str
    mounted: bool = False
    uuid: str | None = None
    fstype: str | None = None
    source: str | None = None
    findmnt_source: str | None = None
    findmnt_fstype: str | None = None
    mount_sources_agree: bool | None = None
    free_gib: float | None = None
    total_gib: float | None = None
    btrfs_healthy: bool | None = None
    snapshot_count: int | None = None
    smart_healthy: bool | None = None

    model_config = {"extra": "forbid"}


class StorageObserved(BaseModel):
    mounts: list[StorageMountObserved] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class FirewallObserved(BaseModel):
    active: bool = False
    interface_zones: dict[str, str] = Field(default_factory=dict)
    ssh_allowed_zones: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class TailscaleObserved(BaseModel):
    installed: bool = False
    online: bool = False
    hostname: str | None = None
    tailnet: str | None = None

    model_config = {"extra": "forbid"}


class ObservedState(BaseModel):
    hostname: str = ""
    network: NetworkObserved = NetworkObserved()
    ssh: SSHObserved = SSHObserved()
    storage: StorageObserved = StorageObserved()
    firewall: FirewallObserved = FirewallObserved()
    tailscale: TailscaleObserved = TailscaleObserved()

    model_config = {"extra": "forbid"}
