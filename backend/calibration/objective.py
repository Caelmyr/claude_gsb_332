"""仿真执行、时间轴对齐与拟合误差（目标函数）。

校准的一次"打分"= 给定一组参数 → 跑仿真 → 抽取统计指标曲线 → 对齐到目标
时间轴 → 计算逐点误差。每一步都在这里显式处理，结果因此可复现、可解释：

时间对齐
    仿真步数与目标观测的时间点几乎从不会正好一致（仿真按步长 dt、观测按
    天/周上报）。对齐规则固定为：**以目标的有效时间点为准网格，对仿真曲线
    做分段线性插值（不外推）**。支持 ``time_offset`` / ``time_scale`` 做已知
    的线性时间映射（``t_sim = (t_target - offset) / scale``，例如周报起点错位
    或观测以"天"、仿真以"半天"为步）。落在仿真区间外的目标点被标记为越界，
    不参与误差（并在诊断里报告越界数量——越界过多说明步数设置不合理）。

随机性处理
    CA/ABM 都是随机仿真。对每组参数跑 ``replicates`` 条独立轨迹（种子由主
    种子 + 参数点索引确定性派生），对齐后逐点取均值再算误差，并同时返回
    轨迹间标准差供置信带显示。``replicates=1`` 时退化为确定性单轨迹。
"""

from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..engine import make_engine

# 仿真失败（参数组合导致引擎异常）时施加的大罚分，保证优化器会立刻避开，
# 同时不用特判 None 在排序/比较里到处传播。
FAIL_LOSS = 1e12


# --------------------------------------------------------------------------- #
# 时间对齐
# --------------------------------------------------------------------------- #
@dataclass
class AlignedPair:
    t: float                 # 目标时间
    target: float            # 目标值
    sim: Optional[float]     # 对齐后的仿真值（None=越界）
    weight: float = 1.0
    sim_std: Optional[float] = None   # 重复轨迹间标准差
    outlier: bool = False


def linear_interpolate(xs: Sequence[float], ys: Sequence[float],
                       x: float) -> Optional[float]:
    """分段线性插值；x 超出 xs 范围返回 None（显式拒绝外推）。"""
    if not xs or x < xs[0] or x > xs[-1]:
        return None
    i = bisect.bisect_right(xs, x) - 1
    if i >= len(xs) - 1:
        return ys[-1]
    x0, x1 = xs[i], xs[i + 1]
    if x1 == x0:
        return ys[i]
    f = (x - x0) / (x1 - x0)
    return ys[i] + f * (ys[i + 1] - ys[i])


def align_series(sim_t: Sequence[float], sim_v: Sequence[float],
                 sim_v_std: Optional[Sequence[float]],
                 target_t: Sequence[float], target_v: Sequence[float],
                 weights: Sequence[float],
                 outlier_flags: Sequence[bool],
                 time_offset: float = 0.0,
                 time_scale: float = 1.0) -> List[AlignedPair]:
    """把仿真曲线重采样到目标时间点（线性插值、不外推）。"""
    pairs: List[AlignedPair] = []
    for i, t in enumerate(target_t):
        t_sim = (t - time_offset) / time_scale
        sv = linear_interpolate(sim_t, sim_v, t_sim)
        ss = (linear_interpolate(sim_t, sim_v_std, t_sim)
              if sim_v_std is not None else None)
        pairs.append(AlignedPair(t=float(t), target=float(target_v[i]),
                                 sim=sv, sim_std=ss,
                                 weight=float(weights[i]),
                                 outlier=bool(outlier_flags[i])))
    return pairs


# --------------------------------------------------------------------------- #
# 单次/多次仿真
# --------------------------------------------------------------------------- #
def simulate_series(domain: str, model: str, base_config: Dict[str, Any],
                    overrides: Dict[str, Any], steps: int, metric: str,
                    seed: int) -> Tuple[List[float], List[float]]:
    """跑一条仿真轨迹，返回 (时间步列表, 指标值列表)。直接构造引擎、不落盘，
    避免校准时数百次打分产生垃圾运行目录。"""
    cfg = {**base_config, **overrides}
    eng = make_engine(domain, model, config=cfg, seed=int(seed))
    ts: List[float] = [0]
    vs: List[float] = [float(eng.stats().get(metric, 0.0) or 0.0)]
    for k in range(int(steps)):
        eng.step()
        st = eng.stats()
        ts.append(float(k + 1))
        vs.append(float(st.get(metric, 0.0) or 0.0))
    return ts, vs


def replicate_seed(master_seed: int, rep: int) -> int:
    """第 rep 条重复轨迹的确定性种子。

    所有候选参数点共享同一组重复种子（common random numbers）——这是仿真
    优化的标准降方差技巧：参数组 A 与 B 的差异不会被"这次运气好/差"污染，
    平局判定与局部搜索的梯度方向都更稳。种子不依赖全局随机数，跨机器一致。
    """
    h = (int(master_seed) * 2654435761 + rep * 40503 + 0x9E3779B9) & 0xFFFFFFFF
    return h or 1


