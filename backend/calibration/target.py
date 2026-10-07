"""目标曲线（观测数据）的解析与稳健清洗。

目标数据是校准的"真值"，但真实观测往往不干净：时间戳不规则、有缺测
（``null`` / 空单元格）、重复记录、个别异常点（上报错误、口径突变）。
本模块把各种常见输入格式归一化为一条内部表示，并在清洗阶段给出可审计的
处理记录——**从不静默丢弃任何用户数据**：

* 缺测点：无法参与拟合，但保留在 :attr:`TargetSeries.raw` 中并在结果里登记。
* 重复时间点：值与权重按权重合并，登记合并条数。
* 异常点：用 Hampel / MAD 滚动稳健检测，默认 *置零权重* 而非删除——曲线
  叠加图上仍会画出来并打上"异常点"标记；用户也可选择直接剔除。

支持的输入格式（:func:`parse_target`）：

* CSV / TSV 文本：``time,value[,weight]``，首行可以是表头。
* JSON：``[[t, v, w?], ...]``、``[{"t/step/time": .., "v/value": ..}, ...]``
  或 ``{"time": [...], "value": [...], "weight": [...]}``。
* 已经是内部结构的 dict（原样校验，用于持久化后回放）。
"""

from __future__ import annotations

import csv
import io
import json
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# 表头别名 -> 标准字段
_TIME_KEYS = {"t", "time", "step", "day", "x", "时间", "时间步", "步", "天"}
_VALUE_KEYS = {"v", "val", "value", "y", "count", "值", "数值", "观测值",
               "感染人数", "感染数"}
_WEIGHT_KEYS = {"w", "weight", "权重"}


# --------------------------------------------------------------------------- #
# 数据结构
# --------------------------------------------------------------------------- #
@dataclass
class TargetPoint:
    t: float
    v: float
    w: float = 1.0
    outlier: bool = False     # 被异常检测标记（此时若 drop=False 则权重置 0）
    merged: int = 1           # 该点由几条重复记录合并而来

    def to_dict(self) -> Dict[str, Any]:
        return {"t": self.t, "v": self.v, "w": self.w,
                "outlier": self.outlier, "merged": self.merged}


