"""Process-safe usage accounting and opt-in, preparation-only result reuse.

The SDK hook observes both MiroFish and CAMEL calls without storing prompts,
responses or credentials in the usage ledger. Agent decisions are never cached.
"""

from __future__ import annotations

import contextvars
import functools
import hashlib
import inspect
import json
import math
import os
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

_context = contextvars.ContextVar("mirofish_llm_scope", default=None)
_locks = {}
_guard = threading.Lock()
_database_init_lock = threading.Lock()


class BudgetExceeded(RuntimeError):
    pass


def runtime_dir():
    return Path(os.environ.get("MIROFISH_RUNTIME_DIR") or Path(__file__).resolve().parents[2] / "uploads" / "runtime")


def settings():
    path = runtime_dir() / "settings.json"
    values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    return {"mode": "efficient", "prepare_workers": 4, "max_requests_per_scope": 0,
            "budget_cny_per_scope": 0, "prices": {}, **values}


def save_settings(data):
    current = settings()
    if data.get("mode", current["mode"]) not in {"baseline", "efficient"}:
        raise ValueError("运行模式只能是 baseline 或 efficient")
    for name, maximum in (("prepare_workers", 16), ("max_requests_per_scope", 100000)):
        if name in data:
            value = data[name]
            if isinstance(value, bool) or not isinstance(value, int) or value < (1 if name == "prepare_workers" else 0) or value > maximum:
                raise ValueError(f"{name} 数值无效")
    for value in [data.get("budget_cny_per_scope", 0)]:
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
            raise ValueError("预算必须是非负有限数值")
    prices = data.get("prices", current["prices"])
    if not isinstance(prices, dict) or len(prices) > 100:
        raise ValueError("模型单价格式无效")
    for model, rates in prices.items():
        if not isinstance(model, str) or not model or not isinstance(rates, dict) or not {"input", "output"}.issubset(rates):
            raise ValueError("每个模型需要 input、output 单价（元/百万 Token）")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v < 0 for v in rates.values()):
            raise ValueError("单价必须是非负有限数值")
    current.update({key: data[key] for key in ("mode", "prepare_workers", "max_requests_per_scope", "budget_cny_per_scope", "prices") if key in data})
    from ..research.store import write_json
    write_json(runtime_dir() / "settings.json", current)
    return current


def context():
    return _context.get() or {"scope_id": os.environ.get("MIROFISH_SCOPE_ID", "unscoped"),
                              "stage": os.environ.get("MIROFISH_STAGE", "unscoped"),
                              "mode": os.environ.get("MIROFISH_MODE") or settings()["mode"]}


@contextmanager
def scope(scope_id, stage, mode=None):
    token = _context.set({"scope_id": str(scope_id), "stage": stage, "mode": mode or context()["mode"]})
    try:
        yield
    finally:
        _context.reset(token)


def tracked(stage, id_argument=None):
    def decorate(function):
        signature = inspect.signature(function)

        @functools.wraps(function)
        def wrapped(*args, **kwargs):
            bound = signature.bind(*args, **kwargs)
            identifier = bound.arguments.get(id_argument) if id_argument else None
            if not identifier and id_argument and 'self' in bound.arguments:
                identifier = getattr(bound.arguments['self'], id_argument, None)
            with scope(identifier or context()["scope_id"], stage):
                return function(*args, **kwargs)
        return wrapped
    return decorate


@contextmanager
def database():
    root = runtime_dir()
    root.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(root / "usage.sqlite3", timeout=30)
    db.row_factory = sqlite3.Row
    try:
        # WAL negotiation needs serialization even before the first transaction.
        # Other simulation processes may initialize the same ledger concurrently.
        with _database_init_lock:
            deadline = time.monotonic() + 30
            while True:
                try:
                    db.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError as error:
                    if not any(word in str(error).lower() for word in ('locked', 'busy')) or time.monotonic() >= deadline:
                        raise
                    time.sleep(.05)
            db.executescript("""
        CREATE TABLE IF NOT EXISTS requests (
            id TEXT PRIMARY KEY, scope_id TEXT, stage TEXT, mode TEXT, model TEXT,
            started REAL, duration_ms REAL, status TEXT, input_tokens INTEGER,
            output_tokens INTEGER, cached_tokens INTEGER, estimated_cny REAL,
            usage_complete INTEGER DEFAULT 0, error_type TEXT);
        CREATE INDEX IF NOT EXISTS requests_scope ON requests(scope_id);
        CREATE TABLE IF NOT EXISTS preparation_cache (key TEXT PRIMARY KEY, value TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS cache_events (scope_id TEXT, stage TEXT, created REAL);
        CREATE TABLE IF NOT EXISTS budget_blocks (scope_id TEXT, created REAL);
            """)
        with db:
            yield db
    finally:
        db.close()


