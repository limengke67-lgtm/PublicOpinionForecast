import json
from pathlib import Path

import pytest
from flask import Flask

from app.api import research_bp
from app.config import Config
from app.research.events import EventStore
from app.research.store import ResearchStore, write_json
from app.research.rolling import RevisionConflict


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv('MIROFISH_RUNTIME_DIR', str(tmp_path / 'runtime'))
    monkeypatch.setattr(Config, 'UPLOAD_FOLDER', str(tmp_path))
    return EventStore(tmp_path)


def mutate(workspace, extra=None, request_id=None):
    return {'expected_revision': workspace['event']['revision'],
            'request_id': request_id or f"request_{workspace['event']['revision']:08d}", **(extra or {})}


def test_complete_zero_cost_event_workflow_survives_service_recreation(store):
    workspace = store.demo()
    identifier = workspace['event']['event_id']
    assert workspace['rolling'] and not workspace['simulations']
    for _ in range(6):
        store.replay_step(identifier, mutate(workspace))
        workspace = store.workspace(identifier)
    _, operation = store.create_report(identifier, mutate(workspace))
    report = EventStore(store.root).get_report(operation['report_id'])
    assert report['rolling_summary']['scored_buckets'] == 6
    assert '模型用量与成本' in report['markdown']
    assert '没有关联模型账本' in report['markdown']
    assert report['is_demo'] is True
    loaded = EventStore(store.root).workspace(identifier)
    assert loaded['reports'][-1]['stale'] is False
    assert loaded['stages'][-1]['status'] == 'completed'
    original = report.copy()
    store.refresh_case(identifier, mutate(loaded))
    assert store.workspace(identifier)['reports'][-1]['stale'] is True
    assert store.get_report(operation['report_id']) == original


def test_discovery_of_native_project_simulation_and_actual_log_freezing(store):
    workspace = store.demo()
    identifier, case = workspace['event']['event_id'], store.research.case(workspace['case']['case_id'])
    project_id, simulation_id = 'proj_123456789abc', 'sim_123456789abc'
    write_json(store.root / 'projects' / project_id / 'project.json', {
        'project_id': project_id, 'research_case_id': case['case_id'], 'created_at': '2026-01-01',
        'name': 'real-bound-project', 'status': 'graph_completed', 'graph_id': 'graph-example'})
    folder = store.root / 'simulations' / simulation_id
    write_json(folder / 'state.json', {'simulation_id': simulation_id, 'project_id': project_id,
                                     'status': 'completed', 'created_at': '2026-01-01', 'profiles_count': 4})
    write_json(folder / 'run_state.json', {'runner_status': 'completed', 'runtime_scope_id': 'run_123456789abc', 'reddit_current_round': 6})
    write_json(folder / 'simulation_config.json', {'project_id': project_id, 'time_config': {'minutes_per_round': 60},
               'research_provenance': {'case_id': case['case_id'], 'train_hash': case['train_hash'], 'pristine_graph': True}})
    (folder / 'reddit').mkdir(parents=True)
    (folder / 'reddit' / 'actions.jsonl').write_text('\n'.join(json.dumps({'round': i, 'action_type': 'CREATE_POST'}) for i in range(1, 7)), encoding='utf-8')
    current = store.workspace(identifier)
    assert current['latest_project']['project_id'] == project_id
    assert current['latest_simulation']['simulation_id'] == simulation_id
    store.freeze(identifier, mutate(current, {'method': 'simulation', 'platform': 'reddit'}))
    current = store.workspace(identifier)
    assert current['forecasts'][0]['metadata']['simulation_id'] == simulation_id
    assert current['usage']['totals']['estimated_cny'] is None


def test_event_mutations_are_idempotent_and_revision_guarded(store):
    workspace = store.demo()
    identifier = workspace['event']['event_id']
    body = mutate(workspace)
    first, _ = store.replay_step(identifier, body)
    repeated, _ = store.replay_step(identifier, body)
    assert first['revision'] == repeated['revision']
    assert len(store.workspace(identifier)['rolling']['assessments']) == 1
    with pytest.raises(RevisionConflict):
        store.replay_step(identifier, {**body, 'request_id': 'another_request'})


