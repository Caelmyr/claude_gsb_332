"""校准任务生命周期管理：后台执行、进度落盘、取消。

与对比实验类似，校准是 CPU 密集型长任务，在守护线程里运行，过程中周期性
把进度（已评估次数、当前最优误差）原子写入 ``data/calibrations/``，前端轮
询即可。任务输入在创建时整体快照（场景配置 + 目标数据 + 参数界 + 算法设
置），此后场景再被编辑也不影响这次校准——这是结果可复现的前提。
"""

from __future__ import annotations

import threading
from typing import Any, Dict

from . import calibration as calibration_pkg
from . import models, storage, util


class CalibrationManager:
    def __init__(self) -> None:
        self._cancel: set = set()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ #
    def create(self, scene_dict: Dict[str, Any],
               request: Dict[str, Any]) -> Dict[str, Any]:
        """同步做参数校验（失败直接抛错给调用方），随后后台运行。"""
        scene = models.Scene.from_dict(scene_dict)
        # 提前构建 specs 与目标清洗，把用户输入错误挡在创建阶段
        calibration_pkg.build_specs(
            scene.domain, scene.model, request.get("params", []))
        calibration_pkg.clean_target(request["target"])

        cal_id = util.new_id("cal")
        now = util.now_iso()
        params_spec = []
        for p in request.get("params", []):
            params_spec.append(dict(p))
        record: Dict[str, Any] = {
            "id": cal_id,
            "name": request.get("name") or f"{scene.name} · 参数校准",
            "scene_id": scene.id,
            "scene_name": scene.name,
            "domain": scene.domain,
            "model": scene.model,
            "metric": request.get("metric"),
            "params_spec": params_spec,
            "request": {
                **request,
                # 目标原文随任务快照保存，保证结果可重放
                "target": request.get("target"),
            },
            "scene_snapshot": {
                "id": scene.id, "name": scene.name,
                "domain": scene.domain, "model": scene.model,
                "config": models.resolve_config(scene),
                "interventions": scene.interventions,
            },
            "status": "running",
            "progress": {"n_evals": 0, "best_loss": None, "phase": "init"},
            "best_params": None,
            "best_loss": None,
            "fit": None,
            "warnings": [],
            "error": "",
            "created_at": now,
            "updated_at": now,
        }
        storage.save_calibration(record)

        thread = threading.Thread(target=self._run, args=(cal_id, record),
                                  daemon=True)
        thread.start()
        return record

    # ------------------------------------------------------------------ #
    def _run(self, cal_id: str, record: Dict[str, Any]) -> None:
        try:
            def progress(info: Dict[str, Any]) -> None:
                rec = storage.load_calibration(cal_id)
                if rec is None:
                    return
                rec["progress"] = info
                rec["updated_at"] = util.now_iso()
                storage.save_calibration(rec)
                if cal_id in self._cancel:
                    raise CalibrationCancelled()

            result = calibration_pkg.run_calibration(
                record["scene_snapshot"], record["request"],
                progress=progress)
            rec = storage.load_calibration(cal_id)
            if rec is not None:
                rec.update(result)
                rec["status"] = "finished"
                rec["updated_at"] = util.now_iso()
                storage.save_calibration(rec)
        except CalibrationCancelled:
            rec = storage.load_calibration(cal_id)
            if rec is not None:
                rec["status"] = "cancelled"
                rec["updated_at"] = util.now_iso()
                storage.save_calibration(rec)
        except Exception as exc:  # noqa: BLE001
            rec = storage.load_calibration(cal_id)
            if rec is not None:
                rec["status"] = "error"
                rec["error"] = str(exc)
                rec["updated_at"] = util.now_iso()
                storage.save_calibration(rec)
        finally:
            with self._lock:
                self._cancel.discard(cal_id)

    def cancel(self, cal_id: str) -> None:
        with self._lock:
            self._cancel.add(cal_id)

    def delete(self, cal_id: str) -> bool:
        with self._lock:
            self._cancel.discard(cal_id)
        return storage.delete_calibration(cal_id)


class CalibrationCancelled(Exception):
    pass


calibration_manager = CalibrationManager()
