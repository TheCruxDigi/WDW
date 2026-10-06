"""Player commands. Every interaction changes real simulation state:
needs, health, memories, relationships, resources, corruption or structures.

All functions return a short human-readable result string (shown as a toast)
or None when nothing happened.
"""

from __future__ import annotations

import math

from ..core.clock import TICKS_PER_DAY
from ..entities.memory import PLAYER_ID
from ..sim import corruption as corruption_sys
from ..sim import ecology

INTERACT_RANGE = 2.6
FEEDABLE = ("grass", "fruit", "fungus", "sunbulb", "shoal", "bytebloom")


def _region(sim):
    p = sim.world.player
    return sim.world.regions[p.region_id]


# ---------------------------------------------------------------- movement

def move_player(sim, vx: float, vy: float, dt: float, run: bool = False) -> None:
    world = sim.world
    p = world.player
    if p is None or p.integrity <= 0:
        return
    region = _region(sim)
    if region.terrain is None:
        return
    n = math.hypot(vx, vy)
    if n == 0:
        return
    vx, vy = vx / n, vy / n
    p.facing = (vx, vy)
    speed = p.SPEED * (1.6 if run and p.energy > 5 else 1.0)
    tile_cost = float(region.walk_cost[int(p.y), int(p.x)])
    if tile_cost < 1e8:
        speed /= max(1.0, tile_cost) ** 0.6
    corr = region.corruption_at(p.x, p.y)
    if corr > 0.6:
        speed *= 0.8  # fractured ground resists
    step = speed * min(dt, 0.1)
    if run:
        p.energy = max(0.0, p.energy - 3.0 * dt)
    nx, ny = p.x + vx * step, p.y + vy * step
    # gate crossing
    if not (0 <= nx < region.width and 0 <= ny < region.height):
        tx, ty = int(p.x), int(p.y)
        for nb, (gx, gy) in region.gates.items():
            if abs(gx - tx) <= 1 and abs(gy - ty) <= 1:
                travel(sim, nb)
                return
        return
    moved = False
    if _free(region, nx, p.y):
        p.x = nx
        moved = True
    if _free(region, p.x, ny):
        p.y = ny
        moved = True
    if moved:
        p.stats["distance"] = p.stats.get("distance", 0.0) + step


def _free(region, x: float, y: float) -> bool:
    r = 0.28
    for ox, oy in ((-r, -r), (r, -r), (-r, r), (r, r)):
        tx, ty = int(x + ox), int(y + oy)
        if not region.in_bounds(tx, ty):
            # allow standing in the gate opening at the very edge
            continue
        if region.walk_cost[ty, tx] >= 1e8:
            return False
    return 0 <= x < region.width and 0 <= y < region.height


def travel(sim, target_rid: int) -> str | None:
    world = sim.world
    p = world.player
    src = _region(sim)
    if target_rid not in src.gates:
        return None
    dst = world.regions[target_rid]
    gx, gy = dst.gates[src.id]
    ix = gx + (1 if gx == 0 else -1 if gx == dst.width - 1 else 0)
    iy = gy + (1 if gy == 0 else -1 if gy == dst.height - 1 else 0)
    src.last_player_tick = world.clock.tick
    p.region_id = dst.id
    p.x, p.y = ix + 0.5, iy + 0.5
    p.prev_x, p.prev_y = p.x, p.y
    sim.update_lod()
    tx, ty = dst.nearest_walkable(p.x, p.y)
    if (int(p.x), int(p.y)) != (tx, ty):
        p.x, p.y = tx + 0.5, ty + 0.5
    enter_region(sim, dst.id)
    return f"Entered {dst.name}"


def enter_region(sim, rid: int, first: bool = False) -> None:
    world = sim.world
    p = world.player
    region = world.regions[rid]
    now = world.clock.tick
    biome = world.biome(region)
    if not region.discovered:
        region.discovered = True
        region.first_visit_tick = now
        p.discovered_regions[rid] = now
        world.history.record(now, "player_discovery", f"The Wayfinder discovered {region.name} ({biome.name}).",
                             rid, importance=2 if not first else 1, region_name=region.name)
        p.note(now, f"Discovered {region.name} — {biome.description}", f"Discovered {region.name} ({biome.name})")
    else:
        since = region.last_player_tick
        if since is not None and now - since > TICKS_PER_DAY // 2:
            changes = world.history.since(since, rid, min_importance=2)
            if changes:
                p.note(now, f"{region.name} changed while you were away: {changes[-1].text}")
    region.last_player_tick = now


