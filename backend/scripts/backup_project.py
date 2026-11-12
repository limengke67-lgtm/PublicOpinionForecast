"""Local project-data backup and restore, excluding credentials and dependencies."""

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Config
from app.research.store import utc_now


def backup(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if not source.is_dir() or output.exists() or output.is_relative_to(source):
        raise ValueError('源目录须存在；输出须为数据目录之外尚不存在的文件')
    output.parent.mkdir(parents=True, exist_ok=True)
    records = []
    with tempfile.TemporaryDirectory(prefix='forecast-backup-') as temporary:
        staged = Path(temporary)
        for path in sorted(source.rglob('*')):
            if path.is_symlink():
                raise ValueError('数据目录含符号链接，备份已拒绝')
            if not path.is_file() or path.name.endswith(('-wal', '-shm', '-journal')) or path.name.startswith('.'):
                continue
            relative = path.relative_to(source)
            if path.name.startswith('.env'):
                continue
            target = staged / 'data' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            with path.open('rb') as stream:
                sqlite = stream.read(16) == b'SQLite format 3\x00'
            if sqlite:
                uri = 'file:' + quote(path.as_posix(), safe='/:') + '?mode=ro'
                with closing(sqlite3.connect(uri, uri=True, timeout=10)) as origin, closing(sqlite3.connect(target)) as destination:
                    origin.backup(destination, pages=256)
            else:
                shutil.copyfile(path, target)
                if path.suffix == '.json':
                    json.loads(target.read_text(encoding='utf-8'))
            records.append({'path': relative.as_posix(), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'size': target.stat().st_size})
        manifest = {'schema': 'public-opinion-forecast-backup-v1', 'created_at': utc_now(), 'files': records,
                    'note': '仅本地数据；不包含模型凭据、云图谱服务、依赖环境或未保存的角色进程状态。'}
        (staged / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary_archive = staged / 'archive.zip'
        with zipfile.ZipFile(temporary_archive, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.write(staged / 'manifest.json', 'manifest.json')
            for record in records:
                archive.write(staged / 'data' / record['path'], 'data/' + record['path'])
        # Exclusive creation never overwrites a previous backup.
        with output.open('xb') as destination, temporary_archive.open('rb') as origin:
            shutil.copyfileobj(origin, destination)
    return manifest


def restore(archive_path, destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('恢复目标必须是尚不存在的新目录，禁止覆盖现有项目数据')
    with zipfile.ZipFile(archive_path) as archive:
        infos = archive.infolist()
        if len(infos) > 100000 or sum(item.file_size for item in infos) > 2 * 1024 ** 3:
            raise ValueError('备份文件数量或解压总量超过限制')
        names = [item.filename for item in infos]
        if len(names) != len(set(names)):
            raise ValueError('备份包含重复路径')
        manifest = json.loads(archive.read('manifest.json'))
        if manifest.get('schema') != 'public-opinion-forecast-backup-v1':
            raise ValueError('备份格式不支持')
        approved = {'manifest.json'}
        for record in manifest['files']:
            relative = Path(record['path'])
            target = (destination / relative).resolve()
            if relative.is_absolute() or not target.is_relative_to(destination) or '\\' in record['path'] or ':' in record['path']:
                raise ValueError('备份包含越界路径')
            name = 'data/' + record['path']
            approved.add(name)
            payload = archive.read(name)
            if len(payload) != record['size'] or hashlib.sha256(payload).hexdigest() != record['sha256']:
                raise ValueError('备份内容校验失败，未写入恢复目录')
        if set(names) != approved:
            raise ValueError('备份存在未在清单声明的文件')
        # All paths and bytes are verified before any output is written.
        destination.mkdir(parents=True, exist_ok=False)
        for record in manifest['files']:
            target = destination / record['path']
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read('data/' + record['path']))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--restore', type=Path)
    parser.add_argument('--destination', type=Path)
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[2]
    for name in ('output', 'restore', 'destination'):
        value = getattr(args, name)
        if value is not None and not value.is_absolute():
            setattr(args, name, project_root / value)
    if args.restore:
        if not args.destination:
            parser.error('恢复必须指定新的 --destination 目录')
        result = restore(args.restore, args.destination)
        print(f"已校验并恢复 {len(result['files'])} 个文件；请核查中断运行，云服务和未保存进程不会自动恢复。")
    elif args.output:
        result = backup(Config.UPLOAD_FOLDER, args.output)
        print(f"已备份 {len(result['files'])} 个文件。项目 .env 不在备份内；备份可能包含原始私有资料，请勿公开上传。")
    else:
        parser.error('请选择 --output 或 --restore / --destination')


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, sqlite3.Error, zipfile.BadZipFile) as error:
        print(f'操作未完成：{error}', file=sys.stderr)
        raise SystemExit(1)
