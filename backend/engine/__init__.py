"""Engine registry and factory.

Maps ``(domain, model)`` pairs to concrete :class:`Engine` subclasses so the
run manager can instantiate an engine from a scene without a long if/elif
chain, and so the catalog can stay declarative.
"""

from __future__ import annotations

from typing import Dict, Type

from . import base
from .epidemic_abm import EpidemicABM
from .epidemic_ca import EpidemicCA
from .ecology_abm import EcologyABM
from .ecology_ca import EcologyCA
from .traffic_abm import TrafficABM
from .traffic_ca import TrafficCA

# (domain, model) -> engine class
_REGISTRY: Dict[str, Type[base.Engine]] = {
    "traffic/ca": TrafficCA,
    "traffic/abm": TrafficABM,
    "ecology/ca": EcologyCA,
    "ecology/abm": EcologyABM,
    "epidemic/ca": EpidemicCA,
    "epidemic/abm": EpidemicABM,
}


def available_engines() -> Dict[str, str]:
    """Map ``"domain/model"`` -> class name, for introspection/debugging."""
    return {key: cls.__name__ for key, cls in _REGISTRY.items()}


def make_engine(domain: str, model: str, config: dict = None,
                seed: int = None) -> base.Engine:
    """Instantiate the engine registered for ``domain``/``model``."""
    key = f"{domain}/{model}"
    if key not in _REGISTRY:
        raise KeyError(f"unknown engine: {key}")
    return _REGISTRY[key](config=config, seed=seed)