# ---------------------------------------------------------------- per tick

def player_tick(sim) -> None:
    world = sim.world
    p = world.player
    now = world.clock.tick
    region = _region(sim)
    corr = region.corruption_at(p.x, p.y)
    regen = 0.05 * (1.0 - corr) + (0.08 if sim.near_beacon(region, p.x, p.y) else 0.0)
    p.energy = min(100.0, p.energy + regen)
    if now - p.last_hit_tick > 60:
        p.integrity = min(100.0, p.integrity + 0.05)
    if corr > 0.65:
        p.integrity = max(0.0, p.integrity - (corr - 0.65) * 0.2)
    if p.integrity <= 0:
        _collapse(sim)
        return
    if now % 5 == 0:
        _discover(sim, p, region, now)


def _discover(sim, p, region, now) -> None:
    world = sim.world
    for c in world.nearby(region.id, p.x, p.y, 7.0):
        if c.species_id not in p.discovered_species:
            sp = world.species(c.species_id)
            p.discovered_species[c.species_id] = now
            p.note(now, f"New species observed: {sp.name}. {sp.description}", f"New species observed: {sp.name}")
            world.history.record(now, "species_discovered", f"The Wayfinder first observed a {sp.name} in {region.name}.",
                                 region.id, importance=1, species=sp.id)
    for a in region.anomalies.values():
        if a.id not in p.discovered_anomalies and (a.x - p.x) ** 2 + (a.y - p.y) ** 2 < 36:
            p.discovered_anomalies[a.id] = now
            a.discovered = True
            p.note(now, f"Anomaly found: {a.name}.")
            world.history.record(now, "anomaly_discovered", f"The Wayfinder found a {a.name} in {region.name}.",
                                 region.id, importance=1, anomaly=a.id)


def _collapse(sim) -> None:
    world = sim.world
    p = world.player
    now = world.clock.tick
    p.stats["collapses"] = p.stats.get("collapses", 0) + 1
    rid, hx, hy = p.home
    # prefer the nearest beacon in the current region
    region = _region(sim)
    beacons = [s for s in region.structures.values() if s.kind == "beacon" and s.complete]
    if beacons:
        b = min(beacons, key=lambda s: (s.x - p.x) ** 2 + (s.y - p.y) ** 2)
        tx, ty = region.nearest_walkable(b.x + 1, b.y + 1)
        p.x, p.y = tx + 0.5, ty + 0.5
    else:
        if rid != p.region_id:
            p.region_id = rid
            sim.update_lod()
        p.x, p.y = hx, hy
    p.prev_x, p.prev_y = p.x, p.y
    p.integrity = 60.0
    p.energy = max(p.energy, 30.0)
    p.note(now, "Your signal collapsed. You re-formed at a safe anchor.")
    world.history.record(now, "player_collapse", "The Wayfinder's signal collapsed and re-formed elsewhere.",
                         p.region_id, importance=1)


