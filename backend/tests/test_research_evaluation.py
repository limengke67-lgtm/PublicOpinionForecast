import json
import math
from datetime import datetime, timedelta, timezone

import pytest

from app.research.evaluation import curve_metrics, normalize_rows, parse_rows, score, simulation_curve
from app.research.store import ResearchStore


def rows(values):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    return [{'timestamp': (start + timedelta(hours=i)).isoformat(), 'volume': value} for i, value in enumerate(values)]


def make_case(service, values=(1, 2, 3, 4, 5)):
    data = rows(values)
    return service.create_case({'name': 'test', 'rows': data, 'cutoff': data[2]['timestamp'], 'horizon': 2})


def test_no_future_data_in_public_case_or_seed(tmp_path):
    service = ResearchStore(tmp_path)
    case = make_case(service, (1, 2, 3, 98765, 76543))
    assert 'truth' not in case
    assert [row['volume'] for row in case['train']] == [1, 2, 3]
    assert '98765' not in service.seed_text(case['case_id'])
    assert '76543' not in service.seed_text(case['case_id'])


def test_baseline_is_independent_of_held_out_values(tmp_path):
    service = ResearchStore(tmp_path)
    first = make_case(service, (1, 2, 3, 100, 200))
    second = make_case(service, (1, 2, 3, 800, 900))
    left = service.freeze(first['case_id'], {'method': 'moving_average'})
    right = service.freeze(second['case_id'], {'method': 'moving_average'})
    assert left['prediction'] == right['prediction']
    assert left['train_hash'] == right['train_hash']


def test_hand_calculated_metrics_and_baseline_skill():
    train = rows([1, 2, 3])
    future = rows([1, 2, 3, 4, 5])[3:]
    prediction = [dict(row) for row in future]
    prediction[0]['volume'] = 3
    result = score(train, [row['timestamp'] for row in future], {row['timestamp']: row for row in future}, prediction, 1)
    assert result['volume']['mae'] == .5
    assert result['volume']['rmse'] == pytest.approx(math.sqrt(.5))
    assert result['volume']['wape'] == pytest.approx(1 / 9)
    assert result['baselines']['last_value']['metrics']['mae_skill'] == pytest.approx(2 / 3)
    assert result['negative_ratio']['mae'] is None


def test_zero_and_constant_curves_are_not_fake_perfect_correlations():
    result = curve_metrics([0, 0], [0, 0], 1)
    assert result['mae'] == 0
    assert result['wape'] is None
    assert result['correlation'] is None
    assert result['peak_time_error_hours'] is None
    assert 'accuracy' not in result


@pytest.mark.parametrize('value', [float('nan'), float('inf'), -1, True, 1e100])
def test_invalid_volumes_are_rejected(value):
    with pytest.raises(ValueError):
        normalize_rows(rows([value]))


def test_duplicate_equivalent_timezones_are_rejected():
    with pytest.raises(ValueError, match='重复'):
        normalize_rows([{'timestamp': '2026-01-01T08:00:00+08:00', 'volume': 1}, {'timestamp': '2026-01-01T00:00:00Z', 'volume': 2}])


def test_naive_timestamps_and_missing_buckets_are_rejected(tmp_path):
    with pytest.raises(ValueError, match='时区'):
        normalize_rows([{'timestamp': '2026-01-01T00:00:00', 'volume': 1}])
    data = rows([1, 2, 3, 4, 5])
    del data[3]
    with pytest.raises(ValueError, match='不连续'):
        ResearchStore(tmp_path).create_case({'rows': data, 'cutoff': data[2]['timestamp'], 'horizon': 2})


def test_truth_can_arrive_after_freeze_but_cannot_rewrite_history(tmp_path):
    service = ResearchStore(tmp_path)
    case = make_case(service, (1, 2, 3))
    frozen = service.freeze(case['case_id'], {'method': 'last_value'})
    with pytest.raises(ValueError, match='尚不完整'):
        service.evaluate(frozen['forecast_id'])
    truth = rows([1, 2, 3, 4, 5])[3:]
    service.add_truth(case['case_id'], {'content': json.dumps(truth), 'format': 'json'})
    assert service.evaluate(frozen['forecast_id'])['scores']['volume']['mae'] == 1.5
    truth[0]['volume'] = 999
    with pytest.raises(ValueError, match='不能覆盖'):
        service.add_truth(case['case_id'], {'content': json.dumps(truth), 'format': 'json'})


def test_modified_prediction_fingerprint_is_rejected(tmp_path):
    service = ResearchStore(tmp_path)
    case = make_case(service)
    frozen = service.freeze(case['case_id'], {'method': 'last_value'})
    path = service.forecasts_dir / f"{frozen['forecast_id']}.json"
    frozen['prediction'][0]['volume'] = 999
    path.write_text(json.dumps(frozen), encoding='utf-8')
    with pytest.raises(ValueError, match='指纹'):
        service.evaluate(frozen['forecast_id'])


def test_import_requires_exact_forecast_window(tmp_path):
    service = ResearchStore(tmp_path)
    case = make_case(service)
    with pytest.raises(ValueError, match='逐桶覆盖'):
        service.freeze(case['case_id'], {'method': 'import', 'format': 'json', 'content': json.dumps(rows([1, 2]))})


def test_synthetic_demo_is_labeled_everywhere(tmp_path):
    service = ResearchStore(tmp_path)
    case = service.create_demo()
    frozen = service.freeze(case['case_id'], {'method': 'moving_average'})
    result = service.evaluate(frozen['forecast_id'])
    assert case['is_demo'] and frozen['is_demo']
    assert result['evidence_level'] == 'synthetic_demo'
    assert '合成' in service.seed_text(case['case_id'])


def test_simulation_alignment_excludes_initialization_and_failed_actions(tmp_path):
    path = tmp_path / 'actions.jsonl'
    records = [
        {'round': 0, 'action_type': 'CREATE_POST'},
        {'round': 1, 'action_type': 'CREATE_POST'},
        {'round': 2, 'action_type': 'CREATE_COMMENT'},
        {'round': 3, 'action_type': 'LIKE_POST'},
        {'round': 4, 'action_type': 'CREATE_POST', 'success': False},
    ]
    path.write_text('\n'.join(json.dumps(row) for row in records), encoding='utf-8')
    expected = [row['timestamp'] for row in rows([1, 2])]
    curve, factor = simulation_curve(path, expected, 30, 1, completed_rounds=4, anchor=10)
    assert [row['volume'] for row in curve] == [10, 0]
    assert factor == 5
    assert all('negative_ratio' not in row for row in curve)
    with pytest.raises(ValueError, match='窗口不足'):
        simulation_curve(path, expected, 30, 1, completed_rounds=3)


@pytest.mark.parametrize('identifier', ['../../secret', 'case_..', '/etc/passwd', ''])
def test_resource_ids_cannot_escape_store(tmp_path, identifier):
    with pytest.raises(ValueError):
        ResearchStore(tmp_path).case(identifier)
