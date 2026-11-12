import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from flask import Flask

from app.api import research_bp
from app.config import Config
from app.research.calibration import validate_policy
from app.research.intake import aggregate_posts
from app.research.rolling import RevisionConflict, RollingStore
from app.research.store import ResearchStore


def rows(values, offset=0):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [{'timestamp': (start + timedelta(hours=i + offset)).isoformat(), 'volume': value}
            for i, value in enumerate(values)]


def setup_case(tmp_path, future=None, horizon=8):
    research = ResearchStore(tmp_path)
    values = [10] * 6 + (future or [30] * horizon)
    case = research.create_case({'name': '测试事件', 'rows': rows(values), 'cutoff': rows([10], 5)[0]['timestamp'],
                                 'bucket_hours': 1, 'horizon': horizon,
                                 'source': {'platform': 'example', 'source_name': 'test', 'sampling_method': 'complete'}})
    forecast = research.freeze(case['case_id'], {'method': 'last_value'})
    return case, forecast


def start(tmp_path):
    case, forecast = setup_case(tmp_path)
    store = RollingStore(tmp_path)
    return store, store.create({'case_id': case['case_id'], 'forecast_id': forecast['forecast_id']})


def ingest(store, session, data, identifier=None):
    return store.ingest(session['session_id'], {'rows': data, 'expected_revision': session['revision'],
                                               'request_id': identifier or f'update_{session["revision"]:08d}'})


def test_persistent_bias_activates_causal_calibration_and_improves_in_fixture(tmp_path):
    store, session = start(tmp_path)
    original = copy.deepcopy(session['versions'][0])
    for index in range(8):
        session = ingest(store, session, rows([30], 6 + index))
    result = store.detail(session['session_id'])
    assert result['summary']['calibrated_versions'] > 0
    assert result['summary']['metrics']['predicted']['mae'] < result['summary']['metrics']['base']['mae']
    assert result['summary']['additional_calibration_api_requests'] == 0
    assert session['versions'][0] == original
    assert all(row['version'] < i + 1 for i, row in enumerate(session['assessments']))


def test_future_truth_cannot_change_any_earlier_forecast(tmp_path):
    versions = []
    for name, future in [('a', [30] * 8), ('b', [30] * 3 + [900] * 5)]:
        root = tmp_path / name
        case, forecast = setup_case(root, future)
        store = RollingStore(root)
        session = store.create({'case_id': case['case_id'], 'forecast_id': forecast['forecast_id']})
        for index in range(3):
            session = ingest(store, session, rows([30], 6 + index))
        versions.append([{key: version[key] for key in ('base', 'candidate', 'prediction', 'calibration', 'cutoff')}
                         for version in session['versions']])
    assert versions[0] == versions[1]


def test_batch_observations_are_not_scored_as_if_forecast_between_rows(tmp_path):
    store, session = start(tmp_path)
    session = ingest(store, session, rows([30, 40, 50], 6))
    assert len(session['versions']) == 2
    assert [row['version'] for row in session['assessments']] == [0, 0, 0]
    assert [row['lead_buckets'] for row in session['assessments']] == [1, 2, 3]


def test_observations_beyond_issued_window_are_explicitly_unscored(tmp_path):
    store, session = start(tmp_path)
    session = ingest(store, session, rows([20] * 10, 6))
    assert len(session['assessments']) == 8
    assert len(session['unscored']) == 2


def test_duplicate_retry_is_idempotent_and_conflicting_revision_is_rejected(tmp_path):
    store, session = start(tmp_path)
    body = {'rows': rows([30], 6), 'expected_revision': 0, 'request_id': 'same_request_001'}
    first = store.ingest(session['session_id'], body)
    repeated = store.ingest(session['session_id'], body)
    assert first['revision'] == repeated['revision'] == 1
    assert repeated['replayed_request']
    with pytest.raises(RevisionConflict):
        store.ingest(session['session_id'], {**body, 'rows': rows([99], 6)})
    with pytest.raises(RevisionConflict):
        store.ingest(session['session_id'], {**body, 'request_id': 'different_request'})


