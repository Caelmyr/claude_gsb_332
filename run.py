"""Start the complex-systems simulation platform.

The platform serves both the frontend (10 HTML pages under ``frontend/``) and
the JSON REST API (Flask) from a single process.  It models three domains —
traffic, ecology and epidemic — each with a cellular-automaton and an
agent-based simulation engine, and persists every time step to sharded JSON.

Usage:
    python3 run.py                      # 127.0.0.1:5000
    python3 run.py --port 8080 --seed   # custom port + example scenes
"""

from __future__ import annotations

import argparse

from backend import seed
from backend.app import app


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the simulation platform")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5000)
    parser.add_argument("--seed", action="store_true",
                        help="create example scenes on startup")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    if args.seed:
        scenes = seed.seed_all()
        if scenes:
            print(f"seeded scenes: {len(scenes)}")
        demo = seed.seed_demo_data()
        if demo.get("skipped"):
            print("demo runs already present — skipped")
        else:
            print(f"seeded demo runs: {demo['runs']}, "
                  f"experiments: {demo['experiments']}")

    print(f"serving on http://{args.host}:{args.port}")
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)


if __name__ == "__main__":
    main()
