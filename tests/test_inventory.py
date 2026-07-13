from pathlib import Path
from unittest.mock import patch

from sim.inventory import collect_inventory, write_inventory
from sim.ire.models import (
    FirewallObserved,
    ObservedState,
    SSHObserved,
    StorageMountObserved,
    StorageObserved,
    TailscaleObserved,
)


def test_collect_inventory_has_required_fields(tmp_path: Path):
    inv = collect_inventory(tmp_path, include_infrastructure=False)
    required = {
        "operating_system",
        "kernel",
        "cpu",
        "gpu",
        "ram",
        "storage",
        "installed_packages",
        "installed_services",
        "open_ports",
        "python_version",
        "podman_version",
        "nvidia_version",
        "cuda_version",
        "ollama_version",
        "open_webui_version",
        "grafana_version",
        "prometheus_version",
    }
    assert required.issubset(inv.keys())


def test_collect_inventory_includes_ire_infrastructure(tmp_path: Path):
    observed = ObservedState(
        ssh=SSHObserved(service_active=True, listening_ports=[22]),
        firewall=FirewallObserved(active=True, interface_zones={"tailscale0": "trusted"}),
        storage=StorageObserved(
            mounts=[StorageMountObserved(path="/mnt/ai", mounted=True, uuid="abc-123")],
        ),
        tailscale=TailscaleObserved(installed=True, online=True, hostname="k1"),
    )
    with patch("sim.inventory.collect_observed_state", return_value=observed):
        inv = collect_inventory(tmp_path, storage_paths=[Path("/mnt/ai")])

    assert "infrastructure" in inv
    infra = inv["infrastructure"]
    assert infra["ssh"]["service_active"] is True
    assert infra["firewall"]["interface_zones"]["tailscale0"] == "trusted"
    assert infra["storage"]["mounts"][0]["path"] == "/mnt/ai"
    assert infra["tailscale"]["hostname"] == "k1"


def test_write_inventory_persists_json(tmp_path: Path):
    path = tmp_path / "inventory.json"
    out = write_inventory({"kernel": "x"}, path)
    assert out == path
    text = path.read_text(encoding="utf-8")
    assert '"kernel": "x"' in text
