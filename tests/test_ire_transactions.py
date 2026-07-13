"""Tests for IRE transaction evidence CLI."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from sim.ire.transaction import (
    TransactionRecord,
    TransactionStore,
    format_transaction_markdown,
    generate_transaction_id,
)
from sim.main import app


def _sample_record(**overrides) -> TransactionRecord:
    base = TransactionRecord(
        transaction_id=generate_transaction_id("ssh"),
        timestamp="2026-07-10T12:00:00+00:00",
        target_host="k1",
        status="COMMITTED",
        changed_resources=["/etc/ssh/sshd_config.d/99-sim-ire.conf"],
        validation_results={"sshd -t": "PASS", "sshd active": "PASS"},
        rollback_available=True,
        detail="SSH reconciliation committed",
    )
    if overrides:
        return TransactionRecord(**{**base.__dict__, **overrides})
    return base


def test_transaction_store_filter_by_status(tmp_path: Path):
    db = tmp_path / "tx.db"
    committed = _sample_record(status="COMMITTED")
    blocked = _sample_record(
        transaction_id=generate_transaction_id("ire"),
        status="BLOCKED",
        detail="Safety check failed",
    )
    with TransactionStore(db) as store:
        store.save(committed)
        store.save(blocked)
        all_records = store.history(limit=10)
        blocked_only = store.history(limit=10, status="BLOCKED")
    assert len(all_records) == 2
    assert len(blocked_only) == 1
    assert blocked_only[0].status == "BLOCKED"


def test_format_transaction_markdown():
    record = _sample_record()
    md = format_transaction_markdown(record)
    assert record.transaction_id in md
    assert "sshd -t" in md
    assert "/etc/ssh/sshd_config.d/99-sim-ire.conf" in md


def test_write_transaction_reports(tmp_path: Path):
    from sim.ire.transaction import write_transaction_reports

    record = _sample_record()
    json_path, md_path = write_transaction_reports(record, tmp_path / "reports")
    assert json_path.exists()
    assert md_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["transaction_id"] == record.transaction_id
    assert record.transaction_id in md_path.read_text(encoding="utf-8")


def test_cli_ire_transactions_json(tmp_path: Path):
    db = tmp_path / "tx.db"
    record = _sample_record()
    with TransactionStore(db) as store:
        store.save(record)

    runner = CliRunner()
    result = runner.invoke(app, ["ire", "transactions", "--tx-db", str(db), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    assert payload[0]["transaction_id"] == record.transaction_id
    assert payload[0]["status"] == "COMMITTED"


def test_cli_ire_show_markdown(tmp_path: Path):
    db = tmp_path / "tx.db"
    record = _sample_record()
    with TransactionStore(db) as store:
        store.save(record)

    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ire", "show", record.transaction_id, "--tx-db", str(db), "--markdown"],
    )
    assert result.exit_code == 0
    assert record.transaction_id in result.stdout
    assert "sshd -t" in result.stdout


def test_cli_ire_show_not_found(tmp_path: Path):
    db = tmp_path / "tx.db"
    with TransactionStore(db):
        pass

    runner = CliRunner()
    result = runner.invoke(app, ["ire", "show", "TX-MISSING-001", "--tx-db", str(db)])
    assert result.exit_code == 1
    assert "not found" in result.stdout.lower()


def test_cli_ire_transactions_invalid_status(tmp_path: Path):
    db = tmp_path / "tx.db"
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["ire", "transactions", "--tx-db", str(db), "--status", "INVALID"],
    )
    assert result.exit_code == 2
