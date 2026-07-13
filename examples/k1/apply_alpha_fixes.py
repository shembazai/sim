#!/usr/bin/env python3
"""Apply K1 Alpha Part I source fixes in-place or to a writable overlay tree."""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

K1_ROOT = Path(__file__).resolve().parents[3]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write(root: Path, rel: Path, content: str, *, label: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"updated {label}/{rel}")


def transform_pyproject(text: str) -> str:
    text = text.replace('license = { text = "MIT" }', 'license = "MIT"')
    text = text.replace('license-files = ["README.md"]', 'license-files = ["LICENSE"]')
    text = text.replace("psutil==5.9.0", "psutil>=6.0.0")
    text = re.sub(
        r'\n    "License :: OSI Approved :: MIT License",\n',
        "\n",
        text,
    )
    if "[tool.setuptools.packages.find]" not in text:
        text += """

[tool.setuptools.packages.find]
where = ["."]
include = ["src*", "k1_os*", "modules*"]
exclude = ["SIM*", "tests*", "scripts*", "deploy*", "logs*", "FR_docs*", "guidelines*"]
"""
    if "[tool.pytest.ini_options]" not in text:
        text += """

[tool.pytest.ini_options]
testpaths = ["tests"]
norecursedirs = ["SIM", ".venv", "build", "dist", "node_modules"]
"""
    return text


def transform_k1_core(text: str) -> str:
    new_loader = '''def load_runtime_config() -> Dict[str, Any]:
    """Load runtime configuration from config/k1.yaml when available."""
    return load_runtime_config_with_status()[0]


def load_runtime_config_with_status() -> tuple[Dict[str, Any], Dict[str, Any]]:
    """Load config and return (config_dict, status_metadata) for startup_health."""
    config_path = Path(os.getenv("K1_CONFIG_PATH", "config/k1.yaml"))
    status: Dict[str, Any] = {
        "config_path": str(config_path),
        "config_loaded": False,
        "config_degraded": True,
        "config_error": "",
    }
    if not config_path.exists():
        logging.getLogger("k1_core.bootstrap").warning(
            "Runtime config file not found at %s. Using built-in defaults.",
            config_path,
        )
        status["config_error"] = "config file not found"
        return {}, status

    try:
        import yaml  # type: ignore
    except Exception as exc:
        logging.getLogger("k1_core.bootstrap").warning(
            "PyYAML import failed (%s). Runtime config cannot be loaded; using defaults.",
            exc,
        )
        status["config_error"] = f"pyyaml import failed: {exc}"
        return {}, status

    try:
        with config_path.open("r", encoding="utf-8") as handle:
            parsed = yaml.safe_load(handle) or {}
            if isinstance(parsed, dict):
                status["config_loaded"] = True
                status["config_degraded"] = False
                return parsed, status
            logging.getLogger("k1_core.bootstrap").warning(
                "Runtime config at %s is not a mapping. Using defaults.",
                config_path,
            )
            status["config_error"] = "config root is not a mapping"
            return {}, status
    except Exception as exc:
        logging.getLogger("k1_core.bootstrap").warning(
            "Failed to parse runtime config %s (%s). Using defaults.",
            config_path,
            exc,
        )
        status["config_error"] = str(exc)
        return {}, status
'''

    text = re.sub(
        r"def load_runtime_config\(\) -> Dict\[str, Any\]:.*?return \{\}\n",
        new_loader,
        text,
        count=1,
        flags=re.DOTALL,
    )
    text = text.replace(
        "        self.config = load_runtime_config()",
        "        self.config, self.config_status = load_runtime_config_with_status()",
        1,
    )
    text = text.replace(
        '        state_path = runtime_cfg.get("task_state_path", "./logs/task-lifecycle.json")',
        '        state_path = os.getenv("K1_RUNTIME_TASK_STATE", runtime_cfg.get("task_state_path", "./logs/task-lifecycle.json"))',
        1,
    )
    text = text.replace(
        '        memory_root = runtime_cfg.get("memory_root", ".")',
        '        memory_root = os.getenv("K1_RUNTIME_MEMORY_ROOT", runtime_cfg.get("memory_root", "."))',
        1,
    )
    if '"config_loaded"' not in text:
        text = text.replace(
            '''    def _build_startup_health(self) -> Dict[str, Any]:
        return {
            "discovered_agents": self.discovered_agents,
            "discovery_errors": self.discovery_errors,
            "discovered_count": len(self.discovered_agents),
            "discovery_error_count": len(self.discovery_errors),
        }''',
            '''    def _build_startup_health(self) -> Dict[str, Any]:
        required_agents = {
            "ceo", "planning", "research", "knowledge", "software_engineering", "infrastructure",
        }
        discovered_ids = set(self.discovered_agents.keys())
        missing_required = sorted(required_agents - discovered_ids)
        return {
            "config_loaded": bool(self.config_status.get("config_loaded")),
            "config_degraded": bool(self.config_status.get("config_degraded")),
            "config_path": self.config_status.get("config_path", ""),
            "config_error": self.config_status.get("config_error", ""),
            "memory_initialized": self.memory_store is not None,
            "event_bus_initialized": self.event_bus is not None,
            "agents_discovered": self.discovered_agents,
            "discovered_agents": self.discovered_agents,
            "discovery_errors": self.discovery_errors,
            "discovered_count": len(self.discovered_agents),
            "discovery_error_count": len(self.discovery_errors),
            "required_agents_present": not missing_required,
            "missing_required_agents": missing_required,
        }''',
            1,
        )
    return text


