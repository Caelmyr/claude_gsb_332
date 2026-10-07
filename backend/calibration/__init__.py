"""Parameter calibration: fit model parameters to an observed target curve.

The calibration sub-package answers one question: *given a target statistical
curve (e.g. real infected counts over time), which model parameters make the
simulation reproduce it best?*  Four concerns are deliberately separated so
each one stays auditable:

* :mod:`target`     — parse and clean the user-supplied target curve
                      (missing values, duplicate timestamps, outliers).
* :mod:`align`      — align simulation time to target time when the two do not
                      share time points or even the same step count, and compute
                      the fitting loss on the aligned pairs.
* :mod:`optimizer`  — derivative-free search over the bounded parameter space:
                      a deterministic Latin-hypercube global design followed by
                      Hooke-Jeeves pattern-search refinement (multi-start from
                      the best design points), all stdlib-only.
* :mod:`calibrator` — runs engines for whole batches of parameter candidates,
                      averages stochastic replicates with *fixed* seed sets,
                      re-confirms the finalists on a disjoint seed set (so the
                      winner is not a lucky Monte-Carlo draw), and reports
                      boundary / identifiability / data-quality diagnostics.

Everything is seeded and deterministic: the same request payload (including
its ``seed``) always returns the same best parameters and error.
"""

from __future__ import annotations

from .target import TargetPoint, PreparedTarget, prepare_target, parse_target
from .align import align_and_score, simulate_curve
from .optimizer import latin_hypercube, pattern_search
from .calibrator import (
    CalibrationSpec,
    CalibrationResult,
    run_calibration,
)

__all__ = [
    "TargetPoint",
    "PreparedTarget",
    "prepare_target",
    "parse_target",
    "align_and_score",
    "simulate_curve",
    "latin_hypercube",
    "pattern_search",
    "CalibrationSpec",
    "CalibrationResult",
    "run_calibration",
]
