"""Unified event workflow APIs; paid actions stay in explicit native workflows."""

import json
from flask import Response, jsonify, request

from . import research_bp
from ..config import Config
from ..research.events import EventStore
from ..research.evidence_report import report_html
from ..research.store import ResearchStore


def events():
    return EventStore(Config.UPLOAD_FOLDER)


@research_bp.route('/events', methods=['GET', 'POST'])
def event_list():
    service = events()
    return jsonify(success=True, data=service.create(request.get_json() or {}) if request.method == 'POST' else service.list())


@research_bp.route('/events/demo', methods=['POST'])
def event_demo():
    return jsonify(success=True, data=events().demo())


@research_bp.route('/events/<identifier>', methods=['GET'])
def event_workspace(identifier):
    return jsonify(success=True, data=events().workspace(identifier))


@research_bp.route('/events/<identifier>/freeze', methods=['POST'])
def event_freeze(identifier):
    service = events()
    _, operation = service.freeze(identifier, request.get_json() or {})
    return jsonify(success=True, data=service.workspace(identifier), operation=operation)


@research_bp.route('/events/<identifier>/observations', methods=['POST'])
def event_observations(identifier):
    service = events()
    service.ingest(identifier, request.get_json() or {})
    return jsonify(success=True, data=service.workspace(identifier))


@research_bp.route('/events/<identifier>/replay-step', methods=['POST'])
def event_replay(identifier):
    service = events()
    service.replay_step(identifier, request.get_json() or {})
    return jsonify(success=True, data=service.workspace(identifier))


@research_bp.route('/events/<identifier>/refresh-case', methods=['POST'])
def event_refresh(identifier):
    service = events()
    _, operation = service.refresh_case(identifier, request.get_json() or {})
    return jsonify(success=True, data=service.workspace(identifier), operation=operation)


@research_bp.route('/events/<identifier>/report', methods=['POST'])
def event_report(identifier):
    service = events()
    _, operation = service.create_report(identifier, request.get_json() or {})
    return jsonify(success=True, data=service.get_report(operation['report_id']))


@research_bp.route('/evidence/<identifier>', methods=['GET'])
def evidence(identifier):
    return jsonify(success=True, data=events().get_report(identifier))


@research_bp.route('/evidence/<identifier>/export', methods=['GET'])
def evidence_export(identifier):
    report = events().get_report(identifier)
    format = request.args.get('format', 'md')
    if format not in {'md', 'html', 'json'}:
        return jsonify(success=False, error='报告格式仅支持 md、html、json'), 400
    content = report_html(report) if format == 'html' else json.dumps(report, ensure_ascii=False, indent=2) if format == 'json' else report['markdown']
    mime = {'md': 'text/markdown', 'html': 'text/html', 'json': 'application/json'}[format]
    return Response(content, mimetype=mime, headers={'Content-Disposition': f'attachment; filename={identifier}.{format}'})
