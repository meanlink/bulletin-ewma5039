"""EWMA broadcast exception analysis; delivery is delegated to the real stack worker.

The analysis is intentionally pure: the same records and parameters always produce
the same result, so a rerun can be used to reconcile the page and the delivery file.
"""
from datetime import date, timedelta
import math
import random
import statistics

ALL_CHANNELS = "全台"


def metadata():
    return {
        "name": "播出简报异常编排", "short": "bulletin-ewma", "tagline": "先把持续小偏移识别出来，再把带口径的简报送进本地收件箱",
        "client": "模拟客户：海棠融媒播控中心，9名值班员维护3条节目播出链路。",
        "workflow": "早班下载播控日记，人工按完成率排序，把异常截图复制到值班简报，再由调度员定时整理。",
        "pains": ["小幅连续偏移容易被单日阈值漏掉", "简报缺少基准期和控制限口径", "生成结果与分发计划脱节，难以追踪投递记录"],
        "upstream": "播控日记的计划分钟、实播分钟、停播分钟与收视触达日汇总", "downstream": "播控早会与值班组本地简报收件箱；不会发送真实邮件。",
        "entities": ["播控日记", "异常简报运行", "定时分发计划"], "dataset_name": "海棠融媒_三链路120日播出日记.csv",
        "columns": [{"key": "day", "label": "播出日", "type": "string"}, {"key": "channel", "label": "播出链路", "type": "string"}, {"key": "planned_min", "label": "计划分钟", "type": "number"}, {"key": "aired_min", "label": "实播分钟", "type": "number"}, {"key": "interruption_min", "label": "停播分钟", "type": "number"}, {"key": "reach", "label": "触达估计", "type": "number"}],
        "parameters": [{"key": "channel", "label": "播出链路", "type": "select", "default": ALL_CHANNELS, "options": [{"value": ALL_CHANNELS, "label": "全台（逐链路独立分析）"}, {"value": "新闻一频", "label": "新闻一频"}, {"value": "文化二频", "label": "文化二频"}, {"value": "城市直播", "label": "城市直播"}]}, {"key": "metric", "label": "监测口径", "type": "select", "default": "completion", "options": [{"value": "completion", "label": "实播完成率%"}, {"value": "interruption", "label": "停播分钟"}]}, {"key": "smoothing", "label": "EWMA平滑系数λ", "type": "number", "default": 0.2, "min": 0.05, "max": 0.8}, {"key": "control_limit", "label": "控制限倍数L", "type": "number", "default": 3, "min": 2, "max": 4}],
        "workbench_title": "基准期、控制限与简报编排", "action_label": "生成异常简报", "unit": "% / 分钟", "accent": "oklch(50% 0.14 20)", "style": "broadcast-console",
        "seed_story": ["07:30 上传海棠融媒_三链路120日播出日记.csv，共360行。", "07:40 选择全台，核对三条链路各自的计划分钟口径。", "07:50 每条链路固定最初30日为基准，设置λ=0.2、L=3。", "08:00 生成全台连续异常事件，查看各链路起止日、持续天数与峰值偏离。", "08:10 切换停播分钟口径，人工核对原始播控日志。", "08:20 下载包含基准均值、标准差、事件总数和完整事件明细的简报。", "08:30 为值班组建立定时分发计划，稍后在本地收件箱核对实际worker投递记录。"],
        "method": "每条链路各自用最初30日固定估计μ、样本σ；之后Z_t=λX_t+(1−λ)Z_(t−1)，Z_0=μ。控制限μ±Lσ√{λ/(2−λ)[1−(1−λ)^(2t)]}；连续同向越限合并为事件，遇正常日或反向越限即分开。",
        "limitations": ["基准期需由业务确认稳定；序列自相关、节假日和排播结构会影响误报率。", "控制图识别统计偏移，不自动认定事故；投递仅进入本地收件箱，不发送真实外部消息。"],
    }


