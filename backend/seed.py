"""Seed example scenes for the three domains (one CA + one ABM each).

Each seed scene uses a fixed id so ``--seed`` / repeated calls are idempotent —
re-running simply overwrites the same six files instead of accumulating
duplicates.
"""

from __future__ import annotations

from typing import List

from . import models, storage, util

_SCENES = [
    models.Scene(
        id="scene_traffic_ca",
        name="交通 · 多车道环形高速（元胞自动机）",
        domain="traffic", model="ca",
        description="Nagel-Schreckenberg 元胞自动机：3 车道环形高速，观察密度升高时的走走停停。",
        config={"lanes": 3, "length": 100, "vmax": 5, "p_slow": 0.2, "density": 0.25},
        interventions=[{"type": "incident", "params": {"start": 30, "end": 45},
                        "at_step": 60, "label": "第 60 步在 30–45 格设置事故"}],
    ),
    models.Scene(
        id="scene_traffic_abm",
        name="交通 · 环形道路 IDM 跟驰（智能体）",
        domain="traffic", model="abm",
        description="40 辆车在 1km 环形道路上按智能驾驶员模型 (IDM) 跟驰，产生同步流与拥堵。",
        config={"road_length": 1000.0, "n": 60, "v0": 30.0, "T": 1.5,
                "a": 1.5, "b": 2.0, "s0": 2.0, "dt": 0.5},
        interventions=[{"type": "speed_limit", "params": {"vmax": 10},
                        "at_step": 80, "label": "第 80 步限速 10 m/s"}],
    ),
    models.Scene(
        id="scene_ecology_ca",
        name="生态 · 草地-兔子-狐狸（元胞自动机）",
        domain="ecology", model="ca",
        description="栅格上的捕食者-被捕食者系统：草地生长、兔子吃草、狐狸捕兔。",
        config={"width": 70, "height": 70, "n_rabbits": 400, "n_foxes": 70,
                "grass_growth": 0.06, "rabbit_repro": 8, "fox_repro": 12, "fox_starve": 2},
        interventions=[{"type": "cull_foxes", "params": {"fraction": 0.6},
                        "at_step": 100, "label": "第 100 步捕杀 60% 狐狸"}],
    ),
    models.Scene(
        id="scene_ecology_abm",
        name="生态 · 鸟群与捕食者（智能体）",
        domain="ecology", model="abm",
        description="Boids 三规则鸟群 + 追逐的捕食者，观察集群凝聚与捕食扰动。",
        config={"width": 500, "height": 500, "n_boids": 200, "n_predators": 3,
                "max_speed": 4.0, "pred_speed": 4.5, "perception": 40.0,
                "separation": 30.0, "flee_radius": 60.0},
        interventions=[{"type": "release_predators", "params": {"count": 5},
                        "at_step": 120, "label": "第 120 步投放 5 只捕食者"}],
    ),
    models.Scene(
        id="scene_epidemic_ca",
        name="传染病 · 栅格 SIR（元胞自动机）",
        domain="epidemic", model="ca",
        description="每人占一格的 SIR 元胞自动机，观察同步更新下的感染波前。",
        config={"width": 60, "height": 60, "beta": 0.35, "gamma": 0.08,
                "initial_infected": 5, "vaccination_rate": 0.0},
        interventions=[{"type": "vaccinate", "params": {"fraction": 0.5},
                        "at_step": 30, "label": "第 30 步为 50% 易感者接种"}],
    ),
    models.Scene(
        id="scene_epidemic_abm",
        name="传染病 · 移动接触 SIR（智能体）",
        domain="epidemic", model="abm",
        description="800 名随机移动个体在感染半径内接触传播，比较封锁/口罩/疫苗效果。",
        config={"width": 500, "height": 500, "n": 800, "beta": 0.3, "gamma": 0.05,
                "speed": 2.0, "radius": 6.0, "initial_infected": 10,
                "vaccination_rate": 0.0, "movement": "random_walk"},
        interventions=[{"type": "lockdown", "params": {"scale": 0.7},
                        "at_step": 50, "label": "第 50 步实施 70% 封锁"}],
    ),
]


def seed_all() -> List[str]:
    """Write the six example scenes; return their ids."""
    storage.ensure_dirs()
    for scene in _SCENES:
        if not scene.created_at:
            scene.created_at = scene.updated_at = util.now_iso()
        storage.save_scene(scene.to_dict())
    return [s.id for s in _SCENES]


# --------------------------------------------------------------------------- #
# Demo runs + comparison experiment
# --------------------------------------------------------------------------- #
def seed_demo_data(steps: int = 80) -> dict:
    """Create one demo run per seed scene and one comparison experiment.

    Runs are created with ``snapshot_interval=1`` so every time step has a full
    snapshot on disk — this makes the real-time / replay / individual pages
    immediately clickable without a 404 on any step.  Seeding is idempotent:
    if any runs already exist it does nothing, so restarting the server never
    accumulates duplicate demo data.
    """
    from .run_manager import manager

    if storage.list_runs():
        return {"runs": 0, "experiments": 0, "skipped": True}

    run_ids = []
    for scene in _SCENES:
        meta = manager.create_run(scene, snapshot_interval=1)
        manager.run_batch(meta["id"], steps, keep_engine=True)
        run_ids.append(meta["id"])

    exp_id = _seed_experiment()
    return {"runs": len(run_ids), "experiments": 1 if exp_id else 0,
            "skipped": False, "run_ids": run_ids, "exp_id": exp_id}


def _seed_experiment(steps: int = 120) -> str:
    """Seed one epidemic-ABM comparison: baseline vs. vaccination vs. low β."""
    from .run_manager import manager

    base = storage.load_scene("scene_epidemic_abm")
    if base is None:
        return ""
    exp = models.Experiment(
        id=util.new_id("exp"),
        name="示例 · 疫苗与传播率对比",
        scene_id="scene_epidemic_abm",
        steps=steps,
        groups=[
            {"name": "基线", "config": {}},
            {"name": "疫苗接种 50%", "config": {"vaccination_rate": 0.5}},
            {"name": "低感染率 β=0.12", "config": {"beta": 0.12}},
        ],
        status="finished",
        created_at=util.now_iso(),
    )
    for g in exp.groups:
        cfg = {**base.get("config", {}), **(g.get("config") or {})}
        scene = models.Scene.from_dict(
            {**base, "config": cfg, "name": f"{exp.name} · {g['name']}"})
        meta = manager.create_run(
            scene, name=scene.name, seed=int(cfg.get("seed", 0)),
            snapshot_interval=max(1, steps // 50))
        manager.run_batch(meta["id"], steps, keep_engine=False)
        exp.run_ids.append(meta["id"])
    storage.save_experiment(exp.to_dict())
    return exp.id
