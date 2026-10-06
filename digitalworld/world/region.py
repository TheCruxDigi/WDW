"""Region state: terrain (runtime), resources, corruption, structures,
anomalies, territories and the serialized creatures of a dormant region.

A region is *loaded* when it has live terrain/navigation data and its
creatures live in ``World.creatures``. A *dormant* region keeps its creatures
as plain dicts in ``dormant_creatures`` and is advanced by the abstract
simulator.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .terrain import generate_terrain, apply_mods, passable_mask, cost_grid, Tile

CORR_CELL = 4  # tiles per corruption cell


@dataclass
class ResourceNode:
    id: int
    kind: str
    x: int
    y: int
    amount: float
    capacity: float
    health: float = 1.0          # ecological condition; shrinks with overuse
    depleted_count: int = 0
    depleted_tick: int = -1
    users: list = field(default_factory=list)   # runtime: creature ids feeding now

    @property
    def max_amount(self) -> float:
        return self.capacity * self.health

    @property
    def fullness(self) -> float:
        m = self.max_amount
        return self.amount / m if m > 0 else 0.0

    def to_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "x": self.x, "y": self.y,
                "amount": round(self.amount, 3), "capacity": self.capacity,
                "health": round(self.health, 4), "depleted_count": self.depleted_count,
                "depleted_tick": self.depleted_tick}

    @classmethod
    def from_dict(cls, d: dict) -> "ResourceNode":
        return cls(int(d["id"]), d["kind"], int(d["x"]), int(d["y"]), float(d.get("amount", 0)),
                   float(d.get("capacity", 10)), float(d.get("health", 1.0)),
                   int(d.get("depleted_count", 0)), int(d.get("depleted_tick", -1)))


@dataclass
class Structure:
    id: int
    kind: str                 # "beacon" | "den"
    x: int
    y: int
    integrity: float = 1.0
    progress: float = 1.0     # <1 while under construction
    owner_group: int | None = None
    builder_id: int | None = None
    built_tick: int = 0
    shelter_node: int | None = None

    @property
    def complete(self) -> bool:
        return self.progress >= 1.0

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    @classmethod
    def from_dict(cls, d: dict) -> "Structure":
        return cls(int(d["id"]), d["kind"], int(d["x"]), int(d["y"]), float(d.get("integrity", 1)),
                   float(d.get("progress", 1)), d.get("owner_group"), d.get("builder_id"),
                   int(d.get("built_tick", 0)), d.get("shelter_node"))


ANOMALY_KINDS = {
    "glitch_bloom": "Glitch Bloom",     # emits corruption, seeds bytebloom
    "echo_pillar": "Echo Pillar",       # harmless curiosity; replays the past
    "static_vortex": "Static Vortex",   # dangerous: taints creatures nearby
}


@dataclass
class Anomaly:
    id: int
    kind: str
    x: int
    y: int
    intensity: float
    born_tick: int
    lifetime: int
    discovered: bool = False

    @property
    def name(self) -> str:
        return ANOMALY_KINDS.get(self.kind, self.kind)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    @classmethod
    def from_dict(cls, d: dict) -> "Anomaly":
        return cls(int(d["id"]), d["kind"], int(d["x"]), int(d["y"]), float(d.get("intensity", 0.5)),
                   int(d.get("born_tick", 0)), int(d.get("lifetime", 1440)), bool(d.get("discovered", False)))


@dataclass
class Territory:
    id: int
    group_id: int
    species_id: str
    x: float
    y: float
    radius: float
    strength: float = 0.5
    claimed_tick: int = 0

    def contains(self, x: float, y: float) -> bool:
        return (x - self.x) ** 2 + (y - self.y) ** 2 <= self.radius ** 2

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_")}

    @classmethod
    def from_dict(cls, d: dict) -> "Territory":
        return cls(int(d["id"]), int(d["group_id"]), d["species_id"], float(d["x"]), float(d["y"]),
                   float(d["radius"]), float(d.get("strength", 0.5)), int(d.get("claimed_tick", 0)))


class Region:
    def __init__(self, rid: int, name: str, biome_id: str, gx: int, gy: int, seed: int,
                 width: int, height: int, style: str, coast_side: int = 0):
        self.id = rid
        self.name = name
        self.biome_id = biome_id
        self.gx, self.gy = gx, gy
        self.seed = seed
        self.width, self.height = width, height
        self.style = style
        self.coast_side = coast_side
        self.gates: dict[int, tuple[int, int]] = {}      # neighbour id -> gate tile
        self.tile_mods: dict[str, int] = {}
        self.resources: dict[int, ResourceNode] = {}
        self.structures: dict[int, Structure] = {}
        self.anomalies: dict[int, Anomaly] = {}
        self.territories: dict[int, Territory] = {}
        ch, cw = (height + CORR_CELL - 1) // CORR_CELL, (width + CORR_CELL - 1) // CORR_CELL
        self.corruption = np.zeros((ch, cw), dtype=np.float32)
        self.signal_noise = 0.0
        self.anomaly_intensity = 0.0
        self.resistance = 0.5
        self.spread = 0.5
        self.weather = "clear"
        self.weather_until = 0
        self.rain_now = 0.5
        self.discovered = False
        self.first_visit_tick: int | None = None
        self.last_player_tick: int | None = None
        self.last_sim_tick = 0
        self.dormant_creatures: list[dict] = []
        self.flags: dict = {}            # threshold memory for history detection
        self.stats: dict = {}            # latest census, for summaries/debug
        # runtime
        self.loaded = False
        self.terrain: np.ndarray | None = None
        self.walk_cost: np.ndarray | None = None
        self.swim_cost: np.ndarray | None = None
        self.passable: np.ndarray | None = None
        self.version = 0                 # bumps when terrain/corruption visuals change

    # ---- terrain -------------------------------------------------------
    def build_terrain(self) -> None:
        gates = list(self.gates.values())
        self.terrain = generate_terrain(self.style, self.width, self.height, self.seed, gates, self.coast_side)
        apply_mods(self.terrain, self.tile_mods)
        self.refresh_navigation()

    def refresh_navigation(self) -> None:
        self.walk_cost = cost_grid(self.terrain).astype(np.float32)
        self.swim_cost = cost_grid(self.terrain, swim=True).astype(np.float32)
        self.passable = passable_mask(self.terrain)
        self.version += 1

    def release_terrain(self) -> None:
        self.terrain = self.walk_cost = self.swim_cost = self.passable = None

    def set_tile(self, x: int, y: int, tile: int) -> None:
        self.tile_mods[f"{x},{y}"] = int(tile)
        if self.terrain is not None:
            self.terrain[y, x] = tile
            self.refresh_navigation()

    def in_bounds(self, x: int, y: int) -> bool:
        return 0 <= x < self.width and 0 <= y < self.height

    def is_walkable(self, x: int, y: int, swim: bool = False) -> bool:
        if not self.in_bounds(x, y) or self.terrain is None:
            return False
        cost = self.swim_cost if swim else self.walk_cost
        return cost[y, x] < 1e8

    def nearest_walkable(self, x: float, y: float, swim: bool = False, max_r: int = 12) -> tuple[int, int]:
        ix, iy = int(min(max(x, 0), self.width - 1)), int(min(max(y, 0), self.height - 1))
        if self.is_walkable(ix, iy, swim):
            return ix, iy
        for r in range(1, max_r + 1):
            for dy in range(-r, r + 1):
                for dx in (-r, r) if abs(dy) != r else range(-r, r + 1):
                    if self.is_walkable(ix + dx, iy + dy, swim):
                        return ix + dx, iy + dy
        return self.width // 2, self.height // 2

    # ---- corruption ----------------------------------------------------
    def corruption_at(self, x: float, y: float) -> float:
        cy = min(int(y) // CORR_CELL, self.corruption.shape[0] - 1)
        cx = min(int(x) // CORR_CELL, self.corruption.shape[1] - 1)
        return float(self.corruption[max(cy, 0), max(cx, 0)])

    @property
    def corruption_level(self) -> float:
        return float(self.corruption.mean())

    @property
    def stability(self) -> float:
        """Data stability: how reliably the region holds its own shape."""
        return max(0.0, min(1.0, 1.0 - 0.7 * self.corruption_level - 0.3 * self.signal_noise))

    def corruption_band(self) -> str:
        c = self.corruption_level
        if c < 0.2:
            return "stable"
        if c < 0.45:
            return "flickering"
        if c < 0.7:
            return "corrupted"
        return "fractured"

    # ---- helpers -------------------------------------------------------
    def nodes_of(self, kind: str) -> list[ResourceNode]:
        return [n for n in self.resources.values() if n.kind == kind]

    def territory_at(self, x: float, y: float) -> Territory | None:
        best = None
        for t in self.territories.values():
            if t.contains(x, y) and (best is None or t.strength > best.strength):
                best = t
        return best

    # ---- persistence ---------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "biome_id": self.biome_id, "gx": self.gx, "gy": self.gy,
            "seed": self.seed, "width": self.width, "height": self.height, "style": self.style,
            "coast_side": self.coast_side,
            "gates": {str(k): list(v) for k, v in self.gates.items()},
            "tile_mods": dict(self.tile_mods),
            "resources": [n.to_dict() for n in self.resources.values()],
            "structures": [s.to_dict() for s in self.structures.values()],
            "anomalies": [a.to_dict() for a in self.anomalies.values()],
            "territories": [t.to_dict() for t in self.territories.values()],
            "corruption": np.round(self.corruption, 4).tolist(),
            "signal_noise": self.signal_noise, "anomaly_intensity": self.anomaly_intensity,
            "resistance": self.resistance, "spread": self.spread,
            "weather": self.weather, "weather_until": self.weather_until, "rain_now": self.rain_now,
            "discovered": self.discovered, "first_visit_tick": self.first_visit_tick,
            "last_player_tick": self.last_player_tick, "last_sim_tick": self.last_sim_tick,
            "flags": dict(self.flags), "stats": dict(self.stats),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Region":
        r = cls(int(d["id"]), d["name"], d["biome_id"], int(d["gx"]), int(d["gy"]), int(d["seed"]),
                int(d["width"]), int(d["height"]), d["style"], int(d.get("coast_side", 0)))
        r.gates = {int(k): tuple(v) for k, v in d.get("gates", {}).items()}
        r.tile_mods = {k: int(v) for k, v in d.get("tile_mods", {}).items()}
        for nd in d.get("resources", []):
            n = ResourceNode.from_dict(nd)
            r.resources[n.id] = n
        for sd in d.get("structures", []):
            s = Structure.from_dict(sd)
            r.structures[s.id] = s
        for ad in d.get("anomalies", []):
            a = Anomaly.from_dict(ad)
            r.anomalies[a.id] = a
        for td in d.get("territories", []):
            t = Territory.from_dict(td)
            r.territories[t.id] = t
        corr = d.get("corruption")
        if corr:
            arr = np.array(corr, dtype=np.float32)
            if arr.shape == r.corruption.shape:
                r.corruption = np.clip(arr, 0, 1)
        r.signal_noise = float(d.get("signal_noise", 0.0))
        r.anomaly_intensity = float(d.get("anomaly_intensity", 0.0))
        r.resistance = float(d.get("resistance", 0.5))
        r.spread = float(d.get("spread", 0.5))
        r.weather = d.get("weather", "clear")
        r.weather_until = int(d.get("weather_until", 0))
        r.rain_now = float(d.get("rain_now", 0.5))
        r.discovered = bool(d.get("discovered", False))
        r.first_visit_tick = d.get("first_visit_tick")
        r.last_player_tick = d.get("last_player_tick")
        r.last_sim_tick = int(d.get("last_sim_tick", 0))
        r.flags = dict(d.get("flags", {}))
        r.stats = dict(d.get("stats", {}))
        return r


TILE_NAMES = {t.value: t.name.lower() for t in Tile}