def seed_records():
    rng = random.Random(1020)
    start = date(2026, 7, 1)
    rows = []
    for channel, baseline, drift in (("新闻一频", 3.8, 8), ("文化二频", 5.2, 5.5), ("城市直播", 7.4, 12)):
        for index in range(120):
            planned = 720.0 if channel != "城市直播" else 360.0
            downtime = max(0.2, rng.gauss(baseline, 1.15))
            if 77 <= index <= 85:
                downtime += drift
            if index == 112:
                downtime += 15
            downtime = round(downtime, 2)
            rows.append({"day": str(start + timedelta(days=index)), "channel": channel, "planned_min": planned, "aired_min": round(planned-downtime, 2), "interruption_min": downtime, "reach": round(rng.uniform(140000, 210000))})
    return rows


def _parameter(value, name, low, high):
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name}必须为数值") from None
    if not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{name}须介于{low}至{high}")
    return value


def _channel_of(row):
    value = row.get("channel")
    return "" if value is None else str(value)


def _channel_groups(records):
    """Group records by channel in input order, without validating each channel.

    Ordering by first appearance keeps the result stable for an unchanged batch
    and is more readable than a Unicode sort of the Chinese channel names.
    """
    groups = {}
    for row in records:
        groups.setdefault(_channel_of(row), []).append(row)
    return list(groups.items())


def _parse_channel(records, channel, metric):
    parsed, days = [], set()
    consistency_warnings = 0
    for row in records:
        if _channel_of(row) != channel:
            continue
        if not channel:
            raise ValueError("播出链路为空，无法独立建立基准")
        try:
            day = date.fromisoformat(str(row["day"]))
            planned, aired, interruption = [float(row[key]) for key in ("planned_min", "aired_min", "interruption_min")]
        except (TypeError, ValueError, KeyError):
            raise ValueError("播出日须为ISO日期，计划、实播与停播分钟须为数值") from None
        if not all(math.isfinite(value) for value in (planned, aired, interruption)) or not 0 < planned <= 1440 or not 0 <= aired <= planned or not 0 <= interruption <= planned:
            raise ValueError("日计划分钟须大于0且不超过1440，实播与停播分钟须在0至计划分钟之间")
        if day in days:
            raise ValueError("同一链路同日存在重复日记，请先去重")
        days.add(day)
        if abs(planned - aired - interruption) > 0.05:
            consistency_warnings += 1
        value = aired / planned * 100 if metric == "completion" else interruption
        parsed.append((day, value))
    parsed.sort()
    if len(parsed) < 60:
        raise ValueError("所选链路至少需要60日，前30日作为固定基准")
    if any((parsed[index][0] - parsed[index-1][0]).days != 1 for index in range(1, len(parsed))):
        raise ValueError("EWMA要求连续日历，缺日请补充原始播控日记")
    return parsed, consistency_warnings


def _close_event(active):
    days = active["days"]
    return {
        "channel": active["channel"],
        "direction": active["direction"],
        "start": active["start"],
        "end": active["end"],
        "days": days,
        "duration_days": days,
        "peak_day": active["peak_day"],
        "peak_deviation": round(active["peak_deviation"], 4),
        "peak_sigma": round(active["peak_sigma"], 4),
        "peak_ewma": round(active["peak_ewma"], 4),
        "peak_actual": round(active["peak_actual"], 4),
    }


def _build_events(output, channel, mean, sigma):
    """Merge adjacent same-direction exceedances; normal or reversed days break."""
    events, active = [], None
    for row in output:
        if not row["anomaly"]:
            if active is not None:
                events.append(_close_event(active))
                active = None
            continue
        direction = row["status"]
        if active is None or active["direction"] != direction:
            if active is not None:
                events.append(_close_event(active))
            active = {
                "channel": channel, "direction": direction,
                "start": row["day"], "end": row["day"], "days": 0,
                "peak_day": row["day"], "peak_deviation": -1.0,
                "peak_sigma": -1.0, "peak_ewma": row["ewma"], "peak_actual": row["actual"],
            }
        active["end"] = row["day"]
        active["days"] += 1
        deviation = abs(row["ewma"] - mean)
        if deviation > active["peak_deviation"]:
            active["peak_deviation"] = deviation
            active["peak_sigma"] = deviation / sigma
            active["peak_day"] = row["day"]
            active["peak_ewma"] = row["ewma"]
            active["peak_actual"] = row["actual"]
    if active is not None:
        events.append(_close_event(active))
    return events


