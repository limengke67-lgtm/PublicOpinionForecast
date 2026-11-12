"""Derive anonymous hourly counts from the original PHEME archive and replay them.

No tweet text, user identifiers, model requests or rumour/sentiment labels are
used. Counts describe this fixed conversation sample, not all Twitter activity.
"""

import argparse
import csv
import hashlib
import json
import sys
import tarfile
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Config
from app.research.rolling import RollingStore
from app.research.store import ResearchStore
from app.research.calibration import validate_policy

SOURCE = 'https://figshare.com/articles/dataset/PHEME_dataset_of_rumours_and_non-rumours/4010619'
DOI = '10.6084/m9.figshare.4010619.v1'
ARCHIVE_MD5 = '07f004eca867447d69c71e87be242d75'


def load_counts(path):
    contents = path.read_bytes()
    if hashlib.md5(contents).hexdigest() != ARCHIVE_MD5:
        raise ValueError('PHEME 原始归档 MD5 与 Figshare 发布文件不一致')
    events, roots, duplicates = defaultdict(dict), defaultdict(list), Counter()
    with tarfile.open(path, 'r|bz2') as archive:
        for member in archive:
            parts = member.name.split('/')
            if not member.isfile() or len(parts) < 6 or parts[-2] not in {'source-tweet', 'reactions'} or not parts[-1].endswith('.json'):
                continue
            with archive.extractfile(member) as stream:
                tweet = json.load(stream)
            created = datetime.strptime(tweet['created_at'], '%a %b %d %H:%M:%S %z %Y').astimezone(timezone.utc)
            event, identifier = parts[1], str(tweet['id'])
            if identifier in events[event]:
                if events[event][identifier] != created:
                    raise ValueError('PHEME 相同帖子 ID 的时间不一致')
                duplicates[event] += 1
                continue
            events[event][identifier] = created
            if parts[-2] == 'source-tweet':
                roots[event].append(created)
    prepared = {}
    for event in sorted(events):
        begin = min(roots[event]).replace(minute=0, second=0, microsecond=0)
        buckets = [0] * 72
        for created in events[event].values():
            index = int((created - begin).total_seconds() // 3600)
            if 0 <= index < len(buckets):
                buckets[index] += 1
        prepared[event] = {
            'rows': [{'timestamp': (begin + timedelta(hours=index + 1)).isoformat(), 'volume': value} for index, value in enumerate(buckets)],
            'audit': {'unique_records': len(events[event]), 'duplicates_removed': duplicates[event],
                      'records_in_first_72h': sum(buckets), 'start': begin.isoformat()},
        }
    return prepared, hashlib.sha256(contents).hexdigest()


def make_case(root, event, prepared):
    research = ResearchStore(root)
    case = research.create_case({
        'name': f'PHEME 真实历史样本 / {event}', 'rows': prepared['rows'][:60],
        'cutoff': prepared['rows'][11]['timestamp'], 'bucket_hours': 1, 'horizon': 48,
        'background': '公开历史对话样本的数量回放。此案例未使用帖子正文，不能作为多智能体语义预测效果验证。',
        'source': {'platform': 'twitter', 'source_name': 'PHEME rumours and non-rumours / ' + event,
                   'source_url': SOURCE, 'sampling_method': '固定 PHEME 对话样本；源帖和回复按 ID 去重；首个源帖所在小时起 72 小时，UTC 小时桶；非全平台总量。'},
    })
    frozen = research.freeze(case['case_id'], {'method': 'moving_average', 'variant': '近 3 桶均值起始参考（非 LLM）'})
    return case, frozen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--benchmark', type=Path)
    parser.add_argument('--import-local', action='store_true')
    args = parser.parse_args()
    prepared, archive_hash = load_counts(args.archive)
    args.output.mkdir(parents=True, exist_ok=True)
    metadata = {'dataset': 'PHEME rumours and non-rumours', 'source': SOURCE, 'doi': DOI,
                'archive_md5': ARCHIVE_MD5, 'archive_sha256': archive_hash, 'license': 'CC BY 4.0 (Figshare metadata)',
                'derivation': '仅保留匿名 UTC 小时计数；不分发帖子正文、用户信息或单条帖子 ID。',
                'sampling_limit': '对话样本覆盖范围内的计数；空桶仅表示本归档没有记录，不能当作全平台真实零值。',
                'events': {event: value['audit'] for event, value in prepared.items()}}
    (args.output / 'source.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for event, value in prepared.items():
        with (args.output / f'{event}.csv').open('w', encoding='utf-8', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=['timestamp', 'volume'])
            writer.writeheader()
            writer.writerows(value['rows'])
    if args.import_local:
        for event, value in prepared.items():
            case, _ = make_case(Config.UPLOAD_FOLDER, event, value)
            print(f"已导入 {event}: {case['case_id']}", flush=True)
    if args.benchmark:
        results = []
        with tempfile.TemporaryDirectory(prefix='mirofish-pheme-replay-') as root:
            for event, value in prepared.items():
                case, frozen = make_case(root, event, value)
                started = time.perf_counter()
                result = RollingStore(root).replay(case['case_id'], frozen['forecast_id'])
                methods = Counter(version['calibration']['selected_method'] for version in result['versions'])
                results.append({'event': event, 'summary': result['summary'], 'selected_methods': dict(methods),
                                'elapsed_seconds': round(time.perf_counter() - started, 3)})
                print(f"回放完成 {event}", flush=True)
        benchmark = {'kind': 'real_dataset_local_numeric_replay', 'source': metadata, 'real_api_calls': 0,
                     'policy': validate_policy({}), 'algorithm_version': 'bounded-residual-expert-v1',
                     'implementation_sha256': {name: hashlib.sha256((Path(__file__).resolve().parents[1] / 'app' / 'research' / name).read_bytes()).hexdigest()
                                               for name in ('calibration.py', 'rolling.py', 'evaluation.py')},
                     'protocol': '每事件前 12 小时观测，后 48 小时逐桶释放；固定默认策略，不在结果上调参；初始参考为近 3 桶均值。',
                     'not_a_mirofish_llm_accuracy_benchmark': True, 'events': results,
                     'limitations': ['验证数据管道和本地自适应数值层，未运行真实 LLM 多智能体推演。',
                                     '这是历史对话样本，不能代表当前事件、中文平台或全平台传播量。',
                                     '同信息最近值、均值和影子残差方案均单独报告；算法可能不优于简单基线。']}
        args.benchmark.parent.mkdir(parents=True, exist_ok=True)
        args.benchmark.write_text(json.dumps(benchmark, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')
    print(json.dumps({'events': list(prepared), 'archive_sha256': archive_hash, 'real_api_calls': 0}, ensure_ascii=False))


if __name__ == '__main__':
    main()