# --------------------------------------------------------------------------- #
# 目标函数
# --------------------------------------------------------------------------- #
@dataclass
class EvalResult:
    loss: float                          # 主目标值（归一化、带罚分）
    rmse: float                          # 原始量纲 RMSE（仅有效点）
    nrmse: float                         # 归一化 RMSE（主报告指标）
    r2: Optional[float]                  # 决定系数
    max_abs_residual: float
    n_aligned: int                       # 参与误差的点数
    n_out_of_range: int                  # 越界未对齐的目标点数
    aligned: List[Dict[str, Any]] = field(default_factory=list)
    sim_curve: List[Dict[str, Any]] = field(default_factory=list)  # 均值±std
    failed: bool = False
    failure_reason: str = ""


def _aggregate(pairs: List[AlignedPair], scale: float, loss_type: str,
               huber_delta: float) -> EvalResult:
    valid = [p for p in pairs if p.sim is not None and p.weight > 0
             and not p.outlier]
    n_oor = sum(1 for p in pairs if p.sim is None and not p.outlier
                and p.weight > 0)
    if not valid:
        return EvalResult(FAIL_LOSS, FAIL_LOSS, FAIL_LOSS, None,
                          FAIL_LOSS, 0, n_oor, failed=True,
                          failure_reason="没有任何目标点落在仿真时间范围内")

    wsum = sum(p.weight for p in valid)
    residuals = [p.sim - p.target for p in valid]

    # 1) 主损失：在稳健量纲 scale 上计算，权重归一
    if loss_type == "mse":
        loss = math.sqrt(sum(p.weight * r * r for p, r in zip(valid, residuals))
                         / wsum) / scale
    elif loss_type == "mae":
        loss = sum(p.weight * abs(r) for p, r in zip(valid, residuals)) / wsum / scale
    elif loss_type == "huber":
        d = huber_delta
        terms = []
        for r in residuals:
            a = abs(r) / scale
            terms.append(0.5 * a * a if a <= d else d * a - 0.5 * d * d)
        loss = math.sqrt(2.0 * sum(p.weight * t for p, t in zip(valid, terms))
                         / wsum)
    else:  # nrmse（默认）
        loss = math.sqrt(sum(p.weight * r * r for p, r in zip(valid, residuals))
                         / wsum) / scale

    rmse = math.sqrt(sum(r * r for r in residuals) / len(valid))
    nrmse = rmse / scale
    mean_t = sum(p.target for p in valid) / len(valid)
    ss_tot = sum((p.target - mean_t) ** 2 for p in valid)
    r2 = (1.0 - sum(r * r for r in residuals) / ss_tot) if ss_tot > 1e-12 else None
    max_abs = max(abs(r) for r in residuals)

    aligned = [{"t": p.t, "target": p.target, "sim": p.sim,
                "sim_std": p.sim_std, "weight": p.weight,
                "residual": (p.sim - p.target) if p.sim is not None else None,
                "out_of_range": p.sim is None,
                "outlier": p.outlier} for p in pairs]
    return EvalResult(loss=loss, rmse=rmse, nrmse=nrmse, r2=r2,
                      max_abs_residual=max_abs, n_aligned=len(valid),
                      n_out_of_range=n_oor, aligned=aligned)


def evaluate(domain: str, model: str, base_config: Dict[str, Any],
             overrides: Dict[str, Any], *, steps: int, metric: str,
             target, master_seed: int,
             replicates: int = 1, value_scale: float = 1.0,
             loss_type: str = "nrmse", huber_delta: float = 1.0,
             time_offset: float = 0.0, time_scale: float = 1.0,
             sim_curve_times: Optional[Sequence[float]] = None) -> EvalResult:
    """完整打分：仿真 → 重复平均 → 对齐 → 误差。任何引擎异常转成失败罚分。"""
    try:
        curves: List[Tuple[List[float], List[float]]] = []
        for rep in range(max(1, int(replicates))):
            seed = replicate_seed(master_seed, rep)
            curves.append(simulate_series(domain, model, base_config,
                                          overrides, steps, metric, seed))
    except Exception as exc:  # noqa: BLE001 — 坏参数组合不应中断校准
        return EvalResult(FAIL_LOSS, FAIL_LOSS, FAIL_LOSS, None, FAIL_LOSS,
                          0, 0, failed=True, failure_reason=str(exc))

    # 多条重复轨迹时间网格相同（同样的 steps），逐点统计均值/标准差
    sim_t = curves[0][0]
    n = len(sim_t)
    mean_v, std_v = [], []
    for k in range(n):
        vals = [c[1][k] for c in curves]
        mu = sum(vals) / len(vals)
        var = sum((v - mu) ** 2 for v in vals) / len(vals)
        mean_v.append(mu)
        std_v.append(math.sqrt(var))

    usable = target.usable()
    pairs = align_series(
        sim_t, mean_v, std_v if len(curves) > 1 else None,
        [p.t for p in target.points], [p.v for p in target.points],
        [p.w for p in target.points],
        [p.outlier for p in target.points],
        time_offset, time_scale)

    res = _aggregate(pairs, value_scale, loss_type, huber_delta)

    # 叠加对比用的仿真曲线：默认按仿真自身网格输出（前端可看到完整曲线），
    # 同时 aligned 里保留目标网格上的逐点比对。
    if sim_curve_times is None:
        out_t = sim_t
    else:
        out_t = [t for t in sim_curve_times
                 if sim_t[0] <= t <= sim_t[-1]]
    res.sim_curve = [
        {"t": t,
         "sim": linear_interpolate(sim_t, mean_v, t),
         "sim_std": linear_interpolate(sim_t, std_v, t) if len(curves) > 1 else 0.0}
        for t in out_t]
    return res
