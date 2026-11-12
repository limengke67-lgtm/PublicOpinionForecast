"""Serve the built SPA and API from one local research deployment."""

from pathlib import Path
from flask import jsonify, send_from_directory


def register_frontend(app, directory):
    root = Path(directory).resolve()
    if not (root / 'index.html').is_file():
        raise ValueError('未找到前端构建产物，请先执行 npm run build')

    @app.route('/', defaults={'path': ''})
    @app.route('/<path:path>')
    def frontend(path):
        if path.startswith('api/') or path == 'api' or path.startswith('health/'):
            return jsonify(success=False, error='接口不存在'), 404
        candidate = (root / path).resolve()
        if path and candidate.is_relative_to(root) and candidate.is_file():
            return send_from_directory(root, path)
        if Path(path).suffix:
            return jsonify(success=False, error='静态文件不存在'), 404
        return send_from_directory(root, 'index.html')
