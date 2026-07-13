"""Infrastructure change transaction records."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

TransactionStatus = Literal["PLANNED", "BLOCKED", "COMMITTED", "ROLLED_BACK", "FAILED"]


@dataclass
class TransactionRecord:
    transaction_id: str
    timestamp: str
    target_host: str
    changed_resources: list[str] = field(default_factory=list)
    previous_state: dict[str, Any] = field(default_factory=dict)
    new_state: dict[str, Any] = field(default_factory=dict)
    validation_results: dict[str, str] = field(default_factory=dict)
    rollback_available: bool = False
    status: TransactionStatus = "PLANNED"
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "transaction_id": self.transaction_id,
            "timestamp": self.timestamp,
            "target_host": self.target_host,
            "changed_resources": self.changed_resources,
            "previous_state": self.previous_state,
            "new_state": self.new_state,
            "validation_results": self.validation_results,
            "rollback_available": self.rollback_available,
            "status": self.status,
            "detail": self.detail,
        }


def generate_transaction_id(component: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d")
    suffix = int(time.time() * 1000) % 1000
    return f"TX-{stamp}-{component.upper()}-{suffix:03d}"


_TRANSACTION_SCHEMA = """
CREATE TABLE IF NOT EXISTS transactions (
    transaction_id TEXT PRIMARY KEY,
    timestamp    TEXT NOT NULL,
    target_host  TEXT NOT NULL,
    status       TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
"""


class TransactionStore:
    """Persist IRE transaction evidence in SQLite."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, isolation_level=None)
        self._conn.executescript(_TRANSACTION_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "TransactionStore":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def save(self, record: TransactionRecord) -> None:
        self._conn.execute(
            """
            INSERT INTO transactions (transaction_id, timestamp, target_host, status, payload_json)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(transaction_id) DO UPDATE SET
                status = excluded.status,
                payload_json = excluded.payload_json
            """,
            (
                record.transaction_id,
                record.timestamp,
                record.target_host,
                record.status,
                json.dumps(record.to_dict()),
            ),
        )

    def get(self, transaction_id: str) -> TransactionRecord | None:
        cur = self._conn.execute(
            "SELECT payload_json FROM transactions WHERE transaction_id = ?",
            (transaction_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        data = json.loads(row[0])
        return TransactionRecord(**data)

    def history(
        self,
        limit: int = 50,
        *,
        status: TransactionStatus | None = None,
    ) -> list[TransactionRecord]:
        if status is None:
            cur = self._conn.execute(
                "SELECT payload_json FROM transactions ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            )
        else:
            cur = self._conn.execute(
                """
                SELECT payload_json FROM transactions
                WHERE status = ?
                ORDER BY timestamp DESC LIMIT ?
                """,
                (status, limit),
            )
        return [TransactionRecord(**json.loads(row[0])) for row in cur.fetchall()]


def format_transaction_markdown(record: TransactionRecord) -> str:
    """Render a human-readable evidence report for a transaction."""
    lines = [
        f"# Transaction {record.transaction_id}",
        "",
        f"- **Timestamp:** {record.timestamp}",
        f"- **Target host:** {record.target_host}",
        f"- **Status:** {record.status}",
        f"- **Rollback available:** {'yes' if record.rollback_available else 'no'}",
    ]
    if record.detail:
        lines.append(f"- **Detail:** {record.detail}")
    if record.changed_resources:
        lines.append("")
        lines.append("## Changed resources")
        for resource in record.changed_resources:
            lines.append(f"- {resource}")
    if record.validation_results:
        lines.append("")
        lines.append("## Validation")
        for check, result in sorted(record.validation_results.items()):
            lines.append(f"- {check}: {result}")
    if record.previous_state or record.new_state:
        lines.append("")
        lines.append("## State")
        if record.previous_state:
            lines.append("### Previous")
            lines.append("```json")
            lines.append(json.dumps(record.previous_state, indent=2))
            lines.append("```")
        if record.new_state:
            lines.append("### New")
            lines.append("```json")
            lines.append(json.dumps(record.new_state, indent=2))
            lines.append("```")
    return "\n".join(lines) + "\n"


def write_transaction_reports(record: TransactionRecord, report_dir: Path) -> tuple[Path, Path]:
    """Write JSON and Markdown evidence files for a transaction."""
    report_dir.mkdir(parents=True, exist_ok=True)
    base_name = record.transaction_id
    json_path = report_dir / f"{base_name}.json"
    md_path = report_dir / f"{base_name}.md"
    json_path.write_text(json.dumps(record.to_dict(), indent=2) + "\n", encoding="utf-8")
    md_path.write_text(format_transaction_markdown(record), encoding="utf-8")
    return json_path, md_path
