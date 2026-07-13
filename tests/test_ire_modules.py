"""Tests for IRE SSH and firewall reconciliation modules."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sim.ire.desired import FirewallDesiredState, FirewallInterfaceDesired, FirewallServiceDesired, SSHDesiredState
from sim.ire.drift import DriftItem
from sim.ire.engine import PlanStep
from sim.ire.modules.firewall import FirewallReconciliationModule
from sim.ire.modules.ssh import SSHReconciliationModule


def test_ssh_generate_drop_in_no_listen_address(tmp_path: Path):
    desired = SSHDesiredState(
        port=22,
        root_login=False,
        password_authentication=False,
        allowed_users=["cybershaman"],
    )
    module = SSHReconciliationModule(
        desired,
        sshd_config=tmp_path / "sshd_config",
        drop_in=tmp_path / "99-sim-ire.conf",
    )
    content = module._generate_drop_in()
    assert "ListenAddress" not in content
    assert "Port 22" in content
    assert "PermitRootLogin no" in content
    assert "AllowUsers cybershaman" in content


def test_ssh_plan_from_drift():
    desired = SSHDesiredState()
    module = SSHReconciliationModule(desired, drop_in=Path("/tmp/99-sim.conf"))
    drift = [
        DriftItem(
            component="ssh",
            field="permit_root_login",
            severity="critical",
            desired="no",
            observed="yes",
            message="Root login enabled",
            auto_repairable=True,
        )
    ]
    steps = module.plan(drift)
    assert len(steps) == 1
    assert steps[0].component == "ssh"


def test_ssh_apply_validates_and_rolls_back_on_sshd_t_failure(tmp_path: Path, monkeypatch):
    drop_in = tmp_path / "99-sim-ire.conf"
    sshd_config = tmp_path / "sshd_config"
    sshd_config.write_text("# original\n", encoding="utf-8")
    backup_dir = tmp_path / "backups"

    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> tuple[bool, str]:
        calls.append(args)
        if args[:2] == ["sshd", "-t"]:
            return False, "config error"
        return True, "ok"

    monkeypatch.setattr("sim.ire.modules.ssh.run_command", fake_run)

    module = SSHReconciliationModule(
        SSHDesiredState(allowed_users=["user"]),
        sshd_config=sshd_config,
        drop_in=drop_in,
    )
    steps = [
        PlanStep("ssh", "reconcile.port", "fix port", True),
    ]
    with pytest.raises(RuntimeError, match="sshd -t failed"):
        module.apply(steps, backup_dir=backup_dir)


def test_ssh_apply_success(tmp_path: Path, monkeypatch):
    drop_in = tmp_path / "99-sim-ire.conf"
    sshd_config = tmp_path / "sshd_config"
    sshd_config.write_text("# original\n", encoding="utf-8")
    backup_dir = tmp_path / "backups"

    def fake_run(args: list[str]) -> tuple[bool, str]:
        if args[:2] == ["sshd", "-t"]:
            return True, ""
        if args[:3] == ["systemctl", "restart", "sshd"]:
            return True, "active"
        if args[:2] == ["systemctl", "is-active"]:
            return True, "active"
        if args[:2] == ["ss", "-lntH"]:
            return True, "LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:((\"sshd\"))"
        return True, "ok"

    monkeypatch.setattr("sim.ire.modules.ssh.run_command", fake_run)

    module = SSHReconciliationModule(
        SSHDesiredState(port=22, allowed_users=["cybershaman"]),
        sshd_config=sshd_config,
        drop_in=drop_in,
    )
    result = module.apply(
        [PlanStep("ssh", "reconcile.port", "fix", True)],
        backup_dir=backup_dir,
    )
    assert str(drop_in) in result
    assert drop_in.exists()
    assert "ListenAddress" not in drop_in.read_text(encoding="utf-8")

    verify = module.verify()
    assert verify["sshd -t"] == "PASS"
    assert verify["sshd active"] == "PASS"


def test_ssh_apply_requires_elevation_when_drop_in_inaccessible(tmp_path: Path, monkeypatch):
    drop_in = tmp_path / "99-sim-ire.conf"
    drop_in.write_text("# existing\n", encoding="utf-8")
    os.chmod(drop_in, 0o000)
    backup_dir = tmp_path / "backups"

    module = SSHReconciliationModule(
        SSHDesiredState(allowed_users=["cybershaman"]),
        sshd_config=tmp_path / "sshd_config",
        drop_in=drop_in,
    )
    steps = [PlanStep("ssh", "reconcile.allowed_users", "fix users", True)]

    with pytest.raises(PermissionError, match="Elevated privileges"):
        module.apply(steps, backup_dir=backup_dir)

    os.chmod(drop_in, 0o644)


def test_firewall_apply_assigns_zones(tmp_path: Path, monkeypatch):
    desired = FirewallDesiredState(
        interfaces={"tailscale0": FirewallInterfaceDesired(zone="trusted")},
        services={"ssh": FirewallServiceDesired(allowed_interfaces=["tailscale0"])},
    )
    module = FirewallReconciliationModule(desired)
    commands: list[list[str]] = []

    def fake_run(args: list[str]) -> tuple[bool, str]:
        commands.append(args)
        if args[:3] == ["systemctl", "is-active", "firewalld"]:
            return True, "active"
        return True, "success"

    monkeypatch.setattr("sim.ire.modules.firewall.run_command", fake_run)

    result = module.apply(
        [PlanStep("firewall", "reconcile.active", "ensure active", True)],
        backup_dir=tmp_path,
    )
    assert "interface:tailscale0" in result
    assert any(
        c[0] == "firewall-cmd"
        and any("--zone=trusted" in arg for arg in c)
        and any("tailscale0" in arg for arg in c)
        for c in commands
    )
    assert any(
        c[0] == "firewall-cmd" and "--add-service=ssh" in c for c in commands
    )
