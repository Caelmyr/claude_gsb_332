"""File-system persistence with atomic writes and per-time-step sharding.

Layout (all under :data:`DATA_DIR`)::

    data/
      scenes/<scene_id>.json        # scene definitions (config + interventions)
      runs/<run_id>/
          meta.json                 # run config, status, current step
          series.json               # compact per-step aggregate statistics
          events.json               # applied intervention log
          steps/<nnnnnnnn>.json     # full per-step snapshot (individuals + grid)
          report.json               # generated report
      experiments/<exp_id>.json     # comparison experiments (param groups)
      exports/                      # exported CSV / JSON files

Every write goes through :func:`atomic_write_json`: serialise, write to a temp
file in the same directory, ``fsync``, then ``os.replace``.  ``os.replace`` is
atomic on POSIX, so a crash mid-write can never leave a truncated/corrupt JSON
file, and a concurrent reader always sees either the old or the new complete
document.  This is what makes the sharded time-step storage safe.

The *series* file is the "fast replay" path: it keeps only the aggregate
statistics for every step in one compact array so charts and replay can scan
thousands of steps without opening the heavy per-step snapshots.
"""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any, Dict, List, Optional

DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

_DIRS = ("scenes", "runs", "experiments", "reports", "exports")


def ensure_dirs() -> None:
    """Create the on-disk directory skeleton if it does not exist yet."""
    for name in _DIRS:
        os.makedirs(os.path.join(DATA_DIR, name), exist_ok=True)


# --------------------------------------------------------------------------- #
# Atomic IO primitives
# --------------------------------------------------------------------------- #
def atomic_write_json(path: str, obj: Any) -> None:
    """Atomically write ``obj`` to ``path`` as compact JSON."""
    payload = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
    atomic_write_bytes(path, payload.encode("utf-8"))


