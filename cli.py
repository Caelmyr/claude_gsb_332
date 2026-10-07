"""Headless command-line batch runner + parameter calibration.

Run a scene from the terminal without the web UI — useful for quick parameter
sweeps and for verifying the simulation + storage layer independently:

    python3 cli.py --list
    python3 cli.py --scene scene_epidemic_abm --steps 200 --report
    python3 cli.py --scene scene_traffic_ca --steps 500 --snapshot-interval 5

Calibration (fits model parameters to an observed target curve):

    python3 cli.py --calibrate scene_epidemic_ca \\
        --target infected.csv --metric infected \\
        --param beta=0.05:0.9 --param gamma=0.01:0.5 \\
        --steps 80 --seed 42 --global 16 --max-evals 150

The target file is CSV/TSV (``time,value[,weight]``) or JSON.  Results print to
stdout and are persisted under ``data/calibrations/`` (same as the web UI).
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from backend import calibration, models, report, run_manager, storage, util


def list_scenes() -> None:
    for s in storage.list_scenes():
        print(f"{s['id']:24s} {s['domain']:8s}/{s['model']:3s}  {s['name']}")


def run_one(scene_id: str, steps: int, snapshot_interval: int,
            make_report: bool) -> int:
    scene = storage.load_scene(scene_id)
    if scene is None:
        print(f"error: scene not found: {scene_id}", file=sys.stderr)
        return 1
    scene_obj = models.Scene.from_dict(scene)
    meta = run_manager.manager.create_run(
        scene_obj, snapshot_interval=snapshot_interval)
    print(f"run {meta['id']}: {meta['name']} "
          f"({meta['domain']}/{meta['model']}) seed={meta['seed']}")

    result = run_manager.manager.run_batch(meta["id"], steps, keep_engine=True)
    print(f"finished at step {result['step']}")
    for k, v in result["stats"].items():
        print(f"  {k:16s} {v}")

    if make_report:
        rpt = report.generate_report(meta["id"])
        print("\nsummary:")
        for line in rpt["summary"]:
            print(f"  - {line}")
    return 0


# --------------------------------------------------------------------------- #
# Calibration
# --------------------------------------------------------------------------- #
def _parse_param_spec(text: str) -> dict:
    """``key=lo:hi`` -> {"key", "min", "max"}; bounds optional (use catalog)."""
    if "=" not in text:
        raise ValueError(f"参数规格格式应为 key=下界:上界，收到: {text}")
    key, bounds = text.split("=", 1)
    key = key.strip()
    spec: dict = {"key": key}
    if bounds.strip():
        parts = bounds.split(":")
        if len(parts) != 2:
            raise ValueError(f"参数 {key} 的上下界应为 lo:hi")
        spec["min"] = float(parts[0])
        spec["max"] = float(parts[1])
    return spec


def run_calibration_cli(args: argparse.Namespace) -> int:
    scene = storage.load_scene(args.scene)
    if scene is None:
        print(f"error: scene not found: {args.scene}", file=sys.stderr)
        return 1
    if not os.path.isfile(args.target):
        print(f"error: target file not found: {args.target}", file=sys.stderr)
        return 1
    with open(args.target, "r", encoding="utf-8") as fh:
        raw = fh.read()
    target: object
    if args.target.endswith(".json"):
        target = json.loads(raw)
    else:
        target = raw

    param_defs = [_parse_param_spec(p) for p in args.param]
    request = {
        "name": args.name or "CLI 参数校准",
        "metric": args.metric,
        "target": target,
        "params": param_defs,
        "steps": args.steps,
        "seed": args.seed,
        "replicates": args.replicates,
        "loss": args.loss,
        "n_global": args.n_global,
        "n_local_starts": args.local_starts,
        "max_evals": args.max_evals,
        "detect_outliers": not args.no_outlier_detection,
        "drop_outliers": args.drop_outliers,
        "time_offset": args.time_offset,
        "time_scale": args.time_scale,
    }
    scene_obj = models.Scene.from_dict(scene)
    snapshot = {"id": scene_obj.id, "name": scene_obj.name,
                "domain": scene_obj.domain, "model": scene_obj.model,
                "config": models.resolve_config(scene_obj),
                "interventions": scene_obj.interventions}

    last = {"n": -1}

    def progress(info: dict) -> None:
        n = info["n_evals"]
        # 每 5 次评估刷新一行，避免长任务刷屏
        if n != last["n"] and (n % 5 == 0 or info["phase"] in ("init",)):
            last["n"] = n
            bl = info["best_loss"]
            print(f"\r  评估 {n:>4d} 次（{_phase_label(info['phase'])}），"
                  f"当前最优误差 {bl if bl is None else round(bl, 5)}        ",
                  end="")
            sys.stdout.flush()

    try:
        result = calibration.run_calibration(snapshot, request,
                                             progress=progress)
    except (ValueError, RuntimeError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        return 1
    print()

    cal_id = util.new_id("cal")
    record = {"id": cal_id, **request, "scene_snapshot": snapshot,
              **result, "status": "finished",
              "created_at": util.now_iso(), "updated_at": util.now_iso()}
    storage.save_calibration(record)
    print(f"校准完成（{result['n_evals']} 次评估，"
          f"耗时 {result['elapsed_seconds']}s，已保存 {cal_id}）")
    print(f"  NRMSE = {result['fit']['nrmse']:.5f}"
          f"   RMSE = {result['fit']['rmse']:.4f}"
          f"   R² = {result['fit']['r2']}")
    print("  最优参数:")
    for spec in result["params_spec"]:
        v = result["best_params"][spec["key"]]
        print(f"    {spec['label']} ({spec['key']}) = {v:g}  "
              f"[界 {spec['min']:g}, {spec['max']:g}]")
    if result["plateau_size"] > 1:
        print(f"  注意：{result['plateau_size']} 组参数误差在 0.1% 内（平台），"
              "参数不完全可辨识")
    if result["warnings"]:
        print("  告警:")
        for w in result["warnings"]:
            print(f"    ! {w}")
    return 0


def _phase_label(p: str) -> str:
    return {"init": "初始化", "seed": "起点评估", "lhs": "全局搜索",
            "nm": "局部细化", "pattern": "模式搜索细化",
            "bound_check": "边界诊断"}.get(p, p or "")


def main() -> int:
    p = argparse.ArgumentParser(description="Headless simulation runner / calibration")
    p.add_argument("--list", action="store_true", help="list available scenes")
    p.add_argument("--scene", help="scene id to run / calibrate")
    p.add_argument("--steps", type=int, default=200, help="steps to run")
    p.add_argument("--snapshot-interval", type=int, default=1,
                   help="persist a full snapshot every N steps")
    p.add_argument("--report", action="store_true", help="generate a report")

    cal = p.add_argument_group("calibration (--calibrate)")
    cal.add_argument("--calibrate", action="store_true",
                     help="calibrate parameters of --scene against --target")
    cal.add_argument("--target", help="target curve file (CSV/TSV or JSON)")
    cal.add_argument("--metric", default="infected",
                     help="stats metric to fit (default: infected)")
    cal.add_argument("--param", action="append", default=[],
                     help="key=lo:hi, repeatable; e.g. beta=0.05:0.9")
    cal.add_argument("--seed", type=int, default=12345, help="master seed")
    cal.add_argument("--replicates", type=int, default=1,
                     help="stochastic replicate trajectories per evaluation")
    cal.add_argument("--loss", choices=["nrmse", "mse", "mae", "huber"],
                     default="nrmse")
    cal.add_argument("--global", dest="n_global", type=int, default=12,
                     help="Latin-hypercube global sample count")
    cal.add_argument("--local-starts", type=int, default=3,
                     help="number of local Nelder-Mead starts")
    cal.add_argument("--max-evals", type=int, default=120,
                     help="maximum simulator evaluations")
    cal.add_argument("--no-outlier-detection", action="store_true")
    cal.add_argument("--drop-outliers", action="store_true",
                     help="drop outliers instead of zeroing their weight")
    cal.add_argument("--time-offset", type=float, default=0.0)
    cal.add_argument("--time-scale", type=float, default=1.0)
    cal.add_argument("--name", default="")
    args = p.parse_args()

    storage.ensure_dirs()
    if args.list:
        list_scenes()
        return 0
    if args.calibrate:
        if not args.scene:
            p.error("--calibrate 需要 --scene")
        return run_calibration_cli(args)
    if not args.scene:
        p.print_help()
        return 1
    return run_one(args.scene, args.steps, args.snapshot_interval, args.report)


if __name__ == "__main__":
    sys.exit(main())
