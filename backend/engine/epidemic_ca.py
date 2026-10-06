"""Epidemic — cellular automaton (synchronous SIR on a lattice).

Each lattice cell is a person in one of three states (susceptible / infected /
recovered).  Every step performs a *synchronous* update: a susceptible cell
becomes infected with probability ``1 - (1-β)^k`` where ``k`` is the number of
infected Moore neighbours, and an infected cell recovers with probability
``γ``.  Synchronous updates are what give the classic CA epidemic its
travelling wavefronts.

Interventions (vaccination, lockdown, masks, social distancing, cure) are
modelled as multiplicative factors on the effective β and γ, so they compose
cleanly and are idempotent per intervention type.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .base import Engine

_STATE_LABEL = {0: "susceptible", 1: "infected", 2: "recovered"}


class EpidemicCA(Engine):
    domain = "epidemic"
    model = "ca"

    def defaults(self) -> Dict[str, Any]:
        return {"width": 50, "height": 50, "beta": 0.4, "gamma": 0.1,
                "initial_infected": 5, "vaccination_rate": 0.0}

    # ------------------------------------------------------------------ #
    def _init(self) -> None:
        self.width = int(self.config["width"])
        self.height = int(self.config["height"])
        w, h = self.width, self.height
        self.state: List[List[int]] = [[0] * w for _ in range(h)]
        self.days: List[List[int]] = [[0] * w for _ in range(h)]
        self._base_beta = float(self.config["beta"])
        self._base_gamma = float(self.config["gamma"])
        # Intervention factors (1.0 = no effect).
        self._f_lockdown = 1.0
        self._f_mask = 1.0
        self._f_dist = 1.0
        self._f_cure = 1.0
        self._last_new = 0

        cells = list(range(w * h))
        self.rng.shuffle(cells)
        for c in cells[:int(self.config["initial_infected"])]:
            y, x = divmod(c, w)
            self.state[y][x] = 1
        vax = float(self.config["vaccination_rate"])
        if vax > 0:
            rest = cells[int(self.config["initial_infected"]):]
            k = int(len(rest) * vax)
            for c in rest[:k]:
                y, x = divmod(c, w)
                self.state[y][x] = 2

    # ------------------------------------------------------------------ #
    def _infected_neighbors(self, x: int, y: int) -> int:
        w, h = self.width, self.height
        state = self.state
        k = 0
        for dy in (-1, 0, 1):
            ny = y + dy
            if not (0 <= ny < h):
                continue
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx = x + dx
                if 0 <= nx < w and state[ny][nx] == 1:
                    k += 1
        return k

    def step(self) -> None:
        w, h = self.width, self.height
        beta = self._base_beta * self._f_lockdown * self._f_mask * self._f_dist
        gamma = self._base_gamma * self._f_cure
        state = self.state
        days = self.days
        nxt = [row[:] for row in state]
        new_inf = 0
        for y in range(h):
            for x in range(w):
                s = state[y][x]
                if s == 1:
                    if self._coin(gamma):
                        nxt[y][x] = 2
                    else:
                        days[y][x] += 1
                elif s == 0:
                    k = self._infected_neighbors(x, y)
                    if k > 0 and self._coin(1 - (1 - beta) ** k):
                        nxt[y][x] = 1
                        days[y][x] = 0
                        new_inf += 1
        self.state = nxt
        self._last_new = new_inf
        self.step_count += 1

    # ------------------------------------------------------------------ #
    def individuals(self) -> List[Dict[str, Any]]:
        w, h = self.width, self.height
        out: List[Dict[str, Any]] = []
        for y in range(h):
            base = y * w
            for x in range(w):
                s = self.state[y][x]
                out.append({"id": f"p{base + x:05d}", "type": "person",
                            "state": _STATE_LABEL[s], "x": x, "y": y,
                            "days": self.days[y][x]})
        return out

    def stats(self) -> Dict[str, Any]:
        w, h = self.width, self.height
        s = i = r = 0
        for y in range(h):
            row = self.state[y]
            s += row.count(0)
            i += row.count(1)
            r += row.count(2)
        n = w * h
        return {
            "susceptible": s,
            "infected": i,
            "recovered": r,
            "new_infections": self._last_new,
            "prevalence": round(i / n, 4),
        }

    def bounds(self) -> Dict[str, float]:
        return {"width": float(self.width), "height": float(self.height)}

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
            k = self._vaccinate(float(p.get("fraction", 0.5)))
            return {"applied": True, "reason": f"为 {k} 名易感者接种疫苗"}
        if t == "lockdown":
            self._f_lockdown = max(0.0, 1 - float(p.get("scale", 0.5)))
            return {"applied": True, "reason": "实施封锁，降低接触率"}
        if t == "mask":
            self._f_mask = max(0.0, 1 - float(p.get("scale", 0.5)))
            return {"applied": True, "reason": "佩戴口罩，降低感染率"}
        if t == "social_distance":
            self._f_dist = max(0.0, 1 - float(p.get("scale", 0.5)))
            return {"applied": True, "reason": "保持距离，降低接触率"}
        if t == "cure":
            self._f_cure = float(p.get("scale", 2.0))
            return {"applied": True, "reason": "特效药提高恢复率"}
        return super().apply_intervention(itv)

    def _vaccinate(self, fraction: float) -> int:
        w, h = self.width, self.height
        susc = [(x, y) for y in range(h) for x in range(w)
                if self.state[y][x] == 0]
        self.rng.shuffle(susc)
        k = int(len(susc) * fraction)
        for x, y in susc[:k]:
            self.state[y][x] = 2
        return k
