import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import httpx
import pytest
from openai import OpenAI, AsyncOpenAI

from app.utils import llm_runtime as runtime


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv('MIROFISH_RUNTIME_DIR', str(tmp_path))
    monkeypatch.delenv('MIROFISH_SCOPE_ID', raising=False)
    runtime.save_settings({'mode': 'efficient', 'prices': {'fixture': {'input': 1, 'output': 2, 'cached_input': .1}}})


def response(incoming=1000, outgoing=100, cached=800):
    return SimpleNamespace(usage=SimpleNamespace(prompt_tokens=incoming, completion_tokens=outgoing,
                                               prompt_tokens_details=SimpleNamespace(cached_tokens=cached)))


def test_actual_usage_and_cached_input_are_counted_once():
    with runtime.scope('test', 'simulation'):
        runtime.observe_sync(response, 'fixture')
    totals = runtime.usage_summary('test')['totals']
    assert totals['requests'] == 1
    assert totals['input_tokens'] == 1000
    assert totals['cached_tokens'] == 800
    assert totals['estimated_cny'] == pytest.approx(.00048)


def test_unpriced_or_missing_usage_is_not_reported_as_free():
    with runtime.scope('unknown', 'simulation'):
        runtime.observe_sync(lambda: SimpleNamespace(), 'fixture')
        runtime.observe_sync(response, 'unpriced-model')
    totals = runtime.usage_summary('unknown')['totals']
    assert totals['estimated_cny'] is None
    assert totals['unknown_usage_requests'] == 1
    assert totals['incomplete_cost_requests'] == 2


def test_failure_is_recorded_without_exception_body_or_credentials():
    def fail():
        raise RuntimeError('sensitive prompt or credential')
    with runtime.scope('failed', 'profile'), pytest.raises(RuntimeError):
        runtime.observe_sync(fail, 'fixture')
    result = runtime.usage_summary('failed')
    assert result['totals']['failed_requests'] == 1
    assert 'sensitive' not in str(result)
    assert result['recent_requests'][0]['error_type'] == 'RuntimeError'


def test_atomic_request_limit_under_parallel_calls():
    runtime.save_settings({'max_requests_per_scope': 3})
    calls = []
    def task(index):
        with runtime.scope('budgeted', 'simulation'):
            try:
                return runtime.observe_sync(lambda: calls.append(index) or response(), 'fixture')
            except runtime.BudgetExceeded:
                return None
    with ThreadPoolExecutor(max_workers=10) as pool:
        list(pool.map(task, range(12)))
    assert len(calls) == 3
    assert runtime.usage_summary('budgeted')['totals']['requests'] == 3


def test_money_threshold_blocks_new_calls_and_does_not_claim_hard_cap():
    runtime.save_settings({'budget_cny_per_scope': .0001})
    with runtime.scope('money', 'simulation'):
        runtime.observe_sync(response, 'fixture')
        with pytest.raises(runtime.BudgetExceeded, match='金额预算'):
            runtime.observe_sync(response, 'fixture')
    assert runtime.usage_summary('money')['totals']['requests'] == 1


def test_last_round_cannot_hide_a_budget_denial_inside_agent_library():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
    from runtime_support import check_budget
    runtime.save_settings({'max_requests_per_scope': 1})
    with runtime.scope('last-round', 'simulation'):
        runtime.observe_sync(response, 'fixture')
        check_budget(after_round=True)  # Exactly one completed request is allowed.
        try:
            runtime.observe_sync(response, 'fixture')
        except runtime.BudgetExceeded:
            pass  # Simulates an agent library swallowing the denied decision.
        with pytest.raises(runtime.BudgetExceeded, match='未完整执行'):
            check_budget(after_round=True)


class ProfileFixture:
    api_key = 'dummy-test-credential'
    model_name = 'fixture'
    base_url = 'https://unit.test'
    def __init__(self):
        self.calls = 0

    @runtime.reuse_preparation
    def generate(self, context_text, cacheable=True):
        self.calls += 1
        time.sleep(.02)
        runtime.observe_sync(response, self.model_name)
        return {'bio': 'fixture profile', 'persona': context_text, '_cacheable': cacheable}


def test_preparation_reuse_avoids_request_but_baseline_resamples():
    generator = ProfileFixture()
    with runtime.scope('optimized', 'profile'):
        first = generator.generate('same verified facts')
        second = generator.generate('same verified facts')
    assert first == second
    assert generator.calls == 1
    assert runtime.usage_summary('optimized')['preparation_cache_hits'] == 1
    with runtime.scope('baseline', 'profile', 'baseline'):
        generator.generate('same verified facts')
        generator.generate('same verified facts')
    assert generator.calls == 3
    assert runtime.usage_summary('baseline')['totals']['requests'] == 2


def test_parallel_identical_preparations_have_one_inflight_request():
    generator = ProfileFixture()
    def task(_):
        with runtime.scope('shared', 'profile'):
            return generator.generate('same facts')
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(task, range(6)))
    assert generator.calls == 1
    assert all(item == results[0] for item in results)


def test_model_and_context_changes_invalidate_preparation_cache():
    generator = ProfileFixture()
    generator.generate('first facts')
    generator.generate('changed facts')
    generator.model_name = 'other-model'
    generator.generate('first facts')
    assert generator.calls == 3


def test_rule_fallback_is_not_cached():
    generator = ProfileFixture()
    generator.generate('fallback', cacheable=False)
    generator.generate('fallback', cacheable=False)
    assert generator.calls == 2


def test_sdk_hook_covers_real_sync_and_async_sdk_paths_without_network():
    runtime.install_openai_instrumentation()
    def handler(request):
        return httpx.Response(200, json={'id': 'fixture', 'object': 'chat.completion', 'created': 0, 'model': 'fixture',
                                        'choices': [{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': 'ok'}}],
                                        'usage': {'prompt_tokens': 1000, 'completion_tokens': 100, 'total_tokens': 1100,
                                                  'prompt_tokens_details': {'cached_tokens': 800}}})
    client = OpenAI(api_key='dummy-test-credential', base_url='https://unit.test/v1', http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with runtime.scope('sdk-sync', 'simulation'):
        assert client.chat.completions.create(model='fixture', messages=[{'role': 'user', 'content': 'fixture'}]).choices[0].message.content == 'ok'
    client.close()
    async def check():
        client = AsyncOpenAI(api_key='dummy-test-credential', base_url='https://unit.test/v1', http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
        with runtime.scope('sdk-async', 'simulation'):
            await client.chat.completions.create(model='fixture', messages=[{'role': 'user', 'content': 'fixture'}])
        await client.close()
    asyncio.run(check())
    assert runtime.usage_summary('sdk-sync')['totals']['requests'] == 1
    assert runtime.usage_summary('sdk-async')['totals']['requests'] == 1


@pytest.mark.parametrize('data', [{'prices': {'x': {'input': float('nan'), 'output': 1}}}, {'prepare_workers': 0}, {'max_requests_per_scope': True}, {'budget_cny_per_scope': -1}])
def test_invalid_settings_rejected(data):
    with pytest.raises(ValueError):
        runtime.save_settings(data)