def test_parallel_update_has_only_one_committed_version(tmp_path):
    store, session = start(tmp_path)
    def task(index):
        try:
            return store.ingest(session['session_id'], {'rows': rows([30], 6), 'expected_revision': 0,
                                                       'request_id': f'parallel_{index:08d}'})
        except RevisionConflict:
            return None
    with ThreadPoolExecutor(max_workers=6) as executor:
        results = list(executor.map(task, range(6)))
    assert sum(result is not None for result in results) == 1
    assert len(store.get(session['session_id'])['versions']) == 2


@pytest.mark.parametrize('data', [rows([30], 7), rows([99], 5), rows([30], 4)])
def test_gap_and_rewriting_observations_are_atomic_errors(tmp_path, data):
    store, session = start(tmp_path)
    with pytest.raises(ValueError):
        ingest(store, session, data)
    assert store.get(session['session_id']) == session


def test_live_mode_rejects_future_buckets_and_labels_historical_predictions(tmp_path):
    case, forecast = setup_case(tmp_path)
    store = RollingStore(tmp_path)
    session = store.create({'case_id': case['case_id'], 'forecast_id': forecast['forecast_id'], 'mode': 'live'})
    session = ingest(store, session, rows([30], 6))
    assert session['assessments'][0]['prospective'] is False


def test_zero_counts_never_produce_nan_or_infinite_forecasts(tmp_path):
    research = ResearchStore(tmp_path)
    case = research.create_case({'rows': rows([0] * 4), 'cutoff': rows([0], 3)[0]['timestamp'], 'horizon': 4})
    forecast = research.freeze(case['case_id'], {'method': 'last_value'})
    store = RollingStore(tmp_path)
    session = store.create({'case_id': case['case_id'], 'forecast_id': forecast['forecast_id']})
    for index in range(6):
        session = ingest(store, session, rows([100], 4 + index))
    assert all(0 <= point['volume'] <= 1e12 for point in session['versions'][-1]['candidate'])
    json.dumps(store.detail(session['session_id']), allow_nan=False)


def test_new_case_contains_only_received_data_and_rebase_requires_exact_history(tmp_path):
    store, session = start(tmp_path)
    session = ingest(store, session, rows([30], 6))
    next_case = store.next_case(session['session_id'], {'expected_revision': 1})['case']
    full = ResearchStore(tmp_path).case(next_case['case_id'])
    assert full['truth'] == {}
    assert full['train'] == session['observations']
    bad = ResearchStore(tmp_path).freeze(session['case_id'], {'method': 'last_value'})
    with pytest.raises(ValueError, match='严格使用'):
        store.rebase(session['session_id'], {'forecast_id': bad['forecast_id'], 'expected_revision': 1, 'request_id': 'rebase_bad_001'})
    new = ResearchStore(tmp_path).freeze(next_case['case_id'], {'method': 'last_value'})
    result = store.rebase(session['session_id'], {'forecast_id': new['forecast_id'], 'expected_revision': 1, 'request_id': 'rebase_good_001'})
    assert result['versions'][-1]['calibration']['sample_count'] == 0
    assert result['archived_assessments'][0]


def test_archive_restores_as_paused_replay_and_detects_modification(tmp_path):
    store, session = start(tmp_path / 'original')
    session = ingest(store, session, rows([30], 6))
    archive = store.archive(session['session_id'])
    restored_store = RollingStore(tmp_path / 'restored')
    restored = restored_store.restore(archive)
    assert restored['status'] == 'paused' and restored['mode'] == 'replay'
    assert restored_store.get(restored['session_id'])['versions'] == session['versions']
    archive['session']['observations'][-1]['volume'] += 1
    with pytest.raises(ValueError, match='指纹'):
        RollingStore(tmp_path / 'other').restore(archive)


@pytest.mark.parametrize('policy', [{'window': 2}, {'trigger_ratio': float('nan')}, {'consecutive': 0}, {'unknown': 1}])
def test_invalid_calibration_policy_is_rejected(policy):
    with pytest.raises(ValueError):
        validate_policy(policy)


