import csv
import json

import pytest

from app.services.simulation_manager import SimulationManager, SimulationState
from app.api.simulation import _check_simulation_prepared
from app.config import Config


def _manager(tmp_path, *, twitter, reddit):
    manager = SimulationManager()
    manager.SIMULATION_DATA_DIR = str(tmp_path)
    state = SimulationState(
        simulation_id="sim_test",
        project_id="proj_test",
        graph_id="graph_test",
        enable_twitter=twitter,
        enable_reddit=reddit,
    )
    manager._save_simulation_state(state)
    return manager, tmp_path / "sim_test"


def test_twitter_default_reads_generated_csv(tmp_path):
    manager, sim_dir = _manager(tmp_path, twitter=True, reddit=False)
    with (sim_dir / "twitter_profiles.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["agent_id", "name"])
        writer.writeheader()
        writer.writerow({"agent_id": "0", "name": "Alice"})

    assert manager.get_profiles("sim_test") == [{"agent_id": "0", "name": "Alice"}]


def test_reddit_default_preserves_json_behavior(tmp_path):
    manager, sim_dir = _manager(tmp_path, twitter=False, reddit=True)
    profiles = [{"user_id": 0, "username": "alice"}]
    (sim_dir / "reddit_profiles.json").write_text(json.dumps(profiles), encoding="utf-8")

    assert manager.get_profiles("sim_test") == profiles


def test_explicit_platform_overrides_dual_platform_default(tmp_path):
    manager, sim_dir = _manager(tmp_path, twitter=True, reddit=True)
    with (sim_dir / "twitter_profiles.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["agent_id", "name"])
        writer.writeheader()
        writer.writerow({"agent_id": "1", "name": "Bob"})

    assert manager.get_profiles("sim_test", platform="twitter")[0]["name"] == "Bob"


def test_invalid_platform_is_rejected(tmp_path):
    manager, _ = _manager(tmp_path, twitter=True, reddit=True)

    with pytest.raises(ValueError, match="不支持的平台"):
        manager.get_profiles("sim_test", platform="../state")


@pytest.mark.parametrize(("twitter", "reddit", "profiles"), [
    (True, False, ["twitter_profiles.csv"]),
    (False, True, ["reddit_profiles.json"]),
    (True, True, ["twitter_profiles.csv", "reddit_profiles.json"]),
])
def test_preparation_requires_only_enabled_platform_profiles(tmp_path, monkeypatch, twitter, reddit, profiles):
    monkeypatch.setattr(Config, "OASIS_SIMULATION_DATA_DIR", str(tmp_path))
    _, folder = _manager(tmp_path, twitter=twitter, reddit=reddit)
    state_path = folder / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state.update(status="completed", config_generated=True, profiles_count=1)
    state_path.write_text(json.dumps(state), encoding="utf-8")
    (folder / "simulation_config.json").write_text("{}", encoding="utf-8")
    for name in profiles:
        (folder / name).write_text('[{"user_id": 0}]' if name.endswith('.json') else 'agent_id\n0\n', encoding="utf-8")

    prepared, info = _check_simulation_prepared("sim_test")
    assert prepared
    assert info["profiles_count"] == 1
    (folder / profiles[0]).unlink()
    assert not _check_simulation_prepared("sim_test")[0]
