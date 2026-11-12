"""Full native HTTP orchestration using explicit model/Zep/OASIS service doubles.

This is an integration test, not a live provider or prediction-quality result.
"""

import io
import json
from types import SimpleNamespace

import httpx
from flask import Flask

from app.api import graph_bp, simulation_bp, report_bp, research_bp
from app.api import graph as graph_api, report as report_api
from app.config import Config
from app.models.project import ProjectManager
from app.research.store import ResearchStore, write_json
from app.research.simulation_contract import bind_window
from app.services.simulation_manager import SimulationManager, SimulationStatus
from app.services.simulation_runner import SimulationRunner, SimulationRunState, RunnerStatus
from app.services.report_agent import Report, ReportManager, ReportStatus


def test_event_to_native_graph_run_freeze_observe_report_without_network(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError('No real provider request is permitted in this test')
    monkeypatch.setattr(httpx.Client, 'send', no_network)
    monkeypatch.setenv('MIROFISH_RUNTIME_DIR', str(tmp_path / 'runtime'))
    monkeypatch.setattr(Config, 'UPLOAD_FOLDER', str(tmp_path))
    monkeypatch.setattr(Config, 'OASIS_SIMULATION_DATA_DIR', str(tmp_path / 'simulations'))
    monkeypatch.setattr(Config, 'ZEP_API_KEY', 'test-placeholder-only')
    monkeypatch.setattr(ProjectManager, 'PROJECTS_DIR', str(tmp_path / 'projects'))
    monkeypatch.setattr(SimulationManager, 'SIMULATION_DATA_DIR', str(tmp_path / 'simulations'))
    monkeypatch.setattr(SimulationRunner, 'RUN_STATE_DIR', str(tmp_path / 'simulations'))
    monkeypatch.setattr(ReportManager, 'REPORTS_DIR', str(tmp_path / 'reports'))

    workers = []
    class QueuedThread:
        def __init__(self, *, target, daemon): self.target = target
        def start(self): workers.append(self.target)
    def drain():
        while workers:
            workers.pop(0)()
    monkeypatch.setattr(graph_api.threading, 'Thread', QueuedThread)

    class Ontology:
        def generate(self, **kwargs):
            assert all('"timestamp"' in document for document in kwargs['document_texts'])
            return {'entity_types': [{'name': 'Person', 'description': 'fixture', 'attributes': []}], 'edge_types': []}
    monkeypatch.setattr(graph_api, 'OntologyGenerator', Ontology)

    class Graph:
        def __init__(self, **kwargs): pass
        def validate_batch_chunks(self, *args, **kwargs): pass
        def create_graph(self, *, graph_id_callback, **kwargs):
            graph_id_callback('graph_fixture'); return 'graph_fixture'
        def set_ontology(self, *args): pass
        def add_text_batches(self, graph_id, chunks, **kwargs):
            kwargs['batch_created_callback']('batch_fixture', 'operation_fixture')
            return SimpleNamespace(batch_id='batch_fixture', operation_id='operation_fixture', episode_uuids=[], item_count=len(chunks))
        def wait_for_processing(self, *args, **kwargs): pass
        def _wait_for_batch(self, *args, **kwargs): return None
        def get_graph_data(self, *args): return {'node_count': 2, 'edge_count': 1, 'nodes': [], 'edges': []}
    monkeypatch.setattr(graph_api, 'GraphBuilderService', Graph)

    def prepare(manager, simulation_id, **kwargs):
        state = manager.get_simulation(simulation_id)
        project = ProjectManager.get_project(state.project_id)
        case = ResearchStore(tmp_path).case(project.research_case_id)
        config = bind_window({'project_id': state.project_id, 'time_config': {'minutes_per_round': 60}}, case)
        config['research_provenance'] = {'case_id': case['case_id'], 'train_hash': case['train_hash'], 'pristine_graph': True}
        write_json(tmp_path / 'simulations' / simulation_id / 'simulation_config.json', config)
        state.status = SimulationStatus.READY
        state.entities_count = state.profiles_count = 2
        state.profiles_generated = state.config_generated = True
        manager._save_simulation_state(state)
        return state
    monkeypatch.setattr(SimulationManager, 'prepare_simulation', prepare)

    def start(cls, simulation_id, **kwargs):
        folder = tmp_path / 'simulations' / simulation_id
        (folder / 'reddit').mkdir()
        (folder / 'reddit' / 'actions.jsonl').write_text('\n'.join(json.dumps({'round': n, 'action_type': 'CREATE_POST'}) for n in range(1, 7)), encoding='utf-8')
        state = SimulationRunState(simulation_id, runner_status=RunnerStatus.COMPLETED, total_rounds=6,
                                   reddit_current_round=6, runtime_scope_id='run_fixture1234')
        write_json(folder / 'run_state.json', state.to_detail_dict())
        return state
    monkeypatch.setattr(SimulationRunner, 'start_simulation', classmethod(start))

    class Agent:
        def __init__(self, simulation_id, graph_id, simulation_requirement):
            self.simulation_id, self.graph_id, self.requirement = simulation_id, graph_id, simulation_requirement
        def generate_report(self, report_id, **kwargs):
            return Report(report_id, self.simulation_id, self.graph_id, self.requirement, ReportStatus.COMPLETED,
                          markdown_content='Service-double narrative, not a real model result.', runtime_scope_id='run_fixture1234')
    monkeypatch.setattr(report_api, 'ReportAgent', Agent)

    app = Flask(__name__)
    for blueprint, prefix in ((research_bp, '/api/research'), (graph_bp, '/api/graph'), (simulation_bp, '/api/simulation'), (report_bp, '/api/report')):
        app.register_blueprint(blueprint, url_prefix=prefix)
    client = app.test_client()
    case = client.post('/api/research/demo').json['data']
    event = client.post('/api/research/events', json={'case_id': case['case_id']}).json['data']
    seed = client.get(f"/api/research/cases/{case['case_id']}/seed").json['data']
    response = client.post('/api/graph/ontology/generate', data={
        'files': (io.BytesIO(seed['text'].encode()), 'early.txt'), 'project_name': 'event fixture',
        'research_case_id': case['case_id'], 'simulation_requirement': seed['requirement']})
    assert response.status_code == 200, response.json
    project_id = response.json['data']['project_id']
    response = client.post('/api/graph/build', json={'project_id': project_id})
    assert response.status_code == 200, response.json
    drain()
    assert ProjectManager.get_project(project_id).status.value == 'graph_completed'
    response = client.post('/api/simulation/create', json={'project_id': project_id, 'enable_twitter': False})
    assert response.status_code == 200, response.json
    sim_id = response.json['data']['simulation_id']
    assert client.post('/api/simulation/prepare', json={'simulation_id': sim_id}).status_code == 200
    drain()
    response = client.post('/api/simulation/start', json={'simulation_id': sim_id, 'platform': 'reddit'})
    assert response.status_code == 200, response.json
    response = client.post('/api/report/generate', json={'simulation_id': sim_id})
    assert response.status_code == 200, response.json
    drain()
    url = '/api/research/events/' + event['event_id']
    workspace = client.get(url).json['data']
    assert workspace['latest_simulation']['simulation_id'] == sim_id
    response = client.post(url + '/freeze', json={'expected_revision': 0, 'request_id': 'freeze_fixture', 'method': 'simulation'})
    assert response.status_code == 200, response.json
    for index in range(6):
        response = client.post(url + '/replay-step', json={'expected_revision': index + 1, 'request_id': f'fixture_step_{index}'})
        assert response.status_code == 200, response.json
    workspace = response.json['data']
    response = client.post(url + '/report', json={'expected_revision': workspace['event']['revision'], 'request_id': 'report_fixture'})
    assert response.status_code == 200, response.json
    assert response.json['data']['rolling_summary']['scored_buckets'] == 6
    assert 'Service-double narrative' in response.json['data']['markdown']
