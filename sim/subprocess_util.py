"""Shared subprocess helpers for SIM host checks."""

from __future__ import annotations

import subprocess


def run_command(args: list[str]) -> tuple[bool, str]:
    """Run a command and return (success, combined stdout/stderr text)."""
    try:
        proc = subprocess.run(args, capture_output=True, text=True, check=False)
    except OSError as exc:
        return False, str(exc)
    output = (proc.stdout or proc.stderr).strip()
    return proc.returncode == 0, output