def transform_conftest(text: str) -> str:
    if "K1_RUNTIME_MEMORY_ROOT" in text:
        return text
    return text + '''

import pytest


@pytest.fixture(autouse=True)
def _k1_writable_runtime_dirs(monkeypatch, tmp_path_factory):
    runtime_dir = tmp_path_factory.mktemp("k1_runtime")
    monkeypatch.setenv("K1_RUNTIME_MEMORY_ROOT", str(runtime_dir))
    monkeypatch.setenv("K1_RUNTIME_TASK_STATE", str(runtime_dir / "task-lifecycle.json"))
'''


def transform_software_engineer(text: str) -> str:
    needle = '            desc = node.get("description", "")\n            # For now, only support simple filesystem edits'
    if needle in text and "LocalFallbackAdapter().decompose_intent(desc)" not in text:
        text = text.replace(
            needle,
            '            desc = node.get("description", "")\n'
            '            if "edit:" in desc.lower() and not desc.strip().lower().startswith("edit:"):\n'
            '                extracted = LocalFallbackAdapter().decompose_intent(desc)\n'
            '                if extracted:\n'
            '                    desc = extracted[0].get("description", desc)\n'
            '            # For now, only support simple filesystem edits',
            1,
        )
    return text


def transform_systemd(text: str, k1_root: Path) -> str:
    text = text.replace("K1_ROOT=/home/shemba/AI/K1", f"K1_ROOT={k1_root}")
    text = text.replace("K1_PYTHON=/usr/bin/python3", f"K1_PYTHON={k1_root}/.venv/bin/python")
    text = text.replace("PYTHONPATH=/home/shemba/AI/K1", f"PYTHONPATH={k1_root}/src:{k1_root}/modules:{k1_root}")
    text = text.replace("WorkingDirectory=/home/shemba/AI/K1", f"WorkingDirectory={k1_root}")
    return text


def transform_runtime_packaging_test(text: str) -> str:
    return text.replace('assert version == "0.1.0"', 'assert version == "0.1.0a1"')


def transform_executive_test(text: str) -> str:
    return text.replace(
        "def test_memory_store_persists_and_retrieves():\n    store = MemoryStore()",
        "def test_memory_store_persists_and_retrieves(tmp_path):\n    store = MemoryStore(root_dir=tmp_path)",
        1,
    )


def transform_software_engineer_test(text: str) -> str:
    return text.replace(
        "def test_agent_extracts_edit_instruction_from_mission_text():\n    agent = SoftwareEngineerAgent(DummyLLM(), tools={})",
        "def test_agent_extracts_edit_instruction_from_mission_text(tmp_path, monkeypatch):\n    monkeypatch.chdir(tmp_path)\n    agent = SoftwareEngineerAgent(DummyLLM(), tools={})",
        1,
    )


def apply_fixes(*, target_root: Path, k1_root: Path, label: str) -> None:
    _write(target_root, Path("pyproject.toml"), transform_pyproject(_read(k1_root / "pyproject.toml")), label=label)
    _write(
        target_root,
        Path("src/k1_core.py"),
        transform_k1_core(_read(k1_root / "src/k1_core.py")),
        label=label,
    )
    _write(
        target_root,
        Path("tests/conftest.py"),
        transform_conftest(_read(k1_root / "tests/conftest.py")),
        label=label,
    )
    _write(
        target_root,
        Path("k1_os/agents/software_engineer_agent.py"),
        transform_software_engineer(_read(k1_root / "k1_os/agents/software_engineer_agent.py")),
        label=label,
    )
    for name in ("k1-runtime.service", "k1-openwebui-bridge.service"):
        rel = Path("deploy/systemd") / name
        _write(
            target_root,
            rel,
            transform_systemd(_read(k1_root / rel), k1_root),
            label=label,
        )
    _write(
        target_root,
        Path("tests/test_runtime_packaging.py"),
        transform_runtime_packaging_test(_read(k1_root / "tests/test_runtime_packaging.py")),
        label=label,
    )
    _write(
        target_root,
        Path("tests/test_executive_agent.py"),
        transform_executive_test(_read(k1_root / "tests/test_executive_agent.py")),
        label=label,
    )
    _write(
        target_root,
        Path("tests/test_software_engineer_agent.py"),
        transform_software_engineer_test(_read(k1_root / "tests/test_software_engineer_agent.py")),
        label=label,
    )


def materialize_overlay(overlay_root: Path, *, k1_root: Path = K1_ROOT) -> Path:
    """Copy K1 tree into overlay and apply alpha fixes to the copy."""
    if overlay_root.exists():
        shutil.rmtree(overlay_root)
    shutil.copytree(
        k1_root,
        overlay_root,
        ignore=shutil.ignore_patterns("SIM", ".venv", ".pytest_cache", "__pycache__", ".git"),
        dirs_exist_ok=False,
    )
    apply_fixes(target_root=overlay_root, k1_root=k1_root, label=overlay_root.name)
    return overlay_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--overlay",
        type=Path,
        help="Write patched files into this overlay directory instead of K1 root",
    )
    parser.add_argument("--k1-root", type=Path, default=K1_ROOT)
    args = parser.parse_args(argv)
    k1_root = args.k1_root.resolve()

    if args.overlay:
        materialize_overlay(args.overlay.resolve(), k1_root=k1_root)
        print(f"K1 Alpha overlay ready at {args.overlay.resolve()}")
        return 0

    apply_fixes(target_root=k1_root, k1_root=k1_root, label=str(k1_root))
    print("K1 Alpha fixes applied.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
