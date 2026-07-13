"""Tests for IRE runtime observers using real command output fixtures."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sim.config import ManifestConfig
from sim.ire.drift import detect_drift
from sim.ire.observed import (
    _observe_firewall,
    _observe_network,
    _observe_ssh,
    _parse_firewall_active_zones,
    _parse_ip_addr_show,
    _parse_ss_listeners,
    _ssh_ports_from_config,
    collect_observed_state,
)

FIREWALL_ACTIVE_ZONES = """\
public (default)
  interfaces: enp5s0
trusted
  interfaces: tailscale0
"""

IP_ADDR_SHOW = """\
1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever
2: enp5s0    inet 192.168.1.50/24 brd 192.168.1.255 scope global dynamic noprefixroute enp5s0\\       valid_lft 3600sec preferred_lft 3600sec
3: tailscale0    inet 100.64.0.1/32 scope global tailscale0\\       valid_lft forever preferred_lft forever
"""

SS_LISTEN = """\
LISTEN 0      128          0.0.0.0:22    0.0.0.0:*
LISTEN 0      4096               *:11434       *:*
LISTEN 0      128             [::]:22       [::]:*
"""


def test_parse_firewall_active_zones_maps_interfaces_to_zones():
    zones = _parse_firewall_active_zones(FIREWALL_ACTIVE_ZONES)
    assert zones == {"enp5s0": "public", "tailscale0": "trusted"}


def test_parse_ip_addr_show_extracts_interface_ips():
    interfaces = _parse_ip_addr_show(IP_ADDR_SHOW)
    assert interfaces["lo"].ip == "127.0.0.1"
    assert interfaces["enp5s0"].ip == "192.168.1.50"
    assert interfaces["tailscale0"].ip == "100.64.0.1"
    assert interfaces["tailscale0"].state is None


def test_parse_ss_listeners_extracts_bind_host_and_port():
    listeners = _parse_ss_listeners(SS_LISTEN)
    assert ("0.0.0.0", 22) in listeners
    assert ("*", 11434) in listeners
    assert ("::", 22) in listeners


def test_ssh_ports_from_config_defaults_to_22():
    assert _ssh_ports_from_config({}) == [22]
    assert _ssh_ports_from_config({"port": "2222"}) == [2222]


def test_observe_ssh_matches_configured_port_without_process_names(monkeypatch, tmp_path: Path):
    sshd_config = tmp_path / "sshd_config"
    sshd_config.write_text("Port 22\n", encoding="utf-8")

    def fake_run(args: list[str]) -> tuple[bool, str]:
        if args[:3] == ["systemctl", "is-active", "sshd"]:
            return True, "active"
        if args[:2] == ["ss", "-lntH"]:
            return True, SS_LISTEN
        return False, ""

    monkeypatch.setattr("sim.ire.observed.run_command", fake_run)
    with patch("sim.ire.observed._parse_sshd_config", return_value={"port": "22"}):
        observed = _observe_ssh()

    assert observed.service_active is True
    assert observed.listening_ports == [22]
    assert "0.0.0.0" in observed.bind_addresses
    assert "::" in observed.bind_addresses


def test_observe_firewall_parses_active_zones(monkeypatch):
    def fake_run(args: list[str]) -> tuple[bool, str]:
        if args[:3] == ["systemctl", "is-active", "firewalld"]:
            return True, "active"
        if args[:2] == ["firewall-cmd", "--get-active-zones"]:
            return True, FIREWALL_ACTIVE_ZONES
        if args[:3] == ["firewall-cmd", "--list-services", "--zone=trusted"]:
            return True, "ssh dhcpv6-client"
        return False, ""

    monkeypatch.setattr("sim.ire.observed.run_command", fake_run)
    observed = _observe_firewall()

    assert observed.active is True
    assert observed.interface_zones["tailscale0"] == "trusted"
    assert observed.interface_zones["enp5s0"] == "public"
    assert "trusted" in observed.ssh_allowed_zones


def test_observe_network_parses_ip_addresses(monkeypatch):
    monkeypatch.setattr(
        "sim.ire.observed.run_command",
        lambda args: (True, IP_ADDR_SHOW) if args[:4] == ["ip", "-o", "addr", "show"] else (False, ""),
    )
    observed = _observe_network()

    assert observed.interfaces["tailscale0"].ip == "100.64.0.1"
    assert observed.interfaces["enp5s0"].ip == "192.168.1.50"


def test_firewall_zone_drift_not_raised_when_tailscale_in_trusted(monkeypatch):
    cfg = ManifestConfig.model_validate(
        {
            "server": {"hostname": "k1", "role": "production"},
            "infrastructure": {
                "firewall": {
                    "interfaces": {"tailscale0": {"zone": "trusted"}},
                },
            },
        }
    )

    def fake_collect(**_kwargs):
        from sim.ire.models import FirewallObserved, ObservedState

        return ObservedState(
            firewall=FirewallObserved(
                active=True,
                interface_zones=_parse_firewall_active_zones(FIREWALL_ACTIVE_ZONES),
                ssh_allowed_zones=["trusted"],
            ),
        )

    monkeypatch.setattr("sim.ire.observed.collect_observed_state", fake_collect)
    drift = detect_drift(cfg.infrastructure, fake_collect())
    zone_drift = [d for d in drift if d.field == "interfaces.tailscale0.zone"]
    assert zone_drift == []


def test_collect_observed_state_integration(monkeypatch):
    monkeypatch.setattr(
        "sim.ire.observed._observe_tailscale",
        lambda: __import__("sim.ire.models", fromlist=["TailscaleObserved"]).TailscaleObserved(),
    )
    monkeypatch.setattr(
        "sim.ire.observed._observe_storage",
        lambda _paths=None: __import__("sim.ire.models", fromlist=["StorageObserved"]).StorageObserved(),
    )
    monkeypatch.setattr("sim.ire.observed._observe_network", _observe_network)
    monkeypatch.setattr("sim.ire.observed._observe_firewall", _observe_firewall)

    def fake_run(args: list[str]) -> tuple[bool, str]:
        if args[:4] == ["ip", "-o", "addr", "show"]:
            return True, IP_ADDR_SHOW
        if args[:2] == ["firewall-cmd", "--get-active-zones"]:
            return True, FIREWALL_ACTIVE_ZONES
        if args[:3] == ["systemctl", "is-active", "firewalld"]:
            return True, "active"
        if args[:3] == ["systemctl", "is-active", "sshd"]:
            return True, "active"
        if args[:2] == ["ss", "-lntH"]:
            return True, SS_LISTEN
        if args[:3] == ["firewall-cmd", "--list-services", "--zone=trusted"]:
            return True, "ssh"
        return False, ""

    monkeypatch.setattr("sim.ire.observed.run_command", fake_run)
    with patch("sim.ire.observed._parse_sshd_config", return_value={"port": "22"}):
        state = collect_observed_state()

    assert state.firewall.interface_zones["tailscale0"] == "trusted"
    assert state.ssh.listening_ports == [22]
    assert state.network.interfaces["tailscale0"].ip == "100.64.0.1"
