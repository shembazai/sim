"""Desired infrastructure state models.

Desired state expresses intent only. Volatile runtime values (Tailscale IPs,
DHCP addresses, dynamically assigned interface names beyond logical aliases)
must never appear here.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field


class SSHDesiredState(BaseModel):
    """SSH service intent. Access control is via firewall and auth — not bind IPs."""

    enabled: bool = True
    port: int = Field(default=22, ge=1, le=65535)
    authentication: Literal["publickey"] = "publickey"
    root_login: bool = False
    password_authentication: bool = False
    allowed_users: list[str] = Field(default_factory=list)
    remote_access: Literal["tailscale", "lan", "none"] = "tailscale"
    emergency_lan_access: bool = True

    model_config = {"extra": "forbid"}


class FirewallInterfaceDesired(BaseModel):
    zone: str

    model_config = {"extra": "forbid"}


class FirewallServiceDesired(BaseModel):
    allowed_interfaces: list[str] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class FirewallDesiredState(BaseModel):
    interfaces: dict[str, FirewallInterfaceDesired] = Field(default_factory=dict)
    services: dict[str, FirewallServiceDesired] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


class TailscaleDesiredState(BaseModel):
    """Tailscale identity intent. Never store volatile Tailscale IPs here."""

    enabled: bool = True
    hostname: str | None = None
    tailnet: str | None = None

    model_config = {"extra": "forbid"}


class StorageMountDesired(BaseModel):
    path: Path
    uuid: str | None = None
    fstype: str | None = None
    required: bool = True
    min_free_gib: int | None = Field(default=None, ge=0)

    model_config = {"extra": "forbid"}


class StorageDesiredState(BaseModel):
    mounts: list[StorageMountDesired] = Field(default_factory=list)

    model_config = {"extra": "forbid"}


class InfrastructureDesiredState(BaseModel):
    ssh: SSHDesiredState = SSHDesiredState()
    firewall: FirewallDesiredState = FirewallDesiredState()
    tailscale: TailscaleDesiredState = TailscaleDesiredState()
    storage: StorageDesiredState = StorageDesiredState()

    model_config = {"extra": "forbid"}
