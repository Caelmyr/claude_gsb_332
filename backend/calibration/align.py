"""Time alignment between a target curve and a simulated curve.

The two curves rarely line up point-for-point:

* the simulation may run a different number of steps than the observation
  window;
* target observations may be sparse (e.g. one report every 7 days) while the
  simulation emits a statistic every step;
* the observation window may start at a non-zero time, or use physical time
  while the engine counts steps (``time_per_step`` ≠ 1).

Alignment rule — *interpolate the denser/simulated curve onto the target
timestamps*, never the other way around: inventing target values would feed
the optimizer fake information.  Simulation values at target times are taken
by linear interpolation between bracketing steps; target times outside the
simulated window are either skipped (default, counted in the diagnostics) or
clamped (hold the boundary value) when ``extrapolate="clip"``.

The same aligned pairs then feed :func:`loss`, so every candidate parameter
set is scored on exactly the same timestamps with the same weights — a
prerequisite for comparing two "looks-about-the-same" fits numerically.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .target import PreparedTarget


# --------------------------------------------------------------------------- #
# Simulation
# --------------------------------------------------------------------------- #
def simulate_curve(domain: str, model: str, config: Dict[str, Any],
                   steps: int, metric: str, seed: int,
                   interventions: Optional[List[Dict[str, Any]]] = None,
                   engine_factory: Optional[Callable[..., Any]] = None
                   ) -> Tuple[List[float], List[float]]:
    """Run one fresh engine for ``steps`` and return ``(times, values)``.

    Time 0 with its initial stats is included.  ``engine_factory`` is an
    injection seam for tests; production code uses
    :func:`backend.engine.make_engine`.
    """
    if engine_factory is None:
        from ..engine import make_engine as engine_factory  # type: ignore
    eng = engine_factory(domain, model, config=dict(config), seed=seed)
    due = sorted(interventions or [], key=lambda i: int(i.get("at_step", 0)))
    times = [0.0]
    values = [float(eng.stats().get(metric, 0.0))]
    cursor = 0
    for _ in range(int(steps)):
        t_now = eng.step_count
        while cursor < len(due) and int(due[cursor].get("at_step", 0)) <= t_now:
            eng.apply_intervention(due[cursor])
            cursor += 1
        eng.step()
        times.append(float(eng.step_count))
        values.append(float(eng.stats().get(metric, 0.0)))
    return times, values


# --------------------------------------------------------------------------- #
# Interpolation
# --------------------------------------------------------------------------- #
def interp1d(times: Sequence[float], values: Sequence[float],
             t: float, mode: str = "linear",
             clip: bool = False) -> Optional[float]:
    """Linear / previous-value interpolation with boundary handling.

    Returns ``None`` when ``t`` is outside the support and ``clip`` is False.
    """
    if not times:
        return None
    if t < times[0] or t > times[-1]:
        if not clip:
            return None
        if t <= times[0]:
            return float(values[0])
        return float(values[-1])
    # Binary search for the bracketing interval.
    lo, hi = 0, len(times) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if times[mid] <= t:
            lo = mid
        else:
            hi = mid
    if t == times[lo]:
        return float(values[lo])
    if t == times[hi]:
        return float(values[hi])
    if mode == "previous":
        return float(values[lo])
    span = times[hi] - times[lo]
    frac = (t - times[lo]) / span if span > 0 else 0.0
    return float(values[lo] + frac * (values[hi] - values[lo]))


@dataclass
class AlignedPairs:
    t: List[float] = field(default_factory=list)
    target: List[float] = field(default_factory=list)
    sim: List[float] = field(default_factory=list)
    weight: List[float] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.t)


def align_pairs(target: PreparedTarget,
                sim_times: Sequence[float], sim_values: Sequence[float],
                time_per_step: float = 1.0,
                time_offset: float = 0.0,
                interp: str = "linear",
                extrapolate: str = "skip") -> AlignedPairs:
    """Map simulated steps onto target timestamps and build aligned pairs.

    Target time ``t`` corresponds to simulation step
    ``(t - time_offset) / time_per_step``.  Fractional steps are interpolated.
    """
    out = AlignedPairs()
    mapped_times = [time_offset + s * float(time_per_step) for s in sim_times]
    clip = extrapolate == "clip"
    for p in target.kept():
        v = interp1d(mapped_times, sim_values, p.t, mode=interp, clip=clip)
        if v is None or not math.isfinite(v):
            out.skipped.append({"t": p.t, "y": p.y,
                                "reason": "outside_simulation_window"})
            continue
        out.t.append(p.t)
        out.target.append(float(p.y))
        out.sim.append(float(v))
        out.weight.append(float(p.weight))
    return out


# --------------------------------------------------------------------------- #
# Loss functions
# --------------------------------------------------------------------------- #
@dataclass
class LossResult:
    value: float = float("inf")
    n: int = 0
    rmse: float = 0.0
    mae: float = 0.0
    mape: float = 0.0
    mape_n: int = 0
    r2: float = 0.0
    scale: float = 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {"loss": self.value, "n": self.n, "rmse": self.rmse,
                "mae": self.mae, "mape": self.mape, "mape_n": self.mape_n,
                "r2": self.r2, "scale": self.scale}


def loss(pairs: AlignedPairs, kind: str = "nrmse",
         target_range: Optional[Tuple[float, float]] = None,
         scale: Optional[float] = None) -> LossResult:
    """Weighted fitting error on aligned pairs.

    ``kind`` chooses the primary loss:

    * ``nrmse``   — RMSE normalised by the target range (default; scale-free,
                    comparable across metrics/counts of very different size);
    * ``rmse``    — raw root-mean-square error;
    * ``mae``     — mean absolute error (less sensitive to one bad region);
    * ``mape``    — mean absolute percentage error (relative error; use when
                    the early near-zero part of the curve must be respected —
                    a small epsilon floor avoids division by zero);
    * ``log_rmse``— RMSE on ``log1p`` values (balances early/large phases for
                    exponential-growth epidemic curves).
    """
    res = LossResult()
    n = len(pairs)
    res.n = n
    if n == 0:
        return res
    wsum = sum(pairs.weight) or 1.0
    err = [(s - y) for y, s in zip(pairs.target, pairs.sim)]
    res.rmse = math.sqrt(sum(w * e * e for w, e in zip(pairs.weight, err)) / wsum)
    res.mae = sum(w * abs(e) for w, e in zip(pairs.weight, err)) / wsum

    # MAPE is undefined on zero targets: use only points whose target exceeds
    # 5 % of the target range (a symmetric MAPE-style floor), and report how
    # many points that was so a number built on 1-2 points is never mistaken
    # for a whole-curve error.
    eps = 0.05 * (max(pairs.target) - min(pairs.target) or abs(max(pairs.target)))
    mape_terms = [(w, e, y) for w, e, y in
                  zip(pairs.weight, err, pairs.target) if abs(y) > eps]
    wm = sum(t[0] for t in mape_terms) or 1.0
    res.mape_n = len(mape_terms)
    res.mape = (sum(w * abs(e) / abs(y) for w, e, y in mape_terms) / wm
                * 100.0) if mape_terms else float("nan")

    ybar = sum(w * y for w, y in zip(pairs.weight, pairs.target)) / wsum
    sst = sum(w * (y - ybar) ** 2 for w, y in zip(pairs.weight, pairs.target))
    sse = sum(w * e * e for w, e in zip(pairs.weight, err))
    res.r2 = 1.0 - sse / sst if sst > 0 else 0.0

    if scale is not None and scale > 0:
        res.scale = float(scale)
    else:
        lo = target_range[0] if target_range else min(pairs.target)
        hi = target_range[1] if target_range else max(pairs.target)
        rng = hi - lo
        if rng > 0:
            res.scale = float(rng)
        elif abs(hi) > 0:
            # Constant (but nonzero) target: normalise by its level.
            res.scale = float(abs(hi))
        else:
            # Identically zero target: RMSE is already an absolute count; a
            # single infection must not read as a huge "relative" error.
            res.scale = 1.0

    log_scale = 1.0
    if kind == "nrmse":
        res.value = res.rmse / res.scale
    elif kind == "rmse":
        res.value = res.rmse
    elif kind == "mae":
        res.value = res.mae
    elif kind == "mape":
        res.value = res.mape
    elif kind == "log_rmse":
        lr = [math.log1p(max(s, 0.0)) - math.log1p(max(y, 0.0))
              for y, s in zip(pairs.target, pairs.sim)]
        res.value = math.sqrt(sum(w * e * e for w, e in zip(pairs.weight, lr)) / wsum)
        log_scale = res.value
    else:
        raise ValueError(f"未知损失函数: {kind}")
    if not math.isfinite(res.value):
        res.value = float("inf")
    if kind == "log_rmse":
        res.scale = log_scale
    return res


def align_and_score(target: PreparedTarget,
                    sim_times: Sequence[float], sim_values: Sequence[float],
                    kind: str = "nrmse",
                    time_per_step: float = 1.0,
                    time_offset: float = 0.0,
                    interp: str = "linear",
                    extrapolate: str = "skip") -> Tuple[LossResult, AlignedPairs]:
    """Convenience: align then score (used by the calibrator and by tests)."""
    pairs = align_pairs(target, sim_times, sim_values,
                        time_per_step=time_per_step, time_offset=time_offset,
                        interp=interp, extrapolate=extrapolate)
    res = loss(pairs, kind=kind, target_range=(target.y_min, target.y_max))
    return res, pairs
