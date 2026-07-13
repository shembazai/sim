"""Persistent, resumable state tracking for SIM.

Backed by SQLite rather than a flat file so that:
- writes are atomic (no torn state on power loss mid-write, relevant for a
  tool that provisions the very machine it runs on),
- module history is queryable (needed for the diagnostics/inventory export
  described in the SIM spec) without hand-parsing a log file,
- concurrent invocation (e.g. an operator accidentally running `sim` twice)
  is caught via a simple lock row instead of silently corrupting state.
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Literal

ModuleStatus = Literal[
    "Not Started",
    "Validated",
    "Installing",
    "Verifying",
    "Passed",
    "Failed",
    "Rolled Back",
    # Backward-compatibility aliases used by earlier tests/runs.
    "pending",
    "running",
    "completed",
    "failed",
    "rolled_back",
]

DEFAULT_STATE_DB = Path("/opt/k1/state/sim_state.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS modules (
    name        TEXT PRIMARY KEY,
    status      TEXT NOT NULL,
    started_at  REAL,
    finished_at REAL,
    error       TEXT,
    detail_json TEXT
);

CREATE TABLE IF NOT EXISTS lock (
    id        INTEGER PRIMARY KEY CHECK (id = 1),
    pid       INTEGER NOT NULL,
    acquired_at REAL NOT NULL
);
"""


@dataclass(frozen=True)
class ModuleRecord:
    name: str
    status: ModuleStatus
    started_at: float | None
    finished_at: float | None
    error: str | None
    detail: dict[str, Any]


class StateLockedError(RuntimeError):
    """Raised when another SIM process already holds the state lock."""


def _pid_is_alive(pid: int) -> bool:
    """Return True when pid refers to a live process on this host."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but belongs to another user.
        return True
    return True


class StateManager:
    """Owns the SQLite state database for a single SIM deployment root."""

    def __init__(self, db_path: Path = DEFAULT_STATE_DB) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, isolation_level=None)  # autocommit off; explicit tx
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.executescript(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "StateManager":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- locking -------------------------------------------------------

    @contextmanager
    def process_lock(self, pid: int) -> Iterator[None]:
        """Prevent two SIM processes from mutating state concurrently."""
        cur = self._conn.execute("SELECT pid, acquired_at FROM lock WHERE id = 1")
        row = cur.fetchone()
        if row is not None:
            holder_pid = int(row[0])
            if _pid_is_alive(holder_pid):
                raise StateLockedError(
                    f"State DB already locked by pid={holder_pid} since {row[1]}. "
                    f"Wait for that SIM process to finish or stop it before retrying."
                )
            self._conn.execute("DELETE FROM lock WHERE id = 1")
        self._conn.execute(
            "INSERT INTO lock (id, pid, acquired_at) VALUES (1, ?, ?)",
            (pid, time.time()),
        )
        try:
            yield
        finally:
            self._conn.execute("DELETE FROM lock WHERE id = 1")

    # -- module lifecycle ------------------------------------------------

    def is_completed(self, name: str) -> bool:
        cur = self._conn.execute("SELECT status FROM modules WHERE name = ?", (name,))
        row = cur.fetchone()
        return row is not None and row[0] in ("Passed", "completed")

    def record_status(
        self,
        name: str,
        status: ModuleStatus,
        *,
        error: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        now = time.time()
        started_at = now if status in ("Validated", "Installing", "running") else None
        finished_at = (
            now
            if status in ("Passed", "Failed", "Rolled Back", "completed", "failed", "rolled_back")
            else None
        )
        self._conn.execute(
            """
            INSERT INTO modules (name, status, started_at, finished_at, error, detail_json)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                status = excluded.status,
                started_at = COALESCE(modules.started_at, excluded.started_at),
                finished_at = excluded.finished_at,
                error = excluded.error,
                detail_json = COALESCE(excluded.detail_json, modules.detail_json)
            """,
            (
                name,
                status,
                started_at,
                finished_at,
                error,
                json.dumps(detail) if detail is not None else None,
            ),
        )

    def record_start(self, name: str) -> None:
        self.record_status(name, "Installing")

    def record_success(self, name: str, detail: dict[str, Any] | None = None) -> None:
        self.record_status(name, "Passed", detail=detail or {})

    def record_failure(self, name: str, error: str) -> None:
        self.record_status(name, "Failed", error=error)

    def record_rollback(self, name: str) -> None:
        self.record_status(name, "Rolled Back")

    def get(self, name: str) -> ModuleRecord | None:
        cur = self._conn.execute(
            "SELECT name, status, started_at, finished_at, error, detail_json "
            "FROM modules WHERE name = ?",
            (name,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return ModuleRecord(
            name=row[0],
            status=row[1],
            started_at=row[2],
            finished_at=row[3],
            error=row[4],
            detail=json.loads(row[5]) if row[5] else {},
        )

    def history(self) -> list[ModuleRecord]:
        cur = self._conn.execute(
            "SELECT name, status, started_at, finished_at, error, detail_json "
            "FROM modules ORDER BY started_at ASC"
        )
        return [
            ModuleRecord(
                name=r[0], status=r[1], started_at=r[2], finished_at=r[3],
                error=r[4], detail=json.loads(r[5]) if r[5] else {},
            )
            for r in cur.fetchall()
        ]