def atomic_write_bytes(path: str, data: bytes) -> None:
    """Atomically write ``data`` to ``path`` (temp file + fsync + replace)."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_json(path: str, default: Any = None) -> Any:
    """Read a JSON file, returning ``default`` on missing/corrupt content."""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def delete_file(path: str) -> bool:
    """Remove a file if present; return True when something was deleted."""
    try:
        os.unlink(path)
        return True
    except FileNotFoundError:
        return False


# --------------------------------------------------------------------------- #
# Scenes
# --------------------------------------------------------------------------- #
def scenes_dir() -> str:
    return os.path.join(DATA_DIR, "scenes")


def scene_path(scene_id: str) -> str:
    return os.path.join(scenes_dir(), f"{scene_id}.json")


def save_scene(scene: Dict[str, Any]) -> None:
    atomic_write_json(scene_path(scene["id"]), scene)


def load_scene(scene_id: str) -> Optional[Dict[str, Any]]:
    return read_json(scene_path(scene_id))


def delete_scene(scene_id: str) -> bool:
    return delete_file(scene_path(scene_id))


def list_scenes() -> List[Dict[str, Any]]:
    """All scenes, most recently updated first."""
    out: List[Dict[str, Any]] = []
    for name in os.listdir(scenes_dir()):
        if not name.endswith(".json"):
            continue
        scene = read_json(os.path.join(scenes_dir(), name))
        if scene:
            out.append(scene)
    out.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Runs
# --------------------------------------------------------------------------- #
def runs_dir() -> str:
    return os.path.join(DATA_DIR, "runs")


def run_dir(run_id: str) -> str:
    return os.path.join(runs_dir(), run_id)


def run_meta_path(run_id: str) -> str:
    return os.path.join(run_dir(run_id), "meta.json")


def run_series_path(run_id: str) -> str:
    return os.path.join(run_dir(run_id), "series.json")


def run_events_path(run_id: str) -> str:
    return os.path.join(run_dir(run_id), "events.json")


def run_report_path(run_id: str) -> str:
    return os.path.join(run_dir(run_id), "report.json")


def run_steps_dir(run_id: str) -> str:
    return os.path.join(run_dir(run_id), "steps")


def run_step_path(run_id: str, step: int) -> str:
    return os.path.join(run_steps_dir(run_id), f"{step:08d}.json")


def create_run_dir(run_id: str) -> None:
    os.makedirs(run_steps_dir(run_id), exist_ok=True)


def save_run_meta(meta: Dict[str, Any]) -> None:
    atomic_write_json(run_meta_path(meta["id"]), meta)


def load_run_meta(run_id: str) -> Optional[Dict[str, Any]]:
    return read_json(run_meta_path(run_id))


def delete_run(run_id: str) -> bool:
    import shutil
    path = run_dir(run_id)
    if os.path.isdir(path):
        shutil.rmtree(path)
        return True
    return False


def list_runs() -> List[Dict[str, Any]]:
    """All runs as their meta dicts, most recently updated first."""
    out: List[Dict[str, Any]] = []
    for name in os.listdir(runs_dir()):
        meta = read_json(os.path.join(runs_dir(), name, "meta.json"))
        if meta:
            out.append(meta)
    out.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Per-step snapshots and series
# --------------------------------------------------------------------------- #
def save_step(run_id: str, step: int, snapshot: Dict[str, Any]) -> None:
    """Persist one full time-step snapshot (sharded to its own file)."""
    atomic_write_json(run_step_path(run_id, step), snapshot)


def load_step(run_id: str, step: int) -> Optional[Dict[str, Any]]:
    return read_json(run_step_path(run_id, step))


def list_steps(run_id: str) -> List[int]:
    """Sorted step numbers currently persisted for a run."""
    d = run_steps_dir(run_id)
    if not os.path.isdir(d):
        return []
    steps = []
    for name in os.listdir(d):
        if name.endswith(".json"):
            try:
                steps.append(int(name[:-5]))
            except ValueError:
                continue
    return sorted(steps)


def save_series(run_id: str, series: List[Dict[str, Any]]) -> None:
    atomic_write_json(run_series_path(run_id), series)


def load_series(run_id: str) -> List[Dict[str, Any]]:
    return read_json(run_series_path(run_id), []) or []


def append_series(run_id: str, row: Dict[str, Any]) -> None:
    """Append one series row without rewriting the whole (large) file each time.

    Series files are small relative to snapshots, so a simple read-modify-write
    is acceptable; the write is still atomic.
    """
    series = load_series(run_id)
    series.append(row)
    save_series(run_id, series)


def save_events(run_id: str, events: List[Dict[str, Any]]) -> None:
    atomic_write_json(run_events_path(run_id), events)


def load_events(run_id: str) -> List[Dict[str, Any]]:
    return read_json(run_events_path(run_id), []) or []


def save_report(run_id: str, report: Dict[str, Any]) -> None:
    atomic_write_json(run_report_path(run_id), report)


def load_report(run_id: str) -> Optional[Dict[str, Any]]:
    return read_json(run_report_path(run_id))


# --------------------------------------------------------------------------- #
# Experiments (comparison of several parameter groups)
# --------------------------------------------------------------------------- #
def experiments_dir() -> str:
    return os.path.join(DATA_DIR, "experiments")


def experiment_path(exp_id: str) -> str:
    return os.path.join(experiments_dir(), f"{exp_id}.json")


def save_experiment(exp: Dict[str, Any]) -> None:
    atomic_write_json(experiment_path(exp["id"]), exp)


def load_experiment(exp_id: str) -> Optional[Dict[str, Any]]:
    return read_json(experiment_path(exp_id))


def delete_experiment(exp_id: str) -> bool:
    return delete_file(experiment_path(exp_id))


def list_experiments() -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for name in os.listdir(experiments_dir()):
        if not name.endswith(".json"):
            continue
        exp = read_json(os.path.join(experiments_dir(), name))
        if exp:
            out.append(exp)
    out.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    return out


# --------------------------------------------------------------------------- #
# Reports / exports directories
# --------------------------------------------------------------------------- #
def reports_dir() -> str:
    return os.path.join(DATA_DIR, "reports")


def exports_dir() -> str:
    return os.path.join(DATA_DIR, "exports")
