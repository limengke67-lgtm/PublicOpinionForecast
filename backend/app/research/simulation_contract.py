"""Bind costly simulation length and local activity clock to a research window."""

import math
from datetime import timedelta

from .evaluation import number, positive_int, timestamp


def bind_window(config, case):
    bucket_minutes = case['bucket_hours'] * 60
    time_config = config['time_config']
    generated_minutes = positive_int(time_config.get('minutes_per_round', 60), 'minutes_per_round', maximum=1440)
    minutes = math.gcd(generated_minutes, bucket_minutes)
    hours = case['bucket_hours'] * case['horizon']
    offset = number(case.get('source', {}).get('timezone_offset_hours', 8), 'timezone_offset_hours', minimum=-12, maximum=14)
    local_cutoff = timestamp(case['cutoff']) + timedelta(hours=offset)
    time_config['total_simulation_hours'] = hours
    time_config['minutes_per_round'] = minutes
    config['research_contract'] = {
        'case_id': case['case_id'], 'cutoff': case['cutoff'], 'bucket_hours': case['bucket_hours'],
        'horizon': case['horizon'], 'required_rounds': hours * 60 // minutes,
        'start_minute_of_day': local_cutoff.hour * 60 + local_cutoff.minute,
        'timezone_offset_hours': offset,
        'note': '只运行研究预测窗口；每轮完整落入时间桶，日内活动时钟从真实截止时刻开始。',
    }
    return config


def validate_start(config, max_rounds=None):
    contract = config.get('research_contract')
    if not contract:
        return
    required = contract['required_rounds']
    planned = int(config['time_config']['total_simulation_hours'] * 60 / config['time_config']['minutes_per_round'])
    if planned != required:
        raise ValueError('研究预测时间窗口已被修改，请重新准备仿真')
    if max_rounds is not None and max_rounds < required:
        raise ValueError(f'完整预测窗口需要 {required} 轮，当前上限 {max_rounds} 轮。请缩短预测窗口或提高上限；本次未启动模型。')
