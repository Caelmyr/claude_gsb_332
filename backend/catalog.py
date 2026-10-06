"""Domain, model, intervention and metric metadata.

The catalog is the single source of truth that drives the frontend's config
forms, intervention controls and stat charts, and that the backend uses to
validate scenes and resolve default parameters.  Everything else reads from
here so that adding a domain or model only touches this file plus a new
``backend/engine/*.py`` implementation.

A *param* entry is ``{"key", "label", "type", "default", "min", "max", "step"}``
where ``type`` is one of ``int`` / ``float`` / ``bool`` / ``choice``.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _num(key: str, label: str, default: float, lo: float, hi: float,
         step: float = 1) -> Dict[str, Any]:
    t = "int" if isinstance(default, int) else "float"
    return {"key": key, "label": label, "type": t, "default": default,
            "min": lo, "max": hi, "step": step}


def _bool(key: str, label: str, default: bool) -> Dict[str, Any]:
    return {"key": key, "label": label, "type": "bool", "default": default}


def _choice(key: str, label: str, default: str,
            options: List[str]) -> Dict[str, Any]:
    return {"key": key, "label": label, "type": "choice", "default": default,
            "options": options}


# --------------------------------------------------------------------------- #
# Traffic
# --------------------------------------------------------------------------- #
TRAFFIC_CA_PARAMS = [
    _num("lanes", "车道数", 3, 1, 8),
    _num("length", "车道长度（格）", 80, 20, 400, 10),
    _num("vmax", "最高速度", 5, 1, 20),
    _num("p_slow", "随机减速概率", 0.15, 0.0, 1.0, 0.05),
    _num("density", "初始车辆密度", 0.2, 0.01, 0.9, 0.01),
    _bool("lane_change", "允许变道", True),
]

TRAFFIC_ABM_PARAMS = [
    _num("road_length", "道路长度（米）", 1000.0, 200, 5000, 100),
    _num("n", "车辆数", 40, 5, 200),
    _num("v0", "期望速度 v0", 30.0, 5, 60, 1),
    _num("T", "安全车头时距 T", 1.5, 0.3, 5, 0.1),
    _num("a", "最大加速度 a", 1.5, 0.2, 5, 0.1),
    _num("b", "舒适减速度 b", 2.0, 0.5, 8, 0.1),
    _num("s0", "最小间距 s0", 2.0, 0.5, 10, 0.5),
    _num("dt", "时间步长 dt", 0.5, 0.1, 2.0, 0.1),
]

# --------------------------------------------------------------------------- #
# Ecology
# --------------------------------------------------------------------------- #
ECOLOGY_CA_PARAMS = [
    _num("width", "网格宽度", 60, 20, 200, 10),
    _num("height", "网格高度", 60, 20, 200, 10),
    _num("n_rabbits", "初始兔子数", 300, 0, 5000, 50),
    _num("n_foxes", "初始狐狸数", 60, 0, 1000, 10),
    _num("grass_growth", "草地生长概率", 0.05, 0.0, 0.5, 0.01),
    _num("rabbit_repro", "兔子繁殖能量阈值", 8, 2, 30, 1),
    _num("fox_repro", "狐狸繁殖能量阈值", 12, 3, 40, 1),
    _num("fox_starve", "狐狸饥饿消耗", 2, 1, 10, 1),
]

ECOLOGY_ABM_PARAMS = [
    _num("width", "世界宽度", 400, 100, 1200, 50),
    _num("height", "世界高度", 400, 100, 1200, 50),
    _num("n_boids", "鸟群个体数", 150, 5, 1000, 10),
    _num("n_predators", "捕食者数量", 3, 0, 20),
    _num("max_speed", "鸟群最大速度", 4.0, 0.5, 15, 0.5),
    _num("pred_speed", "捕食者速度", 4.5, 0.5, 20, 0.5),
    _num("perception", "感知半径", 40.0, 5, 150, 5),
    _num("separation", "分离距离", 30.0, 1, 120, 1),
    _num("flee_radius", "逃逸半径", 60.0, 5, 200, 5),
]

# --------------------------------------------------------------------------- #
# Epidemic
# --------------------------------------------------------------------------- #
EPIDEMIC_CA_PARAMS = [
    _num("width", "网格宽度", 50, 20, 200, 10),
    _num("height", "网格高度", 50, 20, 200, 10),
    _num("beta", "感染概率 β", 0.4, 0.0, 1.0, 0.05),
    _num("gamma", "恢复概率 γ", 0.1, 0.0, 1.0, 0.01),
    _num("initial_infected", "初始感染者", 5, 1, 200),
    _num("vaccination_rate", "初始免疫比例", 0.0, 0.0, 1.0, 0.05),
]

EPIDEMIC_ABM_PARAMS = [
    _num("width", "世界宽度", 400, 100, 1200, 50),
    _num("height", "世界高度", 400, 100, 1200, 50),
    _num("n", "人口规模", 800, 20, 5000, 20),
    _num("beta", "接触感染概率 β", 0.3, 0.0, 1.0, 0.05),
    _num("gamma", "恢复概率 γ", 0.05, 0.0, 1.0, 0.01),
    _num("speed", "移动速度", 2.0, 0.0, 10, 0.5),
    _num("radius", "感染半径", 6.0, 1, 60, 1),
    _num("initial_infected", "初始感染者", 10, 1, 200),
    _num("vaccination_rate", "初始免疫比例", 0.0, 0.0, 1.0, 0.05),
    _choice("movement", "移动模型", "random_walk", ["random_walk", "home_range"]),
]

# --------------------------------------------------------------------------- #
# Interventions (applied to a running scene)
# --------------------------------------------------------------------------- #
TRAFFIC_INTERVENTIONS = [
    {"type": "speed_limit", "label": "限速", "params": [
        _num("vmax", "最高速度", 2, 1, 20)]},
    {"type": "slowdown", "label": "提高随机减速", "params": [
        _num("p_slow", "减速概率", 0.5, 0.0, 1.0, 0.05)]},
    {"type": "incident", "label": "事故拥堵（局部限速）", "params": [
        _num("start", "起点", 20, 0, 400, 5),
        _num("end", "终点", 40, 0, 400, 5)]},
    {"type": "clear_incident", "label": "清除事故", "params": []},
]

ECOLOGY_INTERVENTIONS = [
    {"type": "cull_foxes", "label": "捕杀狐狸", "params": [
        _num("fraction", "比例", 0.5, 0.0, 1.0, 0.05)]},
    {"type": "cull_rabbits", "label": "捕杀兔子", "params": [
        _num("fraction", "比例", 0.5, 0.0, 1.0, 0.05)]},
    {"type": "plant_grass", "label": "种草", "params": [
        _num("fraction", "比例", 0.3, 0.0, 1.0, 0.05)]},
    {"type": "release_predators", "label": "投放捕食者", "params": [
        _num("count", "数量", 5, 1, 50)]},
]

EPIDEMIC_INTERVENTIONS = [
    {"type": "vaccinate", "label": "疫苗接种", "params": [
        _num("fraction", "接种比例", 0.5, 0.0, 1.0, 0.05)]},
    {"type": "lockdown", "label": "封锁（降低接触/移动）", "params": [
        _num("scale", "强度", 0.5, 0.0, 1.0, 0.05)]},
    {"type": "mask", "label": "口罩（降低感染）", "params": [
        _num("scale", "强度", 0.5, 0.0, 1.0, 0.05)]},
    {"type": "social_distance", "label": "保持距离（降低半径）", "params": [
        _num("scale", "强度", 0.5, 0.0, 1.0, 0.05)]},
    {"type": "cure", "label": "特效药（提高恢复）", "params": [
        _num("scale", "强度", 2.0, 1.0, 10.0, 0.5)]},
]

# --------------------------------------------------------------------------- #
# Aggregate metric labels (stats dict keys -> human labels)
# --------------------------------------------------------------------------- #
TRAFFIC_METRICS = [
    {"key": "mean_speed", "label": "平均速度"},
    {"key": "flow", "label": "流量（辆/步）"},
    {"key": "density", "label": "车辆密度"},
    {"key": "stopped", "label": "停车比例"},
    {"key": "speed_std", "label": "速度标准差"},
]

ECOLOGY_METRICS = [
    {"key": "rabbits", "label": "兔子数量"},
    {"key": "foxes", "label": "狐狸数量"},
    {"key": "grass_coverage", "label": "草地覆盖率"},
    {"key": "mean_energy", "label": "平均能量"},
]

EPIDEMIC_METRICS = [
    {"key": "susceptible", "label": "易感者 S"},
    {"key": "infected", "label": "感染者 I"},
    {"key": "recovered", "label": "康复者 R"},
    {"key": "new_infections", "label": "新增感染"},
    {"key": "prevalence", "label": "感染率"},
]

# --------------------------------------------------------------------------- #
# Assembled catalog
# --------------------------------------------------------------------------- #
CATALOG: Dict[str, Dict[str, Any]] = {
    "traffic": {
        "label": "交通",
        "description": "车辆在道路网络上的流动：元胞自动机（Nagel-Schreckenberg 多车道环形高速）与智能体模型（IDM 跟驰）。",
        "models": {
            "ca": {"label": "元胞自动机（多车道环形高速）", "params": TRAFFIC_CA_PARAMS,
                   "description": "离散化车道网格，NS 四步规则 + 变道。"},
            "abm": {"label": "智能体模型（IDM 跟驰）", "params": TRAFFIC_ABM_PARAMS,
                    "description": "每辆车是独立智能体，按智能驾驶员模型 (IDM) 加减速。"},
        },
        "interventions": TRAFFIC_INTERVENTIONS,
        "metrics": TRAFFIC_METRICS,
    },
    "ecology": {
        "label": "生态",
        "description": "捕食者-被捕食者系统：草地-兔子-狐狸元胞自动机与鸟群（Boids）+ 捕食者智能体模型。",
        "models": {
            "ca": {"label": "元胞自动机（草地-兔-狐）", "params": ECOLOGY_CA_PARAMS,
                   "description": "草地生长、兔子吃草、狐狸捕兔的栅格演替。"},
            "abm": {"label": "智能体模型（鸟群 + 捕食者）", "params": ECOLOGY_ABM_PARAMS,
                    "description": "Boids 三规则（对齐/聚集/分离）加捕食者追逐。"},
        },
        "interventions": ECOLOGY_INTERVENTIONS,
        "metrics": ECOLOGY_METRICS,
    },
    "epidemic": {
        "label": "传染病",
        "description": "SIR 传播动力学：栅格元胞自动机与移动智能体接触传播。",
        "models": {
            "ca": {"label": "元胞自动机（栅格 SIR）", "params": EPIDEMIC_CA_PARAMS,
                   "description": "每人占一格，向 Moore 邻域按 β 传播，按 γ 康复。"},
            "abm": {"label": "智能体模型（移动接触 SIR）", "params": EPIDEMIC_ABM_PARAMS,
                    "description": "个体随机移动，在感染半径内接触传播。"},
        },
        "interventions": EPIDEMIC_INTERVENTIONS,
        "metrics": EPIDEMIC_METRICS,
    },
}

DOMAIN_ORDER = ["traffic", "ecology", "epidemic"]


def domains() -> Dict[str, Any]:
    """Return the full catalog (used by ``GET /api/catalog``)."""
    return CATALOG


def domain_info(domain: str) -> Dict[str, Any]:
    return CATALOG[domain]


def model_defaults(domain: str, model: str) -> Dict[str, Any]:
    """Resolve default config for a domain/model from its param specs."""
    params = CATALOG[domain]["models"][model]["params"]
    return {p["key"]: p["default"] for p in params}


def model_params(domain: str, model: str) -> List[Dict[str, Any]]:
    return CATALOG[domain]["models"][model]["params"]


def interventions(domain: str) -> List[Dict[str, Any]]:
    return CATALOG[domain]["interventions"]


def metric_labels(domain: str) -> Dict[str, str]:
    return {m["key"]: m["label"] for m in CATALOG[domain]["metrics"]}


def known_domain(domain: str) -> bool:
    return domain in CATALOG


def known_model(domain: str, model: str) -> bool:
    return known_domain(domain) and model in CATALOG[domain]["models"]
