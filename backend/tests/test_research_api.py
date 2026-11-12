import json
from types import SimpleNamespace

import pytest
from flask import Flask

from app.api import research_bp
from app.config import Config
from app.services.simulation_runner import SimulationRunner, SimulationRunState, RunnerStatus
from app.services.zep_graph_memory_updater import ZepGraphMemoryManager


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, 'UPLOAD_FOLDER', str(tmp_path))
    monkeypatch.setattr(Config, 'OASIS_SIMULATION_DATA_DIR', str(tmp_path / 'simulations'))
    monkeypatch.setenv('MIROFISH_RUNTIME_DIR', str(tmp_path / 'runtime'))
    app = Flask(__name__)
    app.register_blueprint(research_bp, url_prefix='/api/research')
    return app.test_client()


def test_offline_demo_freeze_evaluate_export_and_compare(client):
    case = client.post('/api/research/demo').json['data']
    root = '/api/research/cases/' + case['case_id']
    assert 'truth' not in client.get(root).json['data']
    first = client.post(root + '/forecasts', json={'method': 'moving_average'}).json['data']
    second = client.post(root + '/forecasts', json={'method': 'last_value'}).json['data']
    response = client.post(f"/api/research/forecasts/{first['forecast_id']}/evaluate")
    assert response.status_code == 200
    assert response.json['data']['evidence_level'] == 'synthetic_demo'
    assert response.json['data']['usage'] is None
    csv = client.get(f"/api/research/forecasts/{first['forecast_id']}/export?format=csv")
    assert csv.status_code == 200 and 'actual_volume' in csv.text
    exported = client.get(f"/api/research/forecasts/{first['forecast_id']}/export?format=json")
    assert 'usage' in exported.json
    comparison = client.post('/api/research/compare', json={'forecast_ids': [first['forecast_id'], second['forecast_id']]})
    assert comparison.status_code == 200
    assert len(comparison.json['data']['rows']) == 2
    assert all(row['usage'] is None for row in comparison.json['data']['rows'])


def test_comparison_rejects_different_cases(client):
    ids = []
    for _ in range(2):
        case = client.post('/api/research/demo').json['data']
        ids.append(client.post(f"/api/research/cases/{case['case_id']}/forecasts", json={'method': 'last_value'}).json['data']['forecast_id'])
    response = client.post('/api/research/compare', json={'forecast_ids': ids})
    assert response.status_code == 400


def test_started_simulation_options_cannot_be_rewritten(client, tmp_path):
    folder = tmp_path / 'simulations' / 'sim_12345678'
    folder.mkdir(parents=True)
    path = folder / 'simulation_config.json'
    path.write_text('{}', encoding='utf-8')
    url = '/api/research/simulations/sim_12345678/options'
    options = {'mode': 'efficient', 'random_seed': 42, 'llm_concurrency': 4}
    assert client.put(url, json=options).status_code == 200
    original = path.read_text(encoding='utf-8')
    (folder / 'run_state.json').write_text('{}', encoding='utf-8')
    assert client.put(url, json={**options, 'random_seed': 9}).status_code == 400
    assert path.read_text(encoding='utf-8') == original


def test_simulation_curve_is_integrated_with_existing_logs(client, tmp_path):
    case = client.post('/api/research/demo').json['data']
    folder = tmp_path / 'simulations' / 'sim_12345678'
    (folder / 'reddit').mkdir(parents=True)
    (folder / 'run_state.json').write_text(json.dumps({'runner_status': 'completed', 'reddit_current_round': 6}), encoding='utf-8')
    (folder / 'simulation_config.json').write_text(json.dumps({'time_config': {'minutes_per_round': 60}}), encoding='utf-8')
    (folder / 'reddit' / 'actions.jsonl').write_text('\n'.join(json.dumps({'round': index, 'action_type': 'CREATE_POST'}) for index in range(1, 7)), encoding='utf-8')
    response = client.post(f"/api/research/cases/{case['case_id']}/forecasts", json={'method': 'simulation', 'simulation_id': folder.name, 'platform': 'reddit'})
    assert response.status_code == 200
    frozen = response.json['data']
    assert frozen['prediction'][0]['volume'] == case['train'][-1]['volume']
    assert frozen['metadata']['early_input_verified'] is False
    assert client.post(f"/api/research/forecasts/{frozen['forecast_id']}/evaluate").status_code == 200


def test_completed_rounds_drain_graph_before_reporting_without_closing_agents(monkeypatch):
    state = SimulationRunState('sim_test', runner_status=RunnerStatus.RUNNING, twitter_completed=True)
    saved = []
    monkeypatch.setattr(SimulationRunner, '_check_all_platforms_completed', classmethod(lambda cls, value: True))
    monkeypatch.setattr(SimulationRunner, '_save_run_state', classmethod(lambda cls, value: saved.append(value.runner_status)))
    monkeypatch.setattr(SimulationRunner, '_sync_simulation_status', classmethod(lambda *args: None))
    def drain(cls, identifier):
        assert state.runner_status == RunnerStatus.STOPPING
    monkeypatch.setattr(ZepGraphMemoryManager, 'stop_updater', classmethod(drain))
    SimulationRunner._graph_memory_enabled[state.simulation_id] = True
    SimulationRunner._publish_round_completion(state)
    assert saved == [RunnerStatus.STOPPING, RunnerStatus.COMPLETED]
    assert state.completed_at


def test_failed_graph_drain_is_not_success(monkeypatch):
    state = SimulationRunState('sim_failure', runner_status=RunnerStatus.RUNNING)
    monkeypatch.setattr(SimulationRunner, '_check_all_platforms_completed', classmethod(lambda *args: True))
    monkeypatch.setattr(SimulationRunner, '_save_run_state', classmethod(lambda *args: None))
    monkeypatch.setattr(SimulationRunner, '_sync_simulation_status', classmethod(lambda *args: None))
    def fail(*args):
        raise RuntimeError('incomplete')
    monkeypatch.setattr(ZepGraphMemoryManager, 'stop_updater', classmethod(fail))
    SimulationRunner._graph_memory_enabled[state.simulation_id] = True
    try:
        SimulationRunner._publish_round_completion(state)
        assert state.runner_status == RunnerStatus.FAILED
    finally:
        SimulationRunner._graph_memory_enabled.pop(state.simulation_id, None)