def reserve(model):
    config, current = settings(), context()
    identifier = uuid.uuid4().hex
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        counts = db.execute("SELECT COUNT(*) n, SUM(estimated_cny) cost, SUM(CASE WHEN status IN ('ok','error','stream') AND usage_complete=0 THEN 1 ELSE 0 END) unknown FROM requests WHERE scope_id=?", (current["scope_id"],)).fetchone()
        def block(message):
            # Persist the denial even if an agent library catches the exception.
            db.execute('INSERT INTO budget_blocks VALUES(?,?)', (current['scope_id'], time.time()))
            db.commit()
            raise BudgetExceeded(message)
        if config["max_requests_per_scope"] and counts["n"] >= config["max_requests_per_scope"]:
            block("本次运行已达到 SDK 请求次数上限，未继续调用模型")
        if config["budget_cny_per_scope"]:
            if model not in config["prices"] or counts["unknown"]:
                block("金额预算需要已配置模型单价且所有已完成请求均返回用量")
            if (counts["cost"] or 0) >= config["budget_cny_per_scope"]:
                block("本次运行已达到金额预算；并发在途请求可能产生少量超额")
        db.execute("INSERT INTO requests(id,scope_id,stage,mode,model,started,status) VALUES(?,?,?,?,?,?,?)",
                   (identifier, current["scope_id"], current["stage"], current["mode"], str(model), time.time(), "running"))
    return identifier


def field(value, name, default=None):
    return value.get(name, default) if isinstance(value, dict) else getattr(value, name, default)


def finish(identifier, response, duration_ms, *, error=None, stream=False):
    usage = field(response, "usage")
    incoming, outgoing = field(usage, "prompt_tokens"), field(usage, "completion_tokens")
    cached = field(field(usage, "prompt_tokens_details"), "cached_tokens", 0)
    valid = all(isinstance(value, int) and not isinstance(value, bool) and value >= 0 for value in (incoming, outgoing, cached))
    if valid and cached > incoming:
        valid = False
    with database() as db:
        row = db.execute("SELECT model FROM requests WHERE id=?", (identifier,)).fetchone()
        rates = settings()["prices"].get(row["model"])
        cost = None
        if valid and rates:
            cost = ((incoming - cached) * rates["input"] + cached * rates.get("cached_input", rates["input"]) + outgoing * rates["output"]) / 1_000_000
        db.execute("UPDATE requests SET duration_ms=?,status=?,input_tokens=?,output_tokens=?,cached_tokens=?,estimated_cny=?,usage_complete=?,error_type=? WHERE id=?",
                   (duration_ms, "error" if error else "stream" if stream else "ok", incoming if valid else None,
                    outgoing if valid else None, cached if valid else None, cost, int(valid and rates is not None),
                    type(error).__name__ if error else None, identifier))


def observe_sync(function, ledger_model, *args, **kwargs):
    identifier, start = reserve(ledger_model), time.perf_counter()
    try:
        response = function(*args, **kwargs)
    except Exception as error:
        finish(identifier, None, (time.perf_counter() - start) * 1000, error=error)
        raise
    finish(identifier, response, (time.perf_counter() - start) * 1000, stream=bool(kwargs.get("stream")))
    return response


async def observe_async(function, ledger_model, *args, **kwargs):
    identifier, start = reserve(ledger_model), time.perf_counter()
    try:
        response = await function(*args, **kwargs)
    except Exception as error:
        finish(identifier, None, (time.perf_counter() - start) * 1000, error=error)
        raise
    finish(identifier, response, (time.perf_counter() - start) * 1000, stream=bool(kwargs.get("stream")))
    return response


