"""Lifecycle management for parameter calibrations.

A calibration is heavier than an experiment (global design × replicates +
multi-start refinement + confirmation), so it always runs in a background
worker thread.  The manager owns the live spec objects and the per-job abort
flags; all durable state goes through :mod:`backend.storage` as a single
atomic JSON document, written whenever the phase changes and again at the end.

Reproducibility contract: the stored record contains the complete input spec
(domain/model, metric, parameter bounds, target points *after* cleaning,
seeds, replicate counts, loss/align/outlier settings) as well as every
candidate evaluation.  Re-running the same record — same code version —
reproduces the exact best parameters and errors.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, List, Optional

from . import storage, util
from .calibration import CalibrationSpec, run_calibration


class CalibrationManager:
    def __init__(self) -> None:
        self._abort: set = set()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    def start(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Validate synchronously (fast), then run the heavy work in a thread."""
        spec = CalibrationSpec.from_dict(data)
        errors = spec.validate()
        if errors:
            raise ValueError("；".join(errors))

        cal_id = util.new_id("cal")
        record: Dict[str, Any] = {
            "id": cal_id,
            "name": spec.name,
            "domain": spec.domain,
            "model": spec.model,
            "metric": spec.metric,
            "status": "running",
            "progress": {"phase": "init", "done": 0, "total": 0, "note": ""},
            "created_at": util.now_iso(),
            "updated_at": util.now_iso(),
            "input": {
                "params": [dict(p) for p in spec.params],
                "steps": spec.steps,
                "seed": spec.seed,
                "n_replicates": spec.n_replicates,
                "n_design": spec.n_design,
                "n_refine": spec.n_refine,
                "n_refine_starts": spec.n_refine_starts,
                "confirm_replicates": spec.confirm_replicates,
                "loss_kind": spec.loss_kind,
                "time_per_step": spec.time_per_step,
                "time_offset": spec.time_offset,
                "interp": spec.interp,
                "extrapolate": spec.extrapolate,
                "outlier_mode": spec.outlier_mode,
                "base_config": dict(spec.base_config),
                "interventions": [dict(i) for i in spec.interventions],
                "target_points": [p.to_dict() for p in spec.target.points],
                "target_summary": spec.target.summary(),
            },
        }
        storage.save_calibration(record)

        def worker() -> None:
            def on_progress(p: Dict[str, Any]) -> None:
                rec = storage.load_calibration(cal_id)
                if rec is None:
                    return
                rec["progress"] = p
                rec["updated_at"] = util.now_iso()
                storage.save_calibration(rec)

            def should_abort() -> bool:
                with self._lock:
                    return cal_id in self._abort

            try:
                result = run_calibration(spec, on_progress=on_progress,
                                         should_abort=should_abort,
                                         result_id=cal_id)
                rec = result.to_dict()
                if rec.get("status") == "stopped":
                    pass
                storage.save_calibration(rec)
            except Exception as exc:  # noqa: BLE001
                rec = storage.load_calibration(cal_id) or {}
                rec.update({"id": cal_id, "status": "error",
                            "error": str(exc),
                            "updated_at": util.now_iso()})
                storage.save_calibration(rec)
            finally:
                with self._lock:
                    self._abort.discard(cal_id)

        threading.Thread(target=worker, daemon=True).start()
        return record

    def stop(self, cal_id: str) -> bool:
        if storage.load_calibration(cal_id) is None:
            return False
        with self._lock:
            self._abort.add(cal_id)
        return True

    def get(self, cal_id: str) -> Optional[Dict[str, Any]]:
        return storage.load_calibration(cal_id)

    def list(self) -> List[Dict[str, Any]]:
        return storage.list_calibrations()

    def delete(self, cal_id: str) -> bool:
        with self._lock:
            self._abort.discard(cal_id)
        return storage.delete_calibration(cal_id)


calibration_manager = CalibrationManager()
