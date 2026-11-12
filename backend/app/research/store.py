"""Versioned local cases and immutable forecast snapshots."""

from __future__ import annotations

import json
import hashlib
import os
import re
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .evaluation import baseline, digest, normalize_rows, parse_rows, score, simulation_curve, split_case, timestamp, validate_forecast
from .intake import source_metadata

_lock = threading.RLock()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def safe_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z]+_[a-zA-Z0-9]{8,40}", value):
        raise ValueError("资源 ID 格式无效")
    return value


def read_json(path):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class ResearchStore:
    def __init__(self, root):
        self.root = Path(root)
        self.cases_dir = self.root / "research" / "cases"
        self.forecasts_dir = self.root / "research" / "forecasts"

    def case(self, case_id):
        path = self.cases_dir / f"{safe_id(case_id)}.json"
        if not path.exists():
            raise ValueError("验证案例不存在")
        case = read_json(path)
        if case['train_hash'] != digest(case['train']):
            raise ValueError('案例早期数据指纹校验失败，拒绝使用被修改的材料')
        if case.get('rolling_link'):
            from .rolling import RollingStore
            session = RollingStore(self.root).get(case['rolling_link']['session_id'])
            for row in session['observations']:
                if row['timestamp'] in case['expected']:
                    case['truth'][row['timestamp']] = row
        return case

    def public_case(self, case):
        # Held-out data never appears in a pre-forecast API response or seed.
        return {key: case[key] for key in ("case_id", "name", "cutoff", "bucket_hours", "horizon", "train", "expected", "train_hash", "is_demo", "created_at", "unit")} | {"truth_buckets": len(case["truth"]), "background": case.get('background', ''), "source": case.get('source', {})}

    def create_case(self, data, *, demo=False):
        rows = parse_rows(data["content"], data.get("format", "csv")) if "content" in data else normalize_rows(data.get("rows"))
        train, expected, truth = split_case(rows, data.get("cutoff"), data.get("bucket_hours", 1), data.get("horizon", 6))
        name = str(data.get("name", "未命名案例")).strip()[:120]
        if not name:
            raise ValueError("案例名称不能为空")
        background = data.get('background', '')
        if not isinstance(background, str) or len(background) > 20000:
            raise ValueError('截止前背景材料须为不超过 20000 字的文本')
        case = {"case_id": f"case_{uuid.uuid4().hex[:16]}", "name": name,
                "cutoff": timestamp(data["cutoff"]).isoformat(), "bucket_hours": int(data.get("bucket_hours", 1)),
                "horizon": len(expected), "train": train, "expected": expected, "truth": truth,
                "train_hash": digest(train), "is_demo": demo, "created_at": utc_now(),
                "background": background, "source": source_metadata(data.get('source')),
                "unit": "每时间桶发帖与评论数量（须保持同一平台和采样口径）"}
        write_json(self.cases_dir / f"{case['case_id']}.json", case)
        return self.public_case(case)

    def create_demo(self):
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        values = [8, 12, 16, 22, 30, 42, 56, 65, 72, 66, 52, 40, 32, 26, 20, 16, 12, 10]
        rows = [{"timestamp": (start + timedelta(hours=i)).isoformat(), "volume": value,
                 "negative_ratio": round(0.2 + value / 150, 4)} for i, value in enumerate(values)]
        return self.create_case({"name": "合成示例：验证流程演示（非真实事件）", "rows": rows,
                                 "cutoff": rows[11]["timestamp"], "bucket_hours": 1, "horizon": 6,
                                 "background": '纯合成事件：示例学校官方账号发布校园服务调整通知。示例学生甲、示例学生乙和示例媒体账号参与讨论。以上角色、事件及统计均为演示构造。'}, demo=True)

    def list_cases(self):
        if not self.cases_dir.exists():
            return []
        return sorted((self.public_case(self.case(path.stem)) for path in self.cases_dir.glob("case_*.json")), key=lambda case: case["created_at"], reverse=True)

    def seed_text(self, case_id):
        case = self.case(case_id)
        return (f"案例：{case['name']}\n数据性质：{'合成示例，不代表真实事件' if case['is_demo'] else '用户提供，来源需另行核验'}\n"
                f"观测截止时间：{case['cutoff']}\n每桶时长：{case['bucket_hours']} 小时\n"
                "timestamp 表示时间桶结束时刻；截止桶的数据已完整观测。\n"
                f"预测未来 {case['horizon']} 桶。仅允许使用下面的早期观测；未提供后续真实数据。\n"
                f"统计口径：{case['unit']}\n"
                f"截止前背景（用户提供，时效性需人工核验）：{case.get('background', '')}\n"
                + "\n".join(json.dumps(row, ensure_ascii=False) for row in case["train"]))

    def requirement(self, case_id):
        case = self.case(case_id)
        return f"仅基于截止 {case['cutoff']} 的材料，推演后续 {case['horizon'] * case['bucket_hours']} 小时的讨论发展。请模拟足够轮次覆盖预测窗口。"

    def add_truth(self, case_id, data):
        incoming = parse_rows(data["content"], data.get("format", "csv"))
        with _lock:
            case = self.case(case_id)
            train_by_time = {row["timestamp"]: row for row in case["train"]}
            for row in incoming:
                time = row["timestamp"]
                if time in train_by_time:
                    if row != train_by_time[time]:
                        raise ValueError("冻结的早期数据不能修改；请新建案例")
                elif time in case["expected"]:
                    if time in case["truth"] and case["truth"][time] != row:
                        raise ValueError("已提交的真实值不能覆盖；修订数据应新建案例")
                    case["truth"][time] = row
                else:
                    raise ValueError("真实数据包含预测窗口之外的时间桶")
            write_json(self.cases_dir / f"{case_id}.json", case)
        return self.public_case(case)

    def forecast(self, forecast_id):
        path = self.forecasts_dir / f"{safe_id(forecast_id)}.json"
        if not path.exists():
            raise ValueError("预测快照不存在")
        frozen = read_json(path)
        claimed = frozen.pop('snapshot_hash', None)
        if claimed != digest(frozen):
            raise ValueError('预测快照指纹不匹配，拒绝评估被修改的预测')
        frozen['snapshot_hash'] = claimed
        return frozen

    def freeze(self, case_id, data):
        case = self.case(case_id)
        method = data.get("method", "moving_average")
        metadata = {}
        if method in {"last_value", "moving_average"}:
            prediction = baseline(case["train"], case["expected"], method)
        elif method == "import":
            prediction = validate_forecast(parse_rows(data["content"], data.get("format", "csv")), case["expected"])
            metadata["warning"] = "外部预测：系统无法验证其生成过程是否接触过未来数据。"
        elif method == "simulation":
            prediction, metadata = self._from_simulation(case, data)
        elif method == 'rolling':
            from .rolling import RollingStore
            session = RollingStore(self.root).get(data.get('session_id'))
            index = data.get('version')
            if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(session['versions']):
                raise ValueError('滚动预测版本号无效')
            version = session['versions'][index]
            if (case['train'] != session['observations'][:version['observed_buckets']] or
                    case['cutoff'] != version['cutoff'] or case['horizon'] != session['horizon'] or case['bucket_hours'] != session['bucket_hours']):
                raise ValueError('滚动预测版本与冻结案例不匹配')
            prediction = version['prediction']
            metadata = {**version['reference_metadata'], 'rolling_session_id': session['session_id'],
                        'rolling_version': index, 'rolling_version_hash': version['snapshot_hash'],
                        'calibration': version['calibration'],
                        'warning': '数值热度校准，不代表新的语义推演或绝对传播规模保证。'}
        else:
            raise ValueError("未知预测来源")
        frozen = {"forecast_id": f"forecast_{uuid.uuid4().hex[:16]}", "case_id": case_id,
                  "method": method, "variant": str(data.get("variant", method))[:60], "created_at": utc_now(),
                  "prediction": validate_forecast(prediction, case["expected"]), "train_hash": case["train_hash"],
                  "is_demo": case["is_demo"], "metadata": metadata}
        frozen["snapshot_hash"] = digest(frozen)
        write_json(self.forecasts_dir / f"{frozen['forecast_id']}.json", frozen)
        return frozen

    def _from_simulation(self, case, data):
        sim_id = safe_id(data.get("simulation_id"))
        platform = data.get("platform", "reddit")
        if platform not in {"twitter", "reddit"}:
            raise ValueError("须选取一个与真实数据口径一致的平台")
        folder = self.root / "simulations" / sim_id
        if not (folder / "run_state.json").exists():
            raise ValueError("未找到实际仿真运行记录")
        run, config = read_json(folder / "run_state.json"), read_json(folder / "simulation_config.json")
        if run["runner_status"] not in {"completed", "stopped"}:
            raise ValueError("须先等待仿真成功结束或完成停止，再冻结预测")
        anchor_mode = data.get("scale_mode", "history_anchor")
        if anchor_mode not in {"raw", "history_anchor"}:
            raise ValueError("未知热度映射方式")
        log = folder / platform / "actions.jsonl"
        if not log.exists():
            raise ValueError("所选平台没有动作日志")
        curve, factor = simulation_curve(log, case["expected"], config["time_config"]["minutes_per_round"], case["bucket_hours"],
                                         completed_rounds=run.get(f"{platform}_current_round", 0),
                                         anchor=case["train"][-1]["volume"] if anchor_mode == "history_anchor" else None)
        evidence = config.get("research_provenance", {})
        isolated = evidence.get("case_id") == case["case_id"] and evidence.get("train_hash") == case["train_hash"] and evidence.get("pristine_graph") is True
        return curve, {"simulation_id": sim_id, "project_id": config.get('project_id'), "platform": platform, "scale_mode": anchor_mode, "scale_factor": factor,
                       "log_sha256": hashlib.sha256(log.read_bytes()).hexdigest(), "config_sha256": digest(config),
                       "early_input_verified": isolated, "runtime_scope_id": run.get("runtime_scope_id"),
                       "warning": ("仅做热度曲线映射；绝对传播规模需另行校准。" if isolated else
                                   "未确认仅使用早期材料或图谱隔离；本结果只能用于探索性对比。"),
                       "sentiment_note": "原始动作没有可靠情感标注，未自动捏造负面比例。"}

    def evaluate(self, forecast_id):
        frozen = self.forecast(forecast_id)
        case = self.case(frozen["case_id"])
        if frozen['method'] == 'rolling':
            from .rolling import RollingStore
            session = RollingStore(self.root).get(frozen['metadata']['rolling_session_id'])
            for row in session['observations']:
                if row['timestamp'] in case['expected']:
                    case['truth'][row['timestamp']] = row
        if frozen["train_hash"] != case["train_hash"]:
            raise ValueError("早期数据指纹与冻结预测不一致")
        scored = score(case["train"], case["expected"], case["truth"], frozen["prediction"], case["bucket_hours"])
        return {"forecast": frozen, "case": self.public_case(case), "scores": scored,
                "truth_hash": digest(case["truth"]), "evaluated_at": utc_now(),
                "evidence_level": "synthetic_demo" if case["is_demo"] else "user_supplied_observations"}

    def list_forecasts(self, case_id=None):
        if not self.forecasts_dir.exists():
            return []
        values = [read_json(path) for path in self.forecasts_dir.glob("forecast_*.json")]
        return sorted((item for item in values if case_id is None or item["case_id"] == case_id), key=lambda item: item["created_at"], reverse=True)