def install_openai_instrumentation():
    """Observe the SDK boundary, including calls originating inside CAMEL."""
    from openai.resources.chat.completions import Completions, AsyncCompletions
    if getattr(Completions.create, "_mirofish_observed", False):
        return
    sync_create, async_create = Completions.create, AsyncCompletions.create

    @functools.wraps(sync_create)
    def sync_wrapper(self, *args, **kwargs):
        return observe_sync(sync_create, kwargs.get("model", "unknown"), self, *args, **kwargs)

    @functools.wraps(async_create)
    async def async_wrapper(self, *args, **kwargs):
        return await observe_async(async_create, kwargs.get("model", "unknown"), self, *args, **kwargs)

    sync_wrapper._mirofish_observed = True
    Completions.create, AsyncCompletions.create = sync_wrapper, async_wrapper


def reuse_preparation(function):
    """Reuse validated profile data, never role decisions or independent trials."""
    @functools.wraps(function)
    def wrapped(self, *args, **kwargs):
        if context()["mode"] != "efficient":
            return function(self, *args, **kwargs)
        from .locale import get_locale, get_language_instruction
        sources = [inspect.getsource(function)] + [inspect.getsource(getattr(type(self), name)) for name in
                  ('_get_system_prompt', '_build_individual_persona_prompt', '_build_group_persona_prompt') if hasattr(type(self), name)]
        payload = [hashlib.sha256('\n'.join(sources).encode()).hexdigest(),
                   self.model_name, self.base_url, hashlib.sha256(self.api_key.encode()).hexdigest(),
                   get_locale(), get_language_instruction(), args, kwargs]
        key = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        with _guard:
            lock = _locks.setdefault(key, threading.Lock())
        with lock:
            with database() as db:
                cached = db.execute("SELECT value FROM preparation_cache WHERE key=? AND created>?", (key, time.time() - 7 * 86400)).fetchone()
                if cached:
                    current = context()
                    db.execute("INSERT INTO cache_events VALUES(?,?,?)", (current["scope_id"], "profile", time.time()))
                    return json.loads(cached["value"])
            result = function(self, *args, **kwargs)
            if isinstance(result, dict) and result.get("bio") and result.get("persona") and result.get("_cacheable", True):
                with database() as db:
                    db.execute("INSERT OR REPLACE INTO preparation_cache VALUES(?,?,?)", (key, json.dumps(result, ensure_ascii=False), time.time()))
            return result
    return wrapped


def usage_summary(scope_id=None):
    with database() as db:
        if isinstance(scope_id, list):
            params = tuple(dict.fromkeys(scope_id))
            clause = ' WHERE scope_id IN (' + ','.join('?' for _ in params) + ')' if params else ' WHERE 0'
        else:
            clause, params = (" WHERE scope_id=?", (scope_id,)) if scope_id else ("", ())
        rows = [dict(row) for row in db.execute("SELECT * FROM requests" + clause + " ORDER BY started DESC", params)]
        hit_query = "SELECT COUNT(*) FROM cache_events" + clause
        hits = db.execute(hit_query, params).fetchone()[0]
    def summarize(items):
        completed = [row for row in items if row["status"] != "running"]
        known = [row for row in completed if row["estimated_cny"] is not None]
        return {"requests": len(items), "completed_requests": len(completed), "failed_requests": sum(row["status"] == "error" for row in items),
                "input_tokens": sum(row["input_tokens"] or 0 for row in items), "output_tokens": sum(row["output_tokens"] or 0 for row in items),
                "cached_tokens": sum(row["cached_tokens"] or 0 for row in items), "unknown_usage_requests": sum(row["input_tokens"] is None for row in completed),
                "estimated_cny": sum(row["estimated_cny"] for row in known) if len(known) == len(completed) and completed else None,
                "known_estimated_cny": sum(row["estimated_cny"] for row in known), "incomplete_cost_requests": len(completed) - len(known),
                "total_request_latency_ms": sum(row["duration_ms"] or 0 for row in items),
                "wall_time_ms": max(0, (max((row["started"] + (row["duration_ms"] or 0) / 1000) for row in items) - min(row["started"] for row in items)) * 1000) if items else 0}
    stages = sorted({row["stage"] for row in rows})
    return {"scope_id": scope_id, "totals": summarize(rows), "stages": {stage: summarize([row for row in rows if row["stage"] == stage]) for stage in stages},
            "preparation_cache_hits": hits, "recent_requests": rows[:100],
            "notes": ["金额按用户配置单价估算，不等同供应商账单；Zep 费用另计。",
                      "失败、流式或缺失 usage 的调用不伪记为零成本。SDK 内部网络重试次数不在本计数内。",
                      "金额预算在已知费用达到阈值后阻止新请求；并发在途请求可能超额。"]}
