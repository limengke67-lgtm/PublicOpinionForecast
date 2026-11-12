"""Causal, bounded residual calibration. This module never sees held-out truth."""

import math
import statistics

from .evaluation import number, positive_int


DEFAULTS = {
    'window': 6,
    'min_points': 3,
    'consecutive': 2,
    'trigger_ratio': .35,
    'shrinkage': 3.0,
    'decay': .85,
    'minimum_improvement': .05,
    'max_gain': 4.0,
}


def validate_policy(values):
    if not isinstance(values, dict) or set(values) - set(DEFAULTS):
        raise ValueError('校准策略包含未知字段')
    policy = {**DEFAULTS, **values}
    for key in ('window', 'min_points', 'consecutive'):
        policy[key] = positive_int(policy[key], key, maximum=100)
    if policy['window'] < policy['min_points'] or policy['window'] < policy['consecutive']:
        raise ValueError('校准窗口不能小于最少样本数或连续偏差桶数')
    for key, low, high in (('trigger_ratio', .01, 1), ('shrinkage', 0, 100),
                           ('decay', .1, 1), ('minimum_improvement', 0, .9), ('max_gain', 1, 10)):
        policy[key] = number(policy[key], key, minimum=low, maximum=high)
    return policy


def calibration_state(samples, policy):
    """Fit only on already-observed errors; gate with earlier shadow forecasts."""
    recent = samples[-policy['window']:]
    n = len(recent)
    if not n:
        return {'gain': 1., 'bias': 0., 'active': False, 'drift': False,
                'reason': '尚无发出预测之后到来的真实观测', 'sample_count': 0,
                'candidate_mae': None, 'base_mae': None, 'relative_error': None, 'selected_method': 'base'}
    weights = [.7 ** (n - 1 - index) for index in range(n)]
    total_weight = sum(weights)
    mean_actual = sum(row['actual'] * w for row, w in zip(recent, weights)) / total_weight
    mean_base = sum(row['base'] * w for row, w in zip(recent, weights)) / total_weight
    strength = n / (n + policy['shrinkage'])
    ratio = max(1 / policy['max_gain'], min(policy['max_gain'], mean_actual / mean_base)) if mean_base >= 1 else 1.
    gain = math.exp(math.log(max(ratio, 1e-12)) * strength)
    bias = (mean_actual - gain * mean_base) * strength
    signed = [(row['actual'] - row['base']) / max(row['actual'], row['base'], 1) for row in recent]
    tail = signed[-policy['consecutive']:]
    drift = (len(tail) == policy['consecutive'] and
             (all(error >= policy['trigger_ratio'] for error in tail) or
              all(error <= -policy['trigger_ratio'] for error in tail)))
    base_mae = statistics.mean(abs(row['actual'] - row['base']) for row in recent)
    candidate_mae = statistics.mean(abs(row['actual'] - row['candidate']) for row in recent)
    enough = n >= policy['min_points']
    losses = {'base': base_mae, 'candidate': candidate_mae,
              **{key: statistics.mean(abs(row['actual'] - row[key]) for row in recent) for key in ('last_value', 'moving_average')}}
    best = min(losses, key=losses.get)
    improves = base_mae > 0 and losses[best] <= base_mae * (1 - policy['minimum_improvement'])
    active = bool(enough and drift and improves and best != 'base')
    selected = best if active else 'base'
    labels = {'candidate': '有界残差校准', 'last_value': '同信息最近值回退', 'moving_average': '同信息均值回退'}
    reason = (f'持续偏差且历史已发出预测通过改进门槛，采用{labels[best]}' if active else
              '校准误差样本不足，继续积累观测' if not enough else
              '未达到连续同向偏差阈值，保持未校准曲线' if not drift else
              '此前影子校准预测未通过改进门槛，保持未校准曲线')
    return {'gain': gain, 'bias': bias, 'active': active, 'drift': drift,
            'reason': reason, 'sample_count': n, 'candidate_mae': candidate_mae,
            'base_mae': base_mae, 'relative_error': sum(abs(row['actual'] - row['base']) for row in recent) /
            max(sum(row['actual'] for row in recent), sum(row['base'] for row in recent), 1),
            'selected_method': selected, 'validation_losses': losses}


def correct_curve(base, state, policy):
    return [dict(timestamp=row['timestamp'], volume=min(1e12, max(
        0., row['volume'] + (state['gain'] * row['volume'] + state['bias'] - row['volume']) * policy['decay'] ** index)))
        for index, row in enumerate(base)]
