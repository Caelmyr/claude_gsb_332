"""参数校准（parameter calibration）子系统。

* :mod:`target`     —— 目标曲线解析与稳健清洗（缺失/重复/异常点）
* :mod:`objective`  —— 仿真执行、时间轴对齐与拟合误差（目标函数）
* :mod:`optimizer`  —— 确定性混合优化器（LHS 全局 + 有界单纯形）
* :mod:`calibrator` —— 一次完整校准的编排、诊断与告警
"""

from .calibrator import build_specs, run_calibration
from .target import TargetSeries, clean_target, parse_target

__all__ = ["build_specs", "run_calibration", "TargetSeries",
           "clean_target", "parse_target"]
