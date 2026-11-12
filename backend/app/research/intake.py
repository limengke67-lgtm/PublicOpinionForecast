"""Explicit source metadata and deduplicated raw post aggregation."""

import csv
import io
import json
from datetime import timedelta
from urllib.parse import urlsplit

from .evaluation import digest, positive_int, timestamp


def source_metadata(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError('数据来源须为对象')
    fields = {'platform': 100, 'source_name': 200, 'source_url': 2000, 'sampling_method': 2000, 'collected_at': 100}
    result = {}
    for key, maximum in fields.items():
        entry = value.get(key, '')
        if not isinstance(entry, str) or len(entry) > maximum:
            raise ValueError(f'来源字段 {key} 格式无效')
        result[key] = entry.strip()
    if result['source_url']:
        url = urlsplit(result['source_url'])
        if url.scheme not in {'http', 'https'} or not url.netloc or url.username or url.password:
            raise ValueError('来源链接须为不含凭据的 HTTP/HTTPS 地址')
    if result['collected_at']:
        result['collected_at'] = timestamp(result['collected_at']).isoformat()
    if 'timezone_offset_hours' in value:
        from .evaluation import number
        offset = number(value['timezone_offset_hours'], 'timezone_offset_hours', minimum=-12, maximum=14)
        if offset != int(offset):
            raise ValueError('作息时区偏移须为整数小时')
        result['timezone_offset_hours'] = int(offset)
    if not any(result.values()) and 'timezone_offset_hours' not in result:
        return {}
    return result


def aggregate_posts(data):
    content = data.get('content')
    if not isinstance(content, str) or len(content.encode('utf-8')) > 10 * 1024 * 1024:
        raise ValueError('原始帖子文件须为不超过 10 MB 的文本')
    if data.get('format', 'csv') == 'csv':
        rows = list(csv.DictReader(io.StringIO(content.lstrip('\ufeff'))))
    elif data.get('format') == 'json':
        try:
            rows = json.loads(content.lstrip('\ufeff'))
        except json.JSONDecodeError as error:
            raise ValueError('原始帖子 JSON 格式无效') from error
    else:
        raise ValueError('原始帖子仅支持 CSV / JSON')
    if not isinstance(rows, list) or len(rows) > 100000:
        raise ValueError('原始帖子最多 100000 条')
    if data.get('coverage_complete') is not True:
        raise ValueError('请明确确认该时段采集完整；不完整采集不能补零')
    source = source_metadata(data.get('source'))
    if not source.get('platform') or not source.get('sampling_method'):
        raise ValueError('聚合原始帖子须填写平台和采样口径')
    start, end = timestamp(data.get('start')), timestamp(data.get('end'))
    hours = positive_int(data.get('bucket_hours', 1), 'bucket_hours', maximum=168)
    step = timedelta(hours=hours)
    bucket_count = (end - start).total_seconds() / step.total_seconds()
    if bucket_count != int(bucket_count) or not 1 <= bucket_count <= 10000:
        raise ValueError('采集区间必须覆盖 1～10000 个完整时间桶')
    buckets = [{'timestamp': (start + (index + 1) * step).isoformat(), 'volume': 0} for index in range(int(bucket_count))]
    labels = [[] for _ in buckets]
    seen, duplicates, excluded = {}, 0, 0
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not str(row.get('id', '')).strip():
            raise ValueError(f'第 {index + 1} 条帖子需要稳定的 id')
        platform = str(row.get('platform') or source['platform']).strip()
        if platform != source['platform']:
            raise ValueError('同一次聚合不能混合不同平台')
        created = timestamp(row.get('created_at'))
        label = str(row.get('negative_label', '')).strip().lower()
        if label not in {'', '0', '1', 'true', 'false'}:
            raise ValueError('negative_label 仅接受 0/1/true/false，缺失时不估算情感比例')
        record = {'created_at': created.isoformat(), 'negative_label': label}
        key = platform + ':' + str(row['id'])
        if key in seen:
            if seen[key] != record:
                raise ValueError('同一帖子 ID 的时间或标注冲突，请先清洗')
            duplicates += 1
            continue
        seen[key] = record
        if not start <= created < end:
            excluded += 1
            continue
        bucket_index = int((created - start).total_seconds() // step.total_seconds())
        buckets[bucket_index]['volume'] += 1
        labels[bucket_index].append(None if label == '' else int(label in {'1', 'true'}))
    for bucket, marked in zip(buckets, labels):
        if marked and all(value is not None for value in marked):
            bucket['negative_ratio'] = sum(marked) / len(marked)
    return {'rows': buckets, 'source': source, 'audit': {'input_records': len(rows),
            'duplicates_removed': duplicates, 'outside_window': excluded, 'coverage_complete': True,
            'input_hash': digest(content), 'interval': '[start,end)',
            'note': '仅完整标注的非空桶提供负面占比；完整采集的空桶计零。'}}