def intimidated_by(sim, c, sp) -> None:
    world = sim.world
    p = world.player
    now = world.clock.tick
    rel = c.relation(PLAYER_ID, "player")
    c.ban("player_terr", now, 150)
    g = world.groups.get(c.group_id) if c.group_id else None
    warned = c.memory.recall("warned_player", now=now)
    if not warned or (now - warned[-1].tick > 240 and rel.hostility < 0.3):
        # first a display, not a blow
        c.memory.add(now, "warned_player", PLAYER_ID, c.region_id, c.x, c.y, 0.3)
        rel.adjust(now, hostility=0.05)
        p.note(now, f"{c.name} the {sp.name} warns you off its territory. Leave, or it will strike.")
        world.bus.publish("player_warned", now, creature=c.id)
        return
    dmg = sp.attack * 0.9
    p.integrity = max(0.0, p.integrity - dmg)
    p.last_hit_tick = now
    dx, dy = p.x - c.x, p.y - c.y
    d = math.hypot(dx, dy) or 1.0
    region = _region(sim)
    for push in (1.6, 1.0, 0.5):
        nx, ny = p.x + dx / d * push, p.y + dy / d * push
        if _free(region, nx, ny):
            p.x, p.y = nx, ny
            break
    rel.adjust(now, hostility=0.04)
    p.note(now, f"{c.name} the {sp.name} drove you out of {('the ' + g.name + chr(39) + 's') if g else 'its'} territory!")
    key = f"chased_{c.group_id}"
    if not region.flags.get(key):
        region.flags[key] = now
        world.history.record(now, "player_chased",
                             f"The {g.name if g else sp.name} chased the Wayfinder from their ground in {region.name}.",
                             region.id, importance=1, creature=c.id)
    world.bus.publish("player_hit", now, creature=c.id, damage=dmg)


# ---------------------------------------------------------------- targeting

def nearest_creature(sim, max_dist: float = INTERACT_RANGE):
    world = sim.world
    p = world.player
    best, best_d = None, max_dist
    for c in world.nearby(p.region_id, p.x, p.y, max_dist):
        d = math.hypot(c.x - p.x, c.y - p.y)
        if d <= best_d:
            best, best_d = c, d
    return best


def nearest_node(sim, kinds=None, max_dist: float = INTERACT_RANGE):
    p = sim.world.player
    region = _region(sim)
    best, best_d = None, max_dist
    for n in region.resources.values():
        if kinds and n.kind not in kinds:
            continue
        d = math.hypot(n.x + 0.5 - p.x, n.y + 0.5 - p.y)
        if d <= best_d:
            best, best_d = n, d
    return best


def nearest_structure(sim, max_dist: float = INTERACT_RANGE):
    p = sim.world.player
    region = _region(sim)
    best, best_d = None, max_dist
    for s in region.structures.values():
        d = math.hypot(s.x + 0.5 - p.x, s.y + 0.5 - p.y)
        if d <= best_d:
            best, best_d = s, d
    return best


def _witnesses(sim, c, radius: float = 7.0):
    world = sim.world
    return [o for o in world.nearby(c.region_id, c.x, c.y, radius)
            if o is not c and o.group_id is not None and o.group_id == c.group_id]


# ---------------------------------------------------------------- interactions

def feed(sim, c=None) -> str:
    world = sim.world
    p = world.player
    now = world.clock.tick
    c = c or nearest_creature(sim)
    if c is None:
        return "No creature within reach."
    if p.inventory.get("seeds", 0) <= 0:
        return "You carry no nutri-seeds. Gather some from plants (G)."
    sp = world.species(c.species_id)
    rel = c.relation(PLAYER_ID, "player")
    if not any(k in sp.diet for k in FEEDABLE):
        rel.adjust(now, fear=0.05)
        return f"The {sp.name} sniffs the seeds and ignores them."
    if rel.fear > 0.7 and rel.trust < 0.2:
        sim.force_flee(c, None)
        rel.adjust(now, fear=0.03)
        return f"{c.name} is too frightened to take food from you."
    p.inventory["seeds"] -= 1
    c.needs["hunger"] = max(0.0, c.needs["hunger"] - 0.45)
    c.health = min(1.0, c.health + 0.05)
    hungry_bonus = 0.1 if c.needs["hunger"] > 0.3 else 0.0
    rel.adjust(now, trust=0.14 + hungry_bonus, affinity=0.1, fear=-0.1, hostility=-0.08)
    c.memory.add(now, "fed_by_player", PLAYER_ID, c.region_id, c.x, c.y, 0.7)
    p.stats["fed"] += 1
    for o in _witnesses(sim, c):
        o.relation(PLAYER_ID, "player").adjust(now, trust=0.04, fear=-0.03)
    world.bus.publish("player_feed", now, creature=c.id)
    msg = f"You fed {c.name} the {sp.name}. (trust {rel.trust:.2f})"
    if rel.trust > 0.7 and not c.memory.recall("bonded", subject=PLAYER_ID):
        c.memory.add(now, "bonded", PLAYER_ID, c.region_id, c.x, c.y, 0.9)
        world.history.record(now, "player_bond", f"{c.name} the {sp.name} came to trust the Wayfinder.",
                             c.region_id, importance=2, creature=c.id)
        msg += f" {c.name} now trusts you."
        p.note(now, msg, toast=False)
    return msg


