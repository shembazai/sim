"""K1 Alpha Part I preflight checks — read-only diagnostics."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


REQUIRED_AGENTS = frozenset(
    {"ceo", "planning", "research", "knowledge", "software_engineering", "infrastructure"}
)


@dataclass(frozen=True)
class PreflightCheck:
    name: str
    passed: bool
    detail: str
    critical: bool = True


@dataclass
class K1PreflightReport:
    k1_root: Path
    checks: list[PreflightCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed or not c.critical for c in self.checks)

    def to_dict(self) -> dict[str, object]:
        return {
            "k1_root": str(self.k1_root),
            "passed": self.passed,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "detail": c.detail,
                    "critical": c.critical,
                }
                for c in self.checks
            ],
        }


def default_k1_root() -> Path:
    env = os.getenv("K1_ROOT")
    if env:
        return Path(env).resolve()
    sim_root = Path(__file__).resolve().parents[2]
    return sim_root.parent.resolve()


def _runtime_scratch_dir(k1_root: Path) -> Path:
    if os.access(k1_root, os.W_OK):
        target = k1_root / ".k1_runtime"
    else:
        target = Path("/tmp") / f"k1-runtime-{os.getuid()}"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _pythonpath_for(k1_root: Path) -> str:
    parts = [str(k1_root / "src"), str(k1_root / "modules"), str(k1_root)]
    existing = os.environ.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    return os.pathsep.join(parts)


def run_k1_preflight(*, k1_root: Path | None = None, run_overlay: bool = False) -> K1PreflightReport:
    """Run Alpha Part I diagnostics against a K1 tree."""
    root = (k1_root or default_k1_root()).resolve()
    checks: list[PreflightCheck] = []

    checks.append(
        PreflightCheck(
            "k1_root",
            root.is_dir(),
            f"K1 root {root}" + (" exists" if root.is_dir() else " not found"),
        )
    )

    writable = os.access(root, os.W_OK) if root.exists() else False
    checks.append(
        PreflightCheck(
            "k1_root_writable",
            writable,
            "K1 root is writable by current user"
            if writable
            else "K1 root is not writable — run examples/k1/bootstrap_alpha.sh",
            critical=False,
        )
    )

    config_path = root / "config" / "k1.yaml"
    checks.append(
        PreflightCheck(
            "config_file",
            config_path.is_file(),
            f"Config at {config_path}" + (" present" if config_path.is_file() else " missing"),
            critical=False,
        )
    )

    venv_python = root / ".venv" / "bin" / "python"
    sim_python = root / "SIM" / ".venv" / "bin" / "python"

    def _venv_ready(path: Path) -> bool:
        if not path.is_file():
            return False
        probe = subprocess.run(
            [str(path), "-c", "import psutil, yaml"],
            capture_output=True,
        )
        return probe.returncode == 0

    if _venv_ready(venv_python):
        python_exec = venv_python
    elif _venv_ready(sim_python):
        python_exec = sim_python
    else:
        python_exec = venv_python if venv_python.is_file() else sim_python
    checks.append(
        PreflightCheck(
            "python_runtime",
            python_exec.is_file(),
            f"Python interpreter: {python_exec}" if python_exec.is_file() else "No venv python found",
        )
    )

    for module in ("psutil", "yaml"):
        spec_ok = importlib.util.find_spec(module) is not None
        if not spec_ok and python_exec.is_file():
            probe = subprocess.run(
                [str(python_exec), "-c", f"import {module}"],
                capture_output=True,
                text=True,
                env={**os.environ, "PYTHONPATH": _pythonpath_for(root)},
            )
            spec_ok = probe.returncode == 0
        checks.append(
            PreflightCheck(
                f"import_{module}",
                spec_ok,
                f"Module {module!r} importable" if spec_ok else f"Module {module!r} not available",
            )
        )

    startup: dict[str, object] | None = None
    if python_exec.is_file():
        env = {
            **os.environ,
            "PYTHONPATH": _pythonpath_for(root),
            "K1_CONFIG_PATH": str(config_path),
        }
        runtime_dir = _runtime_scratch_dir(root)
        env["K1_RUNTIME_MEMORY_ROOT"] = str(runtime_dir)
        env["K1_RUNTIME_TASK_STATE"] = str(runtime_dir / "task-lifecycle.json")
        env["K1_LOG_LEVEL"] = "CRITICAL"
        probe = subprocess.run(
            [
                str(python_exec),
                "-c",
                "import logging, os; logging.disable(logging.CRITICAL); "
                "os.environ.setdefault('K1_LOG_LEVEL', 'CRITICAL'); "
                "from src.k1_core import K1Runtime; import json; "
                "print(json.dumps(K1Runtime().startup_health))",
            ],
            capture_output=True,
            text=True,
            cwd=str(root),
            env=env,
        )
        if probe.returncode == 0:
            raw = probe.stdout.strip()
            try:
                startup = json.loads(raw)
            except json.JSONDecodeError:
                # k1_core may emit log lines before JSON on stdout
                for line in reversed(raw.splitlines()):
                    line = line.strip()
                    if line.startswith("{"):
                        try:
                            startup = json.loads(line)
                            break
                        except json.JSONDecodeError:
                            continue
                else:
                    checks.append(
                        PreflightCheck(
                            "startup_health",
                            False,
                            f"startup_health returned invalid JSON: {raw[:200]}",
                        )
                    )
        else:
            checks.append(
                PreflightCheck(
                    "startup_health",
                    False,
                    (probe.stderr or probe.stdout or "K1Runtime failed").strip()[:500],
                )
            )

    if startup is not None:
        discovery_errors = startup.get("discovery_errors", [])
        discovered = startup.get("discovered_agents") or startup.get("agents_discovered") or {}
        missing = sorted(REQUIRED_AGENTS - set(discovered.keys()))
        checks.append(
            PreflightCheck(
                "discovery_errors",
                not discovery_errors,
                "No agent discovery errors"
                if not discovery_errors
                else f"{len(discovery_errors)} discovery error(s)",
            )
        )
        checks.append(
            PreflightCheck(
                "required_agents",
                not missing,
                "All required agents discovered"
                if not missing
                else f"Missing agents: {', '.join(missing)}",
            )
        )
        for key in ("config_loaded", "memory_initialized", "event_bus_initialized"):
            if key in startup:
                checks.append(
                    PreflightCheck(
                        key,
                        bool(startup.get(key)),
                        f"{key}={startup.get(key)}",
                        critical=False,
                    )
                )

    if python_exec.is_file():
        runtime_dir = _runtime_scratch_dir(root)
        test_env = {
            **os.environ,
            "PYTHONPATH": _pythonpath_for(root),
            "K1_ROOT": str(root),
            "K1_RUNTIME_MEMORY_ROOT": str(runtime_dir),
            "K1_RUNTIME_TASK_STATE": str(runtime_dir / "task-lifecycle.json"),
            "PYTEST_ADDOPTS": "--basetemp=/tmp/k1-pytest",
        }
        result = subprocess.run(
            [str(python_exec), "-m", "pytest", "tests", "-q"],
            capture_output=True,
            text=True,
            cwd=str(root),
            env=test_env,
        )
        summary = (result.stdout or result.stderr).strip().splitlines()[-1] if result.stdout or result.stderr else ""
        checks.append(
            PreflightCheck(
                "pytest",
                result.returncode == 0,
                summary or f"pytest exit {result.returncode}",
                critical=False,
            )
        )

        if not writable and result.returncode != 0 and run_overlay:
            overlay_script = root / "SIM" / "examples" / "k1" / "run_overlay_tests.sh"
            if overlay_script.is_file():
                overlay = subprocess.run(
                    [str(overlay_script)],
                    capture_output=True,
                    text=True,
                    cwd=str(root / "SIM"),
                    env={**os.environ, "K1_ROOT": str(root)},
                )
                lines = [line.strip() for line in (overlay.stdout or overlay.stderr).splitlines() if line.strip()]
                overlay_summary = next(
                    (line for line in reversed(lines) if "passed" in line.lower() or "failed" in line.lower()),
                    lines[-1] if lines else "",
                )
                checks.append(
                    PreflightCheck(
                        "pytest_overlay",
                        overlay.returncode == 0,
                        overlay_summary or "overlay tests passed"
                        if overlay.returncode == 0
                        else (overlay.stderr or overlay.stdout or "overlay tests failed")[:500],
                        critical=False,
                    )
                )

    return K1PreflightReport(k1_root=root, checks=checks)
