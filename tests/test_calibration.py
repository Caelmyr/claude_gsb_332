"""Tests for parameter calibration: cleaning, alignment, optimization, fitting.

Run directly::

    python3 tests/test_calibration.py

Covers the properties the feature promises:

* target cleaning: missing rows are not treated as zero; duplicate timestamps
  are merged; an isolated wild spike is excluded while genuinely smooth data
  is left untouched;
* alignment: simulated curves with different step counts / physical-time
  scaling are interpolated onto target timestamps; out-of-window targets are
  skipped or clipped per request;
* optimizer: deterministic Latin hypercube (reproducible, one stratum per
  coordinate), bounded pattern search that recovers a known optimum, integer
  parameters never leaving their grid;
* end-to-end: calibrating against a curve produced by known parameters reaches
  a small error and is byte-for-byte reproducible for a fixed seed; a failed
  candidate is survived without aborting the search.
"""

from __future__ import annotations

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.calibration.align import (
    align_and_score, align_pairs, interp1d, loss, simulate_curve,
)
from backend.calibration.calibrator import CalibrationSpec, run_calibration
from backend.calibration.optimizer import (
    ParamSpace, ParamSpec, latin_hypercube, pattern_search,
)
from backend.calibration.target import parse_target, prepare_target


def check(name, fn) -> None:
    try:
        fn()
        print(f"PASS  {name}")
    except AssertionError as exc:
        print(f"FAIL  {name}: {exc}")
        raise


# --------------------------------------------------------------------------- #
# Target cleaning
# --------------------------------------------------------------------------- #
def t_missing_not_zero() -> None:
    raw = parse_target({"points": [{"t": 0, "y": 1}, {"t": 1, "y": None},
                                   {"t": 2, "y": 3}]})
    pt = prepare_target(raw, outlier_mode="keep")
    dropped = [d for d in pt.dropped if d["reason"] == "missing_value"]
    assert len(dropped) == 1, pt.dropped
    assert len(pt.kept()) == 2
    # The missing row must NOT have been coerced to a zero target value.
    assert [p.y for p in pt.kept()] == [1.0, 3.0]


def t_duplicates_merged() -> None:
    raw = parse_target([{"t": 1, "y": 10, "weight": 1},
                        {"t": 1, "y": 20, "weight": 3},
                        {"t": 2, "y": 5}])
    pt = prepare_target(raw, outlier_mode="keep")
    assert len(pt.points) == 2
    # Weighted mean (10*1 + 20*3) / 4 = 17.5
    assert abs(pt.points[0].y - 17.5) < 1e-9, pt.points[0].y
    assert pt.merged == 2


def t_spike_excluded_smooth_kept() -> None:
    smooth = [i * 10 for i in range(15)]
    raw = parse_target([{"t": i, "y": y} for i, y in enumerate(smooth)])
    pt = prepare_target(raw)
    assert all(p.status == "ok" for p in pt.points), \
        [p.note for p in pt.points if p.status != "ok"]

    spiked = list(smooth)
    spiked[8] = 500
    raw2 = parse_target([{"t": i, "y": y} for i, y in enumerate(spiked)])
    pt2 = prepare_target(raw2)
    flagged = [p for p in pt2.points if p.status == "excluded"]
    assert len(flagged) == 1 and flagged[0].t == 8, flagged
    # winsor keeps the point in the fit, pulled to the local trend
    pt3 = prepare_target(raw2, outlier_mode="winsor")
    w = [p for p in pt3.points if p.t == 8][0]
    assert w.status == "suspect" and abs(w.y - 80) < 1e-9


def t_too_many_outliers_errors() -> None:
    raw = parse_target([{"t": 0, "y": 1}, {"t": 1, "y": 999},
                        {"t": 2, "y": 1}, {"t": 3, "y": 999},
                        {"t": 4, "y": 1}, {"t": 5, "y": 999}])
    pt = prepare_target(raw, max_drop_fraction=0.2)
    assert pt.errors, "expected a data-quality error"


# --------------------------------------------------------------------------- #
# Alignment
# --------------------------------------------------------------------------- #
def t_interp_basic() -> None:
    assert interp1d([0, 1, 2], [0, 10, 20], 0.5) == 5
    assert interp1d([0, 1, 2], [0, 10, 20], 3) is None
    assert interp1d([0, 1, 2], [0, 10, 20], 3, clip=True) == 20
    assert interp1d([0, 1, 2], [0, 10, 20], 0.5, mode="previous") == 0


def t_align_sparse_target_and_time_scale() -> None:
    raw = parse_target([{"t": 0, "y": 0}, {"t": 10, "y": 10}])
    pt = prepare_target(raw, outlier_mode="keep")
    # Dense sim, identity time: linear interp must hit the sim values exactly.
    times = [float(i) for i in range(11)]
    values = [float(i) for i in range(11)]
    pairs = align_pairs(pt, times, values)
    assert pairs.target == [0, 10] and pairs.sim == [0, 10]

    # Sim time = step*0.5 with offset: target t=10 -> step 18; sim value = step
    pairs2 = align_pairs(pt, times, values, time_per_step=0.5, time_offset=1.0)
    # t=0 < offset 1 -> out of window, skipped; t=10 -> step (10-1)/0.5=18 >10
    # also outside -> both skipped
    assert len(pairs2) == 0 and len(pairs2.skipped) == 2
    # With clipping both map to boundary values
    pairs3 = align_pairs(pt, times, values, time_per_step=0.5,
                         time_offset=1.0, extrapolate="clip")
    assert pairs3.sim == [0, 10]


