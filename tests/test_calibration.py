"""Calibration subsystem tests: target cleaning, time alignment, optimization,
determinism and diagnostics.

Run directly::

    python3 tests/test_calibration.py

Small grids/steps keep the suite fast; every check asserts a *behavioural*
property of the feature claims (alignment works without a common grid, the best
is reproducible not lucky, bad data is handled, active bounds are detected).
"""

from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import calibration, storage  # noqa: E402
from backend.calibration.objective import (  # noqa: E402
    align_series, evaluate, linear_interpolate, replicate_seed,
    simulate_series)
from backend.calibration.optimizer import ParamSpec, optimize, Trial  # noqa: E402
from backend.calibration.target import clean_target  # noqa: E402

SCENE = {"id": "s", "name": "t", "domain": "epidemic", "model": "ca",
         "config": {"width": 25, "height": 25, "beta": 0.4, "gamma": 0.1,
                    "initial_infected": 5, "vaccination_rate": 0.0},
         "interventions": []}


def check(name, fn) -> None:
    try:
        fn()
        print(f"PASS  {name}")
    except AssertionError as exc:
        print(f"FAIL  {name}: {exc}")
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(f"FAIL  {name}: {exc}")
        sys.exit(1)


# --------------------------------------------------------------------------- #
def target_cleaning() -> None:
    csv = ("time,value\n"
           "0,5\n2,7\n2,9\n"          # duplicate t=2 -> weighted merge
           "4,null\n6,18\n"           # missing value
           "8,3000\n10,40\n12,55\n14,70\n")  # one gross outlier
    t = clean_target(csv)
    s = t.summary()
    assert s["n_total"] == 8, s          # 9 rows - 1 missing
    assert s["n_points"] == 7            # 8 valid rows, duplicate t=2 merged
    assert s["n_missing"] == 1
    assert s["n_duplicate_rows"] == 1
    assert s["n_outliers"] == 1, s["n_outliers"]
    # outliers keep their point but lose fitting weight
    assert all(not p.outlier or p.w == 0 for p in t.points)
    # drop mode removes them outright
    t2 = clean_target(csv, drop_outliers=True)
    assert t2.summary()["n_outliers"] == 0 and len(t2.points) == 6
    # JSON object form
    t3 = clean_target({"time": [0, 1, 2], "value": [1.0, 2.0, 3.0]})
    assert len(t3.usable()) == 3


def interpolation_no_extrapolation() -> None:
    xs, ys = [0.0, 1.0, 2.0], [0.0, 10.0, 30.0]
    assert linear_interpolate(xs, ys, 0.5) == 5.0
    assert linear_interpolate(xs, ys, 1.5) == 20.0
    assert linear_interpolate(xs, ys, 2.0) == 30.0
    assert linear_interpolate(xs, ys, 2.5) is None   # no extrapolation
    assert linear_interpolate(xs, ys, -0.1) is None


def alignment_mismatched_grids() -> None:
    # sim on integer steps; target on arbitrary times; linear resample only
    sim_t = [float(i) for i in range(11)]
    sim_v = [2.0 * t for t in sim_t]
    tgt_t = [0.0, 0.5, 1.25, 5.0, 10.0, 12.0]
    tgt_v = [0.0] * 6
    pairs = align_series(sim_t, sim_v, None, tgt_t, tgt_v,
                         [1.0] * 6, [False] * 6)
    assert pairs[1].sim == 1.0 and pairs[2].sim == 2.5
    assert pairs[-1].sim is None                    # beyond sim range
    assert sum(1 for p in pairs if p.sim is None) == 1
    # time_scale mapping: target time 2k maps to sim k
    pairs2 = align_series(sim_t, sim_v, None, [0.0, 4.0, 22.0],
                          [0.0] * 3, [1.0] * 3, [False] * 3,
                          time_scale=2.0)
    assert pairs2[1].sim == 4.0 and pairs2[2].sim is None


def evaluate_reproducible_and_replicates() -> None:
    tgt = clean_target([[i, 5 + i] for i in range(15)])
    a = evaluate("epidemic", "ca", SCENE["config"], {"beta": 0.3},
                 steps=14, metric="infected", target=tgt, master_seed=7)
    b = evaluate("epidemic", "ca", SCENE["config"], {"beta": 0.3},
                 steps=14, metric="infected", target=tgt, master_seed=7)
    assert a.nrmse == b.nrmse and a.aligned[3]["sim"] == b.aligned[3]["sim"]
    r5 = evaluate("epidemic", "ca", SCENE["config"], {"beta": 0.3},
                  steps=14, metric="infected", target=tgt,
                  master_seed=7, replicates=5)
    assert all("sim_std" in p for p in r5.sim_curve)
    # same seed must produce same replicate seed on any machine
    assert replicate_seed(7, 0) == replicate_seed(7, 0)


def failed_combos_penalised_not_raised() -> None:
    tgt = clean_target([[0, 5], [1, 8], [2, 12]])
    res = evaluate("epidemic", "ca", SCENE["config"],
                   {"initial_infected": "oops"},
                   steps=4, metric="infected", target=tgt, master_seed=1)
    assert res.failed and res.loss >= 1e12


