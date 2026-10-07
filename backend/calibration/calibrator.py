"""Calibration orchestration: search, score, confirm, diagnose.

The pipeline is deliberately staged so every number in the final report is
traceable:

1. **Prepare** the target curve (missing / duplicate / outlier handling — see
   :mod:`target`) and validate the requested parameter box against the catalog.
2. **Global design** — a deterministic Latin-hypercube sweep over the box;
   every candidate is simulated for ``n_replicates`` runs over a *fixed* set
   of seeds and scored on the aligned target timestamps.
3. **Local refinement** — Hooke-Jeeves pattern searches start from the best,
   well-separated design points (multi-start avoids latching onto one local
   basin) and share one evaluation cache.
4. **Confirmation** — the top finalists are re-simulated on a *disjoint* set
   of seeds, more replicates than the search used.  The winner is chosen on
   the confirmation mean, never on the training mean, so a candidate cannot
   win by a lucky Monte-Carlo draw.  When two finalists are statistically
   indistinguishable (gap inside the Monte-Carlo standard error), the winner
   is the one with smaller run-to-run variance and, failing that, the one
   closest to the catalog prior — and the tie is reported, not hidden.
5. **Diagnostics** — boundary hits, per-parameter sensitivity at the optimum
   (identifiability), replicate error distribution, and data-quality notes.
6. **Overlay** — the winner's mean simulation curve (+/-1 std band) sampled
   densely, plus simulated values at each target time for a residual table.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from .. import catalog
from . import align as align_mod
from .optimizer import ParamSpec, ParamSpace, latin_hypercube, pattern_search
from .target import PreparedTarget, prepare_target, parse_target

LOSS_KINDS = ("nrmse", "rmse", "mae", "mape", "log_rmse")
NEAR_TIE_REL = 0.02          # 2 % relative gap counts as "looks the same"
FINALIST_MIN_DIST = 0.08     # min unit-cube distance between refined starts
SENSITIVITY_STEP = 0.05      # +-5 % of box for the local sensitivity probe


@dataclass
class CalibrationSpec:
    domain: str
    model: str
    metric: str
    target: PreparedTarget
    params: List[Dict[str, Any]]
    base_config: Dict[str, Any] = field(default_factory=dict)
    interventions: List[Dict[str, Any]] = field(default_factory=list)
    steps: int = 150
    seed: int = 12345
    n_replicates: int = 3
    n_design: int = 0                 # 0 -> auto from dimensionality
    n_refine: int = 90                # total pattern-search evaluations
    n_refine_starts: int = 3
    confirm_replicates: int = 6
    loss_kind: str = "nrmse"
    time_per_step: float = 1.0
    time_offset: float = 0.0
    interp: str = "linear"
    extrapolate: str = "skip"
    outlier_mode: str = "flag"
    name: str = "参数校准"
    engine_factory: Optional[Callable[..., Any]] = None

    # ------------------------------------------------------------------ #
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CalibrationSpec":
        domain = str(data["domain"])
        model = str(data["model"])
        metric = str(data.get("metric", "infected"))
        tdata = data.get("target_points")
        if tdata is None:
            tdata = data.get("target")
        time_key = str(data.get("time_key", "t"))
        value_key = str(data.get("value_key", "y"))
        weight_key = data.get("weight_key")
        raw = parse_target(tdata if tdata is not None else {},
                           time_key=time_key, value_key=value_key,
                           weight_key=weight_key)
        target = prepare_target(raw,
                                outlier_mode=str(data.get("outlier_mode", "flag")))
        return cls(
            domain=domain, model=model, metric=metric, target=target,
            params=list(data.get("params") or []),
            base_config=dict(data.get("base_config") or {}),
            interventions=list(data.get("interventions") or []),
            steps=int(data.get("steps", 150)),
            seed=int(data.get("seed", 12345)),
            n_replicates=int(data.get("n_replicates", 3)),
            n_design=int(data.get("n_design", 0)),
            n_refine=int(data.get("n_refine", 90)),
            n_refine_starts=int(data.get("n_refine_starts", 3)),
            confirm_replicates=int(data.get("confirm_replicates", 6)),
            loss_kind=str(data.get("loss_kind", "nrmse")),
            time_per_step=float(data.get("time_per_step", 1.0)),
            time_offset=float(data.get("time_offset", 0.0)),
            interp=str(data.get("interp", "linear")),
            extrapolate=str(data.get("extrapolate", "skip")),
            outlier_mode=str(data.get("outlier_mode", "flag")),
            name=str(data.get("name", "参数校准")),
        )

    def validate(self) -> List[str]:
        errors: List[str] = list(self.target.errors)
        if not catalog.known_model(self.domain, self.model):
            errors.append(f"未知模型: {self.domain}/{self.model}")
            return errors
        metrics = {m["key"] for m in catalog.CATALOG[self.domain]["metrics"]}
        if self.metric not in metrics:
            errors.append(f"指标 {self.metric} 不在该模型的统计输出中: {sorted(metrics)}")
        if self.loss_kind not in LOSS_KINDS:
            errors.append(f"未知损失函数 {self.loss_kind}，可选 {LOSS_KINDS}")
        if self.steps <= 0:
            errors.append("steps 必须为正整数")
        if self.n_replicates < 1 or self.confirm_replicates < 1:
            errors.append("重复次数必须 ≥ 1")
        if self.time_per_step <= 0:
            errors.append("time_per_step 必须为正数")
        known = {p["key"]: p for p in catalog.model_params(self.domain, self.model)}
        names = set()
        for ps in self.params:
            name = str(ps.get("name", ""))
            if name not in known:
                errors.append(f"待校准参数 {name} 不是 {self.domain}/{self.model} 的参数")
                continue
            if name in names:
                errors.append(f"参数 {name} 重复指定")
            names.add(name)
            try:
                lo, hi = float(ps["low"]), float(ps["high"])
            except (TypeError, ValueError):
                errors.append(f"参数 {name} 的上下界不是数字")
                continue
            if lo >= hi:
                errors.append(f"参数 {name} 需要严格的 low < high")
            spec = known[name]
            if spec.get("min") is not None and lo < spec["min"] - 1e-9:
                errors.append(f"参数 {name} 下界 {lo:g} 小于模型允许最小值 {spec['min']:g}")
            if spec.get("max") is not None and hi > spec["max"] + 1e-9:
                errors.append(f"参数 {name} 上界 {hi:g} 大于模型允许最大值 {spec['max']:g}")
        if not self.params:
            errors.append("至少需要指定一个待校准参数")
        return errors


@dataclass
class CalibrationResult:
    id: str = ""
    name: str = ""
    domain: str = ""
    model: str = ""
    metric: str = ""
    loss_kind: str = "nrmse"
    status: str = "finished"            # finished | error
    error: str = ""
    best_params: Dict[str, Any] = field(default_factory=dict)
    best_x: List[float] = field(default_factory=list)
    train_error: Dict[str, Any] = field(default_factory=dict)
    confirm_error: Dict[str, Any] = field(default_factory=dict)
    finalists: List[Dict[str, Any]] = field(default_factory=list)
    ranking: List[Dict[str, Any]] = field(default_factory=list)
    sensitivity: List[Dict[str, Any]] = field(default_factory=list)
    target_summary: Dict[str, Any] = field(default_factory=dict)
    target_points: List[Dict[str, Any]] = field(default_factory=list)
    overlay: Dict[str, Any] = field(default_factory=dict)
    residuals: List[Dict[str, Any]] = field(default_factory=list)
    evaluations: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    spec: Dict[str, Any] = field(default_factory=dict)
    seed: int = 0
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe({k: v for k, v in self.__dict__.items()})


def _json_safe(obj: Any) -> Any:
    """Replace non-finite floats with None so the record is strict JSON."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    return obj


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _train_seeds(seed: int, n: int) -> List[int]:
    return [seed + 101 + i for i in range(n)]