def _analyze_channel(records, channel, metric, smoothing, control_limit):
    parsed, consistency_warnings = _parse_channel(records, channel, metric)
    baseline_values = [value for _, value in parsed[:30]]
    mean = statistics.mean(baseline_values)
    sigma = statistics.stdev(baseline_values)
    if sigma < 1e-10:
        raise ValueError("基准期无波动，无法估计控制限；请换用已确认稳定且有波动的基准数据")
    z = mean
    output = []
    for index, (day, value) in enumerate(parsed[30:], 1):
        z = smoothing * value + (1 - smoothing) * z
        half_width = control_limit * sigma * math.sqrt(smoothing / (2-smoothing) * (1-(1-smoothing)**(2*index)))
        lower, upper = mean-half_width, mean+half_width
        anomaly = z < lower or z > upper
        output.append({"channel": channel, "day": str(day), "actual": round(value, 4), "ewma": round(z, 4), "lower": round(lower, 4), "upper": round(upper, 4), "status": "越下限" if z < lower else "越上限" if z > upper else "受控", "anomaly": anomaly})
    anomalies = [row for row in output if row["anomaly"]]
    events = _build_events(output, channel, mean, sigma)
    unit = "%" if metric == "completion" else "分钟"
    latest = output[-1]
    warnings = []
    if consistency_warnings:
        warnings.append(f"有{consistency_warnings}行计划≠实播+停播，简报前需核对源日记口径。")
    if len(anomalies) > len(output) * 0.25:
        warnings.append("越限日超过监测期四分之一，需复核基准期或是否发生结构性变化。")
    chart = {"type": "line", "labels": [row["day"] for row in output], "series": [{"name": "EWMA", "values": [row["ewma"] for row in output]}, {"name": "下控制限", "values": [row["lower"] for row in output]}, {"name": "上控制限", "values": [row["upper"] for row in output]}]}
    summary = f"{channel}以{parsed[0][0]}至{parsed[29][0]}为固定基准；后续{len(output)}日中{len(anomalies)}日EWMA越限，合并为{len(events)}个连续异常事件；最新日{latest['day']}为{latest['status']}。"
    payload = {
        "channel": channel, "status": "completed", "summary": summary,
        "baseline": {"start": str(parsed[0][0]), "end": str(parsed[29][0]), "mean": round(mean, 4), "sigma": round(sigma, 4), "days": 30},
        "monitored_days": len(output), "anomaly_days": len(anomalies), "event_count": len(events),
        "metrics": [
            {"label": "基准均值", "value": round(mean, 4), "unit": unit},
            {"label": "基准标准差", "value": round(sigma, 4), "unit": unit},
            {"label": "越限日数", "value": len(anomalies), "unit": "日"},
            {"label": "连续事件数", "value": len(events), "unit": "个"},
            {"label": "最新EWMA", "value": latest["ewma"], "unit": unit},
        ],
        "events": events,
        "anomalies": [{key: value for key, value in row.items() if key != "anomaly"} for row in anomalies],
        "chart": chart, "warnings": warnings,
    }
    return payload, output, anomalies


def _event_rows(events):
    return [{key: event[key] for key in ("channel", "start", "end", "days", "direction", "peak_deviation", "peak_day")} for event in events]


def _event_columns(unit):
    return [
        {"key": "channel", "label": "播出链路"},
        {"key": "start", "label": "起始日"},
        {"key": "end", "label": "结束日"},
        {"key": "days", "label": "持续天数"},
        {"key": "direction", "label": "方向"},
        {"key": "peak_deviation", "label": f"峰值偏离({unit})"},
        {"key": "peak_day", "label": "峰值日"},
    ]


