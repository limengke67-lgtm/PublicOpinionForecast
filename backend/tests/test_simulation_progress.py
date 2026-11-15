import json

from scripts.action_logger import PlatformActionLogger
from app.services.simulation_runner import SimulationRunner, SimulationRunState


def test_round_log_updates_elapsed_simulated_hours(tmp_path):
    logger = PlatformActionLogger("twitter", str(tmp_path))
    logger.log_round_end(3, 2, simulated_hours=1.5)
    record = json.loads((tmp_path / "twitter" / "actions.jsonl").read_text(encoding="utf-8"))
    assert record["simulated_hours"] == 1.5

    state = SimulationRunState("sim_progress")
    SimulationRunner._read_action_log(logger.log_path, 0, state, "twitter")
    assert state.twitter_current_round == 3
    assert state.twitter_simulated_hours == 1.5
