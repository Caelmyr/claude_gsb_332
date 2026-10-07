"""Stdlib-only derivative-free optimizers over a bounded parameter box.

No numpy/scipy dependency (the project ships with Flask alone), so two small,
well-understood algorithms are implemented:

* :func:`latin_hypercube` — a *deterministic* maximin Latin-hypercube design
  in the unit cube, mapped to parameter bounds.  LHS covers the whole box
  evenly (no corner clustering like a random draw, no curse-of-dimensionality
  blow-up like a grid); the design is generated from one integer ``seed`` and
  a fixed number of random swaps, hence fully reproducible.
* :func:`pattern_search` — Hooke-Jeeves coordinate pattern search with
  successive step halving.  It is robust for noisy-but-averaged objectives,
  makes no smoothness assumptions, and the step lengths are expressed in the
  normalised unit cube so int/float parameters of very different magnitudes
  (β≈0.3 vs n≈800) are treated on equal footing.

Both operate on a :class:`~backend.calibration.calibrator.ParamSpace`-like
object duck-typed as::

    space.names     -> [str, ...]
    space.lows/highs -> [float, ...]
    space.is_int     -> [bool, ...]
    space.denorm(x)  -> dict  (unit-cube vector -> typed parameter dict)
    space.norm(cfg)  -> list   (parameter dict -> unit cube)
    space.clip(x)    -> list   (unit-cube vector, clamped & int-rounded)
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple


# --------------------------------------------------------------------------- #
# Parameter space
# --------------------------------------------------------------------------- #
@dataclass
class ParamSpec:
    name: str
    low: float
    high: float
    is_int: bool = False

    def __post_init__(self) -> None:
        self.low = float(self.low)
        self.high = float(self.high)
        if self.low > self.high:
            raise ValueError(f"参数 {self.name} 下界 {self.low} 大于上界 {self.high}")
        if self.is_int:
            self.low_int = int(math.ceil(self.low))
            self.high_int = int(math.floor(self.high))
            if self.low_int > self.high_int:
                raise ValueError(
                    f"整型参数 {self.name} 在 [{self.low}, {self.high}] 内无整数可取")
        if self.high == self.low:
            raise ValueError(f"参数 {self.name} 上下界相等（{self.low}），"
                             f"无可校准空间，请放宽或移除该参数")


class ParamSpace:
    """Normalised [0,1]^d box with typed de-normalisation."""

    def __init__(self, specs: Sequence[ParamSpec]):
        self.specs: List[ParamSpec] = list(specs)
        self.names: List[str] = [s.name for s in self.specs]
        self.lows: List[float] = [s.low for s in self.specs]
        self.highs: List[float] = [s.high for s in self.specs]
        self.is_int: List[bool] = [s.is_int for s in self.specs]
        # Integer parameters need a discrete grid in the unit cube.
        self._levels: List[int] = [
            (s.high_int - s.low_int + 1) if s.is_int else 0
            for s in self.specs]

    @property
    def dim(self) -> int:
        return len(self.specs)

    def denorm(self, x: Sequence[float]) -> Dict[str, Any]:
        cfg: Dict[str, Any] = {}
        for spec, u in zip(self.specs, x):
            if spec.is_int:
                idx = int(round(u * (spec.high_int - spec.low_int)))
                idx = max(0, min(spec.high_int - spec.low_int, idx))
                cfg[spec.name] = spec.low_int + idx
            else:
                v = spec.low + float(u) * (spec.high - spec.low)
                cfg[spec.name] = v
        return cfg

    def norm(self, cfg: Dict[str, Any]) -> List[float]:
        out = []
        for spec in self.specs:
            v = float(cfg[spec.name])
            u = 0.0 if spec.high == spec.low else (v - spec.low) / (spec.high - spec.low)
            if spec.is_int:
                n = spec.high_int - spec.low_int
                u = round((v - spec.low_int) / n) if n else 0.0
            out.append(min(1.0, max(0.0, u)))
        return out

    def clip(self, x: Sequence[float]) -> List[float]:
        out = []
        for spec, u in zip(self.specs, x):
            u = min(1.0, max(0.0, float(u)))
            if spec.is_int:
                n = spec.high_int - spec.low_int
                u = round(u * n) / n if n else 0.0
            out.append(u)
        return out

    def at_boundary(self, x: Sequence[float], tol: float = 1e-3
                    ) -> Dict[str, str]:
        """Return ``{name: "min"|"max"}`` for parameters sitting on a bound."""
        hits: Dict[str, str] = {}
        for spec, u in zip(self.specs, x):
            if u <= tol:
                hits[spec.name] = "min"
            elif u >= 1 - tol:
                hits[spec.name] = "max"
        return hits

    def describe(self, cfg: Dict[str, Any]) -> Dict[str, Any]:
        out = {}
        for spec in self.specs:
            v = cfg[spec.name]
            out[spec.name] = {"value": v, "low": spec.low, "high": spec.high,
                              "is_int": spec.is_int}
        return out


# --------------------------------------------------------------------------- #
# Latin hypercube
# --------------------------------------------------------------------------- #
def _lhs_levels(n: int, d: int, rng: random.Random,
                discrete_levels: Optional[Sequence[int]] = None
                ) -> List[List[int]]:
    """Build an n-point LHS on integer level indices 0..n-1 per column.

    For discrete (integer) parameters with fewer levels than ``n``, levels are
    stratified across the available distinct values instead (a column would
    otherwise contain duplicates only at the same positions).
    """
    cols: List[List[int]] = []
    for j in range(d):
        k = discrete_levels[j] if discrete_levels and discrete_levels[j] else n
        if k >= n:
            # Classic LHS: each of the n strata exactly once.
            perm = list(range(n))
            rng.shuffle(perm)
            col = perm
        else:
            # Fewer distinct values than samples: cycle through values evenly,
            # shuffling the assignment so projections stay balanced.
            base = [i % k for i in range(n)]
            rng.shuffle(base)
            col = base
        cols.append(col)

    # Maximin improvement: try random column-wise swaps and keep them when the
    # smallest pairwise distance grows (bounded trial count keeps it cheap).
    def min_dist(columns: Sequence[Sequence[int]]) -> float:
        best = float("inf")
        scale = [max(1, (discrete_levels[j] if discrete_levels and discrete_levels[j] else n) - 1)
                 for j in range(d)]
        pts = [[columns[j][i] / scale[j] for j in range(d)] for i in range(n)]
        for i in range(n):
            for k in range(i + 1, n):
                dd = sum((pts[i][j] - pts[k][j]) ** 2 for j in range(d))
                if dd < best:
                    best = dd
        return best

    trials = min(200, 40 * d)
    cur = min_dist(cols)
    for _ in range(trials):
        j = rng.randrange(d)
        a, b = rng.randrange(n), rng.randrange(n)
        cols[j][a], cols[j][b] = cols[j][b], cols[j][a]
        nd = min_dist(cols)
        if nd >= cur:
            cur = nd
        else:
            cols[j][a], cols[j][b] = cols[j][b], cols[j][a]
    return cols


def latin_hypercube(space: ParamSpace, n: int, seed: int = 0,
                    center: bool = False) -> List[List[float]]:
    """Return ``n`` points in the [0,1]^d box for ``space``.

    Each stratum of every coordinate is hit exactly once.  ``center=True``
    places points at stratum centres (used when the objective is expensive and
    the within-stratum jitter would cost a run); otherwise a seeded jitter is
    used so two designs differ reproducibly.
    """
    rng = random.Random(seed)
    d = space.dim
    n = max(1, int(n))
    cols = _lhs_levels(n, d, rng, discrete_levels=space._levels)
    pts: List[List[float]] = []
    for i in range(n):
        u: List[float] = []
        for j, spec in enumerate(space.specs):
            if spec.is_int:
                k = spec.high_int - spec.low_int
                u.append(cols[j][i] / k if k else 0.0)
            else:
                if center:
                    frac = (cols[j][i] + 0.5) / n
                else:
                    frac = (cols[j][i] + rng.random()) / n
                u.append(min(1.0, max(0.0, frac)))
        pts.append(space.clip(u))
    return pts


# --------------------------------------------------------------------------- #
# Hooke-Jeeves pattern search
# --------------------------------------------------------------------------- #
@dataclass
class OptTrace:
    x: List[float]
    score: float
    stage: str

    def to_dict(self) -> Dict[str, Any]:
        return {"x": list(self.x), "score": self.score, "stage": self.stage}


def pattern_search(
    space: ParamSpace,
    objective: Callable[[Dict[str, Any]], float],
    x0: Sequence[float],
    budget: int = 60,
    init_step: float = 0.15,
    min_step: float = 1e-3,
    shrink: float = 0.5,
    stage: str = "local",
    cache: Optional[Dict[Tuple[float, ...], float]] = None,
    on_eval: Optional[Callable[[List[float], float], None]] = None,
) -> Tuple[List[float], float, List[OptTrace]]:
    """Coordinate pattern search from ``x0`` with at most ``budget`` evals.

    Exploratory moves sweep every coordinate in +/- step order; when any move
    improves, an accelerated pattern move in the combined direction is tried.
    Failure shrinks the step.  Integer coordinates are automatically snapped
    by :meth:`ParamSpace.clip`, and an exploratory move that snaps back to the
    same point is not re-evaluated.
    """
    if cache is None:
        cache = {}
    trace: List[OptTrace] = []

    def score(x: Sequence[float]) -> float:
        key = tuple(round(v, 9) for v in x)
        if key not in cache:
            val = float(objective(space.denorm(list(x))))
            if not math.isfinite(val):
                val = 1e18
            cache[key] = val
            trace.append(OptTrace(list(x), val, stage))
            nonlocal_used[0] += 1
            if on_eval:
                on_eval(list(x), val)
        return cache[key]

    nonlocal_used = [0]
    x = space.clip(x0)
    best = score(x)
    used = nonlocal_used[0]
    step = init_step

    while step >= min_step and used < budget:
        improved = False
        base = list(x)
        delta = [0.0] * space.dim
        for j in range(space.dim):
            for direction in (1.0, -1.0):
                if used >= budget:
                    break
                trial = space.clip(
                    [base[k] + (step * direction if k == j else 0.0)
                     for k in range(space.dim)])
                if trial == base or trial == x:
                    continue
                v = score(trial)
                used = nonlocal_used[0]
                if v < best - 1e-12:
                    best = v
                    x = trial
                    delta[j] = step * direction
                    improved = True
                    break
            if used >= budget:
                break

        # Accelerated pattern move.
        if improved and used < budget and any(delta):
            trial = space.clip([x[k] + delta[k] for k in range(space.dim)])
            if trial != x:
                v = score(trial)
                used = nonlocal_used[0]
                if v < best - 1e-12:
                    best, x = v, trial

        if not improved:
            step *= shrink

    return space.clip(x), best, trace
