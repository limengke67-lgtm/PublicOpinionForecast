"""Deterministic, timestamped evidence reports; never ask an LLM to invent metrics."""

import html
from pathlib import Path

from .evaluation import digest
from .store import ResearchStore, read_json, safe_id, utc_now


def number(value):
    return '未定义 / 未知' if value is None else f'{value:,.4f}'


def cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def narrative_reports(root, simulations):
    allowed = {value['simulation_id']: value['run']['runtime_scope_id'] for value in simulations
               if (value.get('run') or {}).get('runtime_scope_id')}
    result = []
    for path in (Path(root) / 'reports').glob('report_*/meta.json'):
        value = read_json(path)
        if (value.get('status') != 'completed' or value.get('simulation_id') not in allowed or
                value.get('runtime_scope_id') != allowed[value['simulation_id']]):
            continue
        markdown = path.parent / 'full_report.md'
        text = markdown.read_text(encoding='utf-8') if markdown.exists() else value.get('markdown_content', '')
        result.append({'report_id': value['report_id'], 'simulation_id': value['simulation_id'],
                       'runtime_scope_id': value.get('runtime_scope_id'), 'markdown': text,
                       'created_at': value.get('created_at'), 'content_hash': digest(text)})
    return sorted(result, key=lambda item: item['created_at'] or '')


def build_event_report(workspace, root):
    event, case = workspace['event'], workspace['case']
    rolling, usage = workspace['rolling'], workspace['usage']
    narratives = narrative_reports(root, workspace['simulations'])
    lines = [f"# PublicOpinionForecast · {event['name']}", '', f"生成时间：{utc_now()}",
             f"事件：{event['event_id']}；观测截止：{case['cutoff']}；每桶 {case['bucket_hours']} 小时；未来 {case['horizon']} 桶。", '',
             '**合成流程演示，不代表真实预测能力。**' if event['is_demo'] else
             '**用户 / 历史观测研究报告；数据真实性、采样代表性和前瞻效果需独立验证。**', '',
             '## 数据来源与运行证据', '',
             f"来源：{case.get('source', {}).get('source_name') or '未填写'}；平台：{case.get('source', {}).get('platform') or '未填写'}。",
             f"采样口径：{case.get('source', {}).get('sampling_method') or '未填写'}", '',
             f"关联项目 {len(workspace['projects'])} 个；仿真实例 {len(workspace['simulations'])} 个。",
             '仿真未运行或未成功结束时，本报告不代表已完成多智能体在线预测。', '',
             '## 冻结预测与真实后续验证', '',
             '| 预测 | 方法 | 窗口是否完整 | MAE | RMSE | WAPE |',
             '| --- | --- | --- | ---: | ---: | ---: |']
    for evaluation in workspace['evaluations']:
        metric = evaluation.get('scores', {}).get('volume', {})
        lines.append('| ' + ' | '.join([cell(evaluation['forecast_id']), cell(evaluation['method']),
            '完整' if evaluation['complete'] else '待完整真实值 / 校验未通过', number(metric.get('mae')), number(metric.get('rmse')),
            number(metric['wape'] * 100) + '%' if metric.get('wape') is not None else '未定义']) + ' |')
        if not evaluation['complete']:
            lines.extend(['', f"待处理：{cell(evaluation.get('reason', ''))}", ''])
    if not workspace['evaluations']:
        lines.extend(['', '尚无冻结预测，不能计算预测误差。'])
    lines.extend(['', '## 滚动观测、自适应校准与同信息对照', ''])
    if rolling:
        summary = rolling['summary']
        lines.extend([f"接收模式：{rolling['mode']}；已评分 {summary['scored_buckets']} 桶；未覆盖 {summary['unscored_buckets']} 桶；前瞻时间顺序记录 {summary['prospective_buckets']} 桶。",
                      f"预测版本 {len(rolling['versions'])} 个；自适应更新 {summary['calibrated_versions']} 个；本地校准追加模型请求为 0。", '',
                      '| 方案 | MAE | RMSE | WAPE |', '| --- | ---: | ---: | ---: |'])
        for method, metric in summary['metrics'].items():
            lines.append(f"| {method} | {number(metric['mae'])} | {number(metric['rmse'])} | {number(metric['wape'] * 100) + '%' if metric['wape'] is not None else '未定义'} |")
        lines.extend(['', '上述方案按相同已知观测评分；自适应方案不保证优于简单基线。', '',
                      '| 版本 | 观测截止 | 决策 | 原因 |', '| --- | --- | --- | --- |'])
        for version in rolling['versions']:
            lines.append(f"| v{version['number']} | {version['cutoff']} | {version['calibration'].get('selected_method', 'base')} | {cell(version['reason'])} |")
    else:
        lines.append('尚未建立滚动预测任务。')
    lines.extend(['', '## 模型用量与成本', ''])
    if usage:
        totals = usage['totals']
        lines.extend([f"SDK 请求：{totals['requests']}；输入 / 输出 Token：{totals['input_tokens']} / {totals['output_tokens']}。",
                      f"估算模型费：{number(totals['estimated_cny'])} 元；完整费用未知请求：{totals['incomplete_cost_requests']}。",
                      f"请求时间窗：{number(totals['wall_time_ms'] / 1000)} 秒，不等同全部任务耗时。"])
    else:
        lines.append('没有关联模型账本记录；免费数值流程未调用模型，未运行的多智能体任务费用不能被伪装成实测零成本。')
    lines.extend(['', '金额按用户配置单价估算，不等于供应商账单；Zep 另计。失败或缺失 usage 的请求不按免费处理。', '',
                  '## 多智能体语义报告', ''])
    if narratives:
        for narrative in narratives:
            lines.extend([f"### {narrative['report_id']}", '', narrative['markdown'], ''])
    else:
        lines.append('当前没有与本次运行匹配的已完成语义报告；不会用旧运行报告或合成文字冒充。')
    lines.extend(['', '## 适用边界与复现', '',
                  'WAPE 是误差，不是准确率；常量峰值与零总量指标可能无定义。',
                  '相对热度映射不等于绝对传播量校准；本地数值更新不代表新一轮语义推演。',
                  '历史回放与前瞻预测分别记录；历史著名事件的 LLM 回测还存在预训练知识泄漏风险。',
                  '重试会创建独立图谱；不会声称已经恢复未保存的角色内部状态。',
                  f"证据指纹：{workspace['evidence_hash']}"])
    return {'event_id': event['event_id'], 'title': event['name'], 'created_at': utc_now(),
            'evidence_hash': workspace['evidence_hash'], 'is_demo': event['is_demo'], 'case': case,
            'evaluations': workspace['evaluations'], 'rolling_summary': rolling['summary'] if rolling else None,
            'usage': usage, 'narrative_reports': narratives, 'markdown': '\n'.join(lines)}


