# SIM (Shembazai Infrastructure Manager)

SIM is a deterministic, idempotent infrastructure manager for Rocky Linux 10+
hosts. It provisions K1 infrastructure and maintains declared desired state
via the **Infrastructure Reconciliation Engine (IRE)** — observation, drift
detection, transactional change, rollback, and evidence.

**Status (July 2026):** Complete — 180 tests passing. See [docs/ROADMAP.md](docs/ROADMAP.md).

## Table of Contents

1. What SIM does
2. Current capabilities
3. Architecture overview
4. Project layout
5. Requirements
6. Quick start
7. Command reference
8. IRE (Infrastructure Reconciliation Engine)
9. Logging, state, and reports
10. Testing
11. Troubleshooting
12. Development notes

## 1) What SIM does

SIM provisions and maintains Shembazai infrastructure hosts in a reproducible
way.

Design goals:

- **Deterministic** — same input produces the same result
- **Idempotent** — safe to re-run
- **Resumable** — SQLite-backed module state
- **Recoverable** — IRE favors rollback over destructive repair
- **Local-first** — runs on the host it manages

## 2) Current capabilities

### Provisioning (implemented)

- CLI with global `--dry-run`, `--verbose`, `--log-dir`
- Phase lifecycle: Detect → Validate → Install → Verify → Complete
- Phase 0 host validation
- Phase 1 service port management
- Stages 2–4 install modules: filesystem, Python venv, host deps, Podman,
  Quadlet, registry policy, NVIDIA driver/CUDA/container toolkit
- Structured logging, phase reports (JSON + Markdown), inventory snapshots
- `sim state-report` for module history

### IRE (implemented)

- Desired state schema in manifest (`infrastructure:` section)
- Runtime observation (`sim ire observe`)
- Drift detection (`sim ire drift`) and execution planning (`sim ire plan`)
- Panic-safe preflight (`sim ire safety`)
- Prometheus textfile metrics (`sim ire metrics`)
- Full reconciliation lifecycle (`sim ire reconcile`, dry-run default)
- SSH and firewalld reconcile modules (backup, validate, rollback)
- Storage Integrity Guardian (`sim ire storage`, read-only)
- Tailscale identity observer (`sim ire tailscale`, read-only)
- Transaction evidence (`sim ire transactions`, `sim ire show TX-...`)
- Unified orchestration: `sim install`, `sim health`, `sim repair`

### Intentionally excluded

- Automatic destructive repair (`mkfs`, `wipefs`, mount repair, etc.)
- Volatile IPs (Tailscale/DHCP) in desired configuration

## 3) Architecture overview

```
Manifest (desired)          Runtime (observed)
       │                            │
       └──────────┬─────────────────┘
                  ▼
         IRE: drift → plan → safety → execute → verify
                  │
                  ▼
         Transaction evidence + phase reports
```

Provisioning phases use the same lifecycle primitives in `sim/phases/lifecycle.py`.
Install modules implement `InstallModule` in `sim/modules/`.

Key paths:

- `sim/main.py` — CLI
- `sim/config.py` — manifest models
- `sim/ire/` — reconciliation engine
- `sim/modules/` — provisioning modules
- `sim/state.py` — module completion state

## 4) Project layout

```
pyproject.toml
sim/                 # active package
tests/
examples/            # reference manifest
docs/ROADMAP.md      # continuation plan (complete)
```

## 5) Requirements

- Python >= 3.12
- typer, rich, pydantic, PyYAML
- Host: Rocky Linux 10+ recommended

## 6) Quick start

```bash
cd /mnt/ai/AI/K1/SIM
python3 -m venv .venv && . .venv/bin/activate
python -m pip install -U pip && python -m pip install -e '.[dev]'
sim --version
sim check-os
sim phase0 --manifest examples/k1_server_manifest.yaml
sim ire drift --manifest examples/k1_server_manifest.yaml
```

## 7) Command reference

### Global options

