import copy

import pytest

from app.research.intake import source_metadata
from app.research.simulation_contract import bind_window, validate_start


def case():
    return {'case_id': 'case_fixture1', 'cutoff': '2026-01-01T22:30:00+00:00',
            'bucket_hours': 1, 'horizon': 6, 'source': {'timezone_offset_hours': 8}}


def test_research_runs_exact_requested_window_instead_of_generated_72_hours():
    config = {'time_config': {'total_simulation_hours': 72, 'minutes_per_round': 60}}
    bind_window(config, case())
    assert config['time_config']['total_simulation_hours'] == 6
    assert config['research_contract']['required_rounds'] == 6
    assert config['research_contract']['start_minute_of_day'] == 390
    validate_start(config, 6)


@pytest.mark.parametrize('minutes,expected', [(120, 60), (90, 30), (60, 60), (30, 30)])
def test_rounds_cannot_straddle_observation_buckets(minutes, expected):
    config = bind_window({'time_config': {'minutes_per_round': minutes}}, case())
    assert config['time_config']['minutes_per_round'] == expected
    assert 60 % expected == 0


def test_incomplete_round_limit_is_rejected_before_simulation_starts():
    config = bind_window({'time_config': {'minutes_per_round': 30}}, case())
    with pytest.raises(ValueError, match='需要 12 轮'):
        validate_start(config, 10)
    changed = copy.deepcopy(config)
    changed['time_config']['total_simulation_hours'] += 1
    with pytest.raises(ValueError, match='修改'):
        validate_start(changed)


def test_non_research_simulation_keeps_original_behavior():
    validate_start({'time_config': {'total_simulation_hours': 72}}, 10)


def test_activity_clock_starts_at_real_local_cutoff():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from runtime_support import simulation_clock
    config = bind_window({'time_config': {'minutes_per_round': 30}}, case())
    assert simulation_clock(config, 0, 30) == (0, 6, 1)
    assert simulation_clock(config, 36, 30) == (1080, 0, 2)
    assert simulation_clock({}, 0, 30) == (0, 0, 1)


def test_empty_source_metadata_stays_canonical_across_restarts():
    assert source_metadata({}) == source_metadata(None) == {}
    with pytest.raises(ValueError):
        source_metadata({'timezone_offset_hours': 15})