def _confirm_seeds(seed: int, n: int) -> List[int]:
    return [seed + 10_001 + i for i in range(n)]


def _round_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = {}
    for k, v in cfg.items():
        if isinstance(v, float):
            out[k] = round(v, 6)
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def run_calibration(spec: CalibrationSpec,
                    on_progress: Optional[Callable[[Dict[str, Any]], None]] = None,
                    should_abort: Optional[Callable[[], bool]] = None,
                    result_id: str = "") -> CalibrationResult:
    """Execute the full calibration pipeline; never raises for bad candidates."""
    from .. import util
    result = CalibrationResult(
        id=result_id or util.new_id("cal"),
        name=spec.name, domain=spec.domain, model=spec.model,
        metric=spec.metric, loss_kind=spec.loss_kind, seed=spec.seed,
        created_at=util.now_iso())
    result.target_summary = spec.target.summary()
    result.target_points = [p.to_dict() for p in spec.target.points]
    result.spec = _spec_to_dict(spec)

    errors = spec.validate()
    if errors:
        result.status = "error"
        result.error = "；".join(errors)
        return result

    def progress(phase: str, done: int, total: int, note: str = "") -> None:
        if on_progress:
            on_progress({"phase": phase, "done": done, "total": total,
                         "note": note})

    def aborted() -> bool:
        return bool(should_abort and should_abort())

    warnings = list(spec.target.warnings)

    # ---- parameter space ------------------------------------------------ #
    type_map = {p["key"]: p for p in catalog.model_params(spec.domain, spec.model)}
    specs: List[ParamSpec] = []
    for ps in spec.params:
        name = str(ps["name"])
        is_int = bool(ps.get("is_int")) or type_map[name].get("type") == "int"
        specs.append(ParamSpec(name, ps["low"], ps["high"], is_int=is_int))
    space = ParamSpace(specs)

    defaults = catalog.model_defaults(spec.domain, spec.model)
    prior_cfg = {**defaults, **spec.base_config}
    anchor_cfg = {s.name: (prior_cfg[s.name] if s.name in prior_cfg
                           else 0.5 * (s.low + s.high)) for s in specs}
    prior_x = space.clip(space.norm(anchor_cfg))

    # ---- candidate simulation + scoring --------------------------------- #
    def run_one(cfg: Dict[str, Any], run_seed: int) -> Tuple[float, List[float], List[float]]:
        full_cfg = {**defaults, **spec.base_config, **cfg}
        times, values = align_mod.simulate_curve(
            spec.domain, spec.model, full_cfg, spec.steps, spec.metric,
            run_seed, interventions=spec.interventions,
            engine_factory=spec.engine_factory)
        res, _pairs = align_mod.align_and_score(
            spec.target, times, values, kind=spec.loss_kind,
            time_per_step=spec.time_per_step, time_offset=spec.time_offset,
            interp=spec.interp, extrapolate=spec.extrapolate)
        return res.value, times, values

    def score_replicates(cfg: Dict[str, Any], seeds: Sequence[int]
                         ) -> Tuple[float, float, List[float], int]:
        vals: List[float] = []
        n_aligned = 0
        for sd in seeds:
            try:
                v, _, _ = run_one(cfg, sd)
            except Exception as exc:  # noqa: BLE001 — a bad candidate must not kill the search
                warnings.append(f"参数 {_round_cfg(cfg)} 在 seed={sd} 仿真失败：{exc}")
                v = float("inf")
            if math.isfinite(v):
                vals.append(v)
        if not vals:
            return float("inf"), float("inf"), [], 0
        mean = statistics.fmean(vals)
        sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
        return mean, sd, vals, len(vals)

    train_seeds = _train_seeds(spec.seed, spec.n_replicates)
    evals_log: List[Dict[str, Any]] = []
    current_stage = ["design"]

    def objective(cfg: Dict[str, Any]) -> float:
        mean, _sd, vals, k = score_replicates(cfg, train_seeds)
        evals_log.append({"params": _round_cfg(cfg), "mean_loss": mean
                          if math.isfinite(mean) else None,
                          "rep_losses": vals, "n": k,
                          "stage": current_stage[0]})
        return mean

    # ---- 1. global Latin-hypercube design ------------------------------- #
    d = space.dim
    n_design = spec.n_design or max(12, min(48, 8 * d))
    design = latin_hypercube(space, n_design, seed=spec.seed)
    # Always include the catalog-prior / current scene config as an anchor,
    # so the search can demonstrably beat (or confirm) the default.
    design = [prior_x] + [p for p in design if p != prior_x]

    progress("design", 0, len(design), "拉丁超立方全局采样")
    current_stage[0] = "design"
    scored: List[Tuple[List[float], float]] = []
    for i, x in enumerate(design):
        if aborted():
            result.status = "stopped"
            return result
        v = objective(space.denorm(x))
        scored.append((x, v))
        progress("design", i + 1, len(design))

    scored.sort(key=lambda r: r[1] if math.isfinite(r[1]) else float("inf"))

    # Pick well-separated starting points for local refinement.
    starts: List[List[float]] = []
    for x, v in scored:
        if not math.isfinite(v):
            break
        if all(sum((a - b) ** 2 for a, b in zip(x, sx)) ** 0.5 >= FINALIST_MIN_DIST
               for sx in starts):
            starts.append(x)
        if len(starts) >= spec.n_refine_starts:
            break
    if not starts:
        result.status = "error"
        result.error = "所有候选参数组合仿真失败或无法对齐到目标曲线，请检查步数/时间设置"
        result.warnings = warnings
        return result

    # ---- 2. multi-start local refinement -------------------------------- #
    cache: Dict[Tuple[float, ...], float] = {}
    per_start_budget = max(12, spec.n_refine // max(1, len(starts)))
    progress("refine", 0, spec.n_refine, "多起点局部寻优")
    current_stage[0] = "refine"
    refined: List[Tuple[List[float], float]] = []
    used_budget = 0
    for si, x0 in enumerate(starts):
        remaining = max(12, spec.n_refine - used_budget)
        budget = min(per_start_budget, remaining) if si < len(starts) - 1 else remaining
        xb, vb, trace = pattern_search(
            space, objective, x0, budget=budget, stage="refine", cache=cache)
        used_budget += len(trace)
        refined.append((xb, vb))
        progress("refine", min(used_budget, spec.n_refine), spec.n_refine)
        if used_budget >= spec.n_refine or aborted():
            break

    # Distinct refined finalists.
    refined.sort(key=lambda r: r[1])
    finalists_x: List[List[float]] = []
    for x, v in refined:
        if not math.isfinite(v):
            continue
        if all(sum((a - b) ** 2 for a, b in zip(x, fx)) ** 0.5 >= FINALIST_MIN_DIST
               for fx in finalists_x):
            finalists_x.append(x)
        if len(finalists_x) >= spec.n_refine_starts:
            break
    if not finalists_x:
        finalists_x = [refined[0][0]]

    # ---- 3. confirmation on disjoint seeds ------------------------------ #
    conf_seeds = _confirm_seeds(spec.seed, spec.confirm_replicates)
    progress("confirm", 0, len(finalists_x), "独立种子集复核")
    confirmed: List[Dict[str, Any]] = []
    for i, x in enumerate(finalists_x):
        if aborted():
            result.status = "stopped"
            return result
        cfg = space.denorm(x)
        mean, sd, vals, k = score_replicates(cfg, conf_seeds)
        se = sd / math.sqrt(k) if k > 1 else 0.0
        train_mean = cache.get(tuple(round(v, 9) for v in x))
        confirmed.append({"x": x, "params": _round_cfg(cfg),
                          "train_loss": train_mean,
                          "confirm_mean": mean, "confirm_std": sd,
                          "confirm_se": se, "rep_losses": vals, "n": k,
                          "prior_dist": math.sqrt(
                              sum((a - b) ** 2 for a, b in zip(x, prior_x)) / max(1, d))})
        progress("confirm", i + 1, len(finalists_x))

    # Winner: confirmation mean -> lower variance -> closer to catalog prior.
    confirmed.sort(key=lambda c: (
        c["confirm_mean"] if math.isfinite(c["confirm_mean"]) else float("inf"),
        c["confirm_std"], c["prior_dist"]))
    winner = confirmed[0]
    winner["winner"] = True

    # Statistical tie detection against the runner-up: gap inside MC noise.
    winner_gap = None
    rel_gap = None
    if len(confirmed) > 1:
        runner = confirmed[1]
        winner_gap = runner["confirm_mean"] - winner["confirm_mean"]
        noise = math.sqrt(winner["confirm_se"] ** 2 + runner["confirm_se"] ** 2)
        if winner["confirm_mean"]:
            rel_gap = winner_gap / abs(winner["confirm_mean"])
        if (math.isfinite(noise) and winner_gap <= 1.96 * noise) or \
           (rel_gap is not None and 0 <= rel_gap <= NEAR_TIE_REL):
            warnings.append(
                f"最优组合与次优组合的误差差距（{winner_gap:.4g}）在蒙特卡洛噪声"
                f"（±{1.96 * noise:.4g}）或 {NEAR_TIE_REL:.0%} 相对容差内，"
                f"统计上不可区分；已按「复核均值→方差→先验距离」确定性地选优，"
                f"建议增加 confirm_replicates 再复跑确认")

    # ---- 4. detailed error numbers for the winner ----------------------- #
    full_cfg = {**defaults, **spec.base_config, **winner["params"]}
    rep_curves: List[Tuple[List[float], List[float]]] = []
    rep_loss_rows: List[Dict[str, Any]] = []
    skipped_emitted = False
    for sd in conf_seeds:
        times, values = align_mod.simulate_curve(
            spec.domain, spec.model, full_cfg, spec.steps, spec.metric, sd,
            interventions=spec.interventions, engine_factory=spec.engine_factory)
        rep_curves.append((times, values))
        lres, pairs = align_mod.align_and_score(
            spec.target, times, values, kind=spec.loss_kind,
            time_per_step=spec.time_per_step, time_offset=spec.time_offset,
            interp=spec.interp, extrapolate=spec.extrapolate)
        rep_loss_rows.append({**lres.to_dict(), "seed": sd})
        if pairs.skipped and not skipped_emitted:
            skipped_emitted = True
            warnings.append(
                f"有 {len(pairs.skipped)} 个目标时间点超出仿真窗口（t="
                f"{pairs.skipped[0]['t']:g} 起），未计入误差；可增大 steps 或检查 time_per_step")

    def _agg_from_rows(rows: List[Dict[str, Any]], metric_key: str) -> Dict[str, float]:
        xs = [r[metric_key] for r in rows
              if isinstance(r.get(metric_key), (int, float))
              and math.isfinite(r[metric_key])]
        if not xs:
            return {"mean": float("nan"), "std": float("nan"), "n_points": 0}
        return {"mean": statistics.fmean(xs),
                "std": statistics.stdev(xs) if len(xs) > 1 else 0.0,
                "n_points": len(xs)}

    def _agg(metric_key: str) -> Dict[str, float]:
        return _agg_from_rows(rep_loss_rows, metric_key)

    confirm_detail = {k: _agg(k) for k in ("loss", "rmse", "mae", "mape", "r2")}
    train_detail = None
    train_rows = []
    for sd in train_seeds:
        times, values = align_mod.simulate_curve(
            spec.domain, spec.model, full_cfg, spec.steps, spec.metric, sd,
            interventions=spec.interventions, engine_factory=spec.engine_factory)
        lres, _ = align_mod.align_and_score(
            spec.target, times, values, kind=spec.loss_kind,
            time_per_step=spec.time_per_step, time_offset=spec.time_offset,
            interp=spec.interp, extrapolate=spec.extrapolate)
        train_rows.append(lres.to_dict())
    train_detail = {k: _agg_from_rows(train_rows, k)
                    for k in ("loss", "rmse", "mae", "mape", "r2")}

    # ---- 5. overlay curve: mean ± std over confirmation replicates ------ #
    n_steps = spec.steps
    grid = list(range(n_steps + 1))
    mat = [vs for _t, vs in rep_curves]
    mean_curve: List[float] = []
    std_curve: List[float] = []
    for k in range(n_steps + 1):
        col = [row[k] for row in mat if k < len(row)]
        mean_curve.append(statistics.fmean(col))
        std_curve.append(statistics.stdev(col) if len(col) > 1 else 0.0)
    overlay_times = [spec.time_offset + s * spec.time_per_step for s in grid]

    # Simulated values at the target times (residual table).
    residuals = []
    for p in spec.target.points:
        v = align_mod.interp1d(overlay_times, mean_curve, p.t,
                               mode=spec.interp,
                               clip=spec.extrapolate == "clip")
        residuals.append({"t": p.t, "target": p.y, "status": p.status,
                          "sim": v,
                          "residual": (v - p.y) if v is not None else None})

    # ---- 6. diagnostics: boundaries + sensitivity ----------------------- #
    hits = space.at_boundary(winner["x"])
    for name, side in hits.items():
        spec_ps = next(s for s in specs if s.name == name)
        bound = spec_ps.low if side == "min" else spec_ps.high
        warnings.append(
            f"参数 {name} 被推到{'下' if side == 'min' else '上'}界 {bound:g}；"
            f"最优可能在给定区间之外，请放宽该参数的边界后重新校准")

    base_loss = winner["confirm_mean"]
    sensitivity: List[Dict[str, Any]] = []
    for j, spec_ps in enumerate(specs):
        deltas = {}
        for direction in (-1.0, 1.0):
            xp = space.clip([winner["x"][k] +
                             (SENSITIVITY_STEP * direction if k == j else 0.0)
                             for k in range(d)])
            cfg_p = space.denorm(xp)
            m, _sd, _vals, _k = score_replicates(cfg_p, train_seeds)
            deltas[direction] = m - (winner.get("train_loss") or base_loss)
        sens = max(deltas.values())
        sensitivity.append({"param": spec_ps.name, "sensitivity": sens,
                            "delta_down": deltas[-1.0], "delta_up": deltas[1.0]})
    sensitivity.sort(key=lambda r: r["sensitivity"])
    if sensitivity:
        s_max = max(s["sensitivity"] for s in sensitivity) or 1.0
        for s in sensitivity:
            s["relative"] = s["sensitivity"] / s_max
        flat = [s["param"] for s in sensitivity if s["relative"] < 0.05]
        if flat:
            warnings.append(
                f"参数 {', '.join(flat)} 在最优点附近对拟合误差几乎无影响"
                f"（灵敏度 < 最大者的 5%）；仅凭这条目标曲线无法可靠识别它们，"
                f"其取值更多由边界/先验决定，建议固定这些参数或补充观测指标")

    # ---- assemble result ------------------------------------------------- #
    result.best_params = winner["params"]
    result.best_x = winner["x"]
    result.train_error = train_detail
    result.confirm_error = confirm_detail
    result.finalists = confirmed
    result.ranking = confirmed
    result.sensitivity = sensitivity
    result.overlay = {
        "time": overlay_times,
        "sim_mean": [round(v, 6) for v in mean_curve],
        "sim_std": [round(v, 6) for v in std_curve],
        "seeds": conf_seeds,
    }
    result.residuals = residuals
    result.evaluations = evals_log
    result.warnings = warnings
    progress("done", 1, 1, "校准完成")
    return result


def _spec_to_dict(spec: CalibrationSpec) -> Dict[str, Any]:
    return {
        "domain": spec.domain, "model": spec.model, "metric": spec.metric,
        "steps": spec.steps, "seed": spec.seed,
        "n_replicates": spec.n_replicates,
        "confirm_replicates": spec.confirm_replicates,
        "loss_kind": spec.loss_kind,
        "time_per_step": spec.time_per_step, "time_offset": spec.time_offset,
        "interp": spec.interp, "extrapolate": spec.extrapolate,
        "outlier_mode": spec.outlier_mode,
        "params": [dict(p) for p in spec.params],
        "base_config": dict(spec.base_config),
        "interventions": [dict(i) for i in spec.interventions],
    }
