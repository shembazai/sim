"""Tests for IRE Storage Integrity Guardian."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from sim.config import InfrastructureDesiredState, StorageDesiredState, StorageMountDesired
from sim.ire.drift import detect_drift
from sim.ire.modules.storage import (
    StorageObserver,
    build_storage_report,
    storage_report_exit_code,
)
from sim.ire.observed import ObservedState, StorageMountObserved, StorageObserved
from sim.main import app


PROC_MOUNTS_MOUNTED = """\
UUID=aaaa-bbbb-cccc /mnt/ai btrfs rw,relatime 0 0
"""

PROC_MOUNTS_EMPTY = ""


def _run_command_factory(responses: dict[tuple[str, ...], tuple[bool, str]]):
    def _fake(args: list[str]) -> tuple[bool, str]:
        key = tuple(args)
        if key in responses:
            return responses[key]
        return False, "not mocked"
    return _fake


def test_storage_observer_unmounted(tmp_path: Path):
    proc = tmp_path / "mounts"
    proc.write_text(PROC_MOUNTS_EMPTY, encoding="utf-8")
    observer = StorageObserver(proc_mounts_path=proc)
    mount = observer.observe_mount("/mnt/ai")
    assert mount.mounted is False
    assert mount.path == "/mnt/ai"


def test_storage_observer_mounted_with_findmnt(tmp_path: Path, monkeypatch):
    proc = tmp_path / "mounts"
    proc.write_text(PROC_MOUNTS_MOUNTED, encoding="utf-8")
    monkeypatch.setattr(
        "sim.ire.modules.storage.run_command",
        _run_command_factory({
            ("findmnt", "-n", "-o", "SOURCE,FSTYPE", "/mnt/ai"): (True, "/dev/nvme1n1p1 btrfs"),
            ("blkid", "-U", "aaaa-bbbb-cccc"): (True, "/dev/nvme1n1p1"),
            ("blkid", "-o", "value", "-s", "UUID", "/dev/nvme1n1p1"): (True, "aaaa-bbbb-cccc"),
            ("btrfs", "filesystem", "show", "/mnt/ai"): (True, "Label: none"),
            ("btrfs", "subvolume", "list", "-s", "/mnt/ai"): (True, "ID 256 gen 10"),
            ("smartctl", "-H", "/dev/nvme1n1p1"): (True, "SMART overall-health self-assessment test result: PASSED"),
        }),
    )
    observer = StorageObserver(proc_mounts_path=proc)
    mount = observer.observe_mount("/mnt/ai")
    assert mount.mounted is True
    assert mount.fstype == "btrfs"
    assert mount.uuid == "aaaa-bbbb-cccc"
    assert mount.mount_sources_agree is True
    assert mount.btrfs_healthy is True
    assert mount.snapshot_count == 1
    assert mount.smart_healthy is True


def test_storage_observer_findmnt_disagreement(tmp_path: Path, monkeypatch):
    proc = tmp_path / "mounts"
    proc.write_text(PROC_MOUNTS_MOUNTED, encoding="utf-8")
    monkeypatch.setattr(
        "sim.ire.modules.storage.run_command",
        _run_command_factory({
            ("findmnt", "-n", "-o", "SOURCE,FSTYPE", "/mnt/ai"): (True, "/dev/nvme2n1p1 xfs"),
            ("blkid", "-U", "aaaa-bbbb-cccc"): (True, "/dev/nvme1n1p1"),
            ("blkid", "-o", "value", "-s", "UUID", "/dev/nvme1n1p1"): (True, "aaaa-bbbb-cccc"),
        }),
    )
    observer = StorageObserver(proc_mounts_path=proc)
    mount = observer.observe_mount("/mnt/ai")
    assert mount.mount_sources_agree is False


def test_storage_drift_smart_unhealthy_observe_only():
    desired = InfrastructureDesiredState(
        storage=StorageDesiredState(
            mounts=[StorageMountDesired(path=Path("/mnt/ai"), required=True)],
        )
    )
    observed = ObservedState(
        storage=StorageObserved(
            mounts=[
                StorageMountObserved(
                    path="/mnt/ai",
                    mounted=True,
                    smart_healthy=False,
                )
            ]
        )
    )
    drift = detect_drift(desired, observed)
    smart = next(d for d in drift if d.field.endswith("smart_health"))
    assert smart.severity == "critical"
    assert smart.auto_repairable is False


def test_storage_drift_source_agreement_observe_only():
    desired = InfrastructureDesiredState(
        storage=StorageDesiredState(
            mounts=[StorageMountDesired(path=Path("/mnt/ai"), required=True)],
        )
    )
    observed = ObservedState(
        storage=StorageObserved(
            mounts=[
                StorageMountObserved(
                    path="/mnt/ai",
                    mounted=True,
                    source="UUID=aaaa",
                    findmnt_source="/dev/other",
                    mount_sources_agree=False,
                )
            ]
        )
    )
    drift = detect_drift(desired, observed)
    item = next(d for d in drift if "source_agreement" in d.field)
    assert item.severity == "warning"
    assert item.auto_repairable is False


def test_storage_report_exit_code_missing_required(tmp_path: Path):
    desired = InfrastructureDesiredState(
        storage=StorageDesiredState(
            mounts=[StorageMountDesired(path=Path("/mnt/ai"), required=True)],
        )
    )
    observed = StorageObserved(
        mounts=[StorageMountObserved(path="/mnt/ai", mounted=False)]
    )
    reports = build_storage_report(desired, observed)
    assert storage_report_exit_code(reports, desired.storage.mounts) == 1


def test_storage_report_exit_code_uuid_mismatch():
    desired = InfrastructureDesiredState(
        storage=StorageDesiredState(
            mounts=[
                StorageMountDesired(
                    path=Path("/mnt/ai"),
                    uuid="expected-uuid",
                    required=True,
                )
            ],
        )
    )
    observed = StorageObserved(
        mounts=[
            StorageMountObserved(
                path="/mnt/ai",
                mounted=True,
                uuid="wrong-uuid",
            )
        ]
    )
    reports = build_storage_report(desired, observed)
    assert storage_report_exit_code(reports, desired.storage.mounts) == 1


def test_cli_ire_storage_json(tmp_path: Path, monkeypatch):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        """
server:
  hostname: k1
  role: production
infrastructure:
  storage:
    mounts:
      - path: /mnt/ai
        required: true
""".strip(),
        encoding="utf-8",
    )
    proc = tmp_path / "mounts"
    proc.write_text("", encoding="utf-8")

    def fake_observe_mounts(self, paths):
        return StorageObserved(
            mounts=[StorageMountObserved(path="/mnt/ai", mounted=False)]
        )

    monkeypatch.setattr(StorageObserver, "observe_mounts", fake_observe_mounts)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ire", "storage", "--manifest", str(manifest), "--json"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["mounts"][0]["mounted"] is False


def test_cli_ire_storage_output_file(tmp_path: Path, monkeypatch):
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        """
server:
  hostname: k1
  role: production
infrastructure:
  storage:
    mounts:
      - path: /mnt/ai
        required: false
""".strip(),
        encoding="utf-8",
    )
    out = tmp_path / "storage.json"

    def fake_observe_mounts(self, paths):
        return StorageObserved(
            mounts=[StorageMountObserved(path="/mnt/ai", mounted=True, fstype="btrfs")]
        )

    monkeypatch.setattr(StorageObserver, "observe_mounts", fake_observe_mounts)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ire", "storage", "--manifest", str(manifest), "--output", str(out), "--json"],
    )
    assert result.exit_code == 0
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["mounts"][0]["fstype"] == "btrfs"
