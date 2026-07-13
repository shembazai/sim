from pathlib import Path

import typer
from typer.testing import CliRunner

from sim.main import app
from sim.phases.phase1_ports import (
    apply_assignments,
    assignment_checks,
    choose_ports_non_interactive,
    detect_used_tcp_ports,
    verify_assignments,
)


runner = CliRunner()


def _manifest_text() -> str:
    return """
server:
  hostname: k1
  role: production
services:
  ollama:
    enabled: true
    port: 11434
  open_webui:
    enabled: true
    port: 3000
  grafana:
    enabled: true
    port: 3001
  prometheus:
    enabled: true
    port: 9090
  k1:
    enabled: true
    port: 8000
security:
  selinux: enforcing
  firewall: firewalld
""".strip() + "\n"


def test_detect_used_tcp_ports_parses_ss_output(monkeypatch):
    monkeypatch.setattr(
        "sim.phases.phase1_ports.run_command",
        lambda _args: (
            True,
            "LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\nLISTEN 0 128 127.0.0.1:9090 0.0.0.0:*",
        ),
    )
    assert detect_used_tcp_ports() == {22, 9090}


def test_detect_used_tcp_ports_raises_on_command_failure(monkeypatch):
    monkeypatch.setattr("sim.phases.phase1_ports.run_command", lambda _args: (False, "ss not found"))
    try:
        detect_used_tcp_ports()
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "ss not found" in str(exc)


def test_choose_ports_non_interactive_selects_first_available():
    from sim.config import ManifestConfig

    cfg = ManifestConfig.model_validate({"server": {"hostname": "k1", "role": "production"}})
    used = {3000, 3001, 9090}
    assignments = choose_ports_non_interactive(cfg, used)
    assert assignments["open_webui"] == 8080
    assert assignments["grafana"] == 3100
    assert assignments["prometheus"] == 9092
    assert assignments["ollama"] == 11434
    assert assignments["k1"] == 8000


def test_assignment_checks_reject_duplicate_and_in_use():
    checks = assignment_checks(
        {
            "open_webui": 8080,
            "grafana": 8080,
            "prometheus": 9090,
        },
        {9090},
    )
    failures = [c for c in checks if c.status == "failed"]
    assert len(failures) == 2
    assert any("Duplicate port" in c.detail for c in failures)
    assert any("is in use" in c.detail for c in failures)


def test_apply_assignments_preserves_unrelated_manifest_fields(tmp_path: Path):
    from sim.config import ManifestConfig

    manifest = tmp_path / "k1_server_manifest.yaml"
    manifest.write_text(_manifest_text(), encoding="utf-8")
    cfg = ManifestConfig.load(manifest)

    apply_assignments(cfg, {"open_webui": 8080, "grafana": 3100}, manifest)

    reloaded = ManifestConfig.load(manifest)
    assert reloaded.services.open_webui.port == 8080
    assert reloaded.services.grafana.port == 3100
    assert reloaded.services.ollama.port == 11434
    assert reloaded.security.firewall == "firewalld"


def test_verify_assignments_checks_manifest_after_apply(tmp_path: Path):
    from sim.config import ManifestConfig

    manifest = tmp_path / "k1_server_manifest.yaml"
    manifest.write_text(_manifest_text(), encoding="utf-8")
    cfg = ManifestConfig.load(manifest)
    assignments = {"open_webui": 8080, "grafana": 3100}

    apply_assignments(cfg, assignments, manifest)
    checks = verify_assignments(
        assignments,
        set(),
        cfg=cfg,
        manifest_path=manifest,
        dry_run=False,
    )
    assert all(c.status == "passed" for c in checks)


def test_verify_assignments_skips_manifest_check_in_dry_run(tmp_path: Path):
    from sim.config import ManifestConfig

    manifest = tmp_path / "k1_server_manifest.yaml"
    manifest.write_text(_manifest_text(), encoding="utf-8")
    cfg = ManifestConfig.load(manifest)
    assignments = {"open_webui": 8080}

    checks = verify_assignments(
        assignments,
        set(),
        cfg=cfg,
        manifest_path=manifest,
        dry_run=True,
    )
    assert all(c.name.startswith("port_") for c in checks)


def test_cli_phase1_ports_non_interactive_success(tmp_path: Path, monkeypatch):
    manifest = tmp_path / "k1_server_manifest.yaml"
    manifest.write_text(_manifest_text(), encoding="utf-8")
    state_db = tmp_path / "state.db"

    monkeypatch.setattr("sim.main.detect_used_tcp_ports", lambda: set())

    result = runner.invoke(
        app,
        [
            "phase1-ports",
            "--manifest",
            str(manifest),
            "--state-db",
            str(state_db),
            "--no-interactive",
        ],
    )

    assert result.exit_code == 0
    updated = manifest.read_text(encoding="utf-8")
    assert "open_webui" in updated
    assert "port: 3000" in updated


def test_cli_phase1_ports_rejects_unavailable_ports(tmp_path: Path, monkeypatch):
    manifest = tmp_path / "k1_server_manifest.yaml"
    manifest.write_text(_manifest_text(), encoding="utf-8")
    state_db = tmp_path / "state.db"

    monkeypatch.setattr("sim.main.detect_used_tcp_ports", lambda: {11434, 3000, 3001, 9090, 8000})

    result = runner.invoke(
        app,
        [
            "phase1-ports",
            "--manifest",
            str(manifest),
            "--state-db",
            str(state_db),
            "--no-interactive",
        ],
    )

    assert result.exit_code == 1


def test_cli_phase1_ports_dry_run_skips_manifest(tmp_path: Path, monkeypatch):
    manifest = tmp_path / "k1_server_manifest.yaml"
    original = _manifest_text()
    manifest.write_text(original, encoding="utf-8")
    state_db = tmp_path / "state.db"

    monkeypatch.setattr("sim.main.detect_used_tcp_ports", lambda: set())

    result = runner.invoke(
        app,
        [
            "--dry-run",
            "phase1-ports",
            "--manifest",
            str(manifest),
            "--state-db",
            str(state_db),
            "--no-interactive",
        ],
    )

    assert result.exit_code == 0
    assert manifest.read_text(encoding="utf-8") == original
