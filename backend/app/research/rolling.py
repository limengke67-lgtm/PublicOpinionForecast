"""Durable observation -> assessment -> calibration -> immutable forecast loop."""

from __future__ import annotations

import json
import copy
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from .calibration import calibration_state, correct_curve, validate_policy
from .evaluation import baseline, curve_metrics, digest, normalize_rows, parse_rows, timestamp
from .store import ResearchStore, safe_id, utc_now, write_json
from .intake import source_metadata

_init_lock = threading.Lock()
MAX_VERSIONS = 1000


class RevisionConflict(ValueError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))


class RollingStore:
    def __init__(self, root):
        self.root = Path(root)
        self.research = ResearchStore(root)
        self.path = self.root / 'research' / 'rolling.sqlite3'

    @contextmanager
    def database(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with _init_lock:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        connection.execute('PRAGMA journal_mode=WAL')
                        break
                    except sqlite3.OperationalError as error:
                        if 'locked' not in str(error).lower() or time.monotonic() > deadline:
                            raise
                        time.sleep(.05)
                connection.executescript('''
                    CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, revision INTEGER, payload TEXT);
                    CREATE TABLE IF NOT EXISTS operations(session_id TEXT, request_id TEXT, body_hash TEXT,
                        revision INTEGER, PRIMARY KEY(session_id, request_id));
                ''')
            with connection:
                yield connection
        finally:
            connection.close()

    def _verify(self, session):
        if session.get('session_hash') != digest({key: value for key, value in session.items() if key != 'session_hash'}):
            raise ValueError('滚动任务内容指纹校验失败')
        previous = None
        for index, version in enumerate(session['versions']):
            content = {key: value for key, value in version.items() if key != 'snapshot_hash'}
            if (version.get('snapshot_hash') != digest(content) or version['number'] != index or
                    version['previous_hash'] != previous):
                raise ValueError('滚动预测版本链校验失败，拒绝读取被修改的记录')
            previous = version['snapshot_hash']
        if not session['versions'] or digest(session['observations']) != session['versions'][-1]['observations_hash']:
            raise ValueError('观测数据与最新冻结版本指纹不一致')
        return session

    def get(self, identifier):
        with self.database() as db:
            row = db.execute('SELECT payload FROM sessions WHERE id=?', (safe_id(identifier),)).fetchone()
        if not row:
            raise ValueError('滚动预测任务不存在')
        return self._verify(json.loads(row['payload']))

    def list(self):
        with self.database() as db:
            rows = db.execute('SELECT payload FROM sessions ORDER BY rowid DESC').fetchall()
        return [{key: session[key] for key in ('session_id', 'name', 'case_id', 'status', 'revision', 'mode', 'is_demo', 'created_at')}
                | {'cutoff': session['versions'][-1]['cutoff'], 'version_count': len(session['versions'])}
                for row in rows for session in [self._verify(json.loads(row['payload']))]]

    def create(self, data):
        case = self.research.case(data.get('case_id'))
        frozen = self.research.forecast(data.get('forecast_id'))
        if frozen['case_id'] != case['case_id'] or frozen['train_hash'] != case['train_hash']:
            raise ValueError('起始预测与案例早期数据不匹配')
        mode = data.get('mode', 'replay')
        if mode not in {'live', 'replay'}:
            raise ValueError('模式须为 live（实际接收）或 replay（历史回放）')
        if mode == 'live' and any(timestamp(row['timestamp']) > timestamp(utc_now()) for row in case['train']):
            raise ValueError('实际接收模式不能使用尚未结束的观测桶')
        session = {'session_id': f'rolling_{uuid.uuid4().hex[:16]}', 'case_id': case['case_id'],
                   'name': str(data.get('name') or case['name'])[:120], 'created_at': utc_now(), 'status': 'active',
                   'mode': mode, 'is_demo': case['is_demo'], 'revision': 0, 'bucket_hours': case['bucket_hours'],
                   'horizon': case['horizon'], 'policy': validate_policy(data.get('policy', {})),
                   'algorithm_version': 'bounded-residual-expert-v1', 'replay_end': case['expected'][-1],
                   'source': case.get('source', {}), 'background': case.get('background', ''),
                   'initial_forecast_id': frozen['forecast_id'], 'reference_forecast_id': frozen['forecast_id'],
                   'initial_forecast_hash': frozen['snapshot_hash'], 'reference': frozen['prediction'],
                   'reference_metadata': frozen['metadata'], 'observations': list(case['train']),
                   'assessments': [], 'unscored': [], 'versions': [], 'source_updates': [], 'context_updates': []}
        self._version(session, initial=frozen['prediction'])
        self._seal(session)
        with self.database() as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (session['session_id'], 0, encode(session)))
        return session

    @staticmethod
    def _seal(session):
        session['session_hash'] = digest({key: value for key, value in session.items() if key != 'session_hash'})
        if len(encode(session).encode('utf-8')) > 8 * 1024 * 1024:
            raise ValueError('任务记录超过 8 MB，请导出备份并新建接续任务')

    def _base_curve(self, session, expected):
        reference = {row['timestamp']: row for row in session['reference']}
        last = session['reference'][-1]['volume']
        return [dict(timestamp=value, volume=reference[value]['volume'] if value in reference else last) for value in expected]

    def _version(self, session, initial=None, reason=None):
        cutoff = session['observations'][-1]['timestamp']
        step = timedelta(hours=session['bucket_hours'])
        expected = [(timestamp(cutoff) + step * index).isoformat() for index in range(1, session['horizon'] + 1)]
        base = initial if initial is not None else self._base_curve(session, expected)
        state = calibration_state(session['assessments'], session['policy'])
        candidate = correct_curve(base, state, session['policy'])
        last_value = baseline(session['observations'], expected, 'last_value')
        moving_average = baseline(session['observations'], expected, 'moving_average')
        selected = {'base': base, 'candidate': candidate, 'last_value': last_value, 'moving_average': moving_average}[state['selected_method']]
        version = {'number': len(session['versions']), 'issued_at': utc_now(), 'cutoff': cutoff,
                   'previous_hash': session['versions'][-1]['snapshot_hash'] if session['versions'] else None,
                   'observations_hash': digest(session['observations']), 'observed_buckets': len(session['observations']),
                   'base': base, 'candidate': candidate, 'prediction': selected,
                   'last_value': last_value, 'moving_average': moving_average,
                   'calibration': state, 'reason': reason or state['reason'],
                   'reference_forecast_id': session['reference_forecast_id'],
                   'reference_metadata': session['reference_metadata'],
                   'tail_extrapolated_buckets': sum(value > session['reference'][-1]['timestamp'] for value in expected),
                   'additional_api_requests': 0}
        version['snapshot_hash'] = digest(version)
        session['versions'].append(version)
        return version

    def _mutate(self, identifier, data, function):
        request_id = data.get('request_id')
        expected = data.get('expected_revision')
        if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}', request_id):
            raise ValueError('请提供 8～80 位幂等 request_id')
        if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
            raise ValueError('请提供当前 expected_revision')
        body_hash = digest({key: value for key, value in data.items() if key != 'expected_revision'})
        with self.database() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT payload FROM sessions WHERE id=?', (safe_id(identifier),)).fetchone()
            if not row:
                raise ValueError('滚动预测任务不存在')
            session = self._verify(json.loads(row['payload']))
            operation = db.execute('SELECT * FROM operations WHERE session_id=? AND request_id=?', (identifier, request_id)).fetchone()
            if operation:
                if operation['body_hash'] != body_hash:
                    raise RevisionConflict('同一 request_id 不能对应不同内容')
                return session | {'replayed_request': True}
            if session['revision'] != expected:
                raise RevisionConflict('数据已由其他操作更新，请刷新后重试')
            function(session)
            session['revision'] += 1
            self._seal(session)
            db.execute('UPDATE sessions SET revision=?,payload=? WHERE id=?', (session['revision'], encode(session), identifier))
            db.execute('INSERT INTO operations VALUES(?,?,?,?)', (identifier, request_id, body_hash, session['revision']))
        return session

    def ingest(self, identifier, data):
        incoming = parse_rows(data['content'], data.get('format', 'csv')) if 'content' in data else normalize_rows(data.get('rows'))
        if len(incoming) > 500:
            raise ValueError('单次最多接收 500 个观测桶')
        note = str(data.get('source_note', '')).strip()[:2000]
        def apply(session):
            if session['status'] != 'active':
                raise ValueError('任务已暂停，请先恢复')
            if len(session['versions']) >= MAX_VERSIONS or len(session['observations']) + len(incoming) > 10000:
                raise ValueError('任务记录达到上限，请导出并新建任务')
            existing = {row['timestamp']: row for row in session['observations']}
            new = []
            for row in incoming:
                if row['timestamp'] in existing:
                    if row != existing[row['timestamp']]:
                        raise ValueError('已接收的观测值不可覆盖；请创建修订案例')
                else:
                    new.append(row)
            if not new:
                raise ValueError('没有新的观测桶；已有观测保持不变')
            step = timedelta(hours=session['bucket_hours'])
            previous = timestamp(session['observations'][-1]['timestamp'])
            for row in new:
                if timestamp(row['timestamp']) != previous + step:
                    raise ValueError('新观测必须紧接当前截止时间且连续，缺失桶不能当作零')
                previous = timestamp(row['timestamp'])
            if session['mode'] == 'live' and previous > timestamp(utc_now()):
                raise ValueError('实际接收模式不能提交尚未结束的时间桶')
            received = utc_now()
            context_text = data.get('context_text', '')
            if not isinstance(context_text, str) or len(context_text) > 5000:
                raise ValueError('本次新增背景须为不超过 5000 字的文本')
            context_text = context_text.strip()
            if context_text:
                addition = {'text': context_text, 'observed_cutoff': new[-1]['timestamp'],
                            'known_at_version': len(session['versions']), 'received_at': received, 'source_note': note}
                total = len(self._background(session)) + len(self._context_text(addition))
                if total > 20000:
                    raise ValueError('累计背景超过 20000 字，请整理材料后新建任务')
                session['context_updates'].append(addition)
            # All rows in a batch are scored before issuing ONE new version.
            # We never pretend to have forecast intermediate rows after seeing them.
            for row in new:
                match = None
                for version in reversed(session['versions']):
                    index = next((i for i, point in enumerate(version['prediction']) if point['timestamp'] == row['timestamp']), None)
                    if index is not None:
                        match = version, index
                        break
                if match is None:
                    session['unscored'].append({'timestamp': row['timestamp'], 'reason': '没有此前发出的预测覆盖该桶'})
                    continue
                version, index = match
                session['assessments'].append({
                    'timestamp': row['timestamp'], 'actual': row['volume'], 'received_at': received,
                    'version': version['number'], 'lead_buckets': index + 1,
                    'prospective': session['mode'] == 'live' and not session.get('restored') and
                                   timestamp(version['issued_at']) < timestamp(row['timestamp']),
                    **{key: version[key][index]['volume'] for key in ('base', 'candidate', 'last_value', 'moving_average')},
                    'predicted': version['prediction'][index]['volume'],
                })
            session['observations'].extend(new)
            session['source_updates'].append({'received_at': received, 'count': len(new), 'note': note, 'hash': digest(new)})
            self._version(session)
        return self._mutate(identifier, data, apply)

    def set_status(self, identifier, data):
        if data.get('status') not in {'active', 'paused'}:
            raise ValueError('状态须为 active 或 paused')
        return self._mutate(identifier, data, lambda session: session.update(status=data['status']))

    def summary(self, session):
        assessments = [row for epoch in session.get('archived_assessments', []) for row in epoch] + session['assessments']
        groups = {}
        if assessments:
            actual = [row['actual'] for row in assessments]
            for key in ('predicted', 'base', 'candidate', 'last_value', 'moving_average'):
                metrics = curve_metrics(actual, [row[key] for row in assessments], session['bucket_hours'])
                if metrics['peak_time_error_hours'] is not None:
                    left = max(range(len(actual)), key=lambda index: actual[index])
                    right = max(range(len(actual)), key=lambda index: assessments[index][key])
                    metrics['peak_time_error_hours'] = abs((timestamp(assessments[left]['timestamp']) - timestamp(assessments[right]['timestamp'])).total_seconds()) / 3600
                groups[key] = metrics
        base_error = groups.get('base', {}).get('mae')
        skill = 1 - groups['predicted']['mae'] / base_error if base_error else None
        by_lead = {}
        for lead in sorted({row['lead_buckets'] for row in assessments}):
            subset = [row for row in assessments if row['lead_buckets'] == lead]
            by_lead[str(lead)] = {'count': len(subset), 'mae': sum(abs(row['actual'] - row['predicted']) for row in subset) / len(subset)}
        return {'scored_buckets': len(assessments), 'unscored_buckets': len(session['unscored']),
                'prospective_buckets': sum(row['prospective'] for row in assessments) if not session.get('restored') else 0,
                'calibrated_versions': sum(version['calibration']['active'] for version in session['versions']),
                'metrics': groups, 'mae_improvement_vs_base': skill, 'by_lead': by_lead,
                'refresh_recommended': bool(session['versions'][-1]['calibration']['drift'] and
                                            not session['versions'][-1]['calibration']['active'] and
                                            len(session['assessments']) >= session['policy']['min_points']),
                'additional_calibration_api_requests': 0,
                'note': '按每个桶实际接收前已发出的最新版本评分；所有滚动基线使用相同观测。多步与一步预测请分层比较。'}

    def detail(self, identifier):
        session = self.get(identifier)
        return session | {'summary': self.summary(session)}

    def next_case(self, identifier, data):
        session = self.get(identifier)
        if session['revision'] != data.get('expected_revision'):
            raise RevisionConflict('请刷新后再生成下一轮材料')
        case = self.research.create_case({'name': session['name'] + f" / 滚动 v{len(session['versions']) - 1}",
                                         'rows': session['observations'], 'cutoff': session['versions'][-1]['cutoff'],
                                         'bucket_hours': session['bucket_hours'], 'horizon': session['horizon'],
                                         'background': self._background(session), 'source': session['source']},
                                        demo=session['is_demo'])
        return {'case': case, 'seed': self.research.seed_text(case['case_id']),
                'requirement': self.research.requirement(case['case_id']),
                'note': '仅包含本次已接收的真实观测；请新建项目与图谱，避免复用模拟未来。'}

    @staticmethod
    def _context_text(item):
        return f"\n新增已知背景（截至 {item['observed_cutoff']}，来源说明：{item['source_note']}）：{item['text']}"

    @staticmethod
    def _background(session, version=None):
        number = len(session['versions']) - 1 if version is None else version
        updates = [RollingStore._context_text(item)
                   for item in session.get('context_updates', []) if item['known_at_version'] <= number]
        return session['background'] + ''.join(updates)

    def validate_rebase_case(self, session, case):
        if session['status'] != 'active':
            raise ValueError('任务已暂停')
        if case['train'] != session['observations'] or case['bucket_hours'] != session['bucket_hours'] or case['horizon'] != session['horizon']:
            raise ValueError('请先生成下一轮材料；新推演必须严格使用本轮已接收观测、相同桶长和预测窗口')
        if source_metadata(case.get('source', {})) != source_metadata(session['source']) or case.get('background', '') != self._background(session):
            raise ValueError('新推演必须使用本轮的相同来源和已知背景材料')
        if len(session['versions']) >= MAX_VERSIONS:
            raise ValueError('版本数量达到上限')

    def rebase(self, identifier, data):
        frozen = self.research.forecast(data.get('forecast_id'))
        case = self.research.case(frozen['case_id'])
        def apply(session):
            self.validate_rebase_case(session, case)
            if frozen['method'] == 'simulation' and not frozen['metadata'].get('early_input_verified'):
                raise ValueError('新推演未通过独立早期材料与图谱检查')
            session['reference'] = frozen['prediction']
            session['reference_metadata'] = frozen['metadata']
            session['reference_forecast_id'] = frozen['forecast_id']
            # Residuals before rebase belong to an older base model: do not fit them to the new one.
            session.setdefault('archived_assessments', []).append(session['assessments'])
            session['assessments'] = []
            self._version(session, initial=frozen['prediction'], reason='新推演已接入，旧版本与评分留档，重新积累校准样本')
        return self._mutate(identifier, data, apply)

    def publish(self, identifier, data):
        result = {}
        def apply(session):
            number = data.get('version', len(session['versions']) - 1)
            if isinstance(number, bool) or not isinstance(number, int) or not 0 <= number < len(session['versions']):
                raise ValueError('预测版本号无效')
            existing = next((item for item in session.get('publications', []) if item['version'] == number), None)
            if existing:
                result.update(existing)
                return
            version = session['versions'][number]
            case = self.research.create_case({
                'name': session['name'] + f' / 冻结 v{number}',
                'rows': session['observations'][:version['observed_buckets']], 'cutoff': version['cutoff'],
                'bucket_hours': session['bucket_hours'], 'horizon': session['horizon'],
                'background': self._background(session, number), 'source': session['source']}, demo=session['is_demo'])
            frozen = self.research.freeze(case['case_id'], {'method': 'rolling', 'session_id': identifier, 'version': number})
            linked_case = self.research.case(case['case_id'])
            linked_case['rolling_link'] = {'session_id': identifier, 'version': number}
            write_json(self.research.cases_dir / f"{case['case_id']}.json", linked_case)
            result.update(case_id=case['case_id'], forecast_id=frozen['forecast_id'], version=number)
            session.setdefault('publications', []).append(result.copy())
        updated = self._mutate(identifier, data, apply)
        if not result:  # Idempotent request after a lost response.
            number = data.get('version', len(updated['versions']) - 1)
            result = next(item for item in reversed(updated['publications']) if item['version'] == number)
        return result

    def replay(self, case_id, forecast_id, policy=None):
        case = self.research.case(case_id)
        if any(time not in case['truth'] for time in case['expected']):
            raise ValueError('历史回放需要完整后续真实值')
        session = self.create({'case_id': case_id, 'forecast_id': forecast_id, 'mode': 'replay', 'policy': policy or {}})
        for index, time in enumerate(case['expected']):
            session = self.ingest(session['session_id'], {'rows': [case['truth'][time]], 'expected_revision': session['revision'],
                                                        'request_id': f'replay_step_{index:04d}', 'source_note': '历史数据按时间顺序逐桶释放'})
        return self.detail(session['session_id'])

    def replay_step(self, identifier, data):
        session = self.get(identifier)
        if session['mode'] != 'replay':
            raise ValueError('实际接收模式不能从历史隐藏数据自动取值')
        case = self.research.case(session['case_id'])
        time = timestamp(data.get('timestamp')).isoformat()
        if time not in case['truth']:
            raise ValueError('所选时间没有可回放的真实值；可导入新增观测')
        return self.ingest(identifier, {**data, 'rows': [case['truth'][time]], 'source_note': '按时间顺序释放历史观测'})

    def demo(self):
        from datetime import datetime, timezone
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        values = [10] * 6 + [30] * 8
        rows = [{'timestamp': (start + timedelta(hours=index)).isoformat(), 'volume': value}
                for index, value in enumerate(values)]
        case = self.research.create_case({'name': '合成闭环示例：持续低估后的自动纠偏', 'rows': rows,
                                         'cutoff': rows[5]['timestamp'], 'bucket_hours': 1, 'horizon': 8,
                                         'background': '完全合成的流程测试，非真实事件，不用于证明预测能力。'}, demo=True)
        frozen = self.research.freeze(case['case_id'], {'method': 'last_value', 'variant': '合成示例起始预测'})
        return self.create({'case_id': case['case_id'], 'forecast_id': frozen['forecast_id'], 'mode': 'replay'})

    def archive(self, identifier):
        session = self.get(identifier)
        payload = {'schema': 'mirofish-rolling-v1', 'exported_at': utc_now(), 'session': session,
                   'summary': self.summary(session)}
        return payload | {'archive_hash': digest(payload)}

    def restore(self, archive):
        if not isinstance(archive, dict) or archive.get('schema') != 'mirofish-rolling-v1':
            raise ValueError('备份格式不支持')
        content = {key: value for key, value in archive.items() if key != 'archive_hash'}
        if archive.get('archive_hash') != digest(content):
            raise ValueError('备份指纹校验失败')
        session = self._verify(copy.deepcopy(archive['session']))
        safe_id(session['session_id'])
        session['restored'] = True
        session['status'] = 'paused'
        session['mode'] = 'replay'
        session['previous_publications'] = session.pop('publications', [])
        session['publications'] = []
        self._seal(session)
        with self.database() as db:
            if db.execute('SELECT 1 FROM sessions WHERE id=?', (session['session_id'],)).fetchone():
                raise RevisionConflict('相同任务已存在，拒绝覆盖本地版本')
            db.execute('INSERT INTO sessions VALUES(?,?,?)', (session['session_id'], session['revision'], encode(session)))
        return session
