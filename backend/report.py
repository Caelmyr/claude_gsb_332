"""Per-run summary report generation.

A report distils the aggregate time series plus the intervention log into a
small JSON document: per-metric first/final/min/max/peak figures and a short
domain-specific narrative.  It is written next to the run (``report.json``) so
the report page and the history page can both read it without recomputation.
"""

from __future__ import annotations

from typing import Any, Dict, List

from . import catalog, storage, util


def _metric_rows(domain: str, series: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for spec in catalog.CATALOG[domain]["metrics"]:
        key = spec["key"]
        vals = [(r["step"], r[key]) for r in series if key in r]
        if not vals:
            continue
        values = [v for _, v in vals]
        peak_step = max(vals, key=lambda p: p[1])[0]
        rows[key] = {
            "label": spec["label"],
            "initial": values[0],
            "final": values[-1],
            "min": min(values),
            "max": max(values),
            "peak_step": peak_step,
        }
    return rows


def _narrative(domain: str, metrics: Dict[str, Dict[str, Any]],
               steps: int) -> List[str]:
    """Return short, domain-specific summary sentences."""
    lines: List[str] = []
    m = metrics

    def fmt(key: str) -> str:
        row = m.get(key)
        if not row:
            return "—"
        v = row["final"]
        return f"{v:g}" if isinstance(v, (int, float)) else str(v)

    if domain == "epidemic":
        inf = m.get("infected", {})
        rec = m.get("recovered", {})
        peak = inf.get("max", 0)
        lines.append(f"共模拟 {steps} 步，累计康复 {fmt('recovered')} 人。")
        lines.append(f"感染峰值 {peak:g} 人，出现在第 {inf.get('peak_step', 0)} 步。")
        final_inf = inf.get("final", 0)
        if final_inf == 0:
            lines.append("疫情已消亡（无现存感染者）。")
        else:
            lines.append(f"当前仍有 {final_inf:g} 名现存感染者，疫情仍在传播。")
        lines.append("建议：结合 β/γ 与干预时间点，评估封锁与疫苗对峰值和总感染规模的压降效果。")
    elif domain == "traffic":
        lines.append(f"共模拟 {steps} 步。")
        lines.append(f"平均速度 初始 {fmt('mean_speed')}（最新 {m.get('mean_speed', {}).get('final', '—')}）。")
        lines.append(f"单步流量 峰值 {m.get('flow', {}).get('max', '—')}，停车比例 峰值 {m.get('stopped', {}).get('max', '—')}。")
        lines.append("停车比例接近 1 表明出现拥堵；可通过限速或清除事故观察流量-密度关系。")
    elif domain == "ecology":
        rabbits = m.get("rabbits", {})
        foxes = m.get("foxes", {})
        lines.append(f"共模拟 {steps} 步。")
        lines.append(f"兔子 峰值 {rabbits.get('max', '—')}，狐狸 峰值 {foxes.get('max', '—')}。")
        if foxes.get("final", 0) == 0 and rabbits.get("final", 0) > 0:
            lines.append("狐狸已灭绝，兔子失去天敌后可能爆发式增长。")
        elif rabbits.get("final", 0) == 0:
            lines.append("兔子已灭绝，狐狸因缺乏猎物也将随之消亡。")
        else:
            lines.append("两个种群仍在共存，系统呈现捕食者-被捕食者振荡。")
        lines.append("可对比捕杀或投放捕食者干预对种群平衡的影响。")
    else:
        lines.append(f"共模拟 {steps} 步。")
    return lines


def generate_report(run_id: str) -> Dict[str, Any]:
    """Build and persist the report for ``run_id``, returning it."""
    meta = storage.load_run_meta(run_id)
    if meta is None:
        raise KeyError(f"run not found: {run_id}")
    series = storage.load_series(run_id)
    events = storage.load_events(run_id)
    domain = meta["domain"]

    metrics = _metric_rows(domain, series)
    report = {
        "run_id": run_id,
        "name": meta["name"],
        "scene_id": meta["scene_id"],
        "scene_name": meta["scene_name"],
        "domain": domain,
        "model": meta["model"],
        "steps": len(series),
        "config": meta["config"],
        "metrics": metrics,
        "events": events,
        "summary": _narrative(domain, metrics, len(series)),
        "generated_at": util.now_iso(),
    }
    storage.save_report(run_id, report)
    return report


def load_report(run_id: str) -> Dict[str, Any]:
    report = storage.load_report(run_id)
    if report is None:
        raise KeyError(f"report not found for run: {run_id}")
    return report
