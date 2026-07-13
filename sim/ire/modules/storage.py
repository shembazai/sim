"""Storage Integrity Guardian — read-only storage observation.

Never runs destructive commands: no mount, umount, mkfs, wipefs, btrfs rescue,
or write-mode fsck.
"""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from sim.ire.desired import InfrastructureDesiredState, StorageMountDesired
from sim.ire.models import ObservedState, StorageMountObserved, StorageObserved
from sim.subprocess_util import run_command

logger = logging.getLogger(__name__)

DEFAULT_PROC_MOUNTS = Path("/proc/mounts")


@dataclass(frozen=True)
class StorageMountReport:
    """Structured per-mount report for CLI output."""

    path: str
    mounted: bool
    uuid: str | None
    fstype: str | None
    source: str | None
    findmnt_source: str | None
    findmnt_fstype: str | None
    mount_sources_agree: bool | None
    free_gib: float | None
    total_gib: float | None
    btrfs_healthy: bool | None
    snapshot_count: int | None
    smart_healthy: bool | None
    drift: list


class StorageObserver:
    """Discover storage state from /proc/mounts, findmnt, blkid, btrfs, smartctl."""

    def __init__(self, proc_mounts_path: Path = DEFAULT_PROC_MOUNTS) -> None:
        self._proc_mounts_path = proc_mounts_path

    def _read_proc_mounts(self) -> list[tuple[str, str, str]]:
        if not self._proc_mounts_path.exists():
            return []
        entries: list[tuple[str, str, str]] = []
        try:
            content = self._proc_mounts_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return []
        for line in content.splitlines():
            parts = line.split()
            if len(parts) >= 3:
                entries.append((parts[0], parts[1], parts[2]))
        return entries

    def _find_proc_mount(self, mount_point: str) -> tuple[str, str, str] | None:
        for source, target, fstype in self._read_proc_mounts():
            if target == mount_point:
                return source, target, fstype
        return None

    def _findmnt_entry(self, mount_point: str) -> tuple[str, str] | None:
        ok, output = run_command(
            ["findmnt", "-n", "-o", "SOURCE,FSTYPE", mount_point],
        )
        if not ok or not output.strip():
            return None
        parts = output.split()
        if len(parts) >= 2:
            return parts[0], parts[1]
        if len(parts) == 1:
            return parts[0], ""
        return None

    def _resolve_device(self, source: str, mount_point: str) -> str | None:
        if source.startswith("/dev/"):
            return source
        if source.startswith("UUID="):
            uuid = source.split("=", 1)[1]
            ok, output = run_command(["blkid", "-U", uuid])
            if ok and output.strip().startswith("/dev/"):
                return output.strip()
        findmnt = self._findmnt_entry(mount_point)
        if findmnt and findmnt[0].startswith("/dev/"):
            return findmnt[0]
        return None

    def _uuid_from_source(self, source: str, mount_point: str) -> str | None:
        if source.startswith("UUID="):
            return source.split("=", 1)[1]
        device = self._resolve_device(source, mount_point)
        if device:
            ok, output = run_command(["blkid", "-o", "value", "-s", "UUID", device])
            if ok and output.strip():
                return output.strip()
        return None

    def _btrfs_health(self, mount_point: str) -> tuple[bool | None, int | None]:
        ok, output = run_command(["btrfs", "filesystem", "show", mount_point])
        if not ok:
            return None, None
        healthy = "some devices failed" not in output.lower()
        ok_snap, snap_out = run_command(["btrfs", "subvolume", "list", "-s", mount_point])
        snapshot_count = len(snap_out.splitlines()) if ok_snap and snap_out.strip() else 0
        return healthy, snapshot_count

    def _smart_health(self, device: str) -> bool | None:
        ok, output = run_command(["smartctl", "-H", device])
        if not ok:
            return None
        lowered = output.lower()
        if "passed" in lowered:
            return True
        if "failed" in lowered:
            return False
        return None

    def _sources_agree(
        self,
        proc_source: str | None,
        proc_fstype: str | None,
        findmnt: tuple[str, str] | None,
    ) -> bool | None:
        if proc_source is None or findmnt is None:
            return None
        fm_source, fm_fstype = findmnt
        if proc_fstype and fm_fstype and proc_fstype != fm_fstype:
            return False
        if proc_source == fm_source:
            return True
        if proc_source.startswith("UUID=") and fm_source.startswith("/dev/"):
            uuid = proc_source.split("=", 1)[1]
            ok, resolved = run_command(["blkid", "-U", uuid])
            return ok and resolved.strip() == fm_source
        return False

    def observe_mount(self, mount_point: str) -> StorageMountObserved:
        proc = self._find_proc_mount(mount_point)
        findmnt = self._findmnt_entry(mount_point)

        if proc is None:
            return StorageMountObserved(
                path=mount_point,
                mounted=False,
                findmnt_source=findmnt[0] if findmnt else None,
                findmnt_fstype=findmnt[1] if findmnt else None,
                mount_sources_agree=False if findmnt else None,
            )

        source, path, fstype = proc
        fm_source = findmnt[0] if findmnt else None
        fm_fstype = findmnt[1] if findmnt else None
        agree = self._sources_agree(source, fstype, findmnt)
        uuid = self._uuid_from_source(source, mount_point)

        free_gib = total_gib = None
        try:
            usage = shutil.disk_usage(mount_point)
            free_gib = round(usage.free / (1024**3), 2)
            total_gib = round(usage.total / (1024**3), 2)
        except OSError:
            pass

        btrfs_healthy = snapshot_count = None
        if fstype == "btrfs":
            btrfs_healthy, snapshot_count = self._btrfs_health(mount_point)

        device = self._resolve_device(source, mount_point)
        smart_healthy = self._smart_health(device) if device else None

        return StorageMountObserved(
            path=path,
            mounted=True,
            uuid=uuid,
            fstype=fstype,
            source=source,
            findmnt_source=fm_source,
            findmnt_fstype=fm_fstype,
            mount_sources_agree=agree,
            free_gib=free_gib,
            total_gib=total_gib,
            btrfs_healthy=btrfs_healthy,
            snapshot_count=snapshot_count,
            smart_healthy=smart_healthy,
        )

    def observe_mounts(self, paths: list[Path]) -> StorageObserved:
        mounts = [self.observe_mount(str(p)) for p in paths]
        return StorageObserved(mounts=mounts)


