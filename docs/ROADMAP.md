# SIM Continuation Roadmap — Infrastructure Reconciliation Engine

Foundation: [SIM_constitution.txt](../SIM_constitution.txt)
(parent: [K1_constitution.txt](../../K1_constitution.txt)).

This document is the authoritative continuation plan after the IRE foundation.
It aligns SIM with K1_OS control-plane requirements and the operational lessons
from the Tailscale/SSH/firewalld incident.

## Current State (July 2026)

SIM is a **provisioning orchestrator** evolving into an **Infrastructure
Reconciliation Engine (IRE)** — a deterministic control plane that maintains
declared infrastructure state with observation, drift detection, transactional
change, rollback, and evidence.

| Layer | Status |
| --- | --- |
| CLI + logging + SQLite state | Complete |
| Phase lifecycle (Detect→Verify) | Complete |
| Phase 0 host validation | Complete |
| Phase 1 port management | Complete (manifest port assignment; firewall via IRE only) |
| Stages 2–4 install modules | Complete (Podman, GPU, dirs, deps) |
| IRE Phase 1 foundation | Complete — schemas, observe/drift/plan, safety, transactions |
| IRE Phase 2 — Storage Integrity Guardian | Complete — `sim ire storage`, SMART/btrfs drift |
| IRE Phase 3 — SSH reconcile module | Complete — backup, `sshd -t`, rollback |
| IRE Phase 4 — Firewalld reconcile module | Complete — zone/service reconciliation |
| IRE Phase 5 — Tailscale module | Complete — `sim ire tailscale`, identity drift |
| IRE Phase 6 — Transaction evidence | Complete — `sim ire transactions`, `sim ire show TX-...` |
| IRE Phase 7 — Unified orchestration | Complete — `sim install`, `sim health`, `sim repair` |
| VM integration / chaos tests | Complete — unit scenarios + optional `SIM_VM_INTEGRATION=1` gate |
| Legacy `manager/` package removal | Complete |

**Tests:** 180 passing, 1 skipped (`pytest -q` from project venv).

**Status:** SIM is **complete** as a production-hardened Infrastructure Reconciliation Engine.
The only remaining operator action on this host is applying repairable drift with root
(see dress rehearsal below).

**Reference manifest:** `examples/k1_server_manifest.yaml`

Firewall intent belongs in `infrastructure.firewall` and is applied only through
IRE reconcile (`sim ire reconcile --apply`). Phase 1 port assignment does not
modify firewalld.

---

## Architectural Corrections Applied

1. **Desired vs observed separation** — `infrastructure:` manifest section
   holds intent; `sim ire observe` discovers runtime state.
2. **No volatile IPs in desired state** — drift detection flags
   `ListenAddress` bindings to specific addresses (e.g. Tailscale CGNAT).
3. **Storage Integrity Guardian** — missing mounts produce warnings,
   never automatic `mkfs`/`wipefs`/repair.
4. **Panic-safe mode** — `sim ire safety` and pre-reconcile gates block
   changes when required mounts or SSH recovery paths are unavailable.
5. **Transaction evidence** — SQLite store for TX IDs, validation results,
   rollback availability.
6. **Unified orchestration** — `sim install` runs IRE preflight; `sim health`
   combines Phase 0 critical checks with IRE drift; `sim repair` targets both
   install modules and IRE modules (ssh, firewall).

---

## Completed Implementation Phases

### Phase 2 — Storage Integrity Guardian ✓

- `sim/ire/modules/storage.py` — read-only observer (findmnt, blkid, btrfs, SMART)
- Drift detection for SMART/btrfs/mount-source cross-check
- CLI: `sim ire storage` with structured report and exit codes
- Tests with mocked `/proc/mounts` fixtures

### Phase 3 — SSH Reconciliation Module ✓

- `sim/ire/modules/ssh.py` implementing `ReconciliationModule`
- Config generation without Tailscale `ListenAddress` bindings
- Pre-change backup, `sshd -t` validation, automatic restore on failure
- Registered in `ReconciliationEngine` for `sim ire reconcile --apply`

### Phase 4 — Firewalld Reconciliation Module ✓

- `sim/ire/modules/firewall.py` — zone assignment, per-zone service exposure
- Legacy `phase1_ports.update_firewall_rules()` removed; firewall changes are IRE-only
- `sim install` pipeline does not call legacy port flood