def heal(sim, c=None) -> str:
    world = sim.world
    p = world.player
    now = world.clock.tick
    c = c or nearest_creature(sim)
    if c is None:
        return "No creature within reach."
    if p.energy < 20:
        return "Not enough energy to mend (20 needed)."
    sp = world.species(c.species_id)
    p.energy -= 20
    before = c.health, c.taint
    c.health = min(1.0, c.health + 0.4)
    c.taint = max(0.0, c.taint - 0.45)
    rel = c.relation(PLAYER_ID, "player")
    gain = 0.25 if before[0] < 0.6 or before[1] > 0.4 else 0.1
    rel.adjust(now, trust=gain, affinity=gain * 0.7, fear=-0.1)
    c.memory.add(now, "healed_by_player", PLAYER_ID, c.region_id, c.x, c.y, 0.8)
    p.stats["healed"] += 1
    for o in _witnesses(sim, c):
        o.relation(PLAYER_ID, "player").adjust(now, trust=0.05)
    world.bus.publish("player_heal", now, creature=c.id)
    return f"You mended {c.name} the {sp.name}: health {before[0]:.0%}→{c.health:.0%}, taint {before[1]:.0%}→{c.taint:.0%}."


def scare(sim, c=None) -> str:
    world = sim.world
    p = world.player
    now = world.clock.tick
    c = c or nearest_creature(sim, 4.0)
    if c is None:
        return "Nothing to startle."
    sp = world.species(c.species_id)
    rel = c.relation(PLAYER_ID, "player")
    hostile = 0.25 if sp.territorial or c.traits["aggression"] > 0.5 else 0.06
    rel.adjust(now, fear=0.35 * (1.2 - c.traits["boldness"]), hostility=hostile, trust=-0.2, affinity=-0.1)
    c.memory.add(now, "scared_by_player", PLAYER_ID, c.region_id, c.x, c.y, 0.7)
    p.stats["scared"] += 1
    for o in _witnesses(sim, c):
        o.relation(PLAYER_ID, "player").adjust(now, fear=0.15, trust=-0.05)
        o.memory.add(now, "scared_by_player", PLAYER_ID, o.region_id, o.x, o.y, 0.4, "saw it happen")
    if c.traits["boldness"] > 0.7 and sp.attack > 8:
        world.bus.publish("player_scare", now, creature=c.id, outcome="stood")
        return f"{c.name} the {sp.name} stands its ground and glares at you."
    sim.force_flee(c, None)
    world.bus.publish("player_scare", now, creature=c.id, outcome="fled")
    return f"You startled {c.name} the {sp.name}; it bolts."


def scan(sim, c=None) -> str:
    world = sim.world
    p = world.player
    now = world.clock.tick
    c = c or nearest_creature(sim, 6.0)
    if c is None:
        return "Nothing to scan nearby."
    sp = world.species(c.species_id)
    p.discovered_species.setdefault(c.species_id, now)
    first = c.id not in p.scanned
    p.scanned[c.id] = {"name": c.name, "species": c.species_id, "tick": now}
    c.relation(PLAYER_ID, "player").adjust(now, familiarity=0.04)
    rel = c.relations.get(PLAYER_ID)
    mood = rel.label() if rel else "indifferent"
    top = max(c.needs.items(), key=lambda kv: kv[1])
    msg = (f"{c.name} — {sp.name}, {c.age_days(now):.1f} days, health {c.health:.0%}, "
           f"doing: {c.action_name}, strongest need: {top[0]} {top[1]:.0%}, toward you: {mood}")
    if first:
        p.note(now, msg, toast=False)
    return msg


