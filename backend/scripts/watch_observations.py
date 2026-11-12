"""Feed a local CSV/JSON export folder into a rolling task; never call a model."""

import argparse
import hashlib
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Config
from app.research.rolling import RollingStore


def scan(store, identifier, folder):
    failures, applied = [], 0
    files = sorted([path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in {'.csv', '.json'}])
    for path in files:
        try:
            if path.stat().st_size > 10 * 1024 * 1024:
                raise ValueError('文件超过 10 MB')
            content = path.read_text(encoding='utf-8-sig')
            request_id = 'file_' + hashlib.sha256((path.name + '\n' + content).encode()).hexdigest()
            session = store.get(identifier)
            result = store.ingest(identifier, {'expected_revision': session['revision'], 'request_id': request_id,
                                               'content': content, 'format': path.suffix[1:].lower(),
                                               'source_note': '本地采集文件：' + path.name})
            if not result.get('replayed_request'):
                applied += 1
                print(f'已接收 {path.name} -> v{len(result["versions"]) - 1}', flush=True)
        except (ValueError, OSError) as error:
            failures.append((path.name, str(error)))
    return applied, failures


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--session', required=True)
    parser.add_argument('--folder', required=True, type=Path)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', default=15, type=int)
    args = parser.parse_args()
    if not args.folder.is_dir() or not 2 <= args.interval <= 3600:
        parser.error('folder 须为现有目录，interval 须为 2～3600 秒')
    store = RollingStore(Config.UPLOAD_FOLDER)
    store.get(args.session)
    reported = set()
    while True:
        _, failures = scan(store, args.session, args.folder)
        for failure in failures:
            if failure not in reported:
                print(f'待处理 {failure[0]}：{failure[1]}', file=sys.stderr, flush=True)
        reported = set(failures)
        if args.once:
            return int(bool(failures))
        time.sleep(args.interval)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print('已停止接收；任务和原始文件已保留。')
