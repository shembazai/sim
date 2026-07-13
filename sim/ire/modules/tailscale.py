"""Tailscale identity observer — read-only remote access health.

Never stores or reconciles volatile Tailscale IPs. Identity is expressed via
hostname and tailnet membership only.
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass

from sim.ire.desired import InfrastructureDesiredState, TailscaleDesiredState
from sim.ire.drift import DriftItem
from sim.ire.models import ObservedState, TailscaleObserved
from sim.subprocess_util import run_command

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TailscaleReport:
    """Structured Tailscale health report for CLI output."""

    installed: bool
    online: bool
    hostname: str | None
    tailnet: str | None
    drift: list[DriftItem]


class TailscaleObserver:
    """Discover Tailscale install state and identity from tailscale status."""

    def observe(self) -> TailscaleObserved:
        return _parse_tailscale_status()

    def observe_for_desired(self, desired: TailscaleDesiredState) -> TailscaleReport:
        observed = self.observe()
        drift = _tailscale_drift(desired, observed)
        return TailscaleReport(
            installed=observed.installed,
            online=observed.online,
            hostname=observed.hostname,
            tailnet=observed.tailnet,
            drift=drift,
        )


def observe_tailscale() -> TailscaleObserved:
    """Module-level helper used by collect_observed_state."""
    return TailscaleObserver().observe()


def build_tailscale_report(desired: InfrastructureDesiredState) -> TailscaleReport:
    """Build Tailscale report with drift against desired identity."""
    return TailscaleObserver().observe_for_desired(desired.tailscale)


def tailscale_report_exit_code(report: TailscaleReport, desired: TailscaleDesiredState) -> int:
    """Return 1 when Tailscale is required but missing or offline."""
    if not desired.enabled:
        return 0
    if not report.installed or not report.online:
        return 1
    for item in report.drift:
        if item.severity == "critical":
            return 1
    return 0


def _parse_tailscale_status() -> TailscaleObserved:
    ok, output = run_command(["tailscale", "status", "--json"])
    if not ok:
        installed = shutil.which("tailscale") is not None
        return TailscaleObserved(installed=installed, online=False)
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return TailscaleObserved(installed=True, online=False)

    self_info = data.get("Self", {})
    dns_name = self_info.get("DNSName") or self_info.get("HostName")
    if isinstance(dns_name, str) and dns_name.endswith("."):
        dns_name = dns_name.rstrip(".")

    online = bool(self_info.get("Online"))
    tailnet = _extract_tailnet(self_info)
    return TailscaleObserved(
        installed=True,
        online=online,
        hostname=dns_name,
        tailnet=tailnet,
    )


def _extract_tailnet(self_info: dict) -> str | None:
    cert_domains = self_info.get("CertDomains") or []
    if cert_domains:
        domain = cert_domains[0]
        if isinstance(domain, str) and "." in domain:
            return domain.split(".", 1)[-1]
        return domain if isinstance(domain, str) else None

    dns_name = self_info.get("DNSName") or self_info.get("HostName")
    if isinstance(dns_name, str) and "." in dns_name:
        return dns_name.split(".", 1)[-1].rstrip(".")
    return None


def _tailscale_drift(desired: TailscaleDesiredState, observed: TailscaleObserved) -> list[DriftItem]:
    from sim.ire.drift import _compare_tailscale

    return _compare_tailscale(
        InfrastructureDesiredState(tailscale=desired),
        ObservedState(tailscale=observed),
    )
