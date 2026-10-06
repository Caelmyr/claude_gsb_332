"""Ecology — agent-based model (flocking "boids" with predators).

Birds obey Reynolds' three steering rules (alignment, cohesion, separation) and
additionally flee nearby predators; predators chase the nearest bird and eat it
on contact.  The world is a torus (positions wrap), and distance/direction are
computed with wrap-aware deltas.

Performance: naive flocking is O(n²) per step.  To keep it interactive at scale
this engine bins birds into a uniform spatial hash (cell size = perception
radius) and only inspects the 3x3 surrounding cells, so the cost drops to
O(n · k) where k is the number of local neighbours — the same technique the
epidemic ABM uses for contact tracing.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from .base import Engine


def _clamp(vx: float, vy: float, maxv: float) -> Tuple[float, float]:
    sp = math.hypot(vx, vy)
    if sp > maxv:
        return vx / sp * maxv, vy / sp * maxv
    return vx, vy


class EcologyABM(Engine):
    domain = "ecology"
    model = "abm"

    def defaults(self) -> Dict[str, Any]:
        return {"width": 400, "height": 400, "n_boids": 150, "n_predators": 3,
                "max_speed": 4.0, "pred_speed": 4.5, "perception": 40.0,
                "separation": 30.0, "flee_radius": 60.0}

    # ------------------------------------------------------------------ #
    def _init(self) -> None:
        self.width = float(self.config["width"])
        self.height = float(self.config["height"])
        self.perception = float(self.config["perception"])
        self.boids: List[Dict[str, Any]] = []
        self.predators: List[Dict[str, Any]] = []
        for i in range(int(self.config["n_boids"])):
            self.boids.append(self._agent("boid", f"b{i:04d}"))
        for i in range(int(self.config["n_predators"])):
            self.predators.append(self._agent("predator", f"p{i:04d}"))
        self._eaten = 0
        self._last_mean_neighbors = 0.0

    def _agent(self, typ: str, aid: str) -> Dict[str, Any]:
        ang = self.rng.uniform(0, 2 * math.pi)
        sp = self.config["max_speed"] if typ == "boid" else self.config["pred_speed"]
        return {"id": aid, "type": typ, "state": typ,
                "x": self.rng.uniform(0, self.width),
                "y": self.rng.uniform(0, self.height),
                "vx": math.cos(ang) * sp * 0.5,
                "vy": math.sin(ang) * sp * 0.5}

    def individuals(self) -> List[Dict[str, Any]]:
        return self.boids + self.predators

    def _delta(self, ax: float, ay: float, bx: float, by: float) -> Tuple[float, float]:
        dx = (ax - bx + self.width / 2) % self.width - self.width / 2
        dy = (ay - by + self.height / 2) % self.height - self.height / 2
        return dx, dy

    # ------------------------------------------------------------------ #
    # Spatial hash
    # ------------------------------------------------------------------ #
    def _build_hash(self) -> Dict[Tuple[int, int], List[int]]:
        cs = max(self.perception, 1.0)
        grid: Dict[Tuple[int, int], List[int]] = {}
        for i, a in enumerate(self.boids):
            key = (int(a["x"] // cs), int(a["y"] // cs))
            grid.setdefault(key, []).append(i)
        return grid

    def _nearby(self, grid: Dict[Tuple[int, int], List[int]], x: float, y: float,
                radius: float, skip: int) -> List[Tuple[int, float]]:
        cs = max(self.perception, 1.0)
        cx, cy = int(x // cs), int(y // cs)
        out: List[Tuple[int, float]] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for i in grid.get((cx + dx, cy + dy), ()):
                    if i == skip:
                        continue
                    b = self.boids[i]
                    ox, oy = self._delta(b["x"], b["y"], x, y)
                    d = math.hypot(ox, oy)
                    if d < radius:
                        out.append((i, d))
        return out

    # ------------------------------------------------------------------ #
    def step(self) -> None:
        w, h = self.width, self.height
        maxv = float(self.config["max_speed"])
        pv = float(self.config["pred_speed"])
        perc = float(self.config["perception"])
        sep = float(self.config["separation"])
        flee = float(self.config["flee_radius"])
        boids = self.boids
        preds = self.predators
        grid = self._build_hash()

        total_neighbors = 0
        for i, b in enumerate(boids):
            neigh = self._nearby(grid, b["x"], b["y"], perc, skip=i)
            total_neighbors += len(neigh)
            if neigh:
                n = len(neigh)
                ax = sum(boids[j]["vx"] for j, _ in neigh) / n
                ay = sum(boids[j]["vy"] for j, _ in neigh) / n
                cx = sum(boids[j]["x"] for j, _ in neigh) / n
                cy = sum(boids[j]["y"] for j, _ in neigh) / n
                cohx, cohy = self._delta(cx, cy, b["x"], b["y"])
                cl = math.hypot(cohx, cohy)
                if cl > 1e-9:
                    cohx, cohy = cohx / cl, cohy / cl
                sx = sy = 0.0
                cnt = 0
                for j, d in neigh:
                    if 0 < d < sep:
                        ox, oy = self._delta(b["x"], b["y"], boids[j]["x"], boids[j]["y"])
                        ol = math.hypot(ox, oy) or 1.0
                        sx += ox / ol
                        sy += oy / ol
                        cnt += 1
                if cnt:
                    sx /= cnt
                    sy /= cnt
            else:
                ax, ay = b["vx"], b["vy"]
                cohx = cohy = sx = sy = 0.0

            # Flee predators.
            fx = fy = 0.0
            cnt = 0
            for p in preds:
                ox, oy = self._delta(b["x"], b["y"], p["x"], p["y"])
                d = math.hypot(ox, oy)
                if 0 < d < flee:
                    fx += ox / d
                    fy += oy / d
                    cnt += 1
            if cnt:
                fx /= cnt
                fy /= cnt

            dvx = 1.0 * ax + 0.05 * maxv * cohx + 1.0 * maxv * sx + 3.0 * maxv * fx
            dvy = 1.0 * ay + 0.05 * maxv * cohy + 1.0 * maxv * sy + 3.0 * maxv * fy
            dvx, dvy = _clamp(dvx, dvy, maxv)
            b["vx"] += 0.15 * (dvx - b["vx"])
            b["vy"] += 0.15 * (dvy - b["vy"])
            b["vx"], b["vy"] = _clamp(b["vx"], b["vy"], maxv)
            b["x"] = (b["x"] + b["vx"]) % w
            b["y"] = (b["y"] + b["vy"]) % h

        # Predators chase the nearest bird (wrap-aware shortest path).
        eaten: set = set()
        for p in preds:
            best = -1
            bestd = float("inf")
            for j, b in enumerate(boids):
                ox, oy = self._delta(b["x"], b["y"], p["x"], p["y"])
                d = math.hypot(ox, oy)
                if d < bestd:
                    bestd, best = d, j
            if best < 0:
                continue
            b = boids[best]
            ox, oy = self._delta(b["x"], b["y"], p["x"], p["y"])
            d = math.hypot(ox, oy)
            if d < 4.0:
                eaten.add(best)
                self._eaten += 1
            else:
                p["x"] = (p["x"] + ox / d * pv) % w
                p["y"] = (p["y"] + oy / d * pv) % h

        if eaten:
            self.boids = [b for j, b in enumerate(boids) if j not in eaten]

        self._last_mean_neighbors = total_neighbors / len(boids) if boids else 0.0
        self.step_count += 1

    # ------------------------------------------------------------------ #
    def stats(self) -> Dict[str, Any]:
        n = len(self.boids)
        speed = (sum(math.hypot(b["vx"], b["vy"]) for b in self.boids) / n
                 if n else 0.0)
        return {
            "boids": n,
            "predators": len(self.predators),
            "eaten": self._eaten,
            "mean_speed": round(speed, 3),
            "mean_neighbors": round(self._last_mean_neighbors, 3),
        }

    def bounds(self) -> Dict[str, float]:
        return {"width": self.width, "height": self.height}

    def palette(self) -> Dict[str, Dict[str, str]]:
        return {
            "boid": {"label": "鸟", "color": "#3498db"},
            "predator": {"label": "捕食者", "color": "#e74c3c"},
        }

    # ------------------------------------------------------------------ #
    def apply_intervention(self, itv: Dict[str, Any]) -> Dict[str, Any]:
        t = itv["type"]
        p = itv.get("params", {})
        if t == "release_predators":
            count = int(p.get("count", 5))
            for _ in range(count):
                self.predators.append(self._agent("predator", f"p{len(self.predators):04d}"))
            return {"applied": True, "reason": f"投放了 {count} 只捕食者"}
        if t == "cull_foxes":
            k = self._cull_predators(float(p.get("fraction", 0.5)))
            return {"applied": True, "reason": f"移除了 {k} 只捕食者"}
        if t == "cull_rabbits":
            k = self._cull_boids(float(p.get("fraction", 0.5)))
            return {"applied": True, "reason": f"移除了 {k} 只鸟"}
        if t == "plant_grass":
            return {"applied": False, "reason": "鸟群模型没有草地，无法种草"}
        return super().apply_intervention(itv)

    def _cull_predators(self, fraction: float) -> int:
        self.rng.shuffle(self.predators)
        k = int(len(self.predators) * fraction)
        self.predators = self.predators[k:]
        return k

    def _cull_boids(self, fraction: float) -> int:
        self.rng.shuffle(self.boids)
        k = int(len(self.boids) * fraction)
        self.boids = self.boids[k:]
        return k
