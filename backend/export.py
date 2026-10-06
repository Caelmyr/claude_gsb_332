"""Data export: CSV time series and full / per-step JSON dumps.

Exports are written into ``data/exports/`` through the atomic-write helper and
the returned file path is served back to the browser as a download.  Three
formats are offered:

* ``csv``         — the aggregate series (one row per step), openable in Excel.
* ``json``        — a full dump of meta + series + events + snapshots (bounded).
* ``individuals`` — the individual states at a chosen step, as a JSON array.
"""

from __future__ import annotations

import csv
import io
import json
import os
from typing import Any, Dict, List, Optional

from . import storage, util


def _export_path(run_id: str, fmt: str) -> str:
    ext = "csv" if fmt == "csv" else "json"
    return os.path.join(storage.exports_dir(), f"{run_id}_{fmt}.{ext}")


def _series_csv(series: List[Dict[str, Any]]) -> str:
    if not series:
        return "step\n"
    keys: List[str] = ["step"]
    for row in series:
        for k in row.keys():
            if k != "step" and k not in keys:
                keys.append(k)
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
    writer.writeheader()
    for row in series:
        writer.writerow(row)
    return buf.getvalue()


def export_series_csv(run_id: str) -> str:
    series = storage.load_series(run_id)
    path = _export_path(run_id, "csv")
    storage.atomic_write_bytes(path, _series_csv(series).encode("utf-8"))
    return path


def export_run_json(run_id: str) -> str:
    """Full run dump (meta + series + events), a self-contained JSON document."""
    meta = storage.load_run_meta(run_id)
    series = storage.load_series(run_id)
    events = storage.load_events(run_id)
    doc = {"meta": meta, "series": series, "events": events,
           "exported_at": util.now_iso()}
    path = _export_path(run_id, "json")
    storage.atomic_write_bytes(path, json.dumps(doc, ensure_ascii=False).encode("utf-8"))
    return path


def export_individuals_json(run_id: str, step: Optional[int] = None) -> str:
    """Export the individual states at one step as a JSON array."""
    from .run_manager import manager
    snap = manager.get_snapshot(run_id, step)
    doc = {
        "run_id": run_id,
        "step": snap.get("step"),
        "bounds": snap.get("bounds"),
        "stats": snap.get("stats"),
        "individuals": snap.get("individuals", []),
        "exported_at": util.now_iso(),
    }
    path = _export_path(run_id, "individuals")
    storage.atomic_write_bytes(path, json.dumps(doc, ensure_ascii=False).encode("utf-8"))
    return path


def export_run(run_id: str, fmt: str = "csv",
               step: Optional[int] = None) -> str:
    """Dispatch an export by format; return the absolute file path."""
    if fmt == "csv":
        return export_series_csv(run_id)
    if fmt == "json":
        return export_run_json(run_id)
    if fmt == "individuals":
        return export_individuals_json(run_id, step)
    raise ValueError(f"unknown export format: {fmt}")


def formats() -> List[Dict[str, str]]:
    """Describe available formats for the export page."""
    return [
        {"key": "csv", "label": "统计序列 CSV", "desc": "每个时间步一行的聚合统计，可用 Excel 打开。"},
        {"key": "json", "label": "完整运行 JSON", "desc": "配置 + 统计序列 + 干预日志的完整转储。"},
        {"key": "individuals", "label": "个体状态 JSON", "desc": "某一步所有个体的状态数组（车辆/动物/人）。"},
    ]
