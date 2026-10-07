"""Parse and clean an observed target curve.

Real-world data is messy.  Before the optimizer ever sees a target curve it
passes through :func:`prepare_target`, which handles, explicitly and visibly:

* **Missing values** — rows without a numeric value are dropped and counted;
  they never silently become zero (a zero infection count is very different
  from "not reported").
* **Bad rows** — non-numeric / negative timestamps and non-finite values are
  dropped with a per-row reason instead of crashing the calibration.
* **Duplicate timestamps** — several observations at the same time (e.g. two
  reports filed on the same day) are merged into a weighted mean.
* **Outliers** — detected with a *robust* method (median trend + MAD residual
  scale), never with mean/std, because one huge spike must not become the
  reference scale.  A point is flagged ``suspect`` when |residual| > 2.5·MAD
  and ``outlier`` when > 4·MAD **and** the deviation is large in relative
  terms.  Suspect points keep full weight; outliers are flagged and, depending
  on ``outlier_mode``, kept, winsorised to the trend, or excluded.
* **Weighting** — the caller may give each point a weight (e.g. coverage /
  confidence).  Merged duplicate weights add.

The module is deliberately pure (no engine imports) so the cleaning behaviour
can be unit tested in isolation and shown to the user before the (expensive)
calibration runs.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Residual thresholds in MAD units (MAD of a standard normal ≈ 0.6745·σ).
SUSPECT_MAD = 2.5
OUTLIER_MAD = 4.0
# A point must also deviate by at least this fraction of the local trend level
# (or absolute floor) to count as a hard outlier — this protects genuinely
# sharp but real changes early in an epidemic curve near zero.
OUTLIER_REL = 0.5
ABS_FLOOR = 1e-9


@dataclass
class TargetPoint:
    """One cleaned target observation."""
    t: float
    y: float
    weight: float = 1.0
    status: str = "ok"               # ok | suspect | outlier
    note: str = ""                   # human-readable explanation of the status

    def to_dict(self) -> Dict[str, Any]:
        return {"t": self.t, "y": self.y, "weight": self.weight,
                "status": self.status, "note": self.note}


@dataclass
class PreparedTarget:
    """Output of :func:`prepare_target`."""
    points: List[TargetPoint] = field(default_factory=list)
    dropped: List[Dict[str, Any]] = field(default_factory=list)
    merged: int = 0
    y_min: float = 0.0
    y_max: float = 0.0
    t_min: float = 0.0
    t_max: float = 0.0
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    # ------------------------------------------------------------------ #
    def kept(self) -> List[TargetPoint]:
        """Points that participate in the fit (outliers kept with reduced
        weight or winsorised still participate; excluded ones do not)."""
        return [p for p in self.points if p.status != "excluded"]

    def times(self) -> List[float]:
        return [p.t for p in self.kept()]

    def values(self) -> List[float]:
        return [p.y for p in self.kept()]

    def weights(self) -> List[float]:
        return [p.weight for p in self.kept()]

    def summary(self) -> Dict[str, Any]:
        return {
            "n_total_rows": len(self.points) + len(self.dropped),
            "n_kept": len(self.kept()),
            "n_suspect": sum(p.status == "suspect" for p in self.points),
            "n_outliers": sum(p.status in ("outlier", "excluded")
                              for p in self.points),
            "n_dropped_missing": sum(
                d.get("reason") == "missing_value" for d in self.dropped),
            "n_merged_duplicates": self.merged,
            "t_min": self.t_min,
            "t_max": self.t_max,
            "y_min": self.y_min,
            "y_max": self.y_max,
            "errors": self.errors,
            "warnings": self.warnings,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "points": [p.to_dict() for p in self.points],
            "dropped": self.dropped,
            "merged": self.merged,
            "y_min": self.y_min, "y_max": self.y_max,
            "t_min": self.t_min, "t_max": self.t_max,
            "errors": self.errors, "warnings": self.warnings,
        }


# --------------------------------------------------------------------------- #
# Parsing — accept the common shapes a user might paste / upload
# --------------------------------------------------------------------------- #
def _coerce_float(v: Any) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = v.strip()
        if v == "" or v.lower() in ("na", "n/a", "nan", "null", "none", "-"):
            return None
        try:
            v = float(v)
        except ValueError:
            return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def parse_target(data: Any,
                 time_key: str = "t", value_key: str = "y",
                 weight_key: Optional[str] = None) -> List[Tuple[float, float, Optional[float]]]:
    """Parse raw user input into ``(t, y, weight)`` triples (unchecked).

    Accepted shapes::

        {"points": [[t, y], ...]}
        {"points": [{"t": t, "y": y, "weight": w}, ...]}
        {"time": [...], "value": [...]}
        [[t, y], ...]
        [{"t": t, "y": y}, ...]

    ``time_key`` / ``value_key`` rename the per-row keys (e.g. ``day`` /
    ``infected``).  Missing/blank values are returned as ``y=None`` so the
    cleaning step can count them instead of treating them as zero.
    """
    rows: List[Tuple[float, float, Optional[float]]] = []

    def _emit(t_raw: Any, y_raw: Any, w_raw: Any = None) -> None:
        t = _coerce_float(t_raw)
        if t is None:
            return  # unparseable time — handled/reported by the cleaner
        y = _coerce_float(y_raw)  # may be None -> missing value
        w = _coerce_float(w_raw) if w_raw is not None else None
        rows.append((t, y if y is not None else None, w))  # type: ignore[arg-type]

    if isinstance(data, dict):
        # Parallel-array form: {"time": [...], "value": [...]}
        ta = data.get("time") or data.get("times") or data.get("t")
        ya = data.get("value") or data.get("values") or data.get("y")
        if isinstance(ta, (list, tuple)) and isinstance(ya, (list, tuple)):
            wa = data.get(weight_key) if weight_key else None
            for i, (t_raw, y_raw) in enumerate(zip(ta, ya)):
                w_raw = wa[i] if isinstance(wa, (list, tuple)) and i < len(wa) else None
                _emit(t_raw, y_raw, w_raw)
            return rows
        rows_data = data.get("points") or data.get("data") or []
    else:
        rows_data = data if isinstance(data, (list, tuple)) else []

    for raw in rows_data:
        if isinstance(raw, dict):
            t_raw = raw.get(time_key, raw.get("t", raw.get("time", raw.get("day", raw.get("step")))))
            y_raw = raw.get(value_key, raw.get("y", raw.get("value", raw.get("value"))))
            w_raw = raw.get(weight_key) if weight_key else raw.get("weight")
            _emit(t_raw, y_raw, w_raw)
        elif isinstance(raw, (list, tuple)):
            if not raw:
                _emit(None, None)
            elif len(raw) == 1:
                _emit(raw[0], None)
            elif len(raw) == 2:
                _emit(raw[0], raw[1])
            else:
                _emit(raw[0], raw[1], raw[2])
    return rows


def parse_target_csv(text: str, time_col: Optional[str] = None,
                     value_col: Optional[str] = None) -> List[Tuple[float, float, Optional[float]]]:
    """Parse CSV text.

    With a header row, ``time_col`` / ``value_col`` pick columns by name; by
    default the first two columns are used (a third numeric column is taken as
    the weight).  Without a header row, columns 0/1 are used directly.
    """
    import csv
    lines = [ln for ln in text.splitlines() if ln.strip() != ""]
    if not lines:
        return []
    reader = csv.reader(lines)
    table = [row for row in reader]
    if not table:
        return []

    header = [h.strip() for h in table[0]]
    first = _coerce_float(header[0]) if header else None
    ti = vi = wi = None
    if first is None:  # header row present
        if time_col and time_col in header:
            ti = header.index(time_col)
        if value_col and value_col in header:
            vi = header.index(value_col)
        if ti is None:
            ti = 0
        if vi is None:
            vi = 1 if len(header) > 1 else 0
        if len(header) > 2 and _coerce_float(header[2]) is None:
            wi = 2
        body = table[1:]
    else:
        ti, vi = 0, 1 if len(header) > 1 else 0
        if len(header) > 2:
            wi = 2
        body = table

    return _parse_csv_body(body, ti, vi, wi)


def _parse_csv_body(body: Sequence[Sequence[str]], ti: int, vi: int,
                    wi: Optional[int]) -> List[Tuple[float, float, Optional[float]]]:
    out: List[Tuple[float, float, Optional[float]]] = []
    for row in body:
        if len(row) <= max(ti, vi):
            continue
        t = _coerce_float(row[ti])
        y = _coerce_float(row[vi])
        w = _coerce_float(row[wi]) if wi is not None and len(row) > wi else None
        out.append((t, y, w))
    return out


# --------------------------------------------------------------------------- #
# Cleaning
# --------------------------------------------------------------------------- #
def _median(values: Sequence[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])


def prepare_target(raw_rows: Sequence[Tuple[Optional[float], Optional[float], Optional[float]]],
                   outlier_mode: str = "flag",
                   outlier_window: int = 5,
                   max_drop_fraction: float = 0.2) -> PreparedTarget:
    """Validate, merge and flag a target curve.

    ``outlier_mode`` is one of:

    * ``keep``    — do not flag anything (all cleaned points enter the fit);
    * ``flag``    — default; mark suspect/outlier points, keep suspect points
      at full weight and outliers at zero weight (excluded), while still
      returning them so the UI can show *why*;
    * ``winsor``  — replace hard-outlier values with the local trend value
      (they stay in the fit, pulled towards their neighbours).

    At most ``max_drop_fraction`` of valid rows may be excluded; exceeding that
    is treated as a data-quality error rather than silently fitting a handful
    of "convenient" points.
    """
    out = PreparedTarget()
    if outlier_mode not in ("keep", "flag", "winsor"):
        out.errors.append(f"未知异常点处理模式: {outlier_mode}")
        return out

    valid: List[Tuple[float, float, float, int]] = []  # t, y, weight, raw idx
    for idx, row in enumerate(raw_rows):
        t = row[0] if len(row) > 0 else None
        y = row[1] if len(row) > 1 else None
        w = row[2] if len(row) > 2 else None
        if t is None:
            out.dropped.append({"index": idx, "t": t, "y": y,
                                "reason": "bad_time"})
            continue
        if t < 0:
            out.dropped.append({"index": idx, "t": t, "y": y,
                                "reason": "negative_time"})
            continue
        if y is None:
            out.dropped.append({"index": idx, "t": t, "y": None,
                                "reason": "missing_value"})
            continue
        if y < 0:
            out.dropped.append({"index": idx, "t": t, "y": y,
                                "reason": "negative_value"})
            continue
        weight = w if (w is not None and w > 0) else 1.0
        valid.append((float(t), float(y), float(weight), idx))

    if not valid:
        out.errors.append("目标曲线没有任何有效数据点")
        return out

    valid.sort(key=lambda r: r[0])

    # Merge duplicate timestamps as weighted means.
    merged_rows: List[Tuple[float, float, float]] = []
    i = 0
    while i < len(valid):
        t = valid[i][0]
        j = i + 1
        while j < len(valid) and valid[j][0] == t:
            j += 1
        if j - i > 1:
            ws = sum(r[2] for r in valid[i:j])
            ym = sum(r[1] * r[2] for r in valid[i:j]) / ws
            merged_rows.append((t, ym, ws))
            out.merged += j - i
        else:
            merged_rows.append((t, valid[i][1], valid[i][2]))
        i = j

    ys = [r[1] for r in merged_rows]
    y_range = max(ys) - min(ys)
    # Hampel-style outlier detection: each point is compared with a local
    # window that EXCLUDES the point itself, so a single wild spike cannot
    # inflate its own scale estimate (this is what a plain global MAD fallback
    # does wrong on otherwise smooth curves).
    half = max(1, int(outlier_window) // 2)
    for i, (t, y, w) in enumerate(merged_rows):
        lo = max(0, i - half)
        hi = min(len(merged_rows), i + half + 1)
        neigh_idx = [k for k in range(lo, hi) if k != i]
        neigh = [ys[k] for k in neigh_idx]
        m = _median(neigh) if neigh else y
        abs_dev = sorted(abs(v - m) for v in neigh)
        local_mad = abs_dev[len(abs_dev) // 2] if abs_dev else 0.0
        scale = 1.4826 * local_mad
        if scale <= 0:
            # Flat/smooth neighbourhood: use a global-range floor so tiny
            # absolute wiggles near zero do not get flagged.
            scale = max(0.05 * y_range, ABS_FLOOR)
        res = y - m
        pt = TargetPoint(t=t, y=y, weight=w)
        z = abs(res) / scale
        # Relative deviation uses a range-aware floor: protects sharp but real
        # changes near the zero baseline.
        rel = abs(res) / max(abs(m), 0.05 * y_range, ABS_FLOOR)

        # Ramp/boundary awareness: at (or near) the series edges, or when the
        # point lies between the one-sided medians of its left/right
        # neighbours, the curve may simply be steep.  Only exclude points that
        # are gross deviations relative to BOTH sides; interior points get the
        # standard symmetric Hampel treatment.
        left = [ys[k] for k in neigh_idx if k < i]
        right = [ys[k] for k in neigh_idx if k > i]
        lmed = _median(left) if left else None
        rmed = _median(right) if right else None
        tol = 0.1 * y_range
        boundary = lmed is None or rmed is None
        # An isolated spike contradicts BOTH one-sided trends: it lies outside
        # the interval spanned by the left/right medians (with tolerance).
        between = (lmed is not None and rmed is not None and
                   min(lmed, rmed) - tol <= y <= max(lmed, rmed) + tol)
        # At a boundary, or when the point follows the trend across both
        # sides, a steep real curve is a plausible explanation.
        ramp_safe = boundary or between
        z_cut = OUTLIER_MAD if not ramp_safe else OUTLIER_MAD + 2.0
        rel_cut = OUTLIER_REL if not ramp_safe else 1.0

        if outlier_mode != "keep":
            # Hard outlier: symmetric Hampel z>=4 with >=50% deviation, or a
            # grosser deviation (>=120%) at moderate z>=2.5 that cannot be
            # explained by a boundary/ramp.
            hard = ((z >= z_cut and rel >= rel_cut)
                    or (z >= SUSPECT_MAD and rel >= 1.2 and not ramp_safe))
            if hard:
                pt.status = "outlier"
                pt.note = (f"相对局部趋势偏离 {rel * 100:.0f}%（{z:.1f} MAD），"
                           f"疑似异常点")
            elif z >= SUSPECT_MAD and not (ramp_safe and rel < 1.0):
                pt.status = "suspect"
                pt.note = f"偏离局部趋势 {z:.1f} MAD，保留但标记存疑"

        if pt.status == "outlier":
            if outlier_mode == "winsor":
                pt.y = m
                pt.status = "suspect"
                pt.note += "；已按局部趋势缩尾修正"
            elif outlier_mode == "flag":
                pt.status = "excluded"  # excluded from fit, still plotted
        out.points.append(pt)

    kept = out.kept()
    n_excluded = sum(p.status == "excluded" for p in out.points)
    if len(kept) < 3:
        out.errors.append(
            f"清洗后仅剩 {len(kept)} 个有效点（至少需要 3 个才能校准）")
    frac = n_excluded / max(1, len(merged_rows))
    if frac > max_drop_fraction:
        out.errors.append(
            f"有 {n_excluded}/{len(merged_rows)} 个点（{frac * 100:.0f}%）被判为"
            f"异常点，超过 {max_drop_fraction * 100:.0f}% 上限；请检查目标数据"
            f"或改用 outlier_mode=keep/winsor")

    out.t_min = min(p.t for p in kept) if kept else 0.0
    out.t_max = max(p.t for p in kept) if kept else 0.0
    out.y_min = min(p.y for p in kept) if kept else 0.0
    out.y_max = max(p.y for p in kept) if kept else 0.0

    n_missing = sum(d["reason"] == "missing_value" for d in out.dropped)
    if n_missing:
        out.warnings.append(f"{n_missing} 个时间点缺失观测值，已跳过（未按 0 处理）")
    if out.merged:
        out.warnings.append(f"合并了 {out.merged} 个同一时间戳的重复观测（加权平均）")
    n_flag = sum(p.status in ("suspect",) for p in out.points)
    n_excl = sum(p.status == "excluded" for p in out.points)
    if n_flag:
        out.warnings.append(f"{n_flag} 个点偏离较大但保留参与拟合（标记存疑）")
    if n_excl:
        out.warnings.append(f"{n_excl} 个强异常点已排除出拟合（图中以红叉显示）")
    if out.t_min > 0:
        out.warnings.append(
            f"目标数据从 t={out.t_min:g} 才开始，更早的仿真输出不参与误差计算")
    return out
