from pathlib import Path

import pytest
from pydantic import ValidationError

from sim.config import (
    ManifestConfig,
    ServerInfo,
    ServiceConfig,
    ServicesInfo,
    os_recommendation_warning,
)


def _minimal_manifest_dict() -> dict:
    return {
        "server": {"hostname": "k1", "role": "production"},
        "os": {"distro": "rocky", "version": "10"},
    }


def test_manifest_loads_with_defaults():
    cfg = ManifestConfig.model_validate(_minimal_manifest_dict())
    assert cfg.server.hostname == "k1"
    assert cfg.services.ollama.port == 11434
    assert cfg.services.open_webui.port == 3000


def test_manifest_loads_off_recommendation_os_without_error():
    # Corrected policy (2026-07-08): OS is a recommendation, not a hard
    # lock. A manifest declaring a non-recommended distro/version must
    # still load successfully.
    data = _minimal_manifest_dict()
    data["os"] = {"distro": "ubuntu", "version": "24.04"}
    cfg = ManifestConfig.model_validate(data)
    assert cfg.os.distro == "ubuntu"


def test_recommendation_warning_matches_target():
    assert os_recommendation_warning("rocky", "10") is None
    assert os_recommendation_warning("rocky", "10.2") is None
    assert os_recommendation_warning("rocky", "11") is None


def test_recommendation_warning_flags_wrong_distro():
    warning = os_recommendation_warning("ubuntu", "24.04")
    assert warning is not None
    assert "ubuntu" in warning


def test_recommendation_warning_flags_old_version():
    warning = os_recommendation_warning("rocky", "9.3")
    assert warning is not None
    assert "9.3" in warning


def test_recommendation_warning_handles_unparsable_version():
    warning = os_recommendation_warning("rocky", "")
    assert warning is not None


def test_port_collision_rejected():
    with pytest.raises(ValidationError, match="Port collision"):
        ServicesInfo(
            ollama=ServiceConfig(port=3000),
            open_webui=ServiceConfig(port=3000),
        )


def test_disabled_service_excluded_from_collision_check():
    # A disabled service must not block reuse of its port.
    services = ServicesInfo(
        ollama=ServiceConfig(port=3000, enabled=False),
        open_webui=ServiceConfig(port=3000, enabled=True),
    )
    assert services.open_webui.port == 3000


def test_manifest_round_trip(tmp_path: Path):
    cfg = ManifestConfig.model_validate(_minimal_manifest_dict())
    out_path = tmp_path / "k1_server_manifest.yaml"
    cfg.dump(out_path)
    reloaded = ManifestConfig.load(out_path)
    assert reloaded == cfg


def test_extra_fields_forbidden():
    data = _minimal_manifest_dict()
    data["unexpected_field"] = "should fail"
    with pytest.raises(ValidationError):
        ManifestConfig.model_validate(data)
