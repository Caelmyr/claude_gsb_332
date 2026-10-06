"""Headless command-line batch runner.

Run a scene from the terminal without the web UI — useful for quick parameter
sweeps and for verifying the simulation + storage layer independently:

    python3 cli.py --list
    python3 cli.py --scene scene_epidemic_abm --steps 200 --report
    python3 cli.py --scene scene_traffic_ca --steps 500 --snapshot-interval 5
"""

from __future__ import annotations

import argparse
import sys

from backend import models, report, run_manager, storage, util


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


def main() -> int:
    p = argparse.ArgumentParser(description="Headless simulation batch runner")
    p.add_argument("--list", action="store_true", help="list available scenes")
    p.add_argument("--scene", help="scene id to run")
    p.add_argument("--steps", type=int, default=200, help="steps to run")
    p.add_argument("--snapshot-interval", type=int, default=1,
                   help="persist a full snapshot every N steps")
    p.add_argument("--report", action="store_true", help="generate a report")
    args = p.parse_args()

    storage.ensure_dirs()
    if args.list:
        list_scenes()
        return 0
    if not args.scene:
        p.print_help()
        return 1
    return run_one(args.scene, args.steps, args.snapshot_interval, args.report)


if __name__ == "__main__":
    sys.exit(main())