def t_loss_scale_free() -> None:
    raw = parse_target([{"t": 0, "y": 0}, {"t": 1, "y": 10}])
    pt = prepare_target(raw, outlier_mode="keep")
    times = [0.0, 1.0]
    r1, _ = align_and_score(pt, times, [0.0, 11.0])
    r100, _ = align_and_score(pt, times, [0.0, 20.0])
    # RMSE averages over BOTH aligned points: residuals (0,-1) -> sqrt(1/2),
    # divided by target range 10 -> 0.0707; (0,-10) -> sqrt(100/2)/10 = .7071.
    assert abs(r1.value - 0.070710678) < 1e-9, r1.value
    assert abs(r100.value - 0.707106781) < 1e-9, r100.value
    # Scale-free in the sense that multiplying target AND sim by 100 leaves
    # the normalized error unchanged.
    raw_big = parse_target([{"t": 0, "y": 0}, {"t": 1, "y": 1000}])
    pt_big = prepare_target(raw_big, outlier_mode="keep")
    r_big, _ = align_and_score(pt_big, times, [0.0, 1100.0])
    assert abs(r_big.value - r1.value) < 1e-9, (r_big.value, r1.value)


# --------------------------------------------------------------------------- #
# Optimizer
# --------------------------------------------------------------------------- #
def t_lhs_deterministic_and_stratified() -> None:
    sp = ParamSpace([ParamSpec("a", 0.0, 1.0), ParamSpec("b", 0.0, 1.0)])
    p1 = latin_hypercube(sp, 10, seed=3)
    p2 = latin_hypercube(sp, 10, seed=3)
    assert p1 == p2, "LHS must be deterministic for a fixed seed"
    p3 = latin_hypercube(sp, 10, seed=4)
    assert p3 != p1
    n = 10
    for j in range(2):
        strata = sorted(int(u * n) for u in [p[j] for p in p1])
        assert strata == list(range(n)), strata  # each stratum once
    assert all(0.0 <= u <= 1.0 for p in p1 for u in p)


def t_integer_space_grid() -> None:
    sp = ParamSpace([ParamSpec("n", 3, 7, is_int=True)])
    pts = latin_hypercube(sp, 20, seed=1)
    vals = {sp.denorm(p)["n"] for p in pts}
    assert vals == {3, 4, 5, 6, 7}, vals
    # denorm always yields ints inside the box
    for p in pts:
        v = sp.denorm(p)["n"]
        assert isinstance(v, int) and 3 <= v <= 7


def t_pattern_search_recovers() -> None:
    sp = ParamSpace([ParamSpec("x", -5, 5), ParamSpec("y", -5, 5)])
    best = {"v": None}

    def obj(c):
        return (c["x"] - 1.25) ** 2 + (c["y"] + 2.0) ** 2 + 0.01
    x, v, trace = pattern_search(sp, obj, [0.0, 0.0], budget=120)
    cfg = sp.denorm(x)
    assert abs(cfg["x"] - 1.25) < 0.05, cfg
    assert abs(cfg["y"] + 2.0) < 0.05, cfg
    assert v < 0.02, v
    # reproducible
    x2, v2, _ = pattern_search(sp, obj, [0.0, 0.0], budget=120)
    assert x2 == x and v2 == v


def t_pattern_search_respects_bounds() -> None:
    sp = ParamSpace([ParamSpec("x", 0.0, 1.0)])

    def obj(c):
        return -(c["x"])  # optimum wants -inf; must stop at the bound
    x, v, _ = pattern_search(sp, obj, [0.2], budget=30)
    assert x[0] >= 1.0 - 1e-9 and sp.denorm(x)["x"] <= 1.0


# --------------------------------------------------------------------------- #
# End-to-end calibration
# --------------------------------------------------------------------------- #
def t_mape_safe_with_zero_targets() -> None:
    raw = parse_target([{"t": 0, "y": 0}, {"t": 1, "y": 0},
                        {"t": 2, "y": 100}, {"t": 3, "y": 0}])
    pt = prepare_target(raw, outlier_mode="keep")
    times = [0.0, 1.0, 2.0, 3.0]
    res, pairs = align_and_score(pt, times, [1.0, 2.0, 110.0, 3.0],
                                 kind="mape")
    # Must return a finite number computed only on the one point safely above
    # the 5%-of-range floor (y=100) — never 1e10-style epsilon artifacts.
    assert len(pairs) == 4
    assert math.isfinite(res.mape), res.mape
    assert res.mape_n == 1
    assert abs(res.mape - 10.0) < 1e-9, res.mape  # |110-100|/100


