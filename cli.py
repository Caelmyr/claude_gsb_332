"""Headless command-line runner.

Run a scene from the terminal without the web UI — useful for quick parameter
sweeps and for verifying the simulation + storage layer independently:

    python3 cli.py --list
    python3 cli.py --scene scene_epidemic_abm --steps 200 --report
    python3 cli.py --scene scene_traffic_ca --steps 500 --snapshot-interval 5

Parameter calibration against an observed target curve:

    python3 cli.py calibrate --domain epidemic --model ca --metric infected \\
        --target cases.csv --param beta:0.05:0.8 --param gamma:0.01:0.4 \\
        --steps 100 --seed 12345 --json result.json

Target files are either CSV (first two columns = time, value; a header row is
auto-detected) or JSON ({"points": [[t, y], ...]} / list of {"t","y"}).
The fit is fully deterministic for a given --seed; --json stores the complete
record (spec, every evaluation, overlay curve) for reproducibility.
"""

from __future__ import annotations

import argparse
import json
import sys

from backend import models, report, run_manager, storage, util
from backend.calibration import run_calibration
from backend.calibration.calibrator import CalibrationSpec
from backend.calibration.target import parse_target, parse_target_csv, prepare_target


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
def _load_target(path: str):
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read().strip()
    if path.lower().endswith(".csv"):
        return parse_target_csv(text)
    if path.lower().endswith(".json"):
        return parse_target(json.loads(text))
    # Auto-detect: JSON starts with '{' or '['.
    if text[:1] in ("{", "["):
        return parse_target(json.loads(text))
    return parse_target_csv(text)


def _parse_param(spec: str) -> dict:
    parts = spec.split(":")
    if len(parts) != 3:
        raise argparse.ArgumentTypeError(
            f"参数格式应为 name:low:high，收到: {spec!r}")
    name, lo, hi = parts
    try:
        return {"name": name.strip(), "low": float(lo), "high": float(hi)}
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"参数 {name!r} 上下界不是数字") from exc


def _parse_kv(items):
    out = {}
    for item in items or []:
        if ":" not in item:
            raise argparse.ArgumentTypeError(f"配置格式 key:value，收到: {item!r}")
        k, v = item.split(":", 1)
        v = v.strip()
        try:
            v_cast = int(v)
        except ValueError:
            try:
                v_cast = float(v)
            except ValueError:
                v_cast = v
        out[k.strip()] = v_cast
    return out


def run_calibrate(args: argparse.Namespace) -> int:
    raw = _load_target(args.target)
    target = prepare_target(raw, outlier_mode=args.outlier_mode)
    print(f"目标曲线: {target.summary()['n_kept']} 个有效点 "
          f"(t={target.t_min:g}..{target.t_max:g}, "
          f"y={target.y_min:g}..{target.y_max:g})")
    for w in target.warnings:
        print(f"  ! {w}")

    spec = CalibrationSpec(
        domain=args.domain, model=args.model, metric=args.metric,
        target=target, params=args.param,
        base_config=_parse_kv(args.config),
        steps=args.steps, seed=args.seed,
        n_replicates=args.replicates,
        confirm_replicates=args.confirm_replicates,
        n_refine=args.refine_budget,
        loss_kind=args.loss, time_per_step=args.time_per_step,
        extrapolate=args.extrapolate, outlier_mode=args.outlier_mode,
        name=args.name)
    errors = spec.validate()
    if errors:
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
        return 2

    last_phase = [""]

    def on_progress(p):
        if p["phase"] != last_phase[0]:
            last_phase[0] = p["phase"]
            labels = {"design": "全局采样", "refine": "局部寻优",
                      "confirm": "独立复核", "done": "完成"}
            print(f"  · 阶段: {labels.get(p['phase'], p['phase'])}")

    result = run_calibration(spec, on_progress=on_progress, result_id=util.new_id("cal"))
    if result.status != "finished":
        print(f"error: {result.error}", file=sys.stderr)
        return 1

    print("\n最优参数组合:")
    for k, v in result.best_params.items():
        print(f"  {k:20s} = {v}")
    print("\n拟合误差（独立复核种子集）:")
    for k, agg in result.confirm_error.items():
        m = agg["mean"]
        ms = f"{m:.5g}" if isinstance(m, float) and m == m else "N/A（近零点过多）"
        extra = f"  [基于 {agg.get('n_points', '?')} 个非零点]" if k == "mape" else ""
        std = agg.get("std", 0)
        ss = f"{std:.3g}" if isinstance(std, float) and std == std else "—"
        print(f"  {k:8s} {ms}  (±{ss}){extra}")
    print("\n候选排名（按复核误差）:")
    for i, f in enumerate(result.ranking, 1):
        mark = " *" if f.get("winner") else ""
        print(f"  {i}. {f['params']}  train={f['train_loss']:.5g} "
              f"confirm={f['confirm_mean']:.5g}±{f['confirm_se']:.3g}{mark}")
    if result.warnings:
        print("\n诊断与提醒:")
        for w in result.warnings:
            print(f"  ! {w}")

    if args.json:
        with open(args.json, "w", encoding="utf-8") as fh:
            json.dump(result.to_dict(), fh, ensure_ascii=False, indent=2)
        print(f"\n完整结果已写入 {args.json}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Headless simulation batch runner")
    parser.add_argument("--list", action="store_true", help="list available scenes")
    parser.add_argument("--scene", help="scene id to run")
    parser.add_argument("--steps", type=int, default=200, help="steps to run")
    parser.add_argument("--snapshot-interval", type=int, default=1,
                        help="persist a full snapshot every N steps")
    parser.add_argument("--report", action="store_true", help="generate a report")

    sub = parser.add_subparsers(dest="command")
    cal = sub.add_parser("calibrate", help="calibrate parameters to a target curve")
    cal.add_argument("--domain", required=True)
    cal.add_argument("--model", required=True)
    cal.add_argument("--metric", default="infected")
    cal.add_argument("--target", required=True, help="CSV/JSON target curve file")
    cal.add_argument("--param", action="append", type=_parse_param, default=[],
                     help="待校准参数 name:low:high，可重复")
    cal.add_argument("--config", action="append", default=[],
                     help="固定参数覆盖 key:value，可重复")
    cal.add_argument("--steps", type=int, default=150)
    cal.add_argument("--seed", type=int, default=12345)
    cal.add_argument("--replicates", type=int, default=3,
                     help="寻优阶段每组参数的随机重复次数")
    cal.add_argument("--confirm-replicates", type=int, default=6,
                     help="复核阶段独立种子数")
    cal.add_argument("--refine-budget", type=int, default=90,
                     help="局部寻优的评估次数预算")
    cal.add_argument("--loss", choices=["nrmse", "rmse", "mae", "mape", "log_rmse"],
                     default="nrmse")
    cal.add_argument("--time-per-step", type=float, default=1.0)
    cal.add_argument("--extrapolate", choices=["skip", "clip"], default="skip")
    cal.add_argument("--outlier-mode", choices=["keep", "flag", "winsor"],
                     default="flag")
    cal.add_argument("--name", default="参数校准")
    cal.add_argument("--json", help="把完整结果（含评估轨迹与叠加曲线）写入文件")

    args = parser.parse_args()

    storage.ensure_dirs()
    if args.command == "calibrate":
        return run_calibrate(args)
    if args.list:
        list_scenes()
        return 0
    if not args.scene:
        parser.print_help()
        return 1
    return run_one(args.scene, args.steps, args.snapshot_interval, args.report)


if __name__ == "__main__":
    sys.exit(main())