def _anomaly_columns(unit):
    return [
        {"key": "channel", "label": "播出链路"},
        {"key": "day", "label": "播出日"},
        {"key": "actual", "label": f"实测({unit})"},
        {"key": "ewma", "label": "EWMA"},
        {"key": "lower", "label": "下控制限"},
        {"key": "upper", "label": "上控制限"},
        {"key": "status", "label": "简报标记"},
    ]


def _combined_chart(channels):
    completed = [channel for channel in channels if channel["status"] == "completed"]
    if not completed:
        return None
    if len(completed) == 1:
        return completed[0]["chart"]
    labels = completed[0]["chart"]["labels"]
    series = [{"name": channel["channel"] + " EWMA", "values": channel["chart"]["series"][0]["values"]} for channel in completed if channel["chart"]["labels"] == labels]
    if series:
        return {"type": "line", "labels": labels, "series": series}
    return completed[0]["chart"]


def _single_result(records, channel, unit, metric, smoothing, control_limit):
    payload, output, anomalies = _analyze_channel(records, channel, metric, smoothing, control_limit)
    events = payload["events"]
    latest = output[-1]
    findings = [
        f"λ={smoothing:g}，L={control_limit:g}；仅以最初30日固定基准，不使用未来监测值重新估计均值与标准差。",
        f"连续同向越限合并为事件，遇正常日或反向越限即分开；共{len(events)}个事件，起止日、持续天数与峰值偏离全部列出。",
        "峰值偏离为事件内EWMA相对该链路基准均值的最大绝对偏离，峰值日对应该极值日期。",
        f"{'最近越限日为'+anomalies[-1]['day']+'，状态'+anomalies[-1]['status'] if anomalies else '本次未检测到越限日；仍应留存基准和参数'}。",
        "事件清单与越限日明细均全量输出、不截断；页面和分发文件可按事件总数逐行对账。",
        "在分发模块选择本运行结果并设置计划；worker按实际时间投递至本地收件箱，分析函数不会发送消息。",
    ]
    return {
        "mode": "single", "summary": payload["summary"], "metrics": payload["metrics"],
        "parameters": {"channel": channel, "metric": metric, "smoothing": smoothing, "control_limit": control_limit},
        "table": {"columns": _event_columns(unit), "rows": _event_rows(events)},
        "anomaly_table": {"columns": _anomaly_columns(unit), "rows": payload["anomalies"]},
        "chart": payload["chart"], "findings": findings,
        "method": metadata()["method"], "warnings": payload["warnings"],
        "events": events, "event_total": len(events), "anomaly_total": len(payload["anomalies"]),
        "channels": [payload], "failures": [],
        "counts": {"channels": 1, "completed": 1, "failed": 0, "event_total": len(events), "anomaly_total": len(payload["anomalies"])},
    }


