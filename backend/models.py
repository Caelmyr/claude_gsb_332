"""Data models and validation for scenes and comparison experiments.

A :class:`Scene` captures *what* to simulate: the domain/model choice, the
numeric config (model parameters) and the scheduled interventions.  A
:class:`Experiment` captures *a comparison*: one base scene run under several
parameter groups, each producing its own run whose series are compared.

Runs are managed more loosely as plain dicts by :mod:`backend.run_manager`
because their lifecycle (status, current step, in-memory engine) does not fit
a simple immutable dataclass.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import catalog, util


@dataclass
class Scene:
    id: str = ""
    name: str = "未命名场景"
    domain: str = "traffic"
    model: str = "ca"
    description: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    interventions: List[Dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "domain": self.domain,
            "model": self.model,
            "description": self.description,
            "config": self.config,
            "interventions": self.interventions,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Scene":
        scene = cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "未命名场景")),
            domain=str(data.get("domain", "traffic")),
            model=str(data.get("model", "ca")),
            description=str(data.get("description", "")),
            config=dict(data.get("config") or {}),
            interventions=list(data.get("interventions") or []),
            created_at=str(data.get("created_at", "")),
            updated_at=str(data.get("updated_at", "")),
        )
        if not scene.id:
            scene.id = util.new_id("scene")
        return scene


@dataclass
class Experiment:
    id: str = ""
    name: str = "对比实验"
    scene_id: str = ""
    steps: int = 200
    groups: List[Dict[str, Any]] = field(default_factory=list)
    run_ids: List[str] = field(default_factory=list)
    status: str = "pending"           # pending | running | finished | error
    error: str = ""
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "scene_id": self.scene_id,
            "steps": self.steps,
            "groups": self.groups,
            "run_ids": self.run_ids,
            "status": self.status,
            "error": self.error,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Experiment":
        exp = cls(
            id=str(data.get("id", "")),
            name=str(data.get("name", "对比实验")),
            scene_id=str(data.get("scene_id", "")),
            steps=int(data.get("steps", 200)),
            groups=list(data.get("groups") or []),
            run_ids=list(data.get("run_ids") or []),
            status=str(data.get("status", "pending")),
            error=str(data.get("error", "")),
            created_at=str(data.get("created_at", "")),
        )
        if not exp.id:
            exp.id = util.new_id("exp")
        return exp


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def validate_scene(scene: Scene) -> List[str]:
    """Return a list of human-readable validation errors (empty when valid)."""
    errors: List[str] = []
    if not catalog.known_domain(scene.domain):
        errors.append(f"未知领域: {scene.domain}")
        return errors
    if not catalog.known_model(scene.domain, scene.model):
        errors.append(f"领域 {scene.domain} 下不存在模型: {scene.model}")
        return errors

    # Merge defaults first so validation only flags genuinely bad values.
    defaults = catalog.model_defaults(scene.domain, scene.model)
    merged = {**defaults, **scene.config}
    for spec in catalog.model_params(scene.domain, scene.model):
        value = merged.get(spec["key"])
        if spec["type"] in ("int", "float") and value is not None:
            try:
                fval = float(value)
            except (TypeError, ValueError):
                errors.append(f"参数 {spec['label']} 不是数字")
                continue
            lo, hi = spec.get("min"), spec.get("max")
            if lo is not None and fval < lo:
                errors.append(f"参数 {spec['label']} 小于下限 {lo}")
            if hi is not None and fval > hi:
                errors.append(f"参数 {spec['label']} 大于上限 {hi}")

    if scene.interventions:
        known = {i["type"] for i in catalog.interventions(scene.domain)}
        for itv in scene.interventions:
            if not isinstance(itv, dict) or "type" not in itv:
                errors.append("干预措施缺少 type 字段")
            elif itv["type"] not in known:
                errors.append(f"未知干预措施: {itv.get('type')}")
    return errors


def resolve_config(scene: Scene) -> Dict[str, Any]:
    """Merge model defaults with the scene's overrides (defaults win on gaps)."""
    defaults = catalog.model_defaults(scene.domain, scene.model)
    merged = dict(defaults)
    merged.update(scene.config)
    return merged
