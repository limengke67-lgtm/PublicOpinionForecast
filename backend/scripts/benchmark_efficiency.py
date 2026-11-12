"""Reproducible offline benchmark; no network calls and no real model timings."""

import argparse
import json
import os
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.simulation_config_generator import SimulationConfigGenerator, AgentActivityConfig
from app.utils import llm_runtime as runtime


class ProfileFixture:
    model_name = 'offline-fixture'
    base_url = 'https://offline.invalid'
    api_key = 'not-a-real-key'

    @runtime.reuse_preparation
    def generate(self, material):
        def simulated_request():
            time.sleep(.05)
            return {'usage': {'prompt_tokens': 1000, 'completion_tokens': 100}}
        runtime.observe_sync(simulated_request, self.model_name)
        return {'bio': 'deterministic offline fixture', 'persona': material}


def profile_benchmark():
    rows = {}
    for mode in ('baseline', 'efficient'):
        generator = ProfileFixture()
        begin = time.perf_counter()
        with runtime.scope(f'profiles-{mode}', 'profile', mode):
            outputs = [generator.generate('identical fixed initialization') for _ in range(4)]
        assert all(output == outputs[0] for output in outputs)
        rows[mode] = {'elapsed_ms': round((time.perf_counter() - begin) * 1000, 2),
                      'requests': runtime.usage_summary(f'profiles-{mode}')['totals']['requests']}
    return rows


def configuration_benchmark():
    generator = object.__new__(SimulationConfigGenerator)
    generator.model_name, generator.base_url = 'offline-fixture', 'https://offline.invalid'
    entities = [SimpleNamespace(uuid=str(i), name=f'person-{i}', summary='fixed facts',
                                get_entity_type=lambda: 'Person') for i in range(31)]
    lock = threading.Lock()
    active, maximum = [0], [0]

    def work(value):
        with lock:
            active[0] += 1
            maximum[0] = max(maximum[0], active[0])
        time.sleep(.08)
        with lock:
            active[0] -= 1
        return value

    def agents(context, entities, start_idx, simulation_requirement):
        return work([AgentActivityConfig(agent_id=start_idx + i, entity_uuid=entity.uuid,
                                        entity_name=entity.name, entity_type='Person')
                     for i, entity in enumerate(entities)])

    rows, outputs = {}, []
    with patch.object(generator, '_generate_time_config', lambda *args: work({
        'total_simulation_hours': 6, 'minutes_per_round': 60,
        'agents_per_hour_min': 2, 'agents_per_hour_max': 4})), patch.object(
        generator, '_generate_event_config', lambda *args: work({
            'initial_posts': [{'content': 'same seed', 'poster_type': 'Person'}]})), patch.object(
        generator, '_generate_agent_configs_batch', agents):
        for mode in ('baseline', 'efficient'):
            maximum[0] = 0
            begin = time.perf_counter()
            with runtime.scope(f'config-{mode}', 'config', mode):
                result = generator.generate_config('sim_fixture', 'proj_fixture', 'graph_fixture',
                                                   'same requirement', 'same material', entities).to_dict()
            result.pop('generated_at')
            outputs.append(result)
            rows[mode] = {'elapsed_ms': round((time.perf_counter() - begin) * 1000, 2),
                          'maximum_concurrency': maximum[0]}
    assert outputs[0] == outputs[1]
    return {'outputs_equal': True, 'runs': rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='mirofish-offline-benchmark-') as folder, patch.dict(
            os.environ, {'MIROFISH_RUNTIME_DIR': folder}):
        runtime.save_settings({'mode': 'efficient', 'prepare_workers': 4})
        result = {'kind': 'offline_mock_scheduling_benchmark', 'real_api_calls': 0,
                  'not_real_model_latency': True, 'created_at': datetime.now(timezone.utc).isoformat(),
                  'profile_repeated_preparation': profile_benchmark(),
                  'independent_configuration_jobs': configuration_benchmark(),
                  'limitations': ['固定等待时间和确定性返回值，只验证缓存与并行调度机制。',
                                  '不能据此推断真实 API 节省比例、舆情准确率或端到端提速。']}
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(serialized + '\n', encoding='utf-8')
    print(serialized)


if __name__ == '__main__':
    main()
