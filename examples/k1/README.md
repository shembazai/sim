# K1 Alpha Bootstrap

K1 source under `/mnt/ai/AI/K1` is owned by root on this host. Use this bootstrap
package from the writable SIM tree to unblock Alpha Part I.

## Quick start

```bash
cd /mnt/ai/AI/K1/SIM
sim k1 preflight --k1-root /mnt/ai/AI/K1
examples/k1/run_overlay_tests.sh          # verify fixes without sudo
sudo examples/k1/bootstrap_alpha.sh         # apply fixes to live K1 tree
# If pip fails on k1.egg-info permissions:
sudo examples/k1/finish_bootstrap.sh
sim k1 preflight --k1-root /mnt/ai/AI/K1
```

## What bootstrap does

1. `chown` K1 source to your user (excluding SIM)
2. Run `examples/k1/apply_alpha_fixes.py`:
   - Fix `pyproject.toml` (license, package discovery, psutil wheel)
   - Enhance `startup_health` for Alpha prereq 3
   - Writable runtime dirs for tests (`K1_RUNTIME_*` env)
   - Software engineer embedded `edit:` extraction
   - systemd unit paths for this host
3. `pip install -e ".[dev]"` in K1 venv
4. `pytest -q`

## Alpha Part I checklist

| Prereq | Command |
| --- | --- |
| Install | `pip install -e ".[dev]"` |
| Tests | `pytest -q` |
| Startup health | `python -c "from src.k1_core import K1Runtime; ..."` |
| Bridge | `python -m k1_os.agents.openwebui_agent_api` + `curl /health` |
| systemd | `deploy/systemd/k1-*.service` |

## SIM integration

```bash
sim k1 preflight --json
```

Maps to [K1_constitution.txt](../../../K1_constitution.txt) section 14.4 (Alpha Part I technical prerequisites).