- `--dry-run` — no host mutations (honored by phases and `ire reconcile`)
- `--verbose` / `-v` — DEBUG logging
- `--log-dir PATH` — default `/opt/k1/logs`

### Provisioning

| Command | Purpose |
| --- | --- |
| `sim check-os` 		| Host OS recommendation check |
| `sim install` 		| Full provisioning pipeline with IRE preflight |
| `sim health` 			| Phase 0 critical checks plus IRE drift |
| `sim repair <module>` 	| Rollback and re-apply one install or IRE module |
| `sim phase0` 			| Pre-install validation |
| `sim phase1-ports` 		| Service port assignment |
| `sim phase2-init` 		| K1 directory layout |
| `sim phase2-python` 		| Python venv |
| `sim phase2-deps` 		| Host tool dependencies |
| `sim phase3-podman` 		| Podman runtime |
| `sim phase3-quadlet` 		| Quadlet directories |
| `sim phase3-registry` 	| Registry cache policy |
| `sim phase4-nvidia-driver` 	| NVIDIA driver |
| `sim phase4-cuda` 		| CUDA toolkit |
| `sim phase4-nvidia-container` | NVIDIA container toolkit |
| `sim state-report` 		| Module history from SQLite |

### IRE

| Command | Purpose |
| --- | --- |
| `sim ire observe` | Collect runtime infrastructure state |
| `sim ire storage` | Storage mount health report (read-only) |
| `sim ire tailscale` | Tailscale identity and connectivity report |
| `sim ire drift` | Compare desired vs observed |
| `sim ire plan` | Generate repair plan (no changes) |
| `sim ire safety` | Panic-safe pre-change checks |
| `sim ire metrics` | Prometheus textfile gauges (health, safety, drift) |
| `sim ire reconcile` | Full lifecycle (dry-run unless `--apply`) |
| `sim ire transactions` | List transaction evidence history |
| `sim ire show TX-...` | Full evidence report for one transaction |

Example manifest section:

```yaml
infrastructure:
  ssh:
    enabled: true
    port: 22
    allowed_users: [cybershaman]
    remote_access: tailscale
  tailscale:
    enabled: true
    # hostname: k1          # optional MagicDNS prefix check
    # tailnet: example.ts.net  # optional tailnet membership check
  storage:
    mounts:
      - path: /mnt/ai
        required: true
```

Never put Tailscale or DHCP IPs in desired configuration.

## 8) IRE (Infrastructure Reconciliation Engine)

IRE separates **intent** (manifest `infrastructure:`) from **runtime**
(`sim ire observe`). Critical design rules from the K1 SSH incident:

1. SSH must not bind to Tailscale IPs — use firewall + auth
2. Missing mounts are warnings, not data loss
3. Changes blocked when safety checks fail
4. No automatic `mkfs`, `wipefs`, or filesystem repair

Lifecycle:

```
Observe → Compare → Plan → Safety → Backup → Execute → Verify → Commit/Rollback
```

## 9) Logging, state, and reports

- Logs: `<log-dir>/sim.log` (default `/opt/k1/logs/sim.log`)
- Module state: `/opt/k1/state/sim_state.db`
- IRE transactions: `/opt/k1/state/ire_transactions.db`
- Phase reports: `/opt/k1/reports/`
- IRE transaction evidence (JSON + Markdown): `/opt/k1/reports/TX-...` on each reconcile
- Inventory: configured in manifest (`inventory.file`)

## 10) Testing

```bash
. .venv/bin/activate
pytest -q
```

## 11) Troubleshooting

**externally-managed-environment** — use the project virtualenv.

**phase0 fails** — read terminal output and latest report under `/opt/k1/reports/`.

**Permission denied on /opt/k1** — use `--log-dir ./logs` or grant permissions.

**ire reconcile BLOCKED** — resolve safety failures (e.g. mount `/mnt/ai`) before applying.

## 12) Development notes

- Package import path: `sim.*`
- Add tests for every CLI behavior change
- Follow [docs/ROADMAP.md](docs/ROADMAP.md) for operational runbooks