def test_post_aggregation_deduplicates_and_preserves_missing_sentiment():
    data = {'format': 'json', 'content': json.dumps([
        {'id': 'a', 'created_at': '2026-01-01T00:00:00Z', 'negative_label': 1},
        {'id': 'a', 'created_at': '2026-01-01T00:00:00Z', 'negative_label': 1},
        {'id': 'b', 'created_at': '2026-01-01T01:00:00Z'},
    ]), 'coverage_complete': True, 'source': {'platform': 'test', 'sampling_method': 'complete'},
        'start': '2026-01-01T00:00:00Z', 'end': '2026-01-01T03:00:00Z'}
    result = aggregate_posts(data)
    assert [row['volume'] for row in result['rows']] == [1, 1, 0]
    assert result['rows'][0]['negative_ratio'] == 1
    assert 'negative_ratio' not in result['rows'][1] and 'negative_ratio' not in result['rows'][2]
    assert result['audit']['duplicates_removed'] == 1
    with pytest.raises(ValueError, match='完整'):
        aggregate_posts({**data, 'coverage_complete': False})


def test_full_rolling_api_replay_export_and_revision_conflict(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, 'UPLOAD_FOLDER', str(tmp_path))
    monkeypatch.setenv('MIROFISH_RUNTIME_DIR', str(tmp_path / 'runtime'))
    app = Flask(__name__)
    app.register_blueprint(research_bp, url_prefix='/api/research')
    client = app.test_client()
    case, forecast = setup_case(tmp_path)
    response = client.post('/api/research/rolling/replay', json={'case_id': case['case_id'], 'forecast_id': forecast['forecast_id']})
    assert response.status_code == 200
    session = response.json['data']
    url = '/api/research/rolling/' + session['session_id']
    assert client.get(url).json['data']['summary']['scored_buckets'] == 8
    assert 'actual' in client.get(url + '/export?format=csv').text
    assert client.get(url + '/export').json['schema'] == 'mirofish-rolling-v1'
    assert client.put(url + '/status', json={'status': 'paused', 'request_id': 'pause_0001', 'expected_revision': -1}).status_code == 400
    assert client.put(url + '/status', json={'status': 'paused', 'request_id': 'pause_0001', 'expected_revision': 0}).status_code == 409
    assert client.get('/api/research/readiness?case_id=' + case['case_id']).status_code == 200


def test_publish_freezes_into_existing_evaluation_and_uses_only_later_received_truth(tmp_path):
    case, forecast = setup_case(tmp_path, horizon=2)
    store = RollingStore(tmp_path)
    session = store.create({'case_id': case['case_id'], 'forecast_id': forecast['forecast_id']})
    publication = store.publish(session['session_id'], {'expected_revision': 0, 'request_id': 'publish_001', 'version': 0})
    research = ResearchStore(tmp_path)
    with pytest.raises(ValueError, match='尚不完整'):
        research.evaluate(publication['forecast_id'])
    session = store.get(session['session_id'])
    session = ingest(store, session, rows([30, 40], 6))
    result = research.evaluate(publication['forecast_id'])
    assert [row['volume'] for row in result['scores']['truth']] == [30, 40]
    assert result['forecast']['method'] == 'rolling'
    assert research.case(publication['case_id'])['truth']
    assert next(case for case in research.list_cases() if case['case_id'] == publication['case_id'])['truth_buckets'] == 2
    again = store.publish(session['session_id'], {'expected_revision': session['revision'], 'request_id': 'publish_002', 'version': 0})
    assert again == publication


def test_context_is_available_only_from_the_version_where_it_was_received(tmp_path):
    store, session = start(tmp_path)
    session = store.ingest(session['session_id'], {'rows': rows([30], 6), 'expected_revision': 0,
                                                'request_id': 'context_001', 'context_text': 'newly observed fact'})
    assert 'newly observed fact' in store.next_case(session['session_id'], {'expected_revision': 1})['seed']
    assert 'newly observed fact' not in store._background(session, 0)
    with pytest.raises(ValueError, match='文本'):
        store.ingest(session['session_id'], {'rows': rows([30], 7), 'expected_revision': 1,
                                             'request_id': 'context_002', 'context_text': 123})