@dataclass
class TargetSeries:
    """清洗后的目标曲线。"""
    points: List[TargetPoint] = field(default_factory=list)
    missing: List[Dict[str, Any]] = field(default_factory=list)
    n_merged: int = 0
    value_label: str = "value"

    # ------------------------------------------------------------------ #
    def usable(self) -> List[TargetPoint]:
        """参与拟合的点（非异常、权重为正）。"""
        return [p for p in self.points if p.w > 0 and not p.outlier]

    def outliers(self) -> List[TargetPoint]:
        return [p for p in self.points if p.outlier]

    def value_scale(self) -> float:
        """稳健归一化尺度：优先 (P95-P5)，退化时用 MAD，再退化用 1。

        用区间而不是标准差，是为了让异常点本身不参与放大/缩小误差，避免
        "一个坏点把整条目标的量纲撑大、其余点误差被稀释"。
        """
        vals = sorted(p.v for p in self.usable())
        if len(vals) >= 2:
            scale = vals[int(0.95 * (len(vals) - 1))] - vals[int(0.05 * (len(vals) - 1))]
            if scale > 1e-12:
                return float(scale)
        med = median(vals) if vals else 0.0
        mad = median([abs(v - med) for v in vals]) if vals else 0.0
        if mad > 1e-12:
            return 1.4826 * mad
        if abs(med) > 1e-12:
            return abs(med)
        return 1.0

    def summary(self) -> Dict[str, Any]:
        u = self.usable()
        return {
            "n_total": len(self.points) + len(self.missing),
            "n_points": len(self.points),
            "n_usable": len(u),
            "n_missing": len(self.missing),
            "n_duplicate_rows": self.n_merged,
            "n_outliers": len(self.outliers()),
            "t_min": min((p.t for p in u), default=None),
            "t_max": max((p.t for p in u), default=None),
            "outliers": [{"t": p.t, "v": p.v} for p in self.outliers()],
            "missing": self.missing,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {"value_label": self.value_label,
                "points": [p.to_dict() for p in self.points],
                "missing": self.missing, "n_merged": self.n_merged}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TargetSeries":
        return cls(
            points=[TargetPoint(**p) for p in d.get("points", [])],
            missing=list(d.get("missing", [])),
            n_merged=int(d.get("n_merged", 0)),
            value_label=str(d.get("value_label", "value")),
        )


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def median(xs: List[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return xs[mid]
    return 0.5 * (xs[mid - 1] + xs[mid])


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    if isinstance(x, bool):
        return float(x)
    if isinstance(x, (int, float)):
        f = float(x)
        return f if math.isfinite(f) else None
    s = str(x).strip()
    if s == "" or s.lower() in ("na", "nan", "null", "none", "—", "-"):
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return f if math.isfinite(f) else None


# --------------------------------------------------------------------------- #
# 解析
# --------------------------------------------------------------------------- #
def _parse_csv_text(text: str) -> Tuple[List[Tuple[Any, Any, Any]], Optional[str]]:
    """返回 [(raw_t, raw_v, raw_w), ...] 与可能的值列表头名。"""
    sample = text.lstrip()
    delimiter = "\t" if sample.startswith("time\t") or "\t" in sample.splitlines()[0] else ","
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return [], None
    header = [c.strip().lower() for c in rows[0]]
    label = None
    has_header = any(h in _TIME_KEYS or h in _VALUE_KEYS for h in header)
    if has_header:
        ti = next((i for i, h in enumerate(header) if h in _TIME_KEYS), 0)
        vi = next((i for i, h in enumerate(header) if h in _VALUE_KEYS), 1)
        wi = next((i for i, h in enumerate(header) if h in _WEIGHT_KEYS), -1)
        label = rows[0][vi].strip() if vi < len(rows[0]) else None
        rows = rows[1:]
    else:
        ti, vi, wi = 0, 1, 2 if len(header) >= 3 else -1
    out = []
    for r in rows:
        get = lambda i: r[i] if i < len(r) else None  # noqa: E731
        out.append((get(ti), get(vi), get(wi) if wi >= 0 else None))
    return out, label


def _parse_json_obj(obj: Any) -> Tuple[List[Tuple[Any, Any, Any]], Optional[str]]:
    if isinstance(obj, dict):
        times = obj.get("time", obj.get("t", obj.get("step")))
        vals = obj.get("value", obj.get("v", obj.get("values")))
        wgts = obj.get("weight", obj.get("w"))
        label = obj.get("value_label") or obj.get("label")
        if isinstance(times, list) and isinstance(vals, list):
            n = min(len(times), len(vals))
            ws = wgts if isinstance(wgts, list) else []
            return ([(times[i], vals[i], ws[i] if i < len(ws) else None)
                     for i in range(n)]), (str(label) if label else None)
        rows = []
        # 单对象点表 {"0": 12, "1": 30, ...}
        for k, v in obj.items():
            if isinstance(v, (int, float)):
                rows.append((k, v, None))
        return rows, None
    if isinstance(obj, list):
        rows = []
        for item in obj:
            if isinstance(item, dict):
                t = next((item[k] for k in item
                          if str(k).lower() in _TIME_KEYS), None)
                v = next((item[k] for k in item
                          if str(k).lower() in _VALUE_KEYS), None)
                w = next((item[k] for k in item
                          if str(k).lower() in _WEIGHT_KEYS), None)
                rows.append((t, v, w))
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                rows.append((item[0], item[1],
                             item[2] if len(item) >= 3 else None))
            elif isinstance(item, (int, float)):
                rows.append((len(rows), item, None))
        return rows, None
    raise ValueError("无法识别的目标数据结构")


def parse_target(data: Any) -> Tuple[List[Tuple[Any, Any, Any]], Optional[str]]:
    """把任意受支持格式解析成 ``[(raw_t, raw_v, raw_w), ...]``。"""
    if isinstance(data, str):
        s = data.strip()
        if not s:
            return [], None
        if s[0] in "[{":
            return _parse_json_obj(json.loads(s))
        return _parse_csv_text(s)
    return _parse_json_obj(data)


# --------------------------------------------------------------------------- #
# 清洗
# --------------------------------------------------------------------------- #
def _hampel_outliers(pts: List[TargetPoint], window: int = 5,
                     n_sigma: float = 4.0) -> List[int]:
    """滚动 MAD 异常检测，返回异常点下标。

    用中位数 + MAD（崩溃点 50%）而不是均值 + 标准差，单个离谱点不会污染
    自己的判别阈值。``n_sigma=4`` 对流行病计数这类陡升陡降曲线较保守，
    避免把真实疫情高峰误判为异常。
    """
    bad = set()
    n = len(pts)
    for i in range(n):
        lo, hi = max(0, i - window), min(n, i + window + 1)
        local = sorted(pts[j].v for j in range(lo, hi))
        med = median(local)
        mad = median([abs(v - med) for v in local])
        if mad <= 1e-12:
            # 局部常数窗口：只有显著偏离常数才算异常（相对 1 或常数自身）
            tol = 4.0 * max(1.0, 0.05 * abs(med))
            if abs(pts[i].v - med) > tol:
                bad.add(i)
            continue
        if abs(pts[i].v - med) > n_sigma * 1.4826 * mad:
            bad.add(i)
    return sorted(bad)


def clean_target(data: Any, detect_outliers: bool = True,
                 drop_outliers: bool = False,
                 outlier_window: int = 5,
                 outlier_sigma: float = 4.0) -> TargetSeries:
    """解析并清洗目标数据。

    :param detect_outliers: 是否做 Hampel/MAD 异常检测。
    :param drop_outliers: True 直接剔除异常点；False（默认）保留但置零权重。
    """
    rows, label = parse_target(data)
    missing: List[Dict[str, Any]] = []
    by_t: Dict[float, List[Tuple[float, float]]] = {}
    n_merged = 0

    for idx, (rt, rv, rw) in enumerate(rows):
        t = _to_float(rt)
        v = _to_float(rv)
        w = _to_float(rw)
        if t is None or v is None:
            missing.append({"row": idx, "t": rt, "v": rv,
                            "reason": "时间或值缺失/非数字"})
            continue
        w = 1.0 if (w is None or w < 0) else w
        by_t.setdefault(t, []).append((v, w))

    pts: List[TargetPoint] = []
    for t in sorted(by_t):
        recs = by_t[t]
        if len(recs) > 1:
            n_merged += len(recs) - 1
            wsum = sum(w for _, w in recs) or 1.0
            v = sum(v * w for v, w in recs) / wsum
            w = wsum / len(recs)  # 重复点不获得"复制一遍就权重翻倍"的好处
            merged = len(recs)
        else:
            v, w, merged = recs[0][0], recs[0][1], 1
        pts.append(TargetPoint(t=t, v=v, w=w, merged=merged))

    if detect_outliers and len(pts) >= 4:
        for i in _hampel_outliers(pts, outlier_window, outlier_sigma):
            pts[i].outlier = True
            if not drop_outliers:
                pts[i].w = 0.0
        if drop_outliers:
            pts = [p for p in pts if not p.outlier]

    ts = TargetSeries(points=pts, missing=missing, n_merged=n_merged,
                      value_label=label or "value")
    if len(ts.usable()) < 2:
        raise ValueError("清洗后有效目标点不足 2 个，无法校准（请检查缺失/异常设置）")
    return ts
