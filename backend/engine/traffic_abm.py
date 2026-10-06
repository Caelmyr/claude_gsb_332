"""Traffic — agent-based model (Intelligent Driver Model on a ring road).

Every vehicle is an independent agent with position ``x`` along a periodic
road of length ``road_length``.  Each step applies the IDM acceleration rule
against the vehicle directly ahead, then integrates position with a fixed
``dt``.  Because the road is periodic, the "ahead" gap wraps around the ring.

The IDM acceleration is::

    s*  = s0 + max(0, v·T + v·Δv / (2√(a·b)))
    a_idm = a · (1 - (v/v0)⁴ - (s*/gap)²)

which yields a single, smooth car-following law that reproduces free flow,
congestion and stop-and-go waves without switching regimes.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .base import Engine


class TrafficABM(Engine):
    domain = "traffic"
    model = "abm"

    def defaults(self) -> Dict[str, Any]:
        return {"road_length": 1000.0, "n": 40, "v0": 30.0, "T": 1.5,
                "a": 1.5, "b": 2.0, "s0": 2.0, "dt": 0.5, "length": 5.0}

    # ------------------------------------------------------------------ #
    def _init(self) -> None:
        L = float(self.config["road_length"])
        n = int(self.config["n"])
        veh_len = float(self.config["length"])
        spacing = L / n if n else L
        vehicles: List[Dict[str, Any]] = []
        for i in range(n):
            x = (i * spacing + self.rng.uniform(-0.3 * spacing, 0.3 * spacing)) % L
            vehicles.append({"id": f"v{i:04d}", "type": "vehicle",
                             "state": "stopped", "x": round(x, 2), "y": 0.0,
                             "v": 0.0})
        vehicles.sort(key=lambda v: v["x"])
        self._individuals = vehicles
        self._veh_len = veh_len
        self._base = dict(self.config)  # remember original params for interventions
        self._slow_segments: List[Tuple[float, float]] = []
        self._last_flow = 0

    # ------------------------------------------------------------------ #
    def step(self) -> None:
        L = float(self.config["road_length"])
        v0 = float(self.config["v0"])
        T = float(self.config["T"])
        a = float(self.config["a"])
        b = float(self.config["b"])
        s0 = float(self.config["s0"])
        dt = float(self.config["dt"])
        vehicles = self._individuals
        n = len(vehicles)
        if n == 0:
            self.step_count += 1
            return

        sqrt_ab = (a * b) ** 0.5

        # --- acceleration (IDM) ---------------------------------------- #
        for i, v in enumerate(vehicles):
            ahead = vehicles[(i + 1) % n]
            gap = (ahead["x"] - v["x"] - self._veh_len) % L
            if gap < 0:
                gap += L
            dv = v["v"] - ahead["v"]
            s_star = s0 + max(0.0, v["v"] * T + v["v"] * dv / (2 * sqrt_ab))
            accel = a * (1 - (v["v"] / v0) ** 4 - (s_star / max(gap, 1e-6)) ** 2)
            v["v"] = max(0.0, v["v"] + accel * dt)
            v["v"] = min(v["v"], v0)

        # --- movement + flow detection --------------------------------- #
        flow = 0
        for v in vehicles:
            old = v["x"]
            v["x"] = (old + v["v"] * dt) % L
            if v["x"] < old:
                flow += 1  # crossed the detector at position 0
            v["state"] = "moving" if v["v"] > 0.5 else "stopped"

        self._last_flow = flow
        self.step_count += 1

    # ------------------------------------------------------------------ #
    def stats(self) -> Dict[str, Any]:
        n = len(self._individuals)
        if n == 0:
            return {"mean_speed": 0, "flow": 0, "density": 0,
                    "stopped": 0, "speed_std": 0}
        speeds = [v["v"] for v in self._individuals]
        mean = sum(speeds) / n
        var = sum((s - mean) ** 2 for s in speeds) / n
        L = float(self.config["road_length"])
        return {
            "mean_speed": round(mean, 3),
            "flow": self._last_flow,
            "density": round(n * self._veh_len / L, 4),
            "stopped": round(sum(1 for s in speeds if s < 0.5) / n, 4),
            "speed_std": round(var ** 0.5, 3),
        }

    def bounds(self) -> Dict[str, float]:
        return {"width": float(self.config["road_length"]), "height": 60.0}

    def palette(self) -> Dict[str, Dict[str, str]]:
        return {
            "moving": {"label": "行驶", "color": "#2ecc71"},
            "stopped": {"label": "停车", "color": "#e74c3c"},
        }

    # ------------------------------------------------------------------ #
    def apply_intervention(self, itv: Dict[str, Any]) -> Dict[str, Any]:
        t = itv["type"]
        p = itv.get("params", {})
        if t == "speed_limit":
            self.config["v0"] = float(p.get("vmax", self.config["v0"]))
            return {"applied": True, "reason": f"限速设为 {self.config['v0']}"}
        if t == "slowdown":
            # Interpret the 0..1 slider as extra cautious headway.
            scale = float(p.get("p_slow", 0.0))
            self.config["T"] = self._base["T"] * (1 + 3 * scale)
            return {"applied": True, "reason": f"车头时距提高至 {round(self.config['T'], 2)}s"}
        if t == "incident":
            s = float(p.get("start", 0))
            e = float(p.get("end", s + 100))
            self._slow_segments.append((s, e))
            return {"applied": True, "reason": f"在 [{s},{e}) 米设置事故区"}
        if t == "clear_incident":
            self._slow_segments.clear()
            return {"applied": True, "reason": "已清除所有事故区"}
        return super().apply_intervention(itv)
