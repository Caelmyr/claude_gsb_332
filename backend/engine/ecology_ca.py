"""Ecology — cellular automaton (grass–rabbit–fox predator–prey lattice).

A toroidal ``width x height`` grid where empty cells may grow grass, rabbits
graze and reproduce, and foxes hunt rabbits.  Each animal carries an energy
reserve; it starves to death at zero, and reproduces (splitting its energy)
once it crosses a threshold.  The substrate is the grass layer, returned by
:meth:`substrate` so the frontend can draw the green background.

The lattice is a plain list-of-lists so a step is O(width*height + n); with a
few hundred animals on a 60x60 grid this runs comfortably in real time.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .base import Engine


class EcologyCA(Engine):
    domain = "ecology"
    model = "ca"

    def defaults(self) -> Dict[str, Any]:
        return {
            "width": 60, "height": 60, "n_rabbits": 300, "n_foxes": 60,
            "grass_growth": 0.05, "rabbit_repro": 8, "fox_repro": 12,
            "fox_starve": 2,
            # Fixed (not exposed in the catalog) behavioural constants.
            "rabbit_starve": 1, "rabbit_energy_gain": 4, "fox_energy_gain": 10,
        }

    # ------------------------------------------------------------------ #
    def _init(self) -> None:
        self.width = int(self.config["width"])
        self.height = int(self.config["height"])
        w, h = self.width, self.height
        self.grass: List[List[bool]] = [[False] * w for _ in range(h)]
        self.occ: List[List[int]] = [[-1] * w for _ in range(h)]
        self.animals: List[Dict[str, Any]] = []
        self._id_counter = 0

        # Seed grass across most of the lattice so rabbits can graze.
        for y in range(h):
            for x in range(w):
                if self._coin(0.6):
                    self.grass[y][x] = True

        self._place("rabbit", int(self.config["n_rabbits"]),
                    int(self.config["rabbit_repro"]) // 2)
        self._place("fox", int(self.config["n_foxes"]),
                    int(self.config["fox_repro"]) // 2)

    def _place(self, typ: str, count: int, energy: int) -> None:
        empties = self._empty_cells()
        self.rng.shuffle(empties)
        for x, y in empties[:count]:
            self._spawn(typ, x, y, energy)

    def _spawn(self, typ: str, x: int, y: int, energy: int) -> None:
        prefix = "rb" if typ == "rabbit" else "fx"
        a = {"id": f"{prefix}{self._id_counter:05d}", "type": typ,
             "state": typ, "x": x, "y": y, "energy": energy, "age": 0}
        self._id_counter += 1
        self.animals.append(a)
        self.occ[y][x] = len(self.animals) - 1

    def _empty_cells(self) -> List[Tuple[int, int]]:
        return [(x, y) for y in range(self.height) for x in range(self.width)
                if self.occ[y][x] == -1]

    def _empty_neighbors(self, x: int, y: int) -> List[Tuple[int, int]]:
        w, h = self.width, self.height
        out = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = (x + dx) % w, (y + dy) % h
                if self.occ[ny][nx] == -1:
                    out.append((nx, ny))
        return out

    def _adjacent_rabbit(self, x: int, y: int) -> Optional[Tuple[int, int, int]]:
        w, h = self.width, self.height
        found = []
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                nx, ny = (x + dx) % w, (y + dy) % h
                j = self.occ[ny][nx]
                if j != -1 and self.animals[j]["type"] == "rabbit":
                    found.append((j, nx, ny))
        return self.rng.choice(found) if found else None

    def _rebuild_occ(self) -> None:
        w, h = self.width, self.height
        self.occ = [[-1] * w for _ in range(h)]
        for i, a in enumerate(self.animals):
            self.occ[a["y"]][a["x"]] = i

    # ------------------------------------------------------------------ #
    def step(self) -> None:
        w, h = self.width, self.height
        gg = float(self.config["grass_growth"])

        # 1. Grass grows on empty cells.
        for y in range(h):
            row_occ, row_grass = self.occ[y], self.grass[y]
            for x in range(w):
                if row_occ[x] == -1 and not row_grass[x] and self._coin(gg):
                    row_grass[x] = True

        # 2. Animals act in a random order to avoid update-order bias.
        order = list(range(len(self.animals)))
        self.rng.shuffle(order)
        dead: set = set()
        for idx in order:
            if idx in dead:
                continue
            a = self.animals[idx]
            if a["type"] == "rabbit":
                self._rabbit_step(idx, dead)
            else:
                self._fox_step(idx, dead)

        if dead:
            self.animals = [a for i, a in enumerate(self.animals)
                            if i not in dead]
        self._rebuild_occ()
        self.step_count += 1

    def _rabbit_step(self, idx: int, dead: set) -> None:
        a = self.animals[idx]
        x, y = a["x"], a["y"]
        a["age"] += 1
        a["energy"] -= float(self.config["rabbit_starve"])
        if a["energy"] <= 0:
            dead.add(idx)
            self.occ[y][x] = -1
            return
        if self.grass[y][x]:
            self.grass[y][x] = False
            a["energy"] += float(self.config["rabbit_energy_gain"])

        empties = self._empty_neighbors(x, y)
        repro = a["energy"] >= float(self.config["rabbit_repro"])
        if empties:
            nx, ny = self.rng.choice(empties)
            self.occ[y][x] = -1
            a["x"], a["y"] = nx, ny
            self.occ[ny][nx] = idx
            if repro:
                a["energy"] //= 2
                self._spawn("rabbit", x, y, a["energy"])  # child in vacated cell

    def _fox_step(self, idx: int, dead: set) -> None:
        a = self.animals[idx]
        x, y = a["x"], a["y"]
        a["age"] += 1
        a["energy"] -= float(self.config["fox_starve"])
        if a["energy"] <= 0:
            dead.add(idx)
            self.occ[y][x] = -1
            return

        prey = self._adjacent_rabbit(x, y)
        if prey is not None:
            ridx, rx, ry = prey
            dead.add(ridx)
            self.occ[y][x] = -1
            self.occ[ry][rx] = idx
            a["x"], a["y"] = rx, ry
            a["energy"] += float(self.config["fox_energy_gain"])
        else:
            empties = self._empty_neighbors(x, y)
            if empties:
                nx, ny = self.rng.choice(empties)
                self.occ[y][x] = -1
                a["x"], a["y"] = nx, ny
                self.occ[ny][nx] = idx

        if a["energy"] >= float(self.config["fox_repro"]):
            empties = self._empty_neighbors(a["x"], a["y"])
            if empties:
                nx, ny = self.rng.choice(empties)
                a["energy"] //= 2
                self._spawn("fox", nx, ny, a["energy"])

    # ------------------------------------------------------------------ #
    def individuals(self) -> List[Dict[str, Any]]:
        return self.animals

    def stats(self) -> Dict[str, Any]:
        rabbits = sum(1 for a in self.animals if a["type"] == "rabbit")
        foxes = len(self.animals) - rabbits
        total = self.width * self.height
        grass = sum(sum(1 for c in row if c) for row in self.grass)
        energy = (sum(a["energy"] for a in self.animals) / len(self.animals)
                  if self.animals else 0)
        return {
            "rabbits": rabbits,
            "foxes": foxes,
            "grass_coverage": round(grass / total, 4),
            "mean_energy": round(energy, 2),
        }

    def bounds(self) -> Dict[str, float]:
        return {"width": float(self.width), "height": float(self.height)}

    def substrate(self) -> Optional[List[List[bool]]]:
        return self.grass

    def palette(self) -> Dict[str, Dict[str, str]]:
        return {
            "rabbit": {"label": "兔子", "color": "#ecf0f1"},
            "fox": {"label": "狐狸", "color": "#e67e22"},
        }

    # ------------------------------------------------------------------ #
    def apply_intervention(self, itv: Dict[str, Any]) -> Dict[str, Any]:
        t = itv["type"]
        p = itv.get("params", {})
        if t == "cull_foxes":
            n = self._cull("fox", float(p.get("fraction", 0.5)))
            return {"applied": True, "reason": f"捕杀了 {n} 只狐狸"}
        if t == "cull_rabbits":
            n = self._cull("rabbit", float(p.get("fraction", 0.5)))
            return {"applied": True, "reason": f"捕杀了 {n} 只兔子"}
        if t == "plant_grass":
            n = self._plant(float(p.get("fraction", 0.3)))
            return {"applied": True, "reason": f"在 {n} 个格子种草"}
        if t == "release_predators":
            n = int(p.get("count", 5))
            placed = self._release_foxes(n)
            return {"applied": True, "reason": f"投放了 {placed} 只狐狸"}
        return super().apply_intervention(itv)

    def _cull(self, typ: str, fraction: float) -> int:
        idxs = [i for i, a in enumerate(self.animals) if a["type"] == typ]
        self.rng.shuffle(idxs)
        k = int(len(idxs) * fraction)
        gone = set(idxs[:k])
        self.animals = [a for i, a in enumerate(self.animals) if i not in gone]
        self._rebuild_occ()
        return k

    def _plant(self, fraction: float) -> int:
        empties = [(x, y) for y in range(self.height) for x in range(self.width)
                   if self.occ[y][x] == -1 and not self.grass[y][x]]
        self.rng.shuffle(empties)
        k = int(len(empties) * fraction)
        for x, y in empties[:k]:
            self.grass[y][x] = True
        return k

    def _release_foxes(self, count: int) -> int:
        placed = 0
        for _ in range(count):
            empties = self._empty_cells()
            if not empties:
                break
            x, y = self.rng.choice(empties)
            self._spawn("fox", x, y, int(self.config["fox_repro"]) // 2)
            placed += 1
        return placed
