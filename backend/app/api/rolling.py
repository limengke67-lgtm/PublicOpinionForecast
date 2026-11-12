"""Rolling prediction endpoints. Local calibration never initiates a paid call."""

import csv
import io
import json
import os
from pathlib import Path

from flask import Response, jsonify, request

from . import research_bp
from ..config import Config, credential_present
from ..research.intake import aggregate_posts
from ..research.rolling import RevisionConflict, RollingStore
from ..research.store import ResearchStore
from ..utils.llm_runtime import usage_summary


def rolling():
    return RollingStore(Config.UPLOAD_FOLDER)


@research_bp.errorhandler(RevisionConflict)
def conflict(error):
    return jsonify(success=False, error=str(error)), 409


@research_bp.route('/rolling', methods=['GET', 'POST'])
def sessions():
    service = rolling()
    return jsonify(success=True, data=service.create(request.get_json() or {}) if request.method == 'POST' else service.list())


@research_bp.route('/rolling/demo', methods=['POST'])
def rolling_demo():
    return jsonify(success=True, data=rolling().demo())


@research_bp.route('/rolling/<identifier>/replay-step', methods=['POST'])
def replay_step(identifier):
    rolling().replay_step(identifier, request.get_json() or {})
    return session_detail(identifier)


@research_bp.route('/rolling/<identifier>', methods=['GET'])
def session_detail(identifier):
    result = rolling().detail(identifier)
    scopes = list({version['reference_metadata'][key] for version in result['versions']
                   for key in ('runtime_scope_id', 'simulation_id', 'project_id') if version['reference_metadata'].get(key)})
    result['usage'] = usage_summary(scopes) if scopes else None
    return jsonify(success=True, data=result)


@research_bp.route('/rolling/<identifier>/observations', methods=['POST'])
def observations(identifier):
    service = rolling()
    service.ingest(identifier, request.get_json() or {})
    return session_detail(identifier)


@research_bp.route('/rolling/<identifier>/status', methods=['PUT'])
def status(identifier):
    rolling().set_status(identifier, request.get_json() or {})
    return session_detail(identifier)


@research_bp.route('/rolling/<identifier>/next-case', methods=['POST'])
def next_case(identifier):
    return jsonify(success=True, data=rolling().next_case(identifier, request.get_json() or {}))


@research_bp.route('/rolling/<identifier>/rebase', methods=['POST'])
def rebase(identifier):
    rolling().rebase(identifier, request.get_json() or {})
    return session_detail(identifier)


@research_bp.route('/rolling/<identifier>/publish', methods=['POST'])
def publish(identifier):
    return jsonify(success=True, data=rolling().publish(identifier, request.get_json() or {}))


@research_bp.route('/rolling/replay', methods=['POST'])
def replay():
    data = request.get_json() or {}
    return jsonify(success=True, data=rolling().replay(data.get('case_id'), data.get('forecast_id'), data.get('policy')))


@research_bp.route('/rolling/<identifier>/export', methods=['GET'])
def session_export(identifier):
    service = rolling()
    if request.args.get('format') == 'csv':
        session = service.get(identifier)
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=['timestamp', 'actual', 'predicted', 'base', 'candidate', 'last_value',
                                                   'moving_average', 'version', 'lead_buckets', 'prospective', 'received_at'])
        writer.writeheader()
        writer.writerows([row for epoch in session.get('archived_assessments', []) for row in epoch] + session['assessments'])
        return Response('\ufeff' + stream.getvalue(), mimetype='text/csv',
                        headers={'Content-Disposition': f'attachment; filename={identifier}.csv'})
    return Response(json.dumps(service.archive(identifier), ensure_ascii=False, separators=(',', ':'), allow_nan=False), mimetype='application/json',
                    headers={'Content-Disposition': f'attachment; filename={identifier}.json'})


@research_bp.route('/rolling/restore', methods=['POST'])
def restore():
    return jsonify(success=True, data=rolling().restore(request.get_json() or {}))


@research_bp.route('/intake/aggregate', methods=['POST'])
def aggregate():
    return jsonify(success=True, data=aggregate_posts(request.get_json() or {}))


@research_bp.route('/simulations/<identifier>/plan', methods=['GET'])
def simulation_plan(identifier):
    from ..research.store import safe_id, read_json
    from ..utils.llm_runtime import settings
    path = Path(Config.OASIS_SIMULATION_DATA_DIR) / safe_id(identifier) / 'simulation_config.json'
    if not path.exists():
        raise ValueError('请先完成该仿真实例的准备')
    config = read_json(path)
    policy = settings()
    return jsonify(success=True, data={
        'simulation_id': identifier, 'research_contract': config.get('research_contract'),
        'time_config': config['time_config'], 'agent_count': len(config.get('agent_configs', [])),
        'runtime_options': config.get('runtime_options', {}),
        'max_requests_per_scope': policy['max_requests_per_scope'], 'budget_cny_per_scope': policy['budget_cny_per_scope'],
        'note': '计划不调用模型；实际 API 次数取决于角色激活、决策和重试，未返回用量前不能据此推算真实费用。',
    })


@research_bp.route('/readiness', methods=['GET'])
def readiness():
    case_id = request.args.get('case_id')
    checks = [
        {'id': 'llm', 'label': '模型凭据已配置', 'passed': credential_present(Config.LLM_API_KEY), 'scope': 'simulation'},
        {'id': 'zep', 'label': 'Zep 凭据已配置', 'passed': credential_present(Config.ZEP_API_KEY), 'scope': 'simulation'},
        {'id': 'online_mode', 'label': '完整推演模式已启用', 'passed': os.environ.get('MIROFISH_RESEARCH_ONLY') != '1', 'scope': 'simulation'},
        {'id': 'storage', 'label': '本地存储目录可访问', 'passed': Path(Config.UPLOAD_FOLDER).exists(), 'scope': 'local'},
    ]
    if case_id:
        case = ResearchStore(Config.UPLOAD_FOLDER).case(case_id)
        source = case.get('source', {})
        checks.extend([
            {'id': 'real_data', 'label': '案例未标记为合成示例（真实性需核验）', 'passed': not case['is_demo'], 'scope': 'data'},
            {'id': 'source', 'label': '平台、来源及采样口径已填写',
             'passed': all(source.get(key) for key in ('platform', 'source_name', 'sampling_method')), 'scope': 'data'},
            {'id': 'early', 'label': '早期连续观测至少 3 桶', 'passed': len(case['train']) >= 3, 'scope': 'data'},
            {'id': 'background', 'label': '截止前背景已填写（主体与事实需核验）', 'passed': bool(case.get('background', '').strip()), 'scope': 'data'},
        ])
    return jsonify(success=True, data={'checks': checks,
                   'simulation_configured': all(check['passed'] for check in checks if check['scope'] == 'simulation'),
                   'note': '仅检查本地配置，不联网验证凭据、供应商额度或真实预测效果；数据真实性仍需核验。'})
