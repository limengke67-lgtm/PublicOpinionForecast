import threading
import time
from types import SimpleNamespace

from app.services.simulation_config_generator import SimulationConfigGenerator, AgentActivityConfig
from app.utils.llm_runtime import scope, save_settings


def test_parallel_configuration_keeps_inputs_results_and_agent_order(tmp_path, monkeypatch):
    monkeypatch.setenv('MIROFISH_RUNTIME_DIR', str(tmp_path))
    save_settings({'prepare_workers': 4})
    entities = [SimpleNamespace(uuid=str(i), name=f'person-{i}', summary='same source facts', get_entity_type=lambda: 'Person') for i in range(31)]
    generator = object.__new__(SimulationConfigGenerator)
    generator.model_name = 'fixture'
    generator.base_url = 'https://unit.test'
    active, maximum = [0], [0]
    lock = threading.Lock()

    def work(result):
        with lock:
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
        time.sleep(.025)
        with lock:
            active[0] -= 1
        return result

    monkeypatch.setattr(generator, '_generate_time_config', lambda context, count: work({'total_simulation_hours': 6, 'minutes_per_round': 60, 'agents_per_hour_min': 2, 'agents_per_hour_max': 4}))
    monkeypatch.setattr(generator, '_generate_event_config', lambda *args: work({'initial_posts': [{'content': 'same initial post', 'poster_type': 'Person'}]}))
    monkeypatch.setattr(generator, '_generate_agent_configs_batch', lambda context, entities, start_idx, simulation_requirement: work([
        AgentActivityConfig(agent_id=start_idx + i, entity_uuid=entity.uuid, entity_name=entity.name, entity_type='Person') for i, entity in enumerate(entities)]))

    results, concurrency, durations = [], [], []
    for mode in ('baseline', 'efficient'):
        maximum[0] = 0
        begin = time.perf_counter()
        with scope('sim_test', 'prepare', mode):
            result = generator.generate_config('sim_test', 'proj_test', 'graph_test', 'same requirement', 'same materials', entities)
        value = result.to_dict()
        value.pop('generated_at')
        results.append(value)
        concurrency.append(maximum[0])
        durations.append(time.perf_counter() - begin)
    assert results[0] == results[1]
    assert [item['agent_id'] for item in results[1]['agent_configs']] == list(range(31))
    assert concurrency[0] == 1 and concurrency[1] > 1


def test_independent_platform_rngs_are_reproducible_without_global_state():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from runtime_support import platform_rng
    config = {'runtime_options': {'random_seed': 42}}
    twitter = platform_rng(config, 'twitter')
    reddit = platform_rng(config, 'reddit')
    left = [twitter.random() for _ in range(10)]
    other = platform_rng(config, 'twitter')
    assert left == [other.random() for _ in range(10)]
    assert left != [reddit.random() for _ in range(10)]
