"""Procedural world generation: region layout, biomes, gates, resources,
initial corruption and founding populations."""

from __future__ import annotations

import logging
import random

import numpy as np

from ..core.clock import TICKS_PER_DAY
from ..core.rng import derive_seed, make_rng
from ..entities.creature import Creature, Group, roll_traits
from .names import creature_name, group_name, region_name
from .noise import fractal_noise
from .region import Region, ResourceNode, Anomaly, CORR_CELL
from .terrain import Tile, water_adjacent_tiles
from .world import World

log = logging.getLogger("digitalworld.gen")

STYLE_OF_KIND = {"forest": "forest", "coast": "coast", "swamp": "marsh", "caves": "caves",
                 "desert": "desert", "grassland": "steppe", "anomaly": "rift"}


def _assign_biomes(world: World, W: int, H: int, rng: random.Random) -> dict[tuple[int, int], str]:
    n = W * H
    moist = fractal_noise(W, H, derive_seed(world.seed, "moist"), 2.0, 2)
    rock = fractal_noise(W, H, derive_seed(world.seed, "rock"), 2.0, 2)
    cx, cy = (W - 1) / 2, (H - 1) / 2
    quotas = [
        ("tidewire_coast", max(1, n // 6)),
        ("null_rift", max(1, n // 10)),
        ("glass_dunes", max(1, n // 8)),
        ("echo_hollows", max(1, n // 8)),
        ("murkcache_marsh", max(1, n // 7)),
    ]
    used = sum(q for _, q in quotas)
    rest = max(0, n - used)
    quotas += [("verdant_lattice", (rest + 1) // 2), ("static_steppe", rest // 2)]

    def score(b, gx, gy):
        edge = gx in (0, W - 1) or gy in (0, H - 1)
        far = ((gx - cx) ** 2 + (gy - cy) ** 2) ** 0.5 / max(cx + cy, 1)
        m, r = moist[gy, gx], rock[gy, gx]
        return {
            "tidewire_coast": (2.0 if edge else -5.0) + m,
            "null_rift": far * 2 + rng.random() * 0.5,
            "glass_dunes": gy / max(H - 1, 1) * 1.5 + (1 - m),
            "echo_hollows": r * 2,
            "murkcache_marsh": m * 2 - r,
            "verdant_lattice": m + 0.5,
            "static_steppe": (1 - m) + 0.5 - far,
        }[b] + rng.random() * 0.3

    free = {(x, y) for x in range(W) for y in range(H)}
    out = {}
    for biome, q in quotas:
        for _ in range(q):
            if not free:
                break
            best = max(sorted(free), key=lambda c: score(biome, *c))
            out[best] = biome
            free.discard(best)
    for c in sorted(free):
        out[c] = "static_steppe"
    return out


def _edges(W: int, H: int, rng: random.Random) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    all_edges = []
    for y in range(H):
        for x in range(W):
            if x + 1 < W:
                all_edges.append(((x, y), (x + 1, y)))
            if y + 1 < H:
                all_edges.append(((x, y), (x, y + 1)))
    # random spanning tree (DFS) guarantees connectivity
    start = (rng.randrange(W), rng.randrange(H))
    seen = {start}
    stack = [start]
    tree = set()
    while stack:
        cur = stack[-1]
        x, y = cur
        opts = [(x + dx, y + dy) for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1))
                if 0 <= x + dx < W and 0 <= y + dy < H and (x + dx, y + dy) not in seen]
        if not opts:
            stack.pop()
            continue
        nxt = rng.choice(opts)
        seen.add(nxt)
        tree.add(tuple(sorted((cur, nxt))))
        stack.append(nxt)
    edges = []
    for e in all_edges:
        if tuple(sorted(e)) in tree or rng.random() < 0.6:
            edges.append(e)
    return edges


def _free_tiles(region: Region, rng: random.Random, kind: str) -> list[tuple[int, int]]:
    t = region.terrain
    h, w = t.shape
    walk = region.passable
    water = np.isin(t, [int(Tile.DEEP), int(Tile.SHALLOW)])
    land = walk & ~water
    margin = np.zeros_like(land)
    margin[2:h - 2, 2:w - 2] = True
    for (gx, gy) in region.gates.values():
        margin[max(0, gy - 2):gy + 3, max(0, gx - 2):gx + 3] = False
    land &= margin
    if kind in ("water", "shoal"):
        cands = [(x, y) for x, y in water_adjacent_tiles(t) if margin[y, x]]
        if cands:
            return cands
    if kind in ("shelter", "material"):
        blockers = np.isin(t, [int(Tile.TREE), int(Tile.ROCK), int(Tile.CAVE_WALL), int(Tile.CRYSTAL)])
        near = np.zeros_like(blockers)
        near[1:, :] |= blockers[:-1, :]
        near[:-1, :] |= blockers[1:, :]
        near[:, 1:] |= blockers[:, :-1]
        near[:, :-1] |= blockers[:, 1:]
        ys, xs = np.nonzero(land & near)
        if len(xs):
            return list(zip(xs.tolist(), ys.tolist()))
    ys, xs = np.nonzero(land)
    return list(zip(xs.tolist(), ys.tolist()))


def place_resources(world: World, region: Region, rng: random.Random) -> None:
    biome = world.biome(region)
    for kind, count in sorted(biome.resources.items()):
        rk = world.content.resources[kind]
        cands = _free_tiles(region, rng, kind)
        rng.shuffle(cands)
        placed: list[tuple[int, int]] = []
        for (x, y) in cands:
            if len(placed) >= count:
                break
            if any(abs(x - px) + abs(y - py) < 4 for px, py in placed):
                continue
            if any(n.x == x and n.y == y for n in region.resources.values()):
                continue
            placed.append((x, y))
            cap = rk.capacity * rng.uniform(0.8, 1.2)
            amount = cap if kind == "shelter" else cap * rng.uniform(0.55, 1.0)
            node = ResourceNode(world.new_id(), kind, x, y, amount, cap)
            region.resources[node.id] = node


def seed_corruption(world: World, region: Region) -> None:
    biome = world.biome(region)
    ch, cw = region.corruption.shape
    n = fractal_noise(cw, ch, derive_seed(world.seed, "corr", region.id), 3.0, 2)
    field = biome.base_corruption + (n - 0.5) * 0.25
    if region.biome_id == "null_rift":
        yy, xx = np.mgrid[0:ch, 0:cw]
        d = np.sqrt(((xx - cw / 2) / cw) ** 2 + ((yy - ch / 2) / ch) ** 2)
        field = field + (0.35 - d) * 0.8
    region.corruption = np.clip(field, 0, 1).astype(np.float32)
    region.resistance = biome.resistance
    region.spread = 0.3 + biome.base_corruption
    region.rain_now = biome.rain


def grant_home_knowledge(world: World, c: Creature, region: Region, now: int) -> None:
    """Creatures born or raised somewhere know the nearest water, food and shelter."""
    sp = world.species(c.species_id)
    wanted = {"water": 2, "shelter": 1}
    for k in sp.diet:
        if k != "meat":
            wanted[k] = 2
    for kind, n in wanted.items():
        nodes = sorted(region.nodes_of(kind), key=lambda nd: (nd.x - c.x) ** 2 + (nd.y - c.y) ** 2)
        for nd in nodes[:n]:
            c.remember_node(nd, region.id, now)


def make_creature(world: World, species_id: str, region: Region, x: float, y: float, born_tick: int,
                  rng: random.Random, parents: list[Creature] | None = None) -> Creature:
    sp = world.species(species_id)
    c = Creature(world.new_id(), species_id, creature_name(rng), region.id, x, y, born_tick,
                 rng.choice("fm"))
    c.traits = roll_traits(rng, sp.temperament, parents)
    c.needs.update({"hunger": rng.uniform(0.05, 0.35), "thirst": rng.uniform(0.05, 0.35),
                    "fatigue": rng.uniform(0.0, 0.3), "curiosity": rng.uniform(0.0, 0.4),
                    "loneliness": rng.uniform(0.0, 0.3)})
    c.repro_ready_tick = born_tick + int(sp.maturity_days * TICKS_PER_DAY)
    if parents:
        c.generation = max(p.generation for p in parents) + 1
        c.parents = [p.id for p in parents]
        c.repro_ready_tick = born_tick + int(sp.maturity_days * TICKS_PER_DAY)
    # base attitude toward the player comes from the species and temperament
    pr = c.relation(0, "player")
    pr.trust = sp.player.get("trust", 0.1) * (0.5 + c.traits["sociability"])
    pr.fear = sp.player.get("fear", 0.2) * (1.5 - c.traits["boldness"])
    pr.familiarity = 0.0
    return c


def spawn_group(world: World, region: Region, species_id: str, size: int, rng: random.Random,
                center: tuple[int, int] | None = None, now: int = 0, founding: bool = True) -> list[Creature]:
    sp = world.species(species_id)
    walk = np.argwhere(region.passable) if region.passable is not None else None
    swim = sp.has("swim")
    if center is None:
        tiles = [(int(x), int(y)) for y, x in walk] if walk is not None else [(region.width // 2, region.height // 2)]
        center = rng.choice(tiles)
    members = []
    for i in range(size):
        x, y = center[0] + rng.uniform(-2.5, 2.5), center[1] + rng.uniform(-2.5, 2.5)
        if region.terrain is not None:
            x, y = region.nearest_walkable(x, y, swim)
            x, y = x + 0.5, y + 0.5
        if founding:
            age_days = rng.uniform(sp.maturity_days * 1.1, sp.lifespan_days * 0.55)
        else:
            age_days = rng.uniform(sp.maturity_days * 1.0, sp.maturity_days * 2.5)
        c = make_creature(world, species_id, region, x, y, now - int(age_days * TICKS_PER_DAY), rng)
        c.repro_ready_tick = now + rng.randint(0, int(sp.repro_cooldown_days * TICKS_PER_DAY))
        c.memory.add(now, "born", region=region.id, x=x, y=y, intensity=0.4, detail=region.name)
        grant_home_knowledge(world, c, region, now)
        members.append(c)
    if (sp.social != "solitary" or sp.territorial) and members:
        g = Group(world.new_id(), species_id, sp.social, group_name(rng, sp.social))
        g.members = [c.id for c in members]
        g.leader_id = members[0].id
        g.region_id = region.id
        g.founded_tick = now
        world.groups[g.id] = g
        for c in members:
            c.group_id = g.id
            for o in members:
                if o is not c:
                    r = c.relation(o.id, o.species_id)
                    r.trust, r.affinity, r.familiarity = 0.5, 0.45, 0.6
    return members


def populate_region(world: World, region: Region, rng: random.Random) -> list[Creature]:
    out = []
    biome_id = region.biome_id
    for sid in sorted(world.content.species):
        sp = world.species(sid)
        h = sp.habitat_score(biome_id)
        if h < 0.4:
            continue
        lo, hi = sp.group_size
        if sid == "glitchwyrm":
            n_groups = 1 if (biome_id == "null_rift" or rng.random() < 0.12) else 0
        elif sp.is_predator:
            n_groups = 1 if rng.random() < h * 0.45 else 0
        elif sid == "lumen_keeper":
            n_groups = 1 if rng.random() < h * 0.6 else 0
        else:
            n_groups = (1 if rng.random() < h else 0) + (1 if h >= 0.9 and rng.random() < 0.6 else 0)
            if sp.social == "solitary":
                n_groups *= rng.randint(2, 4)
        for _ in range(n_groups):
            size = rng.randint(lo, hi)
            out.extend(spawn_group(world, region, sid, size, rng))
    return out


def generate_world(world: World) -> None:
    cfg = world.config
    W, H = cfg.world_width, cfg.world_height
    rng = make_rng(world.seed, "world")
    biomes = _assign_biomes(world, W, H, rng)
    ids: dict[tuple[int, int], int] = {}
    used_names: set = set()
    for gy in range(H):
        for gx in range(W):
            bid = biomes[(gx, gy)]
            b = world.content.biomes[bid]
            rid = gy * W + gx + 1
            coast_side = 0
            if b.kind == "coast":
                sides = [s for s, ok in ((0, gx == W - 1), (1, gx == 0), (2, gy == H - 1), (3, gy == 0)) if ok]
                coast_side = rng.choice(sides) if sides else 0
            name = region_name(rng, b.kind)
            for _ in range(20):
                if name not in used_names:
                    break
                name = region_name(rng, b.kind)
            used_names.add(name)
            r = Region(rid, name, bid, gx, gy, derive_seed(world.seed, "region", rid),
                       cfg.region_width, cfg.region_height, STYLE_OF_KIND[b.kind], coast_side)
            world.regions[rid] = r
            ids[(gx, gy)] = rid
    rw, rh = cfg.region_width, cfg.region_height
    for a, b in _edges(W, H, rng):
        ra, rb = world.regions[ids[a]], world.regions[ids[b]]
        if a[1] == b[1]:  # horizontal; a is left
            y = rng.randint(6, rh - 7)
            ra.gates[rb.id] = (rw - 1, y)
            rb.gates[ra.id] = (0, y)
        else:  # vertical; a is top
            x = rng.randint(6, rw - 7)
            ra.gates[rb.id] = (x, rh - 1)
            rb.gates[ra.id] = (x, 0)
    for rid in sorted(world.regions):
        region = world.regions[rid]
        rrng = make_rng(world.seed, "populate", rid)
        region.build_terrain()
        place_resources(world, region, rrng)
        seed_corruption(world, region)
        if region.biome_id == "null_rift":
            ax, ay = region.nearest_walkable(region.width / 2 + rrng.uniform(-8, 8), region.height / 2 + rrng.uniform(-6, 6))
            a = Anomaly(world.new_id(), "glitch_bloom", ax, ay, 0.8, 0, TICKS_PER_DAY * 8)
            region.anomalies[a.id] = a
        elif rrng.random() < 0.25:
            ax, ay = region.nearest_walkable(rrng.uniform(8, region.width - 8), rrng.uniform(8, region.height - 8))
            a = Anomaly(world.new_id(), "echo_pillar", ax, ay, 0.4, 0, TICKS_PER_DAY * 10)
            region.anomalies[a.id] = a
        for c in populate_region(world, region, rrng):
            world.creatures[c.id] = c
        region.loaded = True
        region.last_sim_tick = 0
    log.info("generated %d regions, %d creatures, %d groups", len(world.regions),
             len(world.creatures), len(world.groups))


def choose_start_region(world: World) -> int:
    W, H = world.config.world_width, world.config.world_height
    cx, cy = (W - 1) / 2, (H - 1) / 2
    best, best_s = None, -1e9
    for r in world.regions.values():
        s = -((r.gx - cx) ** 2 + (r.gy - cy) ** 2) * 0.3
        s += {"verdant_lattice": 2.0, "static_steppe": 1.6}.get(r.biome_id, 0.0)
        s -= r.corruption_level * 3
        if s > best_s:
            best, best_s = r.id, s
    return best