def report_html(report):
    return ('<!doctype html><html lang="zh"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>PublicOpinionForecast 综合报告</title><style>body{max-width:1080px;margin:36px auto;padding:0 24px;font:15px/1.9 system-ui;color:#26313c}'
            'pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}@media print{body{margin:0;font-size:11pt}}</style>'
            '<pre>' + html.escape(report['markdown']) + '</pre></html>')


def simulation_appendix(root, simulation_id):
    """Append real numeric evidence to an existing native LLM report, without a call."""
    try:
        safe_id(simulation_id)
    except ValueError:
        return ''
    root = Path(root)
    path = root / 'simulations' / simulation_id / 'state.json'
    if not path.exists():
        return ''
    state = read_json(path)
    project = root / 'projects' / safe_id(state['project_id']) / 'project.json'
    if not project.exists():
        return ''
    case_id = read_json(project).get('research_case_id')
    if not case_id:
        return ''
    research = ResearchStore(root)
    case = research.case(case_id)
    lines = ['\n\n## 定量研究证据附录', '', f"案例 {case_id}；截止 {case['cutoff']}；此附录由本地记录生成，没有新增模型调用。"]
    forecasts = [item for item in research.list_forecasts() if item['metadata'].get('simulation_id') == simulation_id]
    for frozen in forecasts:
        try:
            result = research.evaluate(frozen['forecast_id'])['scores']['volume']
            lines.append(f"预测 {frozen['forecast_id']}：MAE {number(result['mae'])}，RMSE {number(result['rmse'])}。")
        except ValueError:
            lines.append(f"预测 {frozen['forecast_id']}：真实后续窗口尚未齐备或记录未通过校验，暂不评分。")
    if not forecasts:
        lines.append('尚未冻结定量预测。事件综合报告将在冻结和收到真实结果后汇总误差、费用及滚动轨迹。')
    lines.append('本附录是生成时刻的证据快照；最新验证结果请从对应事件生成新的综合报告。')
    return '\n'.join(lines)
