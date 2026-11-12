"""Event-centred workflow: stable context, automatic associations and evidence."""

import threading
import uuid
from pathlib import Path

from .evaluation import digest
from .rolling import RevisionConflict, RollingStore
from .store import ResearchStore, read_json, safe_id, utc_now, write_json

_lock = threading.RLock()


class EventStore:
    def __init__(self, root):
        self.root = Path(root)
        self.directory = self.root / 'events'
        self.research = ResearchStore(root)
        self.rolling = RollingStore(root)

    def get(self, identifier):
        path = self.directory / f'{safe_id(identifier)}.json'
        if not path.exists():
            raise ValueError('事件任务不存在')
        event = read_json(path)
        claimed = event.pop('snapshot_hash', None)
        if claimed != digest(event):
            raise ValueError('事件任务指纹校验失败')
        return event | {'snapshot_hash': claimed}

    def _save(self, event):
        event.pop('snapshot_hash', None)
        event['snapshot_hash'] = digest(event)
        write_json(self.directory / f"{event['event_id']}.json", event)

    def list(self):
        return sorted((self.get(path.stem) for path in self.directory.glob('event_*.json')),
                      key=lambda value: value['created_at'], reverse=True)

    def create(self, data):
        if data.get('case_id'):
            case = self.research.case(data['case_id'])
        elif isinstance(data.get('case'), dict):
            case = self.research.case(self.research.create_case(data['case'])['case_id'])
        else:
            raise ValueError('请选择已有案例或提供新事件观测数据')
        name = str(data.get('name') or case['name']).strip()
        if not name or len(name) > 120:
            raise ValueError('事件名称须为 1～120 个字符')
        event = {'event_id': f'event_{uuid.uuid4().hex[:16]}', 'name': name, 'case_ids': [case['case_id']],
                 'active_case_id': case['case_id'], 'rolling_session_id': None, 'forecast_ids': [],
                 'report_ids': [], 'status': 'active', 'revision': 0, 'created_at': utc_now(),
                 'updated_at': utc_now(), 'is_demo': case['is_demo'], 'operations': {},
                 'timeline': [{'time': utc_now(), 'action': 'created', 'detail': '已建立事件任务和观测案例'}]}
        with _lock:
            self._save(event)
        return event

    def mutate(self, identifier, data, action, function):
        request_id, expected = data.get('request_id'), data.get('expected_revision')
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 80:
            raise ValueError('事件操作需要 8～80 位 request_id')
        body_hash = digest({key: value for key, value in data.items() if key != 'expected_revision'})
        with _lock:
            event = self.get(identifier)
            previous = event['operations'].get(request_id)
            if previous:
                if previous['body_hash'] != body_hash or previous['action'] != action:
                    raise RevisionConflict('同一 request_id 不能对应不同事件操作')
                return event, previous['result']
            if isinstance(expected, bool) or not isinstance(expected, int) or expected != event['revision']:
                raise RevisionConflict('事件已更新，请刷新后重试')
            if event['status'] == 'archived' and action != 'status':
                raise ValueError('事件已归档，请先恢复')
            if len(event['operations']) >= 3000:
                raise ValueError('事件操作达到上限，请备份后创建接续事件')
            result = function(event) or {}
            event['revision'] += 1
            event['updated_at'] = utc_now()
            event['operations'][request_id] = {'action': action, 'body_hash': body_hash, 'result': result}
            event['timeline'].append({'time': utc_now(), 'action': action, 'detail': result.get('message', action)})
            self._save(event)
            return event, result

    def associations(self, event):
        cases = set(event['case_ids'])
        projects = []
        for path in (self.root / 'projects').glob('proj_*/project.json'):
            project = read_json(path)
            if project.get('research_case_id') in cases:
                projects.append({key: project.get(key) for key in ('project_id', 'name', 'status', 'graph_id', 'research_case_id', 'created_at', 'error')})
        project_ids = {item['project_id'] for item in projects}
        simulations = []
        for path in (self.root / 'simulations').glob('sim_*/state.json'):
            state = read_json(path)
            if state.get('project_id') not in project_ids:
                continue
            run_path = path.parent / 'run_state.json'
            run = read_json(run_path) if run_path.exists() else None
            lifecycle = state.get('status', 'created')
            if run:
                lifecycle = run['runner_status']
                if lifecycle in {'running', 'starting', 'paused', 'stopping'}:
                    from ..services.simulation_runner import SimulationRunner
                    process = SimulationRunner._processes.get(state['simulation_id'])
                    if process is None or process.poll() is not None:
                        # Do not fabricate checkpoint recovery or silently restart paid work.
                        lifecycle = 'unmanaged'
            simulations.append({**state, 'run': run, 'lifecycle': lifecycle})
        forecasts = [item for item in self.research.list_forecasts() if item['case_id'] in cases or item['forecast_id'] in event['forecast_ids']]
        for values in (projects, simulations, forecasts):
            values.sort(key=lambda item: item.get('created_at', ''), reverse=True)
        return projects, simulations, forecasts

    def workspace(self, identifier):
        from ..config import Config, credential_present
        from ..utils.llm_runtime import usage_summary
        import os
        event = self.get(identifier)
        case = self.research.case(event['active_case_id'])
        projects, simulations, forecasts = self.associations(event)
        scopes = {item['project_id'] for item in projects}
        scopes.update(item['simulation_id'] for item in simulations)
        scopes.update(item['run']['runtime_scope_id'] for item in simulations if item.get('run') and item['run'].get('runtime_scope_id'))
        rolling = self.rolling.detail(event['rolling_session_id']) if event.get('rolling_session_id') else None
        evaluations = []
        for frozen in forecasts:
            try:
                result = self.research.evaluate(frozen['forecast_id'])
                evaluations.append({'forecast_id': frozen['forecast_id'], 'case_id': frozen['case_id'], 'method': frozen['method'],
                                    'variant': frozen['variant'], 'complete': True, 'scores': result['scores'],
                                    'snapshot_hash': frozen['snapshot_hash']})
            except ValueError as error:
                evaluations.append({'forecast_id': frozen['forecast_id'], 'case_id': frozen['case_id'], 'method': frozen['method'],
                                    'variant': frozen['variant'], 'complete': False, 'reason': str(error)})
        latest_project = next((item for item in projects if item['research_case_id'] == case['case_id']), None)
        latest_simulation = next((item for item in simulations if latest_project and item['project_id'] == latest_project['project_id']), None)
        model_ready = (os.environ.get('MIROFISH_RESEARCH_ONLY') != '1' and credential_present(Config.LLM_API_KEY) and credential_present(Config.ZEP_API_KEY))
        reports = [self.get_report(report_id) for report_id in event['report_ids']]
        from .evidence_report import narrative_reports
        narratives = narrative_reports(self.root, simulations)
        usage = usage_summary(sorted(scopes)) if scopes else None
        if rolling and self.rolling.get(rolling['session_id'])['session_hash'] != rolling['session_hash']:
            raise RevisionConflict('观测正在更新，请刷新后重试以获得一致证据快照')
        current_evidence = digest({'case_id': case['case_id'], 'forecasts': [item['snapshot_hash'] for item in forecasts],
                                   'rolling_hash': rolling.get('session_hash') if rolling else None,
                                   'evaluation_hash': digest(evaluations), 'source': case.get('source', {}),
                                   'narratives': [item['content_hash'] for item in narratives],
                                   'usage_hash': digest(usage['totals']) if usage else None,
                                   'runs': [(item['simulation_id'], item['lifecycle'], (item.get('run') or {}).get('runtime_scope_id')) for item in simulations]})
        return {'event': event, 'case': self.research.public_case(case), 'projects': projects, 'simulations': simulations,
                'forecasts': forecasts, 'evaluations': evaluations, 'rolling': rolling, 'usage': usage,
                'latest_project': latest_project, 'latest_simulation': latest_simulation, 'model_ready': model_ready,
                'reports': [{key: report[key] for key in ('report_id', 'created_at', 'snapshot_hash', 'evidence_hash')} |
                            {'stale': report['evidence_hash'] != current_evidence} for report in reports],
                'evidence_hash': current_evidence,
                'stages': [
                    {'name': '数据与材料', 'status': 'completed'},
                    {'name': '图谱与推演', 'status': 'completed' if latest_simulation and latest_simulation['lifecycle'] in {'completed', 'stopped'} else
                     'attention' if latest_simulation and latest_simulation['lifecycle'] in {'failed', 'unmanaged'} else 'pending'},
                    {'name': '冻结与验证', 'status': 'completed' if forecasts else 'pending'},
                    {'name': '观测与纠偏', 'status': 'completed' if rolling and rolling['summary']['scored_buckets'] else 'pending'},
                    {'name': '综合报告', 'status': 'completed' if reports and reports[-1]['evidence_hash'] == current_evidence else 'pending'},
                ]}

    def freeze(self, identifier, data):
        def apply(event):
            case_id = event['active_case_id']
            method = data.get('method', 'simulation')
            if method not in {'simulation', 'moving_average', 'last_value'}:
                raise ValueError('请选择实际仿真或明确标注的免费基线')
            session = self.rolling.get(event['rolling_session_id']) if event.get('rolling_session_id') else None
            if session:
                self.rolling.validate_rebase_case(session, self.research.case(case_id))
            body = {'method': method, 'variant': '事件初始预测' if method == 'simulation' else '免费数值基线（非多智能体）'}
            if method == 'simulation':
                _, simulations, _ = self.associations(event)
                chosen = data.get('simulation_id')
                selected = next((item for item in simulations if item['simulation_id'] == chosen), None) if chosen else next(
                    (item for item in simulations if item.get('run') and item['run']['runner_status'] in {'completed', 'stopped'} and
                     read_json(self.root / 'projects' / item['project_id'] / 'project.json').get('research_case_id') == case_id), None)
                if not selected:
                    raise ValueError('当前事件没有可以冻结的已完成仿真')
                project_case = read_json(self.root / 'projects' / selected['project_id'] / 'project.json').get('research_case_id')
                if project_case != case_id:
                    raise ValueError('仿真不属于当前观测窗口')
                body.update(simulation_id=selected['simulation_id'], platform=data.get('platform', 'reddit'), scale_mode=data.get('scale_mode', 'history_anchor'))
            frozen = self.research.freeze(case_id, body)
            if session:
                self.rolling.rebase(session['session_id'], {'forecast_id': frozen['forecast_id'], 'expected_revision': session['revision'],
                                                          'request_id': 'event_rebase_' + data['request_id'][:60]})
            else:
                session = self.rolling.create({'case_id': case_id, 'forecast_id': frozen['forecast_id'], 'mode': data.get('mode', 'replay')})
                event['rolling_session_id'] = session['session_id']
            event['forecast_ids'].append(frozen['forecast_id'])
            return {'forecast_id': frozen['forecast_id'], 'rolling_session_id': event['rolling_session_id'], 'message': '预测已冻结，并自动关联滚动任务'}
        return self.mutate(identifier, data, 'freeze', apply)

    def refresh_case(self, identifier, data):
        def apply(event):
            if event.get('rolling_session_id'):
                session = self.rolling.get(event['rolling_session_id'])
                result = self.rolling.next_case(session['session_id'], {'expected_revision': session['revision']})
                case_id = result['case']['case_id']
            else:
                case_id = event['active_case_id']
                original = self.research.case(case_id)
                case_id = self.research.create_case({'name': original['name'] + ' / 独立重试', 'rows': original['train'],
                                                     'cutoff': original['cutoff'], 'bucket_hours': original['bucket_hours'], 'horizon': original['horizon'],
                                                     'background': original.get('background', ''), 'source': original.get('source', {})}, demo=original['is_demo'])['case_id']
            event['active_case_id'] = case_id
            event['case_ids'].append(case_id)
            return {'case_id': case_id, 'message': '已建立独立观测窗口；旧项目与运行记录保留，下一步新建图谱'}
        return self.mutate(identifier, data, 'refresh_case', apply)

    def ingest(self, identifier, data):
        def apply(event):
            if not event.get('rolling_session_id'):
                raise ValueError('请先冻结初始预测')
            session = self.rolling.get(event['rolling_session_id'])
            body = {key: value for key, value in data.items() if key not in {'expected_revision'}}
            body['expected_revision'] = session['revision']
            updated = self.rolling.ingest(session['session_id'], body)
            return {'message': '真实观测已接收，旧预测评分与下一版本已完成', 'rolling_revision': updated['revision']}
        return self.mutate(identifier, data, 'observations', apply)

    def replay_step(self, identifier, data):
        def apply(event):
            if not event.get('rolling_session_id'):
                raise ValueError('请先冻结初始预测')
            session = self.rolling.get(event['rolling_session_id'])
            from datetime import timedelta
            from .evaluation import timestamp
            target = (timestamp(session['observations'][-1]['timestamp']) + timedelta(hours=session['bucket_hours'])).isoformat()
            updated = self.rolling.replay_step(session['session_id'], {'expected_revision': session['revision'],
                        'request_id': 'event_step_' + data['request_id'][:60], 'timestamp': target})
            return {'message': '已按顺序释放一桶历史观测', 'rolling_revision': updated['revision']}
        return self.mutate(identifier, data, 'replay_step', apply)

    def get_report(self, identifier):
        path = self.root / 'event_reports' / f'{safe_id(identifier)}.json'
        if not path.exists():
            raise ValueError('综合报告不存在')
        report = read_json(path)
        content = {key: value for key, value in report.items() if key != 'snapshot_hash'}
        if report['snapshot_hash'] != digest(content):
            raise ValueError('综合报告指纹校验失败')
        return report

    def create_report(self, identifier, data):
        def apply(event):
            from .evidence_report import build_event_report
            workspace = self.workspace(identifier)
            report = build_event_report(workspace, self.root)
            report['report_id'] = f'evidence_{uuid.uuid4().hex[:16]}'
            report['snapshot_hash'] = digest(report)
            write_json(self.root / 'event_reports' / f"{report['report_id']}.json", report)
            event['report_ids'].append(report['report_id'])
            return {'report_id': report['report_id'], 'message': '综合报告已冻结，包含预测、误差、成本、校准和证据边界'}
        return self.mutate(identifier, data, 'report', apply)

    def demo(self):
        case = self.research.create_demo()
        event = self.create({'case_id': case['case_id'], 'name': '完整事件闭环演示（合成数据）'})
        self.freeze(event['event_id'], {'method': 'moving_average', 'mode': 'replay', 'request_id': 'initial_demo_freeze', 'expected_revision': 0})
        return self.workspace(event['event_id'])
