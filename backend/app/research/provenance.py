"""Record whether a simulation started with only the case's early input graph."""

from pathlib import Path

from .evaluation import digest
from .store import read_json


def bind_run_provenance(config, sim_dir, simulations_root):
    from ..utils.zep_lifecycle import graph_lifecycle_lock
    with graph_lifecycle_lock(config.get('graph_id', 'unbound')):
        return _bind_run_provenance(config, sim_dir, simulations_root)


def _bind_run_provenance(config, sim_dir, simulations_root):
    from ..models.project import ProjectManager
    project = ProjectManager.get_project(config.get('project_id', ''))
    if project is None or not project.research_case_id:
        return {'pristine_graph': False, 'reason': '未绑定验证案例的早期材料'}
    from ..config import Config
    from .store import ResearchStore
    case = ResearchStore(Config.UPLOAD_FOLDER).case(project.research_case_id)
    pristine = config.get('graph_id') not in project.research_used_graph_ids
    for folder in Path(simulations_root).iterdir():
        state_path, run_path = folder / 'state.json', folder / 'run_state.json'
        if not state_path.exists() or not run_path.exists():
            continue
        state, run = read_json(state_path), read_json(run_path)
        if state.get('graph_id') == config.get('graph_id') and run.get('started_at'):
            # The new current run was just claimed; an older config scope marks a rerun.
            if folder != Path(sim_dir) or config.get('runtime_scope_id'):
                pristine = False
    source = ProjectManager.get_extracted_text(project.project_id)
    unchanged = bool(source and project.research_input_hash == digest(source))
    if config.get('graph_id') not in project.research_used_graph_ids:
        project.research_used_graph_ids.append(config.get('graph_id'))
        ProjectManager.save_project(project)
    return {'case_id': case['case_id'], 'train_hash': case['train_hash'],
            'pristine_graph': pristine and unchanged, 'input_hash': project.research_input_hash,
            'note': '仅验证系统提供的早期数据和本地图谱运行记录；原始材料时效性仍需人工核验。'}
