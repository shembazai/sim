"""Configuration models and loaders for SIM.

Design decisions this module encodes (per K1 Infrastructure Decisions, as of
2026-07-08 correction):
- Base OS is Rocky Linux 10+, as a RECOMMENDATION, not a hard lock. Earlier
  revisions of this module hard-rejected anything but exactly "rocky"/"10"
  via a Pydantic Literal + ValidationError; that was reverted on user
  correction. SIM now accepts any distro/version in the manifest and instead
  surfaces a non-fatal warning when the host deviates from the recommended
  target (Rocky Linux, major version >= 10). This matters because the
  homelab no longer includes throwaway KVM test VMs on a different distro
  to validate against -- the only two machines are a Kubuntu management
  laptop (T14) and the Rocky Linux 10.2 server itself -- so a hard lock has
  no test value and only risks blocking legitimate future targets (e.g. a
  later Rocky point release).
- All ports are configuration, never hardcoded in module logic. Any module
  that needs a port reads it from ManifestConfig.services.*.port.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator

RECOMMENDED_DISTRO = "rocky"
RECOMMENDED_MIN_MAJOR_VERSION = 10

PortNumber = int  # validated range enforced in ServiceConfig


def _parse_major_version(version_id: str) -> int | None:
    """Extract the leading integer from a VERSION_ID string.

    Handles "10", "10.2", "10.2.1", etc. Returns None if unparsable so
    callers can fall back to a "could not determine" warning instead of
    crashing on a malformed value.
    """
    head = version_id.split(".", 1)[0].strip()
    return int(head) if head.isdigit() else None


def os_recommendation_warning(distro: str, version: str) -> str | None:
    """Return a human-readable warning if (distro, version) deviates from
    the recommended target, or None if it matches closely enough. Never
    raises -- advisory only, per the corrected (non-hardcoded) OS policy."""
    major = _parse_major_version(version)
    if distro != RECOMMENDED_DISTRO:
        return (
            f"Detected distro {distro!r}; recommended target is "
            f"{RECOMMENDED_DISTRO!r}. SIM is developed and tested against "
            f"Rocky Linux -- other distros are untested and may behave "
            f"unexpectedly."
        )
    if major is None:
        return (
            f"Could not parse a major version from VERSION_ID={version!r}; "
            f"recommended target is Rocky Linux {RECOMMENDED_MIN_MAJOR_VERSION}+."
        )
    if major < RECOMMENDED_MIN_MAJOR_VERSION:
        return (
            f"Detected Rocky Linux {version}; recommended minimum is "
            f"{RECOMMENDED_MIN_MAJOR_VERSION}+. Older releases are untested."
        )
    return None


# --------------------------------------------------------------------------
# bootstrap.yaml — first-run interactive answers, input to manifest generation
# --------------------------------------------------------------------------

class BootstrapConfig(BaseModel):
    """Raw answers collected during the interactive first run.

    This is intentionally a thinner, less strict model than ManifestConfig:
    bootstrap.yaml records what the operator asked for; the Port Manager and
    validation modules then produce the authoritative, fully-resolved
    ManifestConfig. Keeping these separate means a bad manifest can always be
    regenerated from bootstrap answers without re-running the interactive
    flow.
    """

    hostname: str
    role: Literal["production", "staging", "development"] = "production"
    enable_gpu: bool = True
    enable_monitoring: bool = True
    requested_ports: dict[str, int] = Field(default_factory=dict)

    model_config = {"extra": "forbid"}


# --------------------------------------------------------------------------
# k1_server_manifest.yaml — authoritative, fully-resolved server description
# --------------------------------------------------------------------------

class ServerInfo(BaseModel):
    hostname: str
    role: Literal["production", "staging", "development"] = "production"

    model_config = {"extra": "forbid"}


class OSInfo(BaseModel):
    """Detected/declared host OS. Open-ended by design -- see module
    docstring. Recommendation checking happens via os_recommendation_warning,
    not via field validation, so an off-recommendation value is loadable
    and installable, just flagged."""

    distro: str = RECOMMENDED_DISTRO
    version: str = str(RECOMMENDED_MIN_MAJOR_VERSION)

    model_config = {"extra": "forbid"}


class PythonInfo(BaseModel):
    version: str = "3.12"
    venv: Path = Path("/opt/k1/.venv")

    model_config = {"extra": "forbid"}


class FilesystemInfo(BaseModel):
    root: Path = Path("/opt/k1")

    model_config = {"extra": "forbid"}


class RegistryInfo(BaseModel):
    """Optional local image cache / mirror policy for Podman."""

    enabled: bool = False
    endpoint: str = "localhost:5000"
    data_dir: Path = Path("/opt/k1/data/registry")

    model_config = {"extra": "forbid"}


class ContainerInfo(BaseModel):
    runtime: Literal["podman"] = "podman"
    quadlet: bool = True
    registry: RegistryInfo = RegistryInfo()

    model_config = {"extra": "forbid"}


class GPUInfo(BaseModel):
    vendor: Literal["nvidia", "none"] = "nvidia"
    install_driver: bool = True
    install_cuda: bool = True

    model_config = {"extra": "forbid"}


class ServiceConfig(BaseModel):
    """A single provisionable service (Ollama, Open WebUI, Grafana, ...)."""

    enabled: bool = True
    port: PortNumber = Field(ge=1, le=65535)

    model_config = {"extra": "forbid"}


class ServicesInfo(BaseModel):
    ollama: ServiceConfig = ServiceConfig(port=11434)
    open_webui: ServiceConfig = ServiceConfig(port=3000)
    grafana: ServiceConfig = ServiceConfig(port=3001)
    prometheus: ServiceConfig = ServiceConfig(port=9090)
    k1: ServiceConfig = ServiceConfig(port=8000)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def no_duplicate_ports(self) -> "ServicesInfo":
        """Enforce the port-collision rule described in the SIM spec.

        This is a defense-in-depth check: the interactive Port Manager
        (Stage 1) is expected to prevent collisions before they ever reach
        the manifest, but the manifest itself must never be *loadable* if it
        contains a collision, since it may be hand-edited or generated by an
        unattended install using a stale bootstrap.yaml.
        """
        seen: dict[int, str] = {}
        for name in type(self).model_fields:
            svc = getattr(self, name)
            if not isinstance(svc, ServiceConfig) or not svc.enabled:
                continue
            if svc.port in seen:
                raise ValueError(
                    f"Port collision detected: {name!r} and {seen[svc.port]!r} "
                    f"both request port {svc.port}. Installation cannot continue."
                )
            seen[svc.port] = name
        return self


class SecurityInfo(BaseModel):
    selinux: Literal["enforcing", "permissive", "disabled"] = "enforcing"
    firewall: Literal["firewalld"] = "firewalld"

    model_config = {"extra": "forbid"}


class MonitoringInfo(BaseModel):
    enabled: bool = True
    prometheus_textfile: Path | None = None

    model_config = {"extra": "forbid"}


class LoggingInfo(BaseModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    model_config = {"extra": "forbid"}


class RequirementsInfo(BaseModel):
    min_free_disk_gib: int = Field(default=20, ge=1)

    model_config = {"extra": "forbid"}


class ReportInfo(BaseModel):
    directory: Path = Path("/opt/k1/reports")

    model_config = {"extra": "forbid"}


class InventoryInfo(BaseModel):
    file: Path = Path("/opt/k1/state/inventory.json")

    model_config = {"extra": "forbid"}


from sim.ire.desired import (
    FirewallDesiredState,
    InfrastructureDesiredState,
    SSHDesiredState,
    StorageDesiredState,
    StorageMountDesired,
    TailscaleDesiredState,
)


class ManifestConfig(BaseModel):
    """The authoritative description of a Shembazai server.

    Every SIM phase after Port Manager resolution reads exclusively from an
    instance of this model — never from raw YAML, never from hardcoded
    defaults scattered across modules.
    """

    server: ServerInfo
    os: OSInfo = OSInfo()
    python: PythonInfo = PythonInfo()
    filesystem: FilesystemInfo = FilesystemInfo()
    container: ContainerInfo = ContainerInfo()
    gpu: GPUInfo = GPUInfo()
    services: ServicesInfo = ServicesInfo()
    security: SecurityInfo = SecurityInfo()
    monitoring: MonitoringInfo = MonitoringInfo()
    logging: LoggingInfo = LoggingInfo()
    requirements: RequirementsInfo = RequirementsInfo()
    report: ReportInfo = ReportInfo()
    inventory: InventoryInfo = InventoryInfo()
    infrastructure: InfrastructureDesiredState = InfrastructureDesiredState()

    model_config = {"extra": "forbid"}

    # -- I/O ---------------------------------------------------------------

    @classmethod
    def load(cls, path: Path) -> "ManifestConfig":
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        return cls.model_validate(raw)

    def dump(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = self.model_dump(mode="json")
        with path.open("w", encoding="utf-8") as fh:
            yaml.safe_dump(data, fh, sort_keys=False, default_flow_style=False)


def detect_host_os() -> tuple[str, str]:
    """Read /etc/os-release and return (ID, VERSION_ID).

    Used by Phase 0 and ``sim check-os``. Recommendation checking is handled
    separately via os_recommendation_warning; this function only detects.
    """
    os_release = Path("/etc/os-release")
    values: dict[str, str] = {}
    if not os_release.exists():
        raise FileNotFoundError("/etc/os-release not found; cannot determine host OS.")
    for line in os_release.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"')
    return values.get("ID", ""), values.get("VERSION_ID", "")