def gather(sim) -> str:
    world = sim.world
    p = world.player
    now = world.clock.tick
    region = _region(sim)
    node = nearest_node(sim, kinds=FEEDABLE + ("material",))
    if node is None:
        return "Nothing to gather here."
    rk = world.content.resources[node.kind]
    take = min(node.amount, 6.0)
    if take < 1:
        return f"The {rk.name} is exhausted."
    got = ecology.consume(world, region, node, take, PLAYER_ID)
    if node.kind == "material":
        p.inventory["shards"] = p.inventory.get("shards", 0) + int(got // 2) + 1
        item = "lumen shards"
    else:
        p.inventory["seeds"] = p.inventory.get("seeds", 0) + int(got // 3) + 1
        item = "nutri-seeds"
    p.stats["gathered"] += 1
    # taking food inside someone's territory is noticed
    t = region.territory_at(node.x, node.y)
    if t is not None:
        g = world.groups.get(t.group_id)
        if g is not None:
            for mid in g.members:
                m = world.creatures.get(mid)
                if m is not None and m.region_id == region.id and (m.x - p.x) ** 2 + (m.y - p.y) ** 2 < 144:
                    m.relation(PLAYER_ID, "player").adjust(now, hostility=0.08, trust=-0.04)
    world.bus.publish("player_gather", now, node=node.id, amount=got, region=region.id)
    left = f"{node.fullness:.0%} left"
    return f"Gathered {item} from {rk.name} ({left})."


def stabilize(sim) -> str:
    world = sim.world
    p = world.player
    now = world.clock.tick
    if p.energy < 30:
        return "Not enough energy to stabilize (30 needed)."
    region = _region(sim)
    p.energy -= 30
    removed = corruption_sys.stabilize_area(region, p.x, p.y, 0.18, 2)
    for c in world.nearby(region.id, p.x, p.y, 6):
        c.taint = max(0.0, c.taint - 0.2)
    p.stats["stabilized"] += 1
    world.bus.publish("player_stabilize", now, region=region.id, removed=removed)
    if removed > 0.3:
        world.history.record(now, "player_stabilize", f"The Wayfinder stabilized corrupted ground in {region.name}.",
                             region.id, importance=1, removed=round(removed, 3))
    return f"Stabilization pulse: corruption reduced by {removed:.2f} (local now {region.corruption_at(p.x, p.y):.0%})."


def build_beacon(sim) -> str:
    world = sim.world
    p = world.player
    region = _region(sim)
    if p.inventory.get("shards", 0) < 5:
        return "A beacon needs 5 lumen shards (gather from shard deposits)."
    x, y = int(p.x + p.facing[0] * 1.2), int(p.y + p.facing[1] * 1.2)
    if not region.is_walkable(x, y):
        x, y = int(p.x), int(p.y)
    for s in region.structures.values():
        if abs(s.x - x) + abs(s.y - y) < 4:
            return "Too close to another structure."
    s = sim.begin_structure(region, "beacon", x, y, None)
    if s is None:
        return "Cannot build here."
    p.inventory["shards"] -= 5
    sim.complete_structure(region, s, None)
    p.stats["beacons"] += 1
    return f"You raised a lumen beacon. It will push back corruption around it."


def dismantle(sim) -> str:
    world = sim.world
    p = world.player
    region = _region(sim)
    s = nearest_structure(sim)
    if s is None:
        return "No structure within reach."
    sim.destroy_structure(region, s, "dismantled by the Wayfinder", by=PLAYER_ID)
    p.inventory["shards"] = p.inventory.get("shards", 0) + 3
    p.stats["dismantled"] += 1
    return "You dismantled the beacon and recovered 3 shards. Its builders will not forget."


def wait(sim, hours: int = 12) -> str:
    """Let time pass quickly (abstract simulation of the whole world)."""
    entries = sim.fast_forward(hours * 60)
    imp = [e for e in entries if e.importance >= 2]
    p = sim.world.player
    p.note(sim.world.clock.tick, f"You rested for {hours} hours. {len(imp)} notable events occurred.")
    return f"{hours} hours passed. {len(entries)} events recorded ({len(imp)} notable)."
