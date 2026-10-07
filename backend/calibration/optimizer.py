"""确定性混合优化器：全局拉丁超立方 + 有界单纯形局部搜索。

设计目标是让"最优参数组合"**可信、可复现，而不是撞运气**：

1. 不依赖 scipy（requirements 里只有 flask），全部用标准库实现。
2. 完全确定性：所有随机采样来自 ``random.Random(master_seed)``，给定同样的
   场景、目标、参数界、种子与预算，任何机器上结果逐位一致。
3. 全局 + 局部两阶段：先用覆盖整个可行域的拉丁超立方（LHS）粗搜，避免一上
   来就陷进局部最优；再对最好的几个起点做有界 Nelder–Mead 单纯形细化，
   混合整型/浮点参数（整型参数在评估时吸附到最近整数）。
4. 多个参数组"看起来差不多"时，用**固定的、与浮点误差无关的字典序规则**
   裁决真正的最优（见 :func:`better`），并把处于同一误差平台（plateau）的
   所有参数组一并返回，提示用户"这些参数在数据上不可区分"。
5. 边界诊断：最优落在界上时做一步内推敏感性试验，判断界是"真边界"还是
   "参数还想往外跑、界设小了"。
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

# 浮点比较容差：相对差小于 1e-3（0.1%）视为"同一平台"
PLATEAU_RTOL = 1e-3
# 评估结果量化精度：相同量化键的评估直接命中缓存（确定性去重）
_QUANT = 1e-9


@dataclass
class ParamSpec:
    key: str
    label: str
    lo: float
    hi: float
    is_int: bool = False

    def __post_init__(self) -> None:
        if self.hi < self.lo:
            raise ValueError(f"参数 {self.key} 的上界小于下界")
        if self.is_int:
            self.lo = math.ceil(self.lo)
            self.hi = math.floor(self.hi)
            if self.hi < self.lo:
                raise ValueError(f"整型参数 {self.key} 的上下界之间没有整数")

    def clamp(self, x: float) -> float:
        return min(self.hi, max(self.lo, x))

    def to_unit(self, v: float) -> float:
        if self.hi == self.lo:
            return 0.0
        return (v - self.lo) / (self.hi - self.lo)

    def from_unit(self, u: float) -> float:
        v = self.clamp(self.lo + min(1.0, max(0.0, u)) * (self.hi - self.lo))
        return float(round(v)) if self.is_int else float(v)


@dataclass
class Trial:
    params: Dict[str, float]
    loss: float
    rmse: float
    nrmse: float
    r2: Optional[float]
    phase: str                       # seed | lhs | nm
    point_index: int
    failed: bool = False
    max_abs_residual: float = 0.0


@dataclass
class BoundStatus:
    key: str
    label: str
    at: str                          # lower | upper | ""
    value: float
    bound: float
    inward_loss: Optional[float] = None
    active: bool = False             # 内推后误差明显变差 => 界是主动约束

    def to_dict(self) -> Dict[str, Any]:
        return {"key": self.key, "label": self.label, "at": self.at,
                "value": self.value, "bound": self.bound,
                "inward_loss": self.inward_loss, "active": self.active}


@dataclass
class OptimizationResult:
    best: Trial
    trials: List[Trial]
    plateau: List[Trial]
    bounds: List[BoundStatus]
    n_evals: int
    seed: int
    stopped_reason: str

    def to_dict(self) -> Dict[str, Any]:
        def tdict(t: Trial) -> Dict[str, Any]:
            return {"params": t.params, "loss": t.loss, "rmse": t.rmse,
                    "nrmse": t.nrmse, "r2": t.r2, "phase": t.phase,
                    "point_index": t.point_index, "failed": t.failed,
                    "max_abs_residual": t.max_abs_residual}
        return {
            "best": tdict(self.best),
            "best_params": self.best.params,
            "best_loss": self.best.loss,
            "n_evals": self.n_evals,
            "seed": self.seed,
            "stopped_reason": self.stopped_reason,
            "plateau": [tdict(t) for t in self.plateau],
            "plateau_size": len(self.plateau),
            "bounds": [b.to_dict() for b in self.bounds],
            "history": [tdict(t) for t in self.trials],
        }


# --------------------------------------------------------------------------- #
# 确定性平局裁决
# --------------------------------------------------------------------------- #
def better(cand: float, cur: float) -> bool:
    """cand 是否严格优于 cur：先比损失（带平台容差），再逐参数字典序。

    同一平台内偏好"更靠近可行域中心"的解，等价于对不敏感参数取最保守、
    最少外推的取值，而不是让随机性决定返回哪一组。
    """
    tol = PLATEAU_RTOL * max(1.0, abs(cur))
    if cand < cur - tol:
        return True
    if cand > cur + tol:
        return False
    return False  # 平台内由调用方用 params 做二次裁决


def _tie_key(u: List[float]) -> Tuple[float, ...]:
    """离中心的归一化距离（越小越保守），再拼原始坐标做最终字典序。"""
    d2 = sum((x - 0.5) ** 2 for x in u)
    return (round(d2, 8),) + tuple(round(x, 8) for x in u)


# --------------------------------------------------------------------------- #
# 采样
# --------------------------------------------------------------------------- #
def latin_hypercube(n: int, dim: int, rng: random.Random) -> List[List[float]]:
    """确定性拉丁超立方：每维 n 个等概率分层各取一次，再独立置换。"""
    perms = []
    for _ in range(dim):
        p = list(range(n))
        rng.shuffle(p)
        perms.append(p)
    pts = []
    for i in range(n):
        pts.append([(perms[d][i] + rng.random()) / n for d in range(dim)])
    return pts


# --------------------------------------------------------------------------- #
# 有界 Nelder–Mead（在单位立方 [0,1]^d 内操作，出界即裁剪）
# --------------------------------------------------------------------------- #
def nelder_mead(fun: Callable[[List[float]], Tuple[float, int]],
                x0: List[float], *, max_evals: int,
                initial_step: float = 0.15,
                x_tol: float = 1e-3, f_tol: float = 1e-5
                ) -> Tuple[List[float], float, int, str]:
    d = len(x0)
    used = 0

    def clip(u: List[float]) -> List[float]:
        return [min(1.0, max(0.0, z)) for z in u]

    def f(u: List[float]) -> float:
        nonlocal used
        u = clip(u)
        val, idx = fun(u)
        used += 1
        return val

    # 初始单纯形：x0 + 沿每维一正步长（裁剪后退化也没关系，NM 能处理）
    simplex = [(clip(list(x0)), f(x0))]
    for i in range(d):
        v = clip(list(x0))
        v[i] = min(1.0, v[i] + initial_step)
        simplex.append((v, f(v)))

    alpha, gamma, rho, sigma = 1.0, 2.0, 0.5, 0.5
    reason = "max_evals"
    while used < max_evals:
        simplex.sort(key=lambda z: (z[1], _tie_key(z[0])))
        best_x, best_f = simplex[0]
        worst_x, worst_f = simplex[-1]
        # 收敛判定
        spread_x = max(max(abs(a - b) for a, b in zip(best_x, s[0]))
                       for s in simplex)
        spread_f = max(s[1] for s in simplex) - best_f
        if spread_x < x_tol and spread_f < f_tol * max(1.0, abs(best_f)):
            reason = "converged"
            break

        centroid = [sum(s[0][j] for s in simplex[:-1]) / d for j in range(d)]

        xr = clip([centroid[j] + alpha * (centroid[j] - worst_x[j])
                   for j in range(d)])
        fr = f(xr)
        if used >= max_evals:
            break
        if simplex[0][1] <= fr < simplex[-2][1]:
            simplex[-1] = (xr, fr)
            continue
        if fr < simplex[0][1]:
            xe = clip([centroid[j] + gamma * (xr[j] - centroid[j])
                       for j in range(d)])
            fe = f(xe)
            simplex[-1] = (xe, fe) if fe < fr else (xr, fr)
            if used >= max_evals:
                break
            continue
        xc = clip([centroid[j] + rho * (worst_x[j] - centroid[j])
                   for j in range(d)])
        fc = f(xc)
        if fc < worst_f:
            simplex[-1] = (xc, fc)
            continue
        # 收缩
        bx = simplex[0][0]
        new_simplex = [simplex[0]]
        for s in simplex[1:]:
            xs = clip([bx[j] + sigma * (s[0][j] - bx[j]) for j in range(d)])
            new_simplex.append((xs, f(xs)))
            if used >= max_evals:
                break
        simplex = new_simplex

    simplex.sort(key=lambda z: (z[1], _tie_key(z[0])))
    return simplex[0][0], simplex[0][1], used, reason


# --------------------------------------------------------------------------- #
# 有界坐标模式搜索（Hooke–Jeeves 风格）
# --------------------------------------------------------------------------- #
def coordinate_search(fun: Callable[[List[float]], Tuple[float, int]],
                      x0: List[float], *, max_evals: int,
                      steps: Tuple[float, ...] = (0.2, 0.1, 0.05, 0.02, 0.01),
                      int_unit_steps: Tuple[float, ...] = (),
                      min_step: float = 2e-4
                      ) -> Tuple[List[float], float, int, str]:
    """逐维 ±方向贪心下降，步长由粗到细；最后一级仍有改进时继续减半细化。

    相比 Nelder–Mead，模式搜索在**非光滑/锯齿状的随机仿真响应面**上更可靠：
    它每次只沿一条坐标轴试探，不会因对角线方向全被拒绝而整团收缩卡死。
    自适应减半（直到 ``min_step``）保证窄而深的谷也能被压到底，而不是停在
    离谷底还有半个网格的平台上。代价是探索各维耦合结构较慢——因此作为 NM
    之后的补充细化阶段。
    """
    d = len(x0)
    used = 0
    base_steps = sorted(set(steps) | set(int_unit_steps), reverse=True)
    int_steps_set = set(int_unit_steps)

    def clip(u: List[float]) -> List[float]:
        return [min(1.0, max(0.0, z)) for z in u]

    x = clip(list(x0))
    fx, _ = fun(x)
    used += 1
    center = [0.5] * d

    def dist_to_center(u: List[float]) -> float:
        return sum((z - 0.5) ** 2 for z in u)

    def sweep(step: float) -> bool:
        """对所有坐标做一轮 ±试探，返回是否有过改进。"""
        nonlocal used, x, fx
        any_improved = False
        improved = True
        while improved and used < max_evals:
            improved = False
            for i in range(d):
                candidates = []
                for direction in (1.0, -1.0):
                    if used >= max_evals:
                        break
                    trial = clip([x[j] + (direction * step if j == i else 0.0)
                                  for j in range(d)])
                    if trial == x:
                        continue
                    fv, _ = fun(trial)
                    used += 1
                    tol = PLATEAU_RTOL * max(1.0, abs(fx))
                    if fv < fx - tol:
                        candidates.append((fv, trial))
                    elif fv <= fx + tol and dist_to_center(trial) < dist_to_center(x) - 1e-12:
                        candidates.append((fv, trial))  # 平台上向中心靠拢
                if candidates:
                    candidates.sort(key=lambda c: (c[0], _tie_key(c[1])))
                    fx2, x2 = candidates[0]
                    # 整型维度上的"平台向中心移动"不应被当作严格改进而无限循环
                    if fx2 < fx - PLATEAU_RTOL * max(1.0, abs(fx)):
                        improved = any_improved = True
                    else:
                        any_improved = True
                    x[:], fx = x2, fx2
                if used >= max_evals:
                    break
        return any_improved

    reason = "max_evals"
    for step in base_steps:
        sweep(step)
        if used >= max_evals:
            return x, fx, used, reason
    # 自适应细化：在最细的非整型步长上继续减半，直到无改进或到 min_step
    refine = min(s for s in base_steps if s not in int_steps_set) \
        if any(s not in int_steps_set for s in base_steps) else min_step
    while refine > min_step and used < max_evals:
        refine *= 0.5
        made = sweep(refine)
        if not made:
            break
    reason = "converged" if used < max_evals else "max_evals"
    return x, fx, used, reason


# --------------------------------------------------------------------------- #
# 主优化流程
# --------------------------------------------------------------------------- #
def optimize(specs: List[ParamSpec],
             eval_fn: Callable[[Dict[str, Any], int, str], Trial],
             *, seed: int, current_params: Dict[str, float],
             n_global: int = 12, n_local_starts: int = 3,
             max_evals: int = 120,
             progress: Optional[Callable[[Dict[str, Any]], None]] = None
             ) -> OptimizationResult:
    """``eval_fn(params, point_index, phase) -> Trial``"""
    rng = random.Random(int(seed))
    dim = len(specs)
    # 整型参数在单位空间的最小可分辨步长，用于放宽单纯形收敛阈值
    int_min_unit = min([1.0 / (s.hi - s.lo) for s in specs
                        if s.is_int and s.hi > s.lo], default=0.0)
    trials: List[Trial] = []
    cache: Dict[Tuple[float, ...], Trial] = {}
    counter = {"i": 0}

    def to_unit(p: Dict[str, float]) -> List[float]:
        return [s.to_unit(float(p.get(s.key, s.lo))) for s in specs]

    def from_unit(u: List[float]) -> Dict[str, float]:
        return {s.key: s.from_unit(u[j]) for j, s in enumerate(specs)}

    def cache_key(p: Dict[str, float]) -> Tuple[float, ...]:
        return tuple(round(float(p.get(s.key, s.lo)) / _QUANT) * _QUANT
                     for s in specs)

    def evaluate_at(p: Dict[str, float], phase: str,
                    idx: Optional[int] = None) -> Trial:
        key = cache_key(p)
        if key in cache:
            return cache[key]
        i = counter["i"] if idx is None else idx
        counter["i"] += 1
        t = eval_fn(p, i, phase)
        cache[key] = t
        trials.append(t)
        if progress:
            best_so_far = min((x for x in trials if not x.failed),
                              key=lambda x: (x.loss, _tie_key(to_unit(x.params))),
                              default=None)
            progress({"n_evals": len(trials),
                      "best_loss": best_so_far.loss if best_so_far else None,
                      "phase": phase})
        return t

    stopped = "max_evals"

    # ---- 阶段 0：确定性起点（场景当前值 + 可行域中心 + 角点）--------------
    seed_points: List[List[float]] = [
        to_unit(current_params),
        [0.5] * dim,
    ]
    for corner in ([0.0] * dim, [1.0] * dim):
        seed_points.append(list(corner))
    for u in seed_points:
        evaluate_at(from_unit(u), "seed")

    # ---- 阶段 1：拉丁超立方全局搜索 --------------------------------------
    budget_left = max_evals - len(trials)
    n_lhs = max(0, min(n_global, budget_left))
    for u in latin_hypercube(n_lhs, dim, rng):
        if len(trials) >= max_evals:
            stopped = "budget_exhausted_global"
            break
        evaluate_at(from_unit(u), "lhs")

    # ---- 阶段 2：最好的几个起点做 Nelder–Mead 细化 ------------------------
    ranked = sorted((t for t in trials if not t.failed),
                    key=lambda t: (t.loss, _tie_key(to_unit(t.params))))
    starts: List[List[float]] = []
    for t in ranked:
        u = to_unit(t.params)
        if all(_tie_key(u) != _tie_key(v) for v in starts):
            starts.append(u)
        if len(starts) >= n_local_starts:
            break

    for k, u0 in enumerate(starts):
        if len(trials) >= max_evals:
            stopped = "budget_exhausted_local"
            break

        def fun(u: List[float]) -> Tuple[float, int]:
            p = from_unit(u)
            tr = evaluate_at(p, "nm")
            return tr.loss, tr.point_index

        per_start = max(12, (max_evals - len(trials)) // max(2, n_local_starts))
        # 整型参数在单位空间最小可分辨步长 = 1/(hi-lo)；x_tol 小于它没有意义，
        # 反而会让单纯形在同一整数网格上空转耗尽预算。
        x_tol_eff = max(1e-3, 0.5 * int_min_unit) if int_min_unit > 0 else 1e-3
        _, _, _, reason_nm = nelder_mead(fun, u0, max_evals=per_start,
                                         x_tol=x_tol_eff)
        if reason_nm == "converged":
            stopped = "converged"

    # ---- 阶段 3：在全局最优点上做坐标模式搜索 ------------------------------
    # 随机仿真的响应面非光滑，NM 有时在窄谷外收缩卡死；逐维试探的模式搜索
    # 对此更鲁棒，用剩余预算再压一次误差。
    if len(trials) < max_evals:
        ranked2 = sorted((t for t in trials if not t.failed),
                         key=lambda t: (t.loss, _tie_key(to_unit(t.params))))
        u_best = to_unit(ranked2[0].params)

        def fun_cs(u: List[float]) -> Tuple[float, int]:
            tr = evaluate_at(from_unit(u), "pattern")
            return tr.loss, tr.point_index

        int_steps = tuple(sorted({1.0 / (s.hi - s.lo)
                                  for s in specs if s.is_int and s.hi > s.lo}))
        _, _, _, reason_cs = coordinate_search(
            fun_cs, u_best, max_evals=max_evals - len(trials),
            int_unit_steps=int_steps)
        if reason_cs == "converged":
            stopped = "converged"

    # ---- 汇总：平台、平局裁决 --------------------------------------------
    good = [t for t in trials if not t.failed]
    if not good:
        raise RuntimeError("所有参数组合的仿真均失败，请检查场景配置、参数界与步数设置")
    good.sort(key=lambda t: (t.loss, _tie_key(to_unit(t.params))))
    best = good[0]
    tol = PLATEAU_RTOL * max(1.0, abs(best.loss))
    plateau = [t for t in good if t.loss <= best.loss + tol]
    plateau.sort(key=lambda t: (t.loss, _tie_key(to_unit(t.params))))

    return OptimizationResult(best=best, trials=trials, plateau=plateau,
                              bounds=[], n_evals=len(trials), seed=int(seed),
                              stopped_reason=stopped)


# --------------------------------------------------------------------------- #
# 边界诊断
# --------------------------------------------------------------------------- #
def diagnose_bounds(specs: List[ParamSpec],
                    best_params: Dict[str, float], best_loss: float,
                    eval_fn: Callable[[Dict[str, Any], int, str], Trial]
                    ) -> List[BoundStatus]:
    """对贴界的最优参数做一步"内推"试验。

    内推 5% 可行域宽度后：
    * 误差明显变差（>1% 相对差）→ 界是**主动约束**，真最优可能在界外，
      应提示用户放宽该参数界；
    * 误差几乎不变 → 只是平台/数值吸附到界上，不构成问题。
    """
    out: List[BoundStatus] = []
    for s in specs:
        v = float(best_params[s.key])
        at = ""
        if s.is_int:
            if v <= s.lo:
                at = "lower"
            elif v >= s.hi:
                at = "upper"
        else:
            span = s.hi - s.lo
            # 允许可行域宽度 2.5% 的数值容差：随机仿真在界附近常有小平台，
            # 单纯形/模式搜索不会精确贴边。是否真为主动约束由内推试验判定，
            # 因此略宽的容差不会误报。
            tol = 2.5e-2 * span
            if v <= s.lo + tol:
                at = "lower"
            elif v >= s.hi - tol:
                at = "upper"
        if not at:
            continue
        delta = 0.05 * (s.hi - s.lo)
        if s.is_int:
            delta = max(1.0, math.ceil(delta))
        inward_v = (s.clamp(v + delta) if at == "lower"
                    else s.clamp(v - delta))
        status = BoundStatus(key=s.key, label=s.label, at=at, value=v,
                             bound=s.lo if at == "lower" else s.hi)
        if abs(inward_v - v) > 1e-12:
            trial = eval_fn({**best_params, s.key: inward_v}, -1, "bound_check")
            status.inward_loss = trial.loss
            status.active = (trial.loss > best_loss
                             * (1.0 + PLATEAU_RTOL) and not trial.failed)
        else:
            # 界本身太窄（如整数 [a,a]），无法内推——直接标记需要关注
            status.inward_loss = None
            status.active = True
        out.append(status)
    return out