def test_local_folder_ingestion_can_restart_without_duplicating_versions(tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from watch_observations import scan
    store, session = start(tmp_path)
    folder = tmp_path / 'incoming'
    folder.mkdir()
    (folder / '001.json').write_text(json.dumps(rows([30], 6)), encoding='utf-8')
    assert scan(store, session['session_id'], folder) == (1, [])
    assert scan(store, session['session_id'], folder) == (0, [])
    assert len(store.get(session['session_id'])['versions']) == 2


def test_replay_releases_only_one_bucket_and_never_runs_in_live_mode(tmp_path):
    store, session = start(tmp_path)
    result = store.replay_step(session['session_id'], {'expected_revision': 0, 'request_id': 'step_0001', 'timestamp': rows([30], 6)[0]['timestamp']})
    assert len(result['observations']) == len(session['observations']) + 1
    case, frozen = setup_case(tmp_path / 'live')
    live_store = RollingStore(tmp_path / 'live')
    live = live_store.create({'case_id': case['case_id'], 'forecast_id': frozen['forecast_id'], 'mode': 'live'})
    with pytest.raises(ValueError, match='不能'):
        live_store.replay_step(live['session_id'], {'expected_revision': 0, 'request_id': 'step_0002', 'timestamp': rows([30], 6)[0]['timestamp']})


def test_prospective_scoring_requires_issuance_before_bucket_end(tmp_path, monkeypatch):
    import app.research.rolling as module
    monkeypatch.setattr(module, 'utc_now', lambda: '2026-01-01T05:30:00+00:00')
    case, frozen = setup_case(tmp_path)
    store = RollingStore(tmp_path)
    session = store.create({'case_id': case['case_id'], 'forecast_id': frozen['forecast_id'], 'mode': 'live'})
    with pytest.raises(ValueError, match='尚未结束'):
        ingest(store, session, rows([30], 6))
    monkeypatch.setattr(module, 'utc_now', lambda: '2026-01-01T06:01:00+00:00')
    result = ingest(store, session, rows([30], 6))
    assert result['assessments'][0]['prospective'] is True
    assert store.summary(result)['prospective_buckets'] == 1


def test_paused_task_preserves_all_records_when_ingestion_is_rejected(tmp_path):
    store, session = start(tmp_path)
    paused = store.set_status(session['session_id'], {'expected_revision': 0, 'request_id': 'pause_001', 'status': 'paused'})
    with pytest.raises(ValueError, match='暂停'):
        ingest(store, paused, rows([30], 6))
    assert store.get(session['session_id']) == paused


def test_corrupt_policy_in_database_is_detected_before_new_forecast(tmp_path):
    store, session = start(tmp_path)
    changed = copy.deepcopy(session)
    changed['policy']['trigger_ratio'] = .01
    with store.database() as db:
        db.execute('UPDATE sessions SET payload=? WHERE id=?', (json.dumps(changed), session['session_id']))
    with pytest.raises(ValueError, match='指纹'):
        store.get(session['session_id'])


def test_residual_candidate_is_not_used_when_same_information_baseline_is_better():
    from app.research.calibration import calibration_state
    samples = [{'actual': 30, 'base': 10, 'candidate': 100, 'last_value': 30, 'moving_average': 35}] * 4
    state = calibration_state(samples, validate_policy({}))
    assert state['active']
    assert state['selected_method'] == 'last_value'


def test_perfect_reference_is_not_changed_by_worse_candidates():
    from app.research.calibration import calibration_state
    samples = [{'actual': 10, 'base': 10, 'candidate': 100, 'last_value': 30, 'moving_average': 35}] * 4
    state = calibration_state(samples, validate_policy({}))
    assert not state['active'] and state['selected_method'] == 'base'


def test_restore_does_not_modify_the_supplied_archive(tmp_path):
    store, session = start(tmp_path / 'original')
    archive = store.archive(session['session_id'])
    original = copy.deepcopy(archive)
    RollingStore(tmp_path / 'new').restore(archive)
    assert archive == original


@pytest.mark.parametrize('value', [None, '', 'your_api_key_here', 'your_zep_api_key_here', 'placeholder', 'changeme'])
def test_template_credentials_do_not_pass_readiness(value):
    from app.config import credential_present
    assert not credential_present(value)
