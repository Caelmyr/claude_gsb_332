"""Epidemic — agent-based model (mobile-contact SIR).

A population of agents moves through a 2D toroidal world; infected agents pass
the disease to susceptibles they come within ``radius`` of, and recover with
probability ``γ`` per step.  Movement is either a random walk or a
home-range walk (agents stay near a fixed home point).

Contact detection is the expensive part of a naive agent SIR (O(n²) per step).
This engine bins infected agents into a uniform spatial hash whose cell size
equals the infection radius, then for each susceptible only checks the 3x3
surrounding cells — turning contact tracing into O(n · k).  Interventions are
multiplicative factors on β / γ / radius / speed, mirroring the CA engine so a
policy can be compared across both model families.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Tuple

from .base import Engine


class EpidemicABM(Engine):
    domain = "epidemic"
    model = "abm"

    def defaults(self) -> Dict[str, Any]:
        return {"width": 400, "height": 400, "n": 800, "beta": 0.3,
                "gamma": 0.05, "speed": 2.0, "radius": 6.0,
                "initial_infected": 10, "vaccination_rate": 0.0,
                "movement": "random_walk"}

    # ------------------------------------------------------------------ #
    def _init(self) -> None:
        self.width = float(self.config["width"])
        self.height = float(self.config["height"])
        self._base_beta = float(self.config["beta"])
        self._base_gamma = float(self.config["gamma"])
        self._base_speed = float(self.config["speed"])
        self._base_radius = float(self.config["radius"])
        self._movement = self.config.get("movement", "random_walk")
        self._f_lockdown = 1.0
        self._f_mask = 1.0
        self._f_dist = 1.0
        self._f_cure = 1.0
        self._last_new = 0

        n = int(self.config["n"])
        self.agents: List[Dict[str, Any]] = []
        for i in range(n):
            a = {"id": f"p{i:05d}", "type": "person", "state": "susceptible",
                 "x": self.rng.uniform(0, self.width),
                 "y": self.rng.uniform(0, self.height),
                 "heading": self.rng.uniform(0, 2 * math.pi), "days": 0}
            if self._movement == "home_range":
                a["hx"] = a["x"]
                a["hy"] = a["y"]
                a["hr"] = self.rng.uniform(20, 80)
            self.agents.append(a)

        self.rng.shuffle(self.agents)
        for a in self.agents[:int(self.config["initial_infected"])]:
            a["state"] = "infected"
        vax = float(self.config["vaccination_rate"])
        if vax > 0:
            susc = [a for a in self.agents if a["state"] == "susceptible"]
            self.rng.shuffle(susc)
            for a in susc[:int(len(susc) * vax)]:
                a["state"] = "recovered"

    def individuals(self) -> List[Dict[str, Any]]:
        return self.agents

    def _delta(self, ax: float, ay: float, bx: float, by: float) -> Tuple[float, float]:
        dx = (ax - bx + self.width / 2) % self.width - self.width / 2
        dy = (ay - by + self.height / 2) % self.height - self.height / 2
        return dx, dy

    # ------------------------------------------------------------------ #
    def _move(self, speed: float) -> None:
        w, h = self.width, self.height
        for a in self.agents:
            if self._movement == "home_range":
                ox, oy = self._delta(a["x"], a["y"], a["hx"], a["hy"])
                if math.hypot(ox, oy) > a["hr"]:
                    ux, uy = -ox / a["hr"], -oy / a["hr"]
                else:
                    a["heading"] += self.rng.uniform(-0.6, 0.6)
                    ux, uy = math.cos(a["heading"]), math.sin(a["heading"])
            else:
                a["heading"] += self.rng.uniform(-0.6, 0.6)
                ux, uy = math.cos(a["heading"]), math.sin(a["heading"])
            a["x"] = (a["x"] + ux * speed) % w
            a["y"] = (a["y"] + uy * speed) % h

    def step(self) -> None:
        beta = self._base_beta * self._f_lockdown * self._f_mask
        gamma = self._base_gamma * self._f_cure
        speed = self._base_speed * self._f_lockdown
        radius = self._base_radius * self._f_dist
        agents = self.agents

        self._move(speed)

        # 1. Recovery (current infected only — the infection pass runs after).
        for a in agents:
            if a["state"] == "infected":
                a["days"] += 1
                if self._coin(gamma):
                    a["state"] = "recovered"

        # 2. Infection via spatial-hash contact detection.
        cell = max(radius, 1.0)
        grid: Dict[Tuple[int, int], List[int]] = {}
        for i, a in enumerate(agents):
            if a["state"] == "infected":
                key = (int(a["x"] // cell), int(a["y"] // cell))
                grid.setdefault(key, []).append(i)

        new_inf = 0
        for a in agents:
            if a["state"] != "susceptible":
                continue
            cx, cy = int(a["x"] // cell), int(a["y"] // cell)
            hit = False
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for j in grid.get((cx + dx, cy + dy), ()):
                        inf = agents[j]
                        ox, oy = self._delta(a["x"], a["y"], inf["x"], inf["y"])
                        if math.hypot(ox, oy) < radius:
                            hit = True
                            break
                    if hit:
                        break
                if hit:
                    break
            if hit and self._coin(beta):
                a["state"] = "infected"
                a["days"] = 0
                new_inf += 1

        self._last_new = new_inf
        self.step_count += 1

    # ------------------------------------------------------------------ #
    def stats(self) -> Dict[str, Any]:
        s = i = r = 0
        for a in self.agents:
            if a["state"] == "susceptible":
                s += 1
            elif a["state"] == "infected":
                i += 1
            else:
                r += 1
        n = len(self.agents)
        return {
            "susceptible": s,
            "infected": i,
            "recovered": r,
            "new_infections": self._last_new,
            "prevalence": round(i / n, 4) if n else 0.0,
        }

    def bounds(self) -> Dict[str, float]:
        return {"width": self.width, "height": self.height}

    def palette(self) -> Dict[str, Dict[str, str]]:
        return {
            "susceptible": {"label": "易感", "color": "#3498db"},
            "infected": {"label": "感染", "color": "#e74c3c"},
            "recovered": {"label": "康复", "color": "#95a5a6"},
        }

    # ------------------------------------------------------------------ #
    def apply_intervention(self, itv: Dict[str, Any]) -> Dict[str, Any]:
        t = itv["type"]
        p = itv.get("params", {})
        if t == "vaccinate":
            susc = [a for a in self.agents if a["state"] == "susceptible"]
            self.rng.shuffle(susc)
            k = int(len(susc) * float(p.get("fraction", 0.5)))
            for a in susc[:k]:
                a["state"] = "recovered"
            return {"applied": True, "reason": f"为 {k} 名易感者接种疫苗"}
        if t == "lockdown":
            self._f_lockdown = max(0.0, 1 - float(p.get("scale", 0.5)))
            return {"applied": True, "reason": "实施封锁，降低移动与接触率"}
        if t == "mask":
            self._f_mask = max(0.0, 1 - float(p.get("scale", 0.5)))
            return {"applied": True, "reason": "佩戴口罩，降低感染率"}
        if t == "social_distance":
            self._f_dist = max(0.0, 1 - float(p.get("scale", 0.5)))
            return {"applied": True, "reason": "保持距离，缩小感染半径"}
        if t == "cure":
            self._f_cure = float(p.get("scale", 2.0))
            return {"applied": True, "reason": "特效药提高恢复率"}
        return super().apply_intervention(itv)
