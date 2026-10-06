"""Flask application: static frontend + JSON REST API.

The API is a thin layer over :mod:`run_manager`, :mod:`storage`, the engines,
:mod:`report` and :mod:`export`.  All mutation goes through the run manager /
storage layer so the atomic-write and time-step-sharding guarantees hold no
matter which endpoint a request arrives from.
"""

from __future__ import annotations

import os
import threading
from typing import Any, Dict

from flask import Flask, jsonify, request, send_file, send_from_directory

from . import catalog, export, models, report, storage, util
from .run_manager import manager

FRONTEND_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend")


def create_app() -> Flask:
    app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path="/static")
    storage.ensure_dirs()

    # ------------------------------------------------------------------ #
    # Static pages
    # ------------------------------------------------------------------ #
    @app.route("/")
    def index():
        return send_from_directory(FRONTEND_DIR, "index.html")

    @app.route("/<path:name>")
    def pages(name: str):
        path = os.path.join(FRONTEND_DIR, name)
        if os.path.isfile(path):
            return send_from_directory(FRONTEND_DIR, name)
        if name.endswith(".html"):
            return send_from_directory(FRONTEND_DIR, name.split("/")[-1])
        return jsonify({"error": "not found"}), 404

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _err(exc: Exception, status: int = 400):
        return jsonify({"error": str(exc)}), status

    def _json() -> Dict[str, Any]:
        return request.get_json(force=True, silent=True) or {}

    # ------------------------------------------------------------------ #
    # Meta / catalog
    # ------------------------------------------------------------------ #
    @app.route("/api/health")
    def health():
        return jsonify({"status": "ok"})

    @app.route("/api/catalog")
    def get_catalog():
        return jsonify({"domains": catalog.domains(),
                        "order": catalog.DOMAIN_ORDER,
                        "engines": manager_engines()})

    def manager_engines():
        from .engine import available_engines
        return available_engines()

    # ------------------------------------------------------------------ #
    # Scenes
    # ------------------------------------------------------------------ #
    @app.route("/api/scenes", methods=["GET"])
    def list_scenes():
        return jsonify({"scenes": storage.list_scenes()})

    @app.route("/api/scenes", methods=["POST"])
    def create_scene():
        scene = models.Scene.from_dict(_json())
        errors = models.validate_scene(scene)
        if errors:
            return jsonify({"error": "validation failed", "details": errors}), 400
        scene.created_at = scene.updated_at = util.now_iso()
        storage.save_scene(scene.to_dict())
        return jsonify(scene.to_dict()), 201

    @app.route("/api/scenes/<scene_id>", methods=["GET"])
    def get_scene(scene_id: str):
        scene = storage.load_scene(scene_id)
        if scene is None:
            return _err(KeyError(f"scene not found: {scene_id}"), 404)
        return jsonify(scene)

    @app.route("/api/scenes/<scene_id>", methods=["PUT"])
    def update_scene(scene_id: str):
        if storage.load_scene(scene_id) is None:
            return _err(KeyError(f"scene not found: {scene_id}"), 404)
        scene = models.Scene.from_dict({**_json(), "id": scene_id})
        errors = models.validate_scene(scene)
        if errors:
            return jsonify({"error": "validation failed", "details": errors}), 400
        scene.created_at = storage.load_scene(scene_id).get("created_at", "")
        scene.updated_at = util.now_iso()
        storage.save_scene(scene.to_dict())
        return jsonify(scene.to_dict())

    @app.route("/api/scenes/<scene_id>", methods=["DELETE"])
    def delete_scene(scene_id: str):
        if not storage.delete_scene(scene_id):
            return _err(KeyError(f"scene not found: {scene_id}"), 404)
        return jsonify({"deleted": scene_id})

    # ------------------------------------------------------------------ #
    # Runs
    # ------------------------------------------------------------------ #
    @app.route("/api/runs", methods=["GET"])
    def list_runs():
        return jsonify({"runs": storage.list_runs()})

    @app.route("/api/runs", methods=["POST"])
    def create_run():
        data = _json()
        scene = storage.load_scene(data.get("scene_id", ""))
        if scene is None:
            return _err(KeyError(f"scene not found: {data.get('scene_id')}"), 404)
        # Optional config override (for experiments / quick tweaks).
        if data.get("config"):
            scene["config"] = {**scene.get("config", {}), **data["config"]}
        scene_obj = models.Scene.from_dict(scene)
        try:
            meta = manager.create_run(
                scene_obj, name=data.get("name"),
                seed=data.get("seed"),
                snapshot_interval=int(data.get("snapshot_interval", 1)))
        except Exception as exc:  # noqa: BLE001
            return _err(exc, 500)
        return jsonify(meta), 201

    @app.route("/api/runs/<run_id>", methods=["GET"])
    def get_run(run_id: str):
        try:
            return jsonify(manager.status(run_id))
        except KeyError as exc:
            return _err(exc, 404)

    @app.route("/api/runs/<run_id>", methods=["DELETE"])
    def delete_run(run_id: str):
        if not manager.delete_run(run_id):
            return _err(KeyError(f"run not found: {run_id}"), 404)
        return jsonify({"deleted": run_id})

    @app.route("/api/runs/<run_id>/step", methods=["POST"])
    def step_run(run_id: str):
        n = int(_json().get("n", 1))
        try:
            return jsonify(manager.step(run_id, n))
        except KeyError as exc:
            return _err(exc, 404)
        except RuntimeError as exc:
            return _err(exc, 409)

    @app.route("/api/runs/<run_id>/batch", methods=["POST"])
    def batch_run(run_id: str):
        data = _json()
        steps = int(data.get("steps", 100))
        interval = data.get("snapshot_interval")
        background = bool(data.get("background", False))

        def worker():
            try:
                manager.run_batch(run_id, steps, interval, keep_engine=True)
            except Exception:  # noqa: BLE001
                pass

        if background:
            threading.Thread(target=worker, daemon=True).start()
            return jsonify({"started": True, "steps": steps})
        try:
            result = manager.run_batch(run_id, steps, interval, keep_engine=True)
            return jsonify(result)
        except KeyError as exc:
            return _err(exc, 404)
        except RuntimeError as exc:
            return _err(exc, 409)

    @app.route("/api/runs/<run_id>/<action>", methods=["POST"])
    def run_action(run_id: str, action: str):
        actions = {"pause": manager.pause, "resume": manager.resume,
                   "stop": manager.stop, "reset": manager.reset}
        if action not in actions:
            return _err(ValueError(f"unknown action: {action}"), 400)
        try:
            return jsonify(actions[action](run_id))
        except KeyError as exc:
            return _err(exc, 404)

    @app.route("/api/runs/<run_id>/snapshot")
    def run_snapshot(run_id: str):
        step = request.args.get("step", type=int)
        try:
            return jsonify(manager.get_snapshot(run_id, step))
        except KeyError as exc:
            return _err(exc, 404)

    @app.route("/api/runs/<run_id>/steps")
    def run_steps(run_id: str):
        if storage.load_run_meta(run_id) is None:
            return _err(KeyError(f"run not found: {run_id}"), 404)
        return jsonify({"steps": storage.list_steps(run_id)})

    @app.route("/api/runs/<run_id>/series")
    def run_series(run_id: str):
        try:
            return jsonify({"series": manager.get_series(run_id)})
        except KeyError as exc:
            return _err(exc, 404)

    @app.route("/api/runs/<run_id>/individuals")
    def run_individuals(run_id: str):
        step = request.args.get("step", type=int)
        try:
            return jsonify({"individuals": manager.get_individuals(run_id, step)})
        except KeyError as exc:
            return _err(exc, 404)

    @app.route("/api/runs/<run_id>/events")
    def run_events(run_id: str):
        try:
            return jsonify({"events": manager.get_events(run_id)})
        except KeyError as exc:
            return _err(exc, 404)

    @app.route("/api/runs/<run_id>/interventions", methods=["POST"])
    def apply_intervention(run_id: str):
        try:
            result = manager.apply_intervention(run_id, _json())
            return jsonify(result)
        except KeyError as exc:
            return _err(exc, 404)
        except RuntimeError as exc:
            return _err(exc, 409)

    # ------------------------------------------------------------------ #
    # Experiments (comparison of parameter groups)
    # ------------------------------------------------------------------ #
    @app.route("/api/experiments", methods=["GET"])
    def list_experiments():
        return jsonify({"experiments": storage.list_experiments()})

    @app.route("/api/experiments", methods=["POST"])
    def create_experiment():
        data = _json()
        scene = storage.load_scene(data.get("scene_id", ""))
        if scene is None:
            return _err(KeyError(f"scene not found: {data.get('scene_id')}"), 404)
        steps = int(data.get("steps", 200))
        groups = data.get("groups") or []
        if not groups:
            return _err(ValueError("至少需要一个参数组"), 400)
        exp = models.Experiment.from_dict(data)
        exp.status = "running"
        exp.created_at = util.now_iso()
        storage.save_experiment(exp.to_dict())

        def worker():
            try:
                for g in groups:
                    cfg = {**scene.get("config", {}), **(g.get("config") or {})}
                    s = models.Scene.from_dict(
                        {**scene, "config": cfg,
                         "name": f"{exp.name} · {g.get('name', '组')}"})
                    meta = manager.create_run(
                        s, name=s.name,
                        seed=int(cfg.get("seed", 0)),
                        snapshot_interval=max(1, steps // 50))
                    manager.run_batch(meta["id"], steps, keep_engine=False)
                    exp.run_ids.append(meta["id"])
                    storage.save_experiment(exp.to_dict())
                exp.status = "finished"
                storage.save_experiment(exp.to_dict())
            except Exception as exc:  # noqa: BLE001
                exp.status = "error"
                exp.error = str(exc)
                storage.save_experiment(exp.to_dict())

        threading.Thread(target=worker, daemon=True).start()
        return jsonify(exp.to_dict()), 201

    @app.route("/api/experiments/<exp_id>", methods=["GET"])
    def get_experiment(exp_id: str):
        exp = storage.load_experiment(exp_id)
        if exp is None:
            return _err(KeyError(f"experiment not found: {exp_id}"), 404)
        runs = []
        for run_id in exp.get("run_ids", []):
            meta = storage.load_run_meta(run_id)
            if meta:
                runs.append({"run_id": run_id, "name": meta.get("name"),
                             "meta": meta,
                             "series": storage.load_series(run_id)})
        return jsonify({**exp, "runs": runs})

    @app.route("/api/experiments/<exp_id>", methods=["DELETE"])
    def delete_experiment(exp_id: str):
        if not storage.delete_experiment(exp_id):
            return _err(KeyError(f"experiment not found: {exp_id}"), 404)
        return jsonify({"deleted": exp_id})

    # ------------------------------------------------------------------ #
    # Reports
    # ------------------------------------------------------------------ #
    @app.route("/api/reports/<run_id>", methods=["GET"])
    def get_report(run_id: str):
        rpt = storage.load_report(run_id)
        if rpt is None:
            return _err(KeyError(f"report not found: {run_id}"), 404)
        return jsonify(rpt)

    @app.route("/api/reports/<run_id>", methods=["POST"])
    def make_report(run_id: str):
        try:
            return jsonify(report.generate_report(run_id))
        except KeyError as exc:
            return _err(exc, 404)

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #
    @app.route("/api/export/formats")
    def export_formats():
        return jsonify({"formats": export.formats()})

    @app.route("/api/export/<run_id>")
    def export_run(run_id: str):
        fmt = request.args.get("format", "csv")
        step = request.args.get("step", type=int)
        try:
            path = export.export_run(run_id, fmt, step)
        except KeyError as exc:
            return _err(exc, 404)
        except ValueError as exc:
            return _err(exc, 400)
        name = os.path.basename(path)
        return send_file(path, as_attachment=True, download_name=name)

    # ------------------------------------------------------------------ #
    # History (scenes + runs + experiments in one view)
    # ------------------------------------------------------------------ #
    @app.route("/api/history")
    def history():
        return jsonify({
            "scenes": storage.list_scenes(),
            "runs": storage.list_runs(),
            "experiments": storage.list_experiments(),
        })

    return app


app = create_app()
