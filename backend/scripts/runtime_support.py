"""Shared runtime settings for all three OASIS entry points."""

import hashlib
import os
import random

from app.utils.llm_runtime import install_openai_instrumentation, settings, context, database, BudgetExceeded


install_openai_instrumentation()


def platform_rng(config, platform):
    options = config.get('runtime_options', {})
    seed = options.get('random_seed')
    if seed is None:
        return random
    value = hashlib.sha256(f'{seed}:{platform}'.encode()).digest()
    return random.Random(int.from_bytes(value[:8], 'big'))


def llm_concurrency(config):
    value = config.get('runtime_options', {}).get('llm_concurrency', 30)
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 128:
        raise ValueError('llm_concurrency 必须是 1～128 的整数')
    return value


def simulation_clock(config, round_index, minutes_per_round):
    elapsed = round_index * minutes_per_round
    minutes = elapsed + config.get('research_contract', {}).get('start_minute_of_day', 0)
    return elapsed, int(minutes // 60) % 24, int(minutes // 1440) + 1


def check_budget(*, after_round=False):
    limits = settings()
    with database() as db:
        denied = db.execute('SELECT 1 FROM budget_blocks WHERE scope_id=? LIMIT 1', (context()['scope_id'],)).fetchone()
        row = db.execute('SELECT COUNT(*) n, SUM(estimated_cny) cost FROM requests WHERE scope_id=?', (context()['scope_id'],)).fetchone()
    if denied:
        raise BudgetExceeded('本轮存在被预算拦截的模型决策，仿真未完整执行。')
    if after_round:
        return
    if limits['max_requests_per_scope'] and row['n'] >= limits['max_requests_per_scope']:
        raise BudgetExceeded('请求次数达到上限，仿真未完成；本次结果不能作为完整预测。')
    if limits['budget_cny_per_scope'] and (row['cost'] or 0) >= limits['budget_cny_per_scope']:
        raise BudgetExceeded('金额预算达到阈值，仿真未完成；本次结果不能作为完整预测。')