def test_stale_window_rejection_does_not_save_a_new_frozen_forecast(store):
    workspace = store.demo()
    identifier = workspace['event']['event_id']
    store.replay_step(identifier, mutate(workspace))
    workspace = store.workspace(identifier)
    original = store.research.list_forecasts()
    with pytest.raises(ValueError, match='下一轮材料'):
        store.freeze(identifier, mutate(workspace, {'method': 'last_value'}))
    assert store.research.list_forecasts() == original
    store.refresh_case(identifier, mutate(workspace))
    workspace = store.workspace(identifier)
    store.freeze(identifier, mutate(workspace, {'method': 'last_value'}))
    assert len(store.workspace(identifier)['forecasts']) == len(original) + 1


def test_unmanaged_run_is_not_claimed_to_be_resumed(store):
    workspace = store.demo()
    project = {'project_id': 'proj_12345678', 'research_case_id': workspace['case']['case_id'], 'status': 'graph_completed'}
    write_json(store.root / 'projects' / 'proj_12345678' / 'project.json', project)
    folder = store.root / 'simulations' / 'sim_12345678'
    write_json(folder / 'state.json', {'simulation_id': 'sim_12345678', 'project_id': 'proj_12345678', 'status': 'running'})
    write_json(folder / 'run_state.json', {'runner_status': 'running'})
    current = store.workspace(workspace['event']['event_id'])
    assert current['latest_simulation']['lifecycle'] == 'unmanaged'
    assert current['stages'][1]['status'] == 'attention'


def test_native_report_is_included_only_when_scope_matches(store):
    workspace = store.demo()
    identifier = workspace['event']['event_id']
    project_id, sim_id = 'proj_12345678', 'sim_12345678'
    write_json(store.root / 'projects' / project_id / 'project.json', {'project_id': project_id, 'research_case_id': workspace['case']['case_id']})
    write_json(store.root / 'simulations' / sim_id / 'state.json', {'simulation_id': sim_id, 'project_id': project_id, 'status': 'completed'})
    write_json(store.root / 'simulations' / sim_id / 'run_state.json', {'runner_status': 'completed', 'runtime_scope_id': 'run_current'})
    path = store.root / 'reports' / 'report_12345678'
    write_json(path / 'meta.json', {'report_id': path.name, 'simulation_id': sim_id, 'status': 'completed', 'runtime_scope_id': 'run_old'})
    (path / 'full_report.md').write_text('outdated narrative', encoding='utf-8')
    current = store.workspace(identifier)
    _, operation = store.create_report(identifier, mutate(current))
    assert 'outdated narrative' not in store.get_report(operation['report_id'])['markdown']
    write_json(path / 'meta.json', {'report_id': path.name, 'simulation_id': sim_id, 'status': 'completed', 'runtime_scope_id': 'run_current'})
    current = store.workspace(identifier)
    assert current['reports'][-1]['stale'] is True
    _, operation = store.create_report(identifier, mutate(current))
    assert 'outdated narrative' in store.get_report(operation['report_id'])['markdown']
    # Missing run identifiers cannot make legacy narrative evidence look current.
    write_json(store.root / 'simulations' / sim_id / 'run_state.json', {'runner_status': 'completed'})
    write_json(path / 'meta.json', {'report_id': path.name, 'simulation_id': sim_id, 'status': 'completed'})
    current = store.workspace(identifier)
    _, operation = store.create_report(identifier, mutate(current))
    assert 'outdated narrative' not in store.get_report(operation['report_id'])['markdown']


def test_full_http_workflow_and_exports_without_credentials(store):
    app = Flask(__name__)
    app.register_blueprint(research_bp, url_prefix='/api/research')
    client = app.test_client()
    workspace = client.post('/api/research/events/demo').json['data']
    url = '/api/research/events/' + workspace['event']['event_id']
    for _ in range(6):
        response = client.post(url + '/replay-step', json=mutate(workspace))
        assert response.status_code == 200
        workspace = response.json['data']
    response = client.post(url + '/report', json=mutate(workspace))
    assert response.status_code == 200
    report = response.json['data']
    for format in ('md', 'html', 'json'):
        download = client.get(f"/api/research/evidence/{report['report_id']}/export?format={format}")
        assert download.status_code == 200
        assert 'attachment' in download.headers['Content-Disposition']
    assert client.get(url).json['data']['reports']