def observe_storage(paths: list[Path] | None = None) -> StorageObserved:
    """Module-level helper used by collect_observed_state."""
    observer = StorageObserver()
    return observer.observe_mounts(paths or [])


def build_storage_report(
    desired: InfrastructureDesiredState,
    observed: StorageObserved,
) -> list[StorageMountReport]:
    """Build per-mount reports with drift for each desired mount."""
    from sim.ire.drift import detect_drift

    all_drift = detect_drift(desired, _storage_observed_to_state(observed))
    reports: list[StorageMountReport] = []
    desired_by_path = {str(m.path): m for m in desired.storage.mounts}
    observed_by_path = {m.path: m for m in observed.mounts}

    for path, desired_mount in desired_by_path.items():
        mount = observed_by_path.get(path)
        if mount is None:
            mount = StorageMountObserved(path=path, mounted=False)
        path_drift = [d for d in all_drift if d.component == "storage" and path in d.field]
        reports.append(
            StorageMountReport(
                path=path,
                mounted=mount.mounted,
                uuid=mount.uuid,
                fstype=mount.fstype,
                source=mount.source,
                findmnt_source=mount.findmnt_source,
                findmnt_fstype=mount.findmnt_fstype,
                mount_sources_agree=mount.mount_sources_agree,
                free_gib=mount.free_gib,
                total_gib=mount.total_gib,
                btrfs_healthy=mount.btrfs_healthy,
                snapshot_count=mount.snapshot_count,
                smart_healthy=mount.smart_healthy,
                drift=path_drift,
            )
        )
    return reports


def storage_report_exit_code(
    reports: list[StorageMountReport],
    desired_mounts: list[StorageMountDesired],
) -> int:
    """Return 1 if any required mount is missing or has critical UUID drift."""
    desired_by_path = {str(m.path): m for m in desired_mounts}
    for report in reports:
        desired_mount = desired_by_path.get(report.path)
        if desired_mount is None:
            continue
        if desired_mount.required and not report.mounted:
            return 1
        for item in report.drift:
            if item.field.endswith(".uuid") and item.severity == "critical":
                return 1
    return 0


def _storage_observed_to_state(observed: StorageObserved) -> ObservedState:
    return ObservedState(storage=observed)
