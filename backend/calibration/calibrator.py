"""参数校准编排：把目标清洗、目标函数、优化器与诊断组装成一次完整校准。

调用流程（:func:`run_calibration`）：

1. 解析场景与待校准参数，校验参数键存在、上下界合法，并与场景当前值合并。
2. 清洗目标曲线（缺失/重复/异常），确定稳健归一化量纲。
3. 确定性两阶段优化（拉丁超立方 → 有界单纯形），每次打分跑 ``replicates``
   条独立随机轨迹取平均。
4. 边界诊断（贴界参数做内推试验）+ 拟合质量指标（RMSE/NRMSE/R²）。
5. 用最优参数再跑一次"报告轨迹"，输出叠加对比所需的仿真曲线与对齐结果。

输出的 JSON 自描述且可复现：包含输入快照、算法设置、主种子、每一次评估的
参数与误差、平台上的近似最优组、诊断与告警。拿着这份 JSON + 相同代码就能
逐位重跑（种子驱动，不使用任何全局随机源）。
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from .. import catalog
from . import objective as obj_mod
from .optimizer import (ParamSpec, Trial, diagnose_bounds, optimize)
from .target import TargetSeries, clean_target


# --------------------------------------------------------------------------- #
# 规格构建
# --------------------------------------------------------------------------- #
def build_specs(domain: str, model: str,
                param_defs: List[Dict[str, Any]]) -> List[ParamSpec]:
    known = {p["key"]: p for p in catalog.model_params(domain, model)}
    specs: List[ParamSpec] = []
    for d in param_defs:
        key = str(d.get("key", ""))
        if key not in known:
            raise ValueError(f"未知或不可校准的参数: {key}")
        cat = known[key]
        if cat["type"] not in ("int", "float"):
            raise ValueError(f"参数 {key} 不是数值类型，无法校准")
        lo = d.get("min")
        hi = d.get("max")
        lo = cat["min"] if lo is None else float(lo)
        hi = cat["max"] if hi is None else float(hi)
        if hi < lo:
            raise ValueError(f"参数 {key} 上界 {hi} 小于下界 {lo}")
        specs.append(ParamSpec(key=key, label=cat["label"], lo=float(lo),
                               hi=float(hi), is_int=(cat["type"] == "int")))
    if not specs:
        raise ValueError("请至少选择一个待校准参数")
    # 去重保序
    seen, uniq = set(), []
    for s in specs:
        if s.key not in seen:
            seen.add(s.key)
            uniq.append(s)
    return uniq


# --------------------------------------------------------------------------- #
# 主入口
# --------------------------------------------------------------------------- #
def run_calibration(scene: Dict[str, Any], request: Dict[str, Any],
                    *, progress: Optional[Any] = None) -> Dict[str, Any]:
    """执行一次校准并返回可持久化的结果 dict。

    ``scene`` 是创建校准时的场景快照（含合并后的 config），``request`` 至少
    包含 ``metric``、``target``、``params``；算法设置有合理默认值。
    """
    domain, model = scene["domain"], scene["model"]
    base_config: Dict[str, Any] = dict(scene.get("config", {}))
    metric = str(request["metric"])
    metrics = {m["key"] for m in catalog.CATALOG[domain]["metrics"]}
    if metric not in metrics:
        raise ValueError(f"该模型没有统计指标: {metric}")

    specs = build_specs(domain, model, request.get("params", []))

    steps = int(request.get("steps", 120))
    if steps < 1:
        raise ValueError("运行步数必须 >= 1")
    replicates = max(1, int(request.get("replicates", 1)))
    master_seed = int(request.get("seed", 12345))
    loss_type = str(request.get("loss", "nrmse"))
    if loss_type not in ("nrmse", "mse", "mae", "huber"):
        raise ValueError(f"未知损失类型: {loss_type}")
    huber_delta = float(request.get("huber_delta", 1.0))
    time_offset = float(request.get("time_offset", 0.0))
    time_scale = float(request.get("time_scale", 1.0))
    if time_scale <= 0:
        raise ValueError("time_scale 必须为正数")
    n_global = int(request.get("n_global", 12))
    n_local_starts = int(request.get("n_local_starts", 3))
    max_evals = int(request.get("max_evals", 120))

    target = clean_target(
        request["target"],
        detect_outliers=bool(request.get("detect_outliers", True)),
        drop_outliers=bool(request.get("drop_outliers", False)),
        outlier_window=int(request.get("outlier_window", 5)),
        outlier_sigma=float(request.get("outlier_sigma", 4.0)))
    value_scale = target.value_scale()

    started = time.time()

    # 每次评估的闭包：优化器给出参数 → 打分
    def eval_fn(params: Dict[str, float], point_index: int,
                phase: str) -> Trial:
        res = obj_mod.evaluate(
            domain, model, base_config, params,
            steps=steps, metric=metric, target=target,
            master_seed=master_seed,
            replicates=replicates, value_scale=value_scale,
            loss_type=loss_type, huber_delta=huber_delta,
            time_offset=time_offset, time_scale=time_scale)
        return Trial(params=dict(params), loss=res.loss, rmse=res.rmse,
                     nrmse=res.nrmse, r2=res.r2, phase=phase,
                     point_index=point_index, failed=res.failed,
                     max_abs_residual=res.max_abs_residual)

    current_params = {s.key: float(base_config.get(s.key, s.lo))
                      for s in specs}

    def on_progress(info: Dict[str, Any]) -> None:
        if progress is not None:
            progress(info)

    result = optimize(specs, eval_fn, seed=master_seed,
                      current_params=current_params,
                      n_global=n_global, n_local_starts=n_local_starts,
                      max_evals=max_evals, progress=on_progress)

    # ---- 边界诊断（内推试验） ---------------------------------------------
    bound_trials: List[Trial] = []

    def diag_eval(params: Dict[str, float], point_index: int,
                  phase: str) -> Trial:
        t = eval_fn(params, 900000 + len(bound_trials), "bound_check")
        bound_trials.append(t)
        return t

    bounds = diagnose_bounds(specs, result.best.params,
                             result.best.loss, diag_eval)

    # ---- 最优参数的报告轨迹（用于叠加图；重复轨迹均值±std） ----------------
    final = obj_mod.evaluate(
        domain, model, base_config, result.best.params,
        steps=steps, metric=metric, target=target,
        master_seed=master_seed,
        replicates=replicates, value_scale=value_scale,
        loss_type=loss_type, huber_delta=huber_delta,
        time_offset=time_offset, time_scale=time_scale)

    # ---- 告警汇总 ---------------------------------------------------------
    warnings: List[str] = build_warnings(
        target=target, specs=specs, result=result, bounds=bounds,
        final=final, steps=steps, time_offset=time_offset,
        time_scale=time_scale)

    elapsed = time.time() - started

    out: Dict[str, Any] = {
        "status": "finished",
        "metric": metric,
        "metric_label": catalog.metric_labels(domain).get(metric, metric),
        "params_spec": [{"key": s.key, "label": s.label, "min": s.lo,
                         "max": s.hi, "is_int": s.is_int} for s in specs],
        "best_params": result.best.params,
        "best_loss": result.best.loss,
        "fit": {"rmse": final.rmse, "nrmse": final.nrmse, "r2": final.r2,
                "max_abs_residual": final.max_abs_residual,
                "n_aligned": final.n_aligned,
                "n_out_of_range": final.n_out_of_range,
                "scale": value_scale},
        "plateau": result.to_dict()["plateau"],
        "plateau_size": len(result.plateau),
        "bounds": [b.to_dict() for b in bounds],
        "history": [t for t in result.to_dict()["history"]
                    if t["phase"] != "bound_check"],
        "bound_checks": [
            {"params": t.params, "loss": t.loss} for t in bound_trials],
        "n_evals": result.n_evals,
        "stopped_reason": result.stopped_reason,
        "warnings": warnings,
        "target": target.to_dict(),
        "target_summary": target.summary(),
        "aligned": final.aligned,
        "sim_curve": final.sim_curve,
        "settings": {"steps": steps, "replicates": replicates,
                     "seed": master_seed, "loss": loss_type,
                     "huber_delta": huber_delta,
                     "time_offset": time_offset, "time_scale": time_scale,
                     "n_global": n_global, "n_local_starts": n_local_starts,
                     "max_evals": max_evals,
                     "detect_outliers": request.get("detect_outliers", True),
                     "drop_outliers": request.get("drop_outliers", False)},
        "elapsed_seconds": round(elapsed, 2),
    }
    return out


# --------------------------------------------------------------------------- #
# 告警
# --------------------------------------------------------------------------- #
def build_warnings(*, target: TargetSeries, specs: List[ParamSpec],
                   result, bounds, final, steps: int,
                   time_offset: float, time_scale: float) -> List[str]:
    w: List[str] = []
    summ = target.summary()
    if summ["n_missing"]:
        w.append(f"目标数据有 {summ['n_missing']} 个缺测点，已从拟合中排除（仍在对比图中标出）。")
    if summ["n_duplicate_rows"]:
        w.append(f"合并了 {summ['n_duplicate_rows']} 条重复时间点记录（按权重平均）。")
    if summ["n_outliers"]:
        w.append(f"检测到 {summ['n_outliers']} 个异常点（Hampel/MAD），"
                 "已置零权重不参与拟合；请在对比图中核对它们是否为真实事件。")
    if final.n_out_of_range:
        t0 = time_offset
        t1 = time_offset + time_scale * steps
        w.append(f"{final.n_out_of_range} 个目标点的时间超出仿真范围"
                 f"（目标时间 {t0:g}–{t1:g}，按 time_offset/time_scale 映射），"
                 "未参与拟合；可调大运行步数或修正时间映射。")
    if final.n_aligned < max(3, int(0.3 * summ["n_points"])):
        w.append(f"仅 {final.n_aligned} 个目标点参与拟合，误差可能不可靠，建议检查时间轴对齐设置。")
    if final.nrmse > 0.5:
        w.append(f"归一化 RMSE = {final.nrmse:.3f} 较大，模型结构或参数界可能不足以复现目标曲线。")
    if final.r2 is not None and final.r2 < 0:
        w.append("R² < 0：拟合效果差于直接用目标均值预测，请检查指标选择、时间对齐或参数界。")
    if result.best.failed:
        w.append("最优点评估失败，结果不可信，请检查参数组合。")
    active_bounds = [b for b in bounds if b.active]
    for b in active_bounds:
        side = "下界" if b.at == "lower" else "上界"
        w.append(f"参数「{b.label}」被拉到{side} {b.value:g} 且内推后误差明显变差，"
                 "说明最优可能在当前参数界之外，建议放宽该参数的上下界后重新校准。")
    inactive_bounds = [b for b in bounds if not b.active]
    for b in inactive_bounds:
        side = "下界" if b.at == "lower" else "上界"
        w.append(f"参数「{b.label}」的最优点贴在{side}，但内推后误差几乎不变"
                 "（平台型边界），通常无需处理。")
    if len(result.plateau) >= 3:
        keys = "、".join(s.label for s in specs)
        w.append(f"存在 {len(result.plateau)} 组误差在 0.1% 以内的参数组合"
                 f"（涉及 {keys}），数据不足以唯一确定这些参数；"
                 "结果已按确定性规则选择最靠近可行域中心的一组，建议增加观测点或缩小参数界。")
    if result.stopped_reason.startswith("budget"):
        w.append("校准在达到评估预算上限时停止，可能未完全收敛；可增大最大评估次数后重跑。")
    return w
