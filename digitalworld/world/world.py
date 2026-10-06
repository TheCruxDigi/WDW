"""World container: the single source of truth for simulation state."""

from __future__ import annotations

import random

import numpy as np

from ..config import Config
from ..content.registry import Content, load_content
from ..core.clock import WorldClock
from ..core.events import EventBus, History
from ..core.profiler import Profiler
from ..entities.creature import Creature, Group
from .region import Region, CORR_CELL
from .spatial import SpatialHash


class World:
    def __init__(self, config: Config, content: Content | None = None):
        self.config = config
        self.content = content or load_content()
        self.seed = config.seed
        self.clock = WorldClock()
        self.rng = random.Random(config.seed)
        self.bus = EventBus()
        self.history = History()
        self.profiler = Profiler()
        self.regions: dict[int, Region] = {}
        self.creatures: dict[int, Creature] = {}       # creatures of loaded regions only
        self.groups: dict[int, Group] = {}
        self.player = None
        self.next_id = 1000                            # 0 is reserved for the player
        self.spatial: dict[int, SpatialHash] = {}
        self.name = "Digital World"
        self.flags: dict = {}
        self.created_real_time = 0.0
        self._corr_tiles: dict[int, tuple[int, list]] = {}
        self._cost_lists: dict[int, tuple[int, list, list]] = {}

    # ---- ids -------------------------------------------------------------
    def new_id(self) -> int:
        i = self.next_id
        self.next_id += 1
        return i

    @property
    def tick(self) -> int:
        return self.clock.tick

    # ---- lookup ----------------------------------------------------------
    def species(self, sid: str):
        return self.content.species[sid]

    def biome(self, region: Region):
        return self.content.biomes[region.biome_id]

    def neighbors(self, rid: int) -> list[int]:
        return sorted(self.regions[rid].gates.keys())

    def loaded_regions(self) -> list[Region]:
        return [r for r in self.regions.values() if r.loaded]

    def creatures_in(self, rid: int) -> list[Creature]:
        return [c for c in self.creatures.values() if c.region_id == rid and c.alive]

    def population(self, rid: int) -> dict[str, int]:
        """Census for any region, loaded or dormant."""
        region = self.regions[rid]
        out: dict[str, int] = {}
        if region.loaded:
            for c in self.creatures.values():
                if c.region_id == rid and c.alive:
                    out[c.species_id] = out.get(c.species_id, 0) + 1
        else:
            for d in region.dormant_creatures:
                out[d["species"]] = out.get(d["species"], 0) + 1
        return out

    def total_population(self) -> int:
        return len(self.creatures) + sum(len(r.dormant_creatures) for r in self.regions.values())

    def all_creature_ids(self) -> list[int]:
        ids = list(self.creatures.keys())
        for r in self.regions.values():
            ids.extend(int(d["id"]) for d in r.dormant_creatures)
        return ids

    def find_creature_record(self, cid: int):
        """Return a live Creature or the dormant dict for any id."""
        c = self.creatures.get(cid)
        if c is not None:
            return c
        for r in self.regions.values():
            for d in r.dormant_creatures:
                if d["id"] == cid:
                    return d
        return None

    # ---- navigation caches ----------------------------------------------
    def cost_lists(self, region: Region) -> tuple[list, list]:
        cached = self._cost_lists.get(region.id)
        if cached and cached[0] == region.version:
            return cached[1], cached[2]
        walk = region.walk_cost.tolist()
        swim = region.swim_cost.tolist()
        self._cost_lists[region.id] = (region.version, walk, swim)
        return walk, swim

    def corruption_tiles(self, region: Region) -> list:
        """Per-tile corruption (upsampled), cached until the field changes."""
        key = region.flags.get("_corr_version", 0)
        cached = self._corr_tiles.get(region.id)
        if cached and cached[0] == key:
            return cached[1]
        up = np.kron(region.corruption, np.ones((CORR_CELL, CORR_CELL), dtype=np.float32))
        up = up[: region.height, : region.width]
        lst = up.tolist()
        self._corr_tiles[region.id] = (key, lst)
        return lst

    def invalidate_region_caches(self, rid: int) -> None:
        self._corr_tiles.pop(rid, None)
        self._cost_lists.pop(rid, None)

    # ---- spatial ---------------------------------------------------------
    def rebuild_spatial(self) -> None:
        for sh in self.spatial.values():
            sh.clear()
        for c in self.creatures.values():
            if not c.alive:
                continue
            sh = self.spatial.get(c.region_id)
            if sh is None:
                sh = self.spatial[c.region_id] = SpatialHash(6.0)
            sh.insert(c.id, c.x, c.y)

    def nearby(self, rid: int, x: float, y: float, r: float) -> list[Creature]:
        sh = self.spatial.get(rid)
        if not sh:
            return []
        cs = self.creatures
        return [cs[i] for i in sh.query(x, y, r) if i in cs and cs[i].alive]