def optimizer_deterministic_and_tiebroken() -> None:
    # objective insensitive to gamma -> plateau; deterministic tie rule picks
    # the point nearest the centre rather than whichever run "got lucky"
    def make():
        def fn(p, idx, phase):
            loss = abs(p["beta"] - 0.3) * 0.1
            return Trial(dict(p), loss, loss, loss, None, phase, idx)
        return fn
    specs = [ParamSpec("beta", "β", 0.0, 1.0),
             ParamSpec("gamma", "γ", 0.0, 1.0)]
    r1 = optimize(specs, make(), seed=7,
                  current_params={"beta": 0.5, "gamma": 0.5},
                  n_global=8, n_local_starts=2, max_evals=60)
    r2 = optimize(specs, make(), seed=7,
                  current_params={"beta": 0.5, "gamma": 0.5},
                  n_global=8, n_local_starts=2, max_evals=60)
    assert r1.best.params == r2.best.params
    assert abs(r1.best.params["beta"] - 0.3) < 0.05
    assert abs(r1.best.params["gamma"] - 0.5) < 0.15
    assert len(r1.plateau) >= 3


def integer_params_stay_integral() -> None:
    ts, vs = simulate_series("epidemic", "ca", SCENE["config"],
                             {"initial_infected": 12}, 25,
                             "infected", replicate_seed(1, 0))
    req = {"metric": "infected", "target": [[t, v] for t, v in zip(ts, vs)],
           "params": [{"key": "initial_infected", "min": 1, "max": 40}],
           "steps": 25, "seed": 1, "n_global": 10,
           "n_local_starts": 2, "max_evals": 60}
    with tempfile.TemporaryDirectory() as td:
        old = storage.DATA_DIR
        storage.DATA_DIR = td
        try:
            r = calibration.run_calibration(SCENE, req, progress=lambda i: None)
        finally:
            storage.DATA_DIR = old
    v = r["best_params"]["initial_infected"]
    assert abs(v - round(v)) < 1e-9
    assert r["fit"]["nrmse"] < 1e-6


def end_to_end_recovers_known_params() -> None:
    ts, vs = simulate_series("epidemic", "ca", SCENE["config"],
                             {"beta": 0.55, "gamma": 0.12}, 50,
                             "infected", replicate_seed(99, 0))
    sparse = [[t, v] for t, v in zip(ts, vs) if int(t) % 3 == 0]
    req = {"metric": "infected", "target": sparse,
           "params": [{"key": "beta", "min": 0.05, "max": 0.95},
                      {"key": "gamma", "min": 0.01, "max": 0.5}],
           "steps": 50, "seed": 99, "n_global": 16,
           "n_local_starts": 3, "max_evals": 200}
    with tempfile.TemporaryDirectory() as td:
        old = storage.DATA_DIR
        storage.DATA_DIR = td
        try:
            r1 = calibration.run_calibration(SCENE, req, progress=lambda i: None)
            r2 = calibration.run_calibration(SCENE, req, progress=lambda i: None)
        finally:
            storage.DATA_DIR = old
    # same inputs => bit-identical outcome (not luck)
    assert r1["best_params"] == r2["best_params"]
    assert r1["best_loss"] == r2["best_loss"]
    # recovery close to truth and a good fit
    assert abs(r1["best_params"]["beta"] - 0.55) < 0.08, r1["best_params"]
    assert abs(r1["best_params"]["gamma"] - 0.12) < 0.04
    assert r1["fit"]["r2"] > 0.98
    assert r1["fit"]["n_out_of_range"] == 0


def active_bound_is_detected() -> None:
    # truth wants beta ~0.9 but the calibration bound stops at 0.5
    ts, vs = simulate_series("epidemic", "ca", SCENE["config"],
                             {"beta": 0.9, "gamma": 0.1}, 40,
                             "infected", replicate_seed(42, 0))
    req = {"metric": "infected", "target": [[t, v] for t, v in zip(ts, vs)],
           "params": [{"key": "beta", "min": 0.05, "max": 0.5},
                      {"key": "gamma", "min": 0.01, "max": 0.5}],
           "steps": 40, "seed": 42, "n_global": 12,
           "n_local_starts": 3, "max_evals": 150}
    with tempfile.TemporaryDirectory() as td:
        old = storage.DATA_DIR
        storage.DATA_DIR = td
        try:
            r = calibration.run_calibration(SCENE, req, progress=lambda i: None)
        finally:
            storage.DATA_DIR = old
    assert any(b["key"] == "beta" and b["at"] == "upper" and b["active"]
               for b in r["bounds"])
    assert any("放宽" in w for w in r["warnings"])


def main() -> None:
    check("target cleaning (missing/dup/outlier)", target_cleaning)
    check("linear interpolation without extrapolation",
          interpolation_no_extrapolation)
    check("alignment of mismatched time grids", alignment_mismatched_grids)
    check("evaluate determinism + replicate band",
          evaluate_reproducible_and_replicates)
    check("failed parameter combos get penalty", failed_combos_penalised_not_raised)
    check("optimizer determinism + plateau tie-break",
          optimizer_deterministic_and_tiebroken)
    check("integer parameters stay integral", integer_params_stay_integral)
    check("end-to-end recovery + bit-identical rerun",
          end_to_end_recovers_known_params)
    check("active bound diagnosis", active_bound_is_detected)
    print("\nall calibration tests passed")


if __name__ == "__main__":
    main()
