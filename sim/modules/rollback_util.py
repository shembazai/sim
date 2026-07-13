"""Shared helpers for non-destructive install module rollback."""

from __future__ import annotations

from pathlib import Path

SIM_MANAGED_MARKER = "Managed by SIM"


def remove_sim_managed_file(path: Path) -> bool:
    """Remove a drop-in only when it carries the SIM managed marker."""
    if not path.is_file():
        return False
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    if SIM_MANAGED_MARKER not in content:
        return False
    path.unlink(missing_ok=True)
    return True
