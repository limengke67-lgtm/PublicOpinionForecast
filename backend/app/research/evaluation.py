"""Time-aligned forecast scoring, with no fitting on the held-out observations."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import statistics
from datetime import datetime, timedelta, timezone


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except (ValueError, TypeError) as exc:
        raise ValueError("时间必须使用 ISO 格式，例如 2026-01-01T08:00:00+08:00") from exc
    if parsed.tzinfo is None:
        raise ValueError("时间必须包含时区，避免真实时间和仿真时间错位")
    return parsed.astimezone(timezone.utc)


def number(value, name, *, minimum=0, maximum=None):
    if isinstance(value, bool):
        raise ValueError(f"{name} 必须是有限数值")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} 必须是数值") from exc
    if not math.isfinite(result) or result < minimum or (maximum is not None and result > maximum):
        raise ValueError(f"{name} 超出允许范围")
    return result


def positive_int(value, name, maximum=1000):
    result = number(value, name, minimum=1, maximum=maximum)
    if result != int(result):
        raise ValueError(f"{name} 必须是整数")
    return int(result)


def digest(value):
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def parse_rows(content, format="csv"):
    if not isinstance(content, str) or len(content.encode("utf-8")) > 10 * 1024 * 1024:
        raise ValueError("数据文件必须是小于 10 MB 的文本")
    if format == "csv":
        rows = list(csv.DictReader(io.StringIO(content.lstrip("\ufeff"))))
    elif format == "json":
        try:
            rows = json.loads(content.lstrip('\ufeff'))
        except json.JSONDecodeError as exc:
            raise ValueError("JSON 数据格式无效") from exc
    else:
        raise ValueError("仅支持 CSV 或 JSON")
    return normalize_rows(rows)


def normalize_rows(rows):
    if not isinstance(rows, list) or not rows or len(rows) > 10000:
        raise ValueError("数据必须包含 1～10000 个时间桶")
    result, seen = [], set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"第 {index + 1} 行必须是对象")
        time = timestamp(row.get("timestamp")).isoformat()
        if time in seen:
            raise ValueError("时间桶重复；请先按同一采样口径聚合")
        seen.add(time)
        item = {"timestamp": time, "volume": number(row.get("volume"), "volume", maximum=1e12)}
        if row.get("negative_ratio") not in (None, ""):
            item["negative_ratio"] = number(row["negative_ratio"], "negative_ratio", maximum=1)
        result.append(item)
    return sorted(result, key=lambda row: row["timestamp"])


def split_case(rows, cutoff, bucket_hours, horizon):
    bucket_hours = positive_int(bucket_hours, "bucket_hours", maximum=168)
    horizon = positive_int(horizon, "horizon", maximum=500)
    cutoff_dt = timestamp(cutoff)
    times = [timestamp(row["timestamp"]) for row in rows]
    if cutoff_dt not in times:
        raise ValueError("预测截止时间必须对应一个已有时间桶")
    step = timedelta(hours=bucket_hours)
    if any(right - left != step for left, right in zip(times, times[1:])):
        raise ValueError("时间桶不连续或间隔不一致；缺失数据不能自动当作零")
    train = [dict(row) for row in rows if timestamp(row["timestamp"]) <= cutoff_dt]
    if len(train) < 3:
        raise ValueError("预测前至少需要 3 个观测时间桶")
    expected = [(cutoff_dt + step * i).isoformat() for i in range(1, horizon + 1)]
    future = {row["timestamp"]: dict(row) for row in rows if row["timestamp"] in expected}
    return train, expected, future


def baseline(train, expected, method):
    if method not in {"last_value", "moving_average"}:
        raise ValueError("未知基线方法")
    sample = train[-1:] if method == "last_value" else train[-3:]
    volume = statistics.mean(row["volume"] for row in sample)
    ratio = [row["negative_ratio"] for row in sample if "negative_ratio" in row]
    return [dict(timestamp=time, volume=volume, **({"negative_ratio": statistics.mean(ratio)} if len(ratio) == len(sample) else {})) for time in expected]


def validate_forecast(rows, expected):
    values = normalize_rows(rows)
    if [row["timestamp"] for row in values] != expected:
        raise ValueError("预测必须逐桶覆盖整个预测窗口，不能缺桶、补桶或包含早期数据")
    return values


def curve_metrics(actual, predicted, bucket_hours):
    errors = [pred - real for real, pred in zip(actual, predicted)]
    n = len(errors)
    mae = sum(abs(error) for error in errors) / n
    rmse = math.sqrt(sum(error * error for error in errors) / n)
    total = sum(abs(value) for value in actual)
    smape = statistics.mean(0 if abs(a) + abs(p) == 0 else 2 * abs(a - p) / (abs(a) + abs(p)) for a, p in zip(actual, predicted))
    a_mean, p_mean = statistics.mean(actual), statistics.mean(predicted)
    denominator = math.sqrt(sum((a - a_mean) ** 2 for a in actual) * sum((p - p_mean) ** 2 for p in predicted))
    correlation = sum((a - a_mean) * (p - p_mean) for a, p in zip(actual, predicted)) / denominator if denominator else None
    # A flat curve has no identifiable peak time. Ties otherwise use the first peak.
    peak = abs(actual.index(max(actual)) - predicted.index(max(predicted))) * bucket_hours if len(set(actual)) > 1 and len(set(predicted)) > 1 else None
    return {"mae": mae, "rmse": rmse, "wape": sum(abs(e) for e in errors) / total if total else None,
            "smape": smape, "correlation": correlation, "peak_time_error_hours": peak,
            "peak_volume_error": abs(max(actual) - max(predicted))}


def score(train, expected, truth, prediction, bucket_hours):
    missing = [time for time in expected if time not in truth]
    if missing:
        raise ValueError(f"真实后续数据尚不完整，缺少 {len(missing)} 个时间桶")
    actual = [truth[time]["volume"] for time in expected]
    predicted = [row["volume"] for row in prediction]
    metrics = curve_metrics(actual, predicted, bucket_hours)
    baselines = {}
    for method in ("last_value", "moving_average"):
        curve = baseline(train, expected, method)
        measured = curve_metrics(actual, [row["volume"] for row in curve], bucket_hours)
        measured["mae_skill"] = 1 - metrics["mae"] / measured["mae"] if measured["mae"] > 0 else None
        baselines[method] = {"metrics": measured, "curve": curve}
    ratio_rows = [(truth[time].get("negative_ratio"), row.get("negative_ratio")) for time, row in zip(expected, prediction)]
    paired = [(a, p) for a, p in ratio_rows if a is not None and p is not None]
    sentiment = {"matched_buckets": len(paired), "total_buckets": len(expected),
                 "mae": statistics.mean(abs(a - p) for a, p in paired) if paired else None}
    return {"volume": metrics, "negative_ratio": sentiment, "baselines": baselines,
            "truth": [truth[time] for time in expected], "prediction": prediction,
            "notes": ["WAPE/SMAPE 是误差，不是准确率；不使用 100%-误差冒充准确率。",
                      "常量曲线不计算相关系数或峰值时间差；零总量时 WAPE 为空。",
                      "一次事件的得分不能证明普遍预测能力；应补充独立案例和重复实验。"]}


def simulation_curve(log_path, expected, minutes_per_round, bucket_hours, *, completed_rounds, anchor=None):
    """Count actual created posts/comments. Do not relabel likes or invent sentiment."""
    if minutes_per_round <= 0:
        raise ValueError("仿真每轮分钟数必须大于零")
    required = math.ceil(len(expected) * bucket_hours * 60 / minutes_per_round)
    if completed_rounds < required:
        raise ValueError(f"仿真窗口不足：需要至少 {required} 轮，实际完成 {completed_rounds} 轮")
    counts = [0] * len(expected)
    with open(log_path, encoding="utf-8") as stream:
        for line in stream:
            if not line.strip():
                continue
            item = json.loads(line)
            if "event_type" in item or item.get("success") is False:
                continue
            round_num = item.get("round", 0)
            if not isinstance(round_num, int) or round_num <= 0:
                continue  # Round zero is initialization, outside the forecast window.
            index = int((round_num - 1) * minutes_per_round // (bucket_hours * 60))
            if index < len(counts) and item.get("action_type", "").upper() in {"CREATE_POST", "CREATE_COMMENT"}:
                counts[index] += 1
    factor = 1.0
    if anchor is not None:
        if counts[0] == 0:
            raise ValueError("首个预测桶没有发帖/评论，无法进行早期热度锚定；可选择原始计数")
        factor = anchor / counts[0]
    return [{"timestamp": time, "volume": count * factor} for time, count in zip(expected, counts)], factor
