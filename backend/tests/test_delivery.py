import hashlib
import json
import sqlite3
import sys
import zipfile
from pathlib import Path

import pytest
from flask import Flask

from app.frontend import register_frontend

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from backup_project import backup, restore


def test_spa_and_assets_serve_from_one_origin_without_swallowing_api_errors(tmp_path):
    (tmp_path / 'index.html').write_text('<main>PublicOpinionForecast</main>', encoding='utf-8')
    (tmp_path / 'assets').mkdir()
    (tmp_path / 'assets' / 'app.js').write_text('window.loaded=true', encoding='utf-8')
    app = Flask(__name__)
    register_frontend(app, tmp_path)
    client = app.test_client()
    assert 'PublicOpinionForecast' in client.get('/').text
    assert client.get('/events/event_12345678').status_code == 200
    assert client.get('/assets/app.js').status_code == 200
    assert client.get('/api/unknown').status_code == 404
    assert client.get('/assets/missing.js').status_code == 404
    assert client.get('/../../private.txt').status_code == 404


def test_backup_and_restore_json_and_sqlite_without_overwriting_or_keys(tmp_path):
    source = tmp_path / 'uploads'
    source.mkdir()
    (source / 'event.json').write_text('{"state":"saved"}', encoding='utf-8')
    (source / '.env').write_text('DO_NOT_EXPORT=secret', encoding='utf-8')
    with sqlite3.connect(source / 'usage.sqlite3') as db:
        db.execute('CREATE TABLE usage(n INTEGER)')
        db.execute('INSERT INTO usage VALUES(3)')
    archive = tmp_path / 'project.backup.zip'
    manifest = backup(source, archive)
    assert len(manifest['files']) == 2
    assert all('.env' not in row['path'] for row in manifest['files'])
    destination = tmp_path / 'restored'
    restore(archive, destination)
    assert json.loads((destination / 'event.json').read_text())['state'] == 'saved'
    with sqlite3.connect(destination / 'usage.sqlite3') as db:
        assert db.execute('SELECT n FROM usage').fetchone()[0] == 3
    with pytest.raises(ValueError, match='覆盖'):
        restore(archive, destination)
    with pytest.raises(ValueError):
        backup(source, archive)


def test_archive_path_traversal_is_rejected_before_writing(tmp_path):
    payload = b'invalid'
    manifest = {'schema': 'public-opinion-forecast-backup-v1', 'files': [
        {'path': '../escape.txt', 'sha256': hashlib.sha256(payload).hexdigest(), 'size': len(payload)}]}
    archive = tmp_path / 'invalid.zip'
    with zipfile.ZipFile(archive, 'w') as writer:
        writer.writestr('manifest.json', json.dumps(manifest))
        writer.writestr('data/../escape.txt', payload)
    with pytest.raises(ValueError, match='越界'):
        restore(archive, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()
    assert not (tmp_path / 'escape.txt').exists()
