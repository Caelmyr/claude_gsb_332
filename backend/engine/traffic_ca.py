"""Traffic — cellular automaton (Nagel-Schreckenberg multi-lane ring highway).

Each lane is a ring of ``length`` discrete cells.  Vehicles follow the classic
four-step NS update (accelerate -> brake -> randomise -> move) plus an optional
symmetric two-lane-changing rule that lets a blocked vehicle move into a
neighbouring lane when it has more room there.  Interventions can lower the
global speed limit, raise the slowdown probability, or drop a local "incident"
speed zone onto the ring to model congestion.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

from .base import Engine


class TrafficCA(Engine):
    domain = "traffic"
    model = "ca"

    def defaults(self) -> Dict[str, Any]:
        return {"lanes": 3, "length": 80, "vmax": 5, "p_slow": 0.15,
                "density": 0.2, "lane_change": True}

    # ------------------------------------------------------------------ #
    def _init(self) -> None:
        self.lanes = int(self.config["lanes"])
        self.length = int(self.config["length"])
        density = float(self.config["density"])
        n = min(int(self.lanes * self.length * density),
                self.lanes * self.length)
        cells = self.rng.sample(range(self.lanes * self.length), n)
        self._individuals = [
            {"id": f"v{i:04d}", "type": "vehicle", "state": "stopped",
             "x": c % self.length, "y": c // self.length, "v": 0}
            for i, c in enumerate(cells)
        ]
        self._slow_zones: List[Tuple[int, int]] = []
        self._last_flow = 0

    # ------------------------------------------------------------------ #
    # Geometry helpers
    # ------------------------------------------------------------------ #
    def _occupancy(self) -> List[List[int]]:
        """Return ``occ[lane][pos]`` = vehicle index or -1."""
        occ = [[-1] * self.length for _ in range(self.lanes)]
        for i, v in enumerate(self._individuals):
            occ[v["y"]][v["x"]] = i
        return occ

    def _gap(self, occ: List[List[int]], lane: int, pos: int) -> int:
        """Empty cells ahead of ``pos`` in ``lane`` before the next vehicle."""
        row = occ[lane]
        for d in range(1, self.length):
            if row[(pos + d) % self.length] != -1:
                return d - 1
        return self.length - 1

    def _gap_behind(self, occ: List[List[int]], lane: int, pos: int) -> int:
        """Empty cells behind ``pos`` in ``lane`` before the previous vehicle."""
        row = occ[lane]
        for d in range(1, self.length):
            if row[(pos - d) % self.length] != -1:
                return d - 1
        return self.length - 1

    def _local_cap(self, pos: int) -> int:
        cap = int(self.config["vmax"])
        for s, e in self._slow_zones:
            if s <= pos < e:
                cap = min(cap, 1)
        return cap

    # ------------------------------------------------------------------ #
    def step(self) -> None:
        lanes, length = self.lanes, self.length
        vmax = int(self.config["vmax"])
        p_slow = float(self.config["p_slow"])
        lc = bool(self.config["lane_change"])
        vehicles = self._individuals
        occ = self._occupancy()

        # --- optional lane changing ----------------------------------- #
        if lc and lanes > 1:
            for i, v in enumerate(vehicles):
                lane, pos = v["y"], v["x"]
                gap = self._gap(occ, lane, pos)
                if gap >= vmax:
                    continue  # not blocked, stay put
                for dl in (1, -1):
                    nl = lane + dl
                    if not (0 <= nl < lanes):
                        continue
                    if occ[nl][pos] != -1:
                        continue  # target cell occupied
                    ahead = self._gap(occ, nl, pos)
                    behind = self._gap_behind(occ, nl, pos)
                    if ahead > gap and behind >= 1 and self._coin(0.5):
                        occ[lane][pos] = -1
                        occ[nl][pos] = i
                        v["y"] = nl
                        break

        # --- NS update, lane by lane, front-to-back -------------------- #
        flow = 0
        for lane in range(lanes):
            idxs = sorted((idx for idx in occ[lane] if idx != -1),
                          key=lambda i: -vehicles[i]["x"])
            for i in idxs:
                v = vehicles[i]
                pos, vv = v["x"], v["v"]
                vv = min(vv + 1, self._local_cap(pos))
                vv = min(vv, self._gap(occ, lane, pos))
                if vv > 0 and self._coin(p_slow):
                    vv -= 1
                v["v"] = vv
                newpos = (pos + vv) % length
                if newpos < pos:
                    flow += 1  # crossed the wrap-around detector
                occ[lane][pos] = -1
                occ[lane][newpos] = i
                v["x"] = newpos
                v["state"] = "moving" if vv > 0 else "stopped"

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
        return {
            "mean_speed": round(mean, 3),
            "flow": self._last_flow,
            "density": round(n / (self.lanes * self.length), 4),
            "stopped": round(sum(1 for s in speeds if s == 0) / n, 4),
            "speed_std": round(var ** 0.5, 3),
        }

    def bounds(self) -> Dict[str, float]:
        return {"width": float(self.length), "height": float(self.lanes)}

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
            self.config["vmax"] = int(p.get("vmax", self.config["vmax"]))
            return {"applied": True, "reason": f"限速设为 {self.config['vmax']}"}
        if t == "slowdown":
            self.config["p_slow"] = float(p.get("p_slow", self.config["p_slow"]))
            return {"applied": True, "reason": f"随机减速概率设为 {self.config['p_slow']}"}
        if t == "incident":
            s = int(p.get("start", 0))
            e = int(p.get("end", s + 10))
            self._slow_zones.append((s, e))
            return {"applied": True, "reason": f"在 [{s},{e}) 设置事故拥堵区"}
        if t == "clear_incident":
            self._slow_zones.clear()
            return {"applied": True, "reason": "已清除所有事故拥堵区"}
        return super().apply_intervention(itv)
