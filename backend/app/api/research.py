"""Local research workbench APIs; evaluation never calls a model."""

import csv
import io
import json
import os
from flask import Response, jsonify, request

from . import research_bp
from ..config import Config
from ..research.store import ResearchStore
from ..research.store import safe_id, read_json, write_json
from ..utils.llm_runtime import save_settings, settings, usage_summary


def store():
    return ResearchStore(Config.UPLOAD_FOLDER)


@research_bp.before_request
def require_object_body():
    if request.method in {'POST', 'PUT'} and request.is_json and not isinstance(request.get_json(silent=True), dict):
        return jsonify(success=False, error='请求体须为有效的 JSON 对象'), 400


@research_bp.errorhandler(ValueError)
@research_bp.errorhandler(KeyError)
def invalid(error):
    return jsonify(success=False, error=str(error)), 400


@research_bp.route('/cases', methods=['GET', 'POST'])
def cases():
    data = store().create_case(request.get_json() or {}) if request.method == 'POST' else store().list_cases()
    return jsonify(success=True, data=data)


@research_bp.route('/demo', methods=['POST'])
def demo():
    return jsonify(success=True, data=store().create_demo())


@research_bp.route('/cases/<case_id>', methods=['GET'])
def case_detail(case_id):
    service = store()
    return jsonify(success=True, data=service.public_case(service.case(case_id)))


@research_bp.route('/cases/<case_id>/truth', methods=['POST'])
def truth(case_id):
    return jsonify(success=True, data=store().add_truth(case_id, request.get_json() or {}))


@research_bp.route('/cases/<case_id>/seed', methods=['GET'])
def seed(case_id):
    service = store()
    return jsonify(success=True, data={"text": service.seed_text(case_id), "requirement": service.requirement(case_id)})


@research_bp.route('/cases/<case_id>/forecasts', methods=['GET', 'POST'])
def forecasts(case_id):
    service = store()
    service.case(case_id)
    data = service.freeze(case_id, request.get_json() or {}) if request.method == 'POST' else service.list_forecasts(case_id)
    return jsonify(success=True, data=data)


@research_bp.route('/forecasts/<forecast_id>/evaluate', methods=['POST'])
def evaluate(forecast_id):
    return jsonify(success=True, data=evaluation_result(forecast_id))


def evaluation_result(forecast_id):
    result = store().evaluate(forecast_id)
    metadata = result['forecast']['metadata']
    scopes = [metadata.get(key) for key in ('runtime_scope_id', 'simulation_id', 'project_id') if metadata.get(key)]
    result['usage'] = usage_summary(scopes) if scopes else None
    return result


@research_bp.route('/forecasts/<forecast_id>/export', methods=['GET'])
def export(forecast_id):
    service = store()
    frozen = service.forecast(forecast_id)
    if request.args.get('format') == 'csv':
        result = service.evaluate(forecast_id)
        stream = io.StringIO()
        writer = csv.writer(stream)
        writer.writerow(['timestamp', 'actual_volume', 'predicted_volume'])
        for actual, prediction in zip(result['scores']['truth'], frozen['prediction']):
            writer.writerow([actual['timestamp'], actual['volume'], prediction['volume']])
        return Response('\ufeff' + stream.getvalue(), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename={forecast_id}.csv'})
    return Response(json.dumps(evaluation_result(forecast_id), ensure_ascii=False, indent=2, allow_nan=False), mimetype='application/json',
                    headers={'Content-Disposition': f'attachment; filename={forecast_id}.json'})


@research_bp.route('/usage', methods=['GET'])
def usage():
    return jsonify(success=True, data=usage_summary(request.args.get('scope_id') or None))


@research_bp.route('/settings', methods=['GET', 'PUT'])
def runtime_settings():
    result = save_settings(request.get_json() or {}) if request.method == 'PUT' else settings()
    return jsonify(success=True, data={**result, 'research_only': os.environ.get('MIROFISH_RESEARCH_ONLY') == '1'})


@research_bp.route('/simulations/<simulation_id>/options', methods=['PUT'])
def simulation_options(simulation_id):
    from pathlib import Path
    from ..services.simulation_runner import SimulationRunner
    folder = Path(Config.OASIS_SIMULATION_DATA_DIR) / safe_id(simulation_id)
    config_path = folder / 'simulation_config.json'
    data = request.get_json() or {}
    if data.get('mode') not in {'baseline', 'efficient'}:
        raise ValueError('请选择 baseline 或 efficient')
    for key, lower, upper in (('random_seed', 0, 4294967295), ('llm_concurrency', 1, 128)):
        value = data.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or not lower <= value <= upper:
            raise ValueError(f'{key} 必须是 {lower}～{upper} 的整数')
    with SimulationRunner._finalization_lock(simulation_id):
        if (folder / 'run_state.json').exists():
            raise ValueError('已运行实例的配置已固定；请新建仿真进行不同配置的实验')
        if not config_path.exists():
            raise ValueError('请先完成该仿真实例的环境准备')
        config = read_json(config_path)
        config['runtime_options'] = {key: data[key] for key in ('mode', 'random_seed', 'llm_concurrency')}
        write_json(config_path, config)
    return jsonify(success=True, data=config['runtime_options'])


@research_bp.route('/compare', methods=['POST'])
def compare():
    data, service = request.get_json() or {}, store()
    ids = data.get('forecast_ids')
    if not isinstance(ids, list) or not 2 <= len(ids) <= 20 or len(set(ids)) != len(ids):
        raise ValueError('选择 2～20 个不同的冻结预测进行对比')
    results = [service.evaluate(identifier) for identifier in ids]
    if len({item['case']['case_id'] for item in results}) != 1:
        raise ValueError('对比必须使用同一案例、同一截止时间和预测窗口')
    rows = []
    for result in results:
        frozen, metrics = result['forecast'], result['scores']['volume']
        metadata = frozen['metadata']
        scopes = [metadata.get(key) for key in ('runtime_scope_id', 'simulation_id', 'project_id') if metadata.get(key)]
        rows.append({'forecast_id': frozen['forecast_id'], 'variant': frozen['variant'], 'method': frozen['method'],
                     'metrics': metrics, 'usage': usage_summary(scopes)['totals'] if scopes else None,
                     'is_demo': frozen['is_demo'], 'early_input_verified': metadata.get('early_input_verified', False)})
    return jsonify(success=True, data={'case': results[0]['case'], 'rows': rows,
                                      'note': '单案例对比，不代表统计显著改进；须保持初始化、模型和采样口径一致并重复实验。'})