def t_constant_target_scaling() -> None:
    # Identically-zero target with an all-zero sim is a perfect fit (0),
    # not a division-by-range-zero artifact.
    pt0 = prepare_target(parse_target([{"t": i, "y": 0} for i in range(5)]),
                         outlier_mode="keep")
    times = [float(i) for i in range(5)]
    r0, _ = align_and_score(pt0, times, [0] * 5)
    assert r0.value == 0.0, r0.value
    # Constant nonzero target normalises by the level, not the (zero) range.
    ptc = prepare_target(parse_target([{"t": i, "y": 100} for i in range(3)]),
                         outlier_mode="keep")
    rc, _ = align_and_score(ptc, [0.0, 1.0, 2.0], [100.0, 110.0, 100.0])
    assert abs(rc.value - 0.0577) < 1e-3, rc.value


def t_end_to_end_recovery_and_reproducibility() -> None:
    # "Real" curve from known CA SIR parameters on fixed seeds.
    target_rows = []
    times, vals = simulate_curve(
        "epidemic", "ca",
        {"width": 40, "height": 40, "beta": 0.35, "gamma": 0.10,
         "initial_infected": 4},
        60, "infected", seed=4242)
    for t, y in zip(times, vals):
        if t % 3 == 0:
            target_rows.append({"t": t, "y": y})
    pt = prepare_target(parse_target({"points": target_rows}))

    def make_spec():
        return CalibrationSpec(
            domain="epidemic", model="ca", metric="infected", target=pt,
            params=[{"name": "beta", "low": 0.05, "high": 0.8},
                    {"name": "gamma", "low": 0.01, "high": 0.4}],
            base_config={"width": 40, "height": 40, "initial_infected": 4},
            steps=60, seed=777, n_replicates=2, n_refine=40,
            n_refine_starts=2, confirm_replicates=4)

    r1 = run_calibration(make_spec())
    r2 = run_calibration(make_spec())
    assert r1.status == "finished", r1.error
    assert r1.best_params == r2.best_params, "same seed must reproduce best params"
    assert r1.confirm_error["loss"]["mean"] == r2.confirm_error["loss"]["mean"]
    # A good fit to data the model itself generated: NRMSE well under 25 %.
    assert r1.confirm_error["loss"]["mean"] < 0.25, r1.confirm_error["loss"]
    # Overlay covers all steps and every residual row carries both values.
    assert len(r1.overlay["sim_mean"]) == 61
    assert all(r["sim"] is not None for r in r1.residuals)
    # Ranking sorted by confirmation mean.
    means = [f["confirm_mean"] for f in r1.ranking]
    assert means == sorted(means)


def t_validation_rejects_bad_box() -> None:
    pt = prepare_target(parse_target([{"t": i, "y": i} for i in range(5)]),
                        outlier_mode="keep")
    spec = CalibrationSpec(
        domain="epidemic", model="ca", metric="infected", target=pt,
        params=[{"name": "beta", "low": 0.8, "high": 0.1}],
        steps=10)
    errs = spec.validate()
    assert any("low < high" in e for e in errs), errs


def t_abort_stops_pipeline() -> None:
    pt = prepare_target(parse_target([{"t": i, "y": 50 + i} for i in range(10)]),
                        outlier_mode="keep")
    spec = CalibrationSpec(
        domain="epidemic", model="ca", metric="infected", target=pt,
        params=[{"name": "beta", "low": 0.0, "high": 1.0}],
        base_config={"width": 20, "height": 20},
        steps=10, n_replicates=1, n_design=40, confirm_replicates=2)
    flag = {"abort": False}

    def on_progress(p):
        if p["phase"] == "design" and p["done"] >= 2:
            flag["abort"] = True
    r = run_calibration(spec, on_progress=on_progress,
                        should_abort=lambda: flag["abort"])
    assert r.status == "stopped", r.status


def main() -> None:
    check("missing target rows are dropped, not zeroed", t_missing_not_zero)
    check("duplicate timestamps merged weighted", t_duplicates_merged)
    check("isolated spike excluded; smooth data kept", t_spike_excluded_smooth_kept)
    check("excessive outliers raise a data error", t_too_many_outliers_errors)
    check("interpolation and boundary clipping", t_interp_basic)
    check("alignment handles sparse targets and time scaling",
          t_align_sparse_target_and_time_scale)
    check("NRMSE is scale-free", t_loss_scale_free)
    check("MAPE ignores zero/near-zero targets", t_mape_safe_with_zero_targets)
    check("constant targets normalise by level", t_constant_target_scaling)
    check("LHS deterministic and stratified", t_lhs_deterministic_and_stratified)
    check("integer LHS stays on the integer grid", t_integer_space_grid)
    check("pattern search recovers the optimum", t_pattern_search_recovers)
    check("pattern search cannot leave the bounds", t_pattern_search_respects_bounds)
    check("end-to-end recovery is accurate and reproducible",
          t_end_to_end_recovery_and_reproducibility)
    check("validation rejects invalid parameter boxes", t_validation_rejects_bad_box)
    check("abort flag stops the pipeline", t_abort_stops_pipeline)
    print("\nall calibration tests passed")


if __name__ == "__main__":
    main()