def _batch_result(groups, unit, metric, smoothing, control_limit):
    channels, failures, all_events, all_anomalies = [], [], [], []
    for channel, channel_records in groups:
        try:
            payload, _, anomalies = _analyze_channel(channel_records, channel, metric, smoothing, control_limit)
        except ValueError as error:
            reason = str(error)
            channels.append({"channel": channel, "status": "failed", "error": reason, "event_count": 0, "anomaly_days": 0, "monitored_days": 0, "events": [], "warnings": []})
            failures.append({"channel": channel, "reason": reason})
            continue
        channels.append(payload)
        all_events.extend(payload["events"])
        all_anomalies.extend(payload["anomalies"])
    completed = [channel for channel in channels if channel["status"] == "completed"]
    event_total = len(all_events)
    anomaly_total = len(all_anomalies)
    unit_label = "全台" if len(groups) > 1 else (groups[0][0] or "（未标注链路）")
    summary = f"{unit_label}共{len(groups)}条链路相互独立分析，各自以最初30日为固定基准；{len(completed)}条完成、{len(failures)}条失败；共{event_total}个连续异常事件、{anomaly_total}个越限日，完整明细见下表。"
    if not completed:
        summary = f"{unit_label}共{len(groups)}条链路均未完成分析，失败原因已逐条列出，未生成异常事件。"
    warnings = [f"{channel['channel'] or '（未标注链路）'}：{warning}" for channel in completed for warning in channel.get("warnings", [])]
    if failures:
        warnings.append(f"{len(failures)}条链路失败，原因已列出；其余链路结果不受影响。")
    findings = [
        f"全台{len(groups)}条链路逐条独立分析；每条链路只用自己的最初30日估计均值与样本标准差，互不共用基准或控制限。",
        "连续同向越限合并为事件，遇正常日或反向越限即分开；事件起止日、持续天数与峰值偏离全部列出。",
        "峰值偏离为事件内EWMA相对该链路基准均值的最大绝对偏离；峰值日对应该极值日期。",
        f"共{event_total}个事件、{anomaly_total}个越限日；事件表与越限日表均全量输出、不截断，可按事件总数逐行对账。",
    ]
    if failures:
        findings.append("失败链路：" + "；".join(f"{failure['channel'] or '（未标注链路）'}（{failure['reason']}）" for failure in failures) + "。其余链路照常给出结果。")
    findings.append("在分发模块选择本运行结果并设置计划；worker按实际时间投递至本地收件箱，分析函数不会发送消息。")
    metrics = [
        {"label": "链路总数", "value": len(groups), "unit": "条"},
        {"label": "完成链路", "value": len(completed), "unit": "条"},
        {"label": "失败链路", "value": len(failures), "unit": "条"},
        {"label": "越限日数", "value": anomaly_total, "unit": "日"},
        {"label": "事件总数", "value": event_total, "unit": "个"},
    ]
    return {
        "mode": "batch", "summary": summary, "metrics": metrics,
        "parameters": {"channel": ALL_CHANNELS, "metric": metric, "smoothing": smoothing, "control_limit": control_limit},
        "table": {"columns": _event_columns(unit), "rows": _event_rows(all_events)},
        "anomaly_table": {"columns": _anomaly_columns(unit), "rows": all_anomalies},
        "chart": _combined_chart(channels), "findings": findings,
        "method": metadata()["method"], "warnings": warnings,
        "events": all_events, "event_total": event_total, "anomaly_total": anomaly_total,
        "channels": channels, "failures": failures,
        "counts": {"channels": len(groups), "completed": len(completed), "failed": len(failures), "event_total": event_total, "anomaly_total": anomaly_total},
    }


def analyze(records, params):
    """Analyze a full diary batch per channel, or a single selected channel.

    Batch mode isolates domain errors per channel: a failed channel is reported
    with its reason while the remaining channels still produce full results.
    Single-channel mode keeps the original behavior and raises on invalid input.
    """
    if not isinstance(records, list) or not records:
        raise ValueError("请先导入播控日记")
    if not all(isinstance(row, dict) for row in records):
        raise ValueError("播控日记每行须为对象")
    channel = str(params.get("channel", ALL_CHANNELS))
    metric = params.get("metric", "completion")
    if metric not in ("completion", "interruption"):
        raise ValueError("未知监测口径")
    smoothing = _parameter(params.get("smoothing", 0.2), "平滑系数", 0.05, 0.8)
    control_limit = _parameter(params.get("control_limit", 3), "控制限倍数", 2, 4)
    unit = "%" if metric == "completion" else "分钟"
    groups = _channel_groups(records)
    if channel == ALL_CHANNELS:
        if not groups:
            raise ValueError("未找到任何播出链路")
        return _batch_result(groups, unit, metric, smoothing, control_limit)
    if channel not in dict(groups):
        raise ValueError(f"未找到播出链路{channel}")
    return _single_result(records, channel, unit, metric, smoothing, control_limit)
