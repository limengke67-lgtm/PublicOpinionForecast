from types import SimpleNamespace

from app.config import Config
from app.models.project import ProjectManager
from app.research.evaluation import digest
from app.research.provenance import bind_run_provenance
from app.research.store import ResearchStore, write_json


def test_first_early_graph_is_verified_but_reuse_is_exploratory(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, 'UPLOAD_FOLDER', str(tmp_path))
    service = ResearchStore(tmp_path)
    case = service.create_demo()
    source = service.seed_text(case['case_id'])
    project = SimpleNamespace(project_id='proj_fixture', research_case_id=case['case_id'],
                              research_input_hash=digest(source), research_used_graph_ids=[])
    monkeypatch.setattr(ProjectManager, 'get_project', classmethod(lambda cls, identifier: project))
    monkeypatch.setattr(ProjectManager, 'get_extracted_text', classmethod(lambda cls, identifier: source))
    monkeypatch.setattr(ProjectManager, 'save_project', classmethod(lambda cls, value: None))
    root, folder = tmp_path / 'simulations', tmp_path / 'simulations' / 'sim_fixture'
    folder.mkdir(parents=True)
    config = {'project_id': project.project_id, 'graph_id': 'graph_fixture'}
    write_json(folder / 'state.json', {'graph_id': 'graph_fixture'})
    write_json(folder / 'run_state.json', {'started_at': '2026-01-01'})
    first = bind_run_provenance(config, folder, root)
    assert first['pristine_graph'] is True
    assert first['train_hash'] == case['train_hash']
    assert bind_run_provenance(config, folder, root)['pristine_graph'] is False
    # A newly built graph is eligible again; changing material invalidates it.
    config['graph_id'] = 'graph_fresh'
    project.research_input_hash = 'changed'
    assert bind_run_provenance(config, folder, root)['pristine_graph'] is False