### Phase 5 — Tailscale Module ✓

- `sim/ire/modules/tailscale.py` — install state, MagicDNS hostname, tailnet drift
- CLI: `sim ire tailscale`
- No desired IP fields

### Phase 6 — Transaction Logs & Evidence Reports ✓

- `sim ire transactions` — list/filter TX history
- `sim ire show TX-...` — full evidence report (JSON + Markdown)

### Phase 7 — Unify Provisioning + Reconciliation ✓

- `sim install` — ordered stage runner with IRE preflight
- `sim health` — Phase 0 critical subset + IRE drift
- Re-verify policy: install modules re-run when `is_completed` but `detect()` has critical failures
- `sim repair <module>` — rollback + re-apply for install and IRE targets
- Scheduled observe-only templates: `examples/sim-observe.service`, `examples/sim-observe.timer`

---

## Post-Phase 7 — Production Hardening

Priority work after the core IRE phases are complete.

### 1. K1 dress rehearsal ✓ (observe-only)

Run against the production manifest before any `--apply`:

```bash
sim health --manifest /opt/k1/k1_server_manifest.yaml
sim ire safety --manifest /opt/k1/k1_server_manifest.yaml
sim ire drift --manifest /opt/k1/k1_server_manifest.yaml
sim ire plan --manifest /opt/k1/k1_server_manifest.yaml
sim ire storage --manifest /opt/k1/k1_server_manifest.yaml
sim ire tailscale --manifest /opt/k1/k1_server_manifest.yaml
```

**Dress rehearsal on this host (2026-07-12, `examples/k1_server_manifest.yaml`):**

| Command | Result |
| --- | --- |
| `sim health` | Pass — 2 repairable drift warnings (SSH AllowUsers, firewalld zone) |
| `sim ire safety` | Pass — `/mnt/ai` mounted, sshd active, rollback dir present after init fix |
| `sim ire drift` | Exit 1 — repairable SSH + firewall drift (expected before reconcile) |
| `sim ire plan` | 2 repairable steps planned; dry-run, no changes |
| `sim ire storage` | `/mnt/ai` mounted ext4, 812 GiB free |
| `sim ire tailscale` | Online — `k1.tail8878ef.ts.net` |

Manifest fix applied: `infrastructure.storage.mounts./mnt/ai.fstype` set to `ext4`
(matched observed). Remaining drift is repairable via `sim ire reconcile --apply`
(AllowUsers + tailscale0 → trusted zone).

**Apply (operator):** Observe-only gates passed. Privilege gate verified (non-root
BLOCKED, exit 2). Final host alignment requires one elevated apply:

```bash
sudo sim ire reconcile --apply --manifest examples/k1_server_manifest.yaml
sim health --manifest examples/k1_server_manifest.yaml
sim ire drift --manifest examples/k1_server_manifest.yaml
```

Expected post-apply: zero repairable drift (SSH AllowUsers + tailscale0 zone).

### 2. Retire legacy Phase 1 firewall ✓

| Task | Status |
| --- | --- |
| Deprecate `update_firewall_rules()` | Done (function removed) |
| `sim install` avoids legacy port flood | Done |
| Remove `--update-firewall` CLI flag | Done |
| Route all firewall changes through IRE | Done |

### 3. Inventory enrichment ✓

`collect_inventory()` includes an `infrastructure` section from IRE observers
(SSH, firewall, storage, Tailscale, network interfaces). Phase 0 snapshots
pass manifest storage mount paths.

### 4. Install module rollback coverage ✓

Every install module implements `rollback()` with non-destructive semantics:

| Module | Rollback behavior |
| --- | --- |
| `init_environment` | No-op (directories retained) |
| `python_runtime` | Remove manifest venv only |
| `dependencies` | No-op (RPMs retained) |
| `podman` | Disable socket activation |
| `quadlet` | No-op (shared Quadlet dir retained) |
| `registry` | Remove SIM-managed drop-in |
| `nvidia_driver` / `cuda` / `nvidia_container` | No-op (GPU stack retained) |

IRE modules (`ssh`, `firewall`) restore from backup on failure.

### 5. VM integration / chaos tests ✓

Unit chaos scenarios in `tests/test_ire_chaos.py` and `tests/test_ire_chaos_scenarios.py`:

- Unmount `/mnt/ai` → `sim ire safety` BLOCKED, no mutation
- Volatile SSH `ListenAddress` bind → critical drift
- Firewall zone drift → repairable plan + reconcile steps

Optional VM gate: `SIM_VM_INTEGRATION=1 pytest tests/test_ire_chaos_scenarios.py`
on a Rocky Linux 10 VM with snapshot revert.

### 6. Evidence export ✓

- IRE transactions export JSON + Markdown to `report.directory` (default
  `/opt/k1/reports/`) on every persisted transaction (PLANNED, BLOCKED, COMMITTED,
  FAILED)
- `sim ire reconcile` prints evidence paths when files are written
- Prometheus textfile gauges via `sim ire metrics` (default `sim_ire.prom` under
  `report.directory`; override with `--output` or `monitoring.prometheus_textfile`)

### 7. Tailscale auth-key rotation ✓ (by design)

Manual approval gate — not automated. Operator workflow:

1. Generate a new reusable or one-off auth key in the Tailscale admin console.
2. Run `sudo tailscale up --auth-key=...` (or set `TS_AUTHKEY` for scripted join).
3. Verify with `sim ire tailscale --manifest <manifest>` (hostname and tailnet must match manifest).
4. Do **not** add Tailscale IPs to `infrastructure.ssh` desired state.

### 8. Remove deprecated `manager/` package ✓

Removed. All provisioning and IRE functionality lives under `sim/`.

---

## Completion Summary (July 2026)

| Track | Status |
| --- | --- |
| Provisioning platform | **100 %** |
| IRE Phases 1–7 | **100 %** |
| Production hardening | **100 %** |
| Test suite | **180 pass**, 1 skip (VM gate) |

---

## Remaining Structural Debt

None. Future work is operational (apply drift on hosts) or new features outside
the IRE scope.

---

## Command Surface (current)

| Command | Purpose |
| --- | --- |
| `sim install` | Full provisioning pipeline with IRE preflight |
| `sim health` | Phase 0 critical checks + IRE drift |
| `sim repair <module>` | Rollback and re-apply one install or IRE module |
| `sim ire observe` | Runtime snapshot (JSON) |
| `sim ire storage` | Storage mount health report (read-only) |
| `sim ire tailscale` | Tailscale identity report (read-only) |
| `sim ire drift` | Desired vs observed comparison |
| `sim ire plan` | Execution plan, no changes |
| `sim ire safety` | Panic-safe preflight |
| `sim ire metrics` | Prometheus textfile gauges (health, safety, drift) |
| `sim ire reconcile` | Full lifecycle (dry-run default) |
| `sim ire reconcile --apply` | Apply repairable drift |
| `sim ire transactions` | Evidence history |
| `sim ire show TX-...` | Full evidence report for one transaction |

---

## Testing Strategy

1. **Unit tests** — drift logic, safety gates, transaction store, module reconcile.
2. **Fixture tests** — mock `sshd_config`, `/proc/mounts`, `firewall-cmd` output.
3. **VM integration** — Rocky Linux 10 VM with snapshot revert between runs.
4. **Chaos scenarios** — unmount `/mnt/ai`, break SSH bind, verify BLOCKED + no mutation.

---

## Decision Log

| Decision | Rationale |
| --- | --- |
| IRE lives under `sim/ire/` | Clear boundary from install modules |
| Desired state in manifest | Single source of truth per host |
| Dry-run is default for reconcile | Human-in-the-loop for critical changes |
| Storage drift is observe-only | Incident lesson: unmounted ≠ wiped |
| SSH never binds to Tailscale IP | IP changes break sshd bind on restart |
| `sim/ire/models.py` for observed types | Breaks circular imports with storage module |
| JSON CLI output uses `typer.echo` | Rich `console.print` breaks JSON parsing |

---

## Long-Term Vision

SIM evolves from *"run scripts that configure a machine"* to *"maintain
declared infrastructure state with evidence."* That positions it as the
deterministic control plane K1_OS depends on — alongside OASIS (knowledge),
EON (finance), and future Xylem agent transport.

EON now lives at `K1/EON/` with `eon health`, `eon self-test`, and FinanceAgent
integration. See [EON/docs/ROADMAP.md](../../EON/docs/ROADMAP.md).

Every subsystem remains replaceable. Every change remains explainable ten
years from now.
