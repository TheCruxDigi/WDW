"""Groups (herds, packs), leadership and territories."""

from __future__ import annotations

from ..core.clock import TICKS_PER_DAY
from ..entities.creature import Group
from ..world.names import group_name
from ..world.region import Territory


def _member_records(sim, g: Group) -> list:
    """Live creatures or dormant dicts of a group's members."""
    world = sim.world
    out = []
    for mid in g.members:
        c = world.creatures.get(mid)
        if c is not None:
            if c.alive:
                out.append(c)
    return out


def update_groups(sim) -> None:
    world = sim.world
    now = world.clock.tick
    alive_ids = set(world.all_creature_ids())
    for g in list(world.groups.values()):
        g.members = [m for m in g.members if m in alive_ids]
        if not g.members:
            _dissolve(sim, g)
            continue
        if g.leader_id not in g.members:
            _elect(sim, g)
        leader = world.creatures.get(g.leader_id)
        if leader is not None:
            g.region_id = leader.region_id
        else:
            rec = world.find_creature_record(g.leader_id)
            if isinstance(rec, dict):
                g.region_id = rec["region_id"]
        if g.migrate_to is not None and (now - g.migrate_tick > TICKS_PER_DAY or g.region_id == g.migrate_to):
            g.migrate_to = None
        sp = world.species(g.species_id)
        if sp.social != "solitary" and len(g.members) > sp.group_size[1] * 1.6:
            _split(sim, g, sp)
    _adopt_loners(sim)


def _elect(sim, g: Group) -> None:
    world = sim.world
    now = world.clock.tick
    best, best_age = None, -1
    for mid in g.members:
        c = world.creatures.get(mid)
        born = c.born_tick if c is not None else (world.find_creature_record(mid) or {}).get("born_tick", now)
        age = now - born
        if age > best_age:
            best, best_age = mid, age
    old = g.leader_id if g.leader_id is not None else g.__dict__.pop("_prev_leader", None)
    g.leader_id = best
    c = world.creatures.get(best)
    if c is not None:
        c.memory.add(now, "became_leader", None, c.region_id, c.x, c.y, 0.7, g.name)
    name = c.name if c is not None else (world.find_creature_record(best) or {}).get("name", "?")
    if old is not None and len(g.members) >= 1 and best is not None:
        cause = g.__dict__.get("_leader_death_event")
        world.history.record(now, "leader_changed",
                             f"{name} now leads the {g.name} ({world.species(g.species_id).name}).",
                             g.region_id, importance=1, causes=[cause] if cause else None, group=g.id)


def _dissolve(sim, g: Group) -> None:
    world = sim.world
    now = world.clock.tick
    if g.territory_id is not None:
        for r in world.regions.values():
            t = r.territories.pop(g.territory_id, None)
            if t is not None and t.strength > 0.3:
                world.history.record(now, "territory_abandoned",
                                     f"The territory of the vanished {g.name} in {r.name} lies unclaimed.",
                                     r.id, importance=1, group=g.id)
    del world.groups[g.id]


def _split(sim, g: Group, sp) -> None:
    world = sim.world
    now = world.clock.tick
    members = [world.creatures[m] for m in g.members if m in world.creatures]
    if len(members) < 4:
        return
    members.sort(key=lambda c: c.born_tick, reverse=True)  # youngest first
    leaving = members[: len(members) // 2]
    ng = Group(world.new_id(), g.species_id, g.kind, group_name(sim.rng, sp.social))
    ng.members = [c.id for c in leaving]
    ng.leader_id = max(leaving, key=lambda c: now - c.born_tick).id
    ng.region_id = g.region_id
    ng.founded_tick = now
    world.groups[ng.id] = ng
    for c in leaving:
        c.group_id = ng.id
        g.members.remove(c.id)
    world.history.record(now, "group_split", f"Young {sp.name}s broke away from the {g.name} to form the {ng.name}.",
                         g.region_id, importance=1, group=ng.id, parent_group=g.id)


def _adopt_loners(sim) -> None:
    world = sim.world
    now = world.clock.tick
    for c in list(world.creatures.values()):
        if not c.alive or c.group_id is not None:
            continue
        sp = world.species(c.species_id)
        if sp.social == "solitary" and not sp.territorial:
            continue
        if sp.social == "solitary" and sp.territorial:
            g = Group(world.new_id(), sp.id, "solitary", f"{c.name}'s line")
            g.members, g.leader_id, g.region_id, g.founded_tick = [c.id], c.id, c.region_id, now
            world.groups[g.id] = g
            c.group_id = g.id
            continue
        for g in world.groups.values():
            if g.species_id == c.species_id and g.region_id == c.region_id and len(g.members) < sp.group_size[1]:
                g.members.append(c.id)
                c.group_id = g.id
                leader = world.creatures.get(g.leader_id)
                if leader is not None:
                    c.relation(leader.id, leader.species_id).adjust(now, trust=0.2, affinity=0.1)
                break
        else:
            g = Group(world.new_id(), sp.id, sp.social, group_name(sim.rng, sp.social))
            g.members, g.leader_id, g.region_id, g.founded_tick = [c.id], c.id, c.region_id, now
            world.groups[g.id] = g
            c.group_id = g.id


# ---------------------------------------------------------------- territories

def _overlap(a: Territory, b: Territory) -> bool:
    return (a.x - b.x) ** 2 + (a.y - b.y) ** 2 < ((a.radius + b.radius) * 0.7) ** 2


def update_territories(sim, region, dt: int = 30) -> None:
    world = sim.world
    now = world.clock.tick
    present: dict[int, list] = {}
    for c in world.creatures_in(region.id):
        if c.group_id is not None:
            present.setdefault(c.group_id, []).append(c)
    # claim
    for gid, members in present.items():
        g = world.groups.get(gid)
        if g is None:
            continue
        sp = world.species(g.species_id)
        if not sp.territorial:
            continue
        t = region.territories.get(g.territory_id) if g.territory_id is not None else None
        if t is None:
            leader = world.creatures.get(g.leader_id) or members[0]
            if leader.region_id != region.id:
                continue
            if leader.age_days(now) < sp.maturity_days:
                continue
            cand = Territory(world.new_id(), g.id, g.species_id, leader.x, leader.y, sp.territory_radius, 0.25, now)
            blocked = any(_overlap(cand, o) and o.strength > 0.55 for o in region.territories.values())
            if blocked:
                continue
            region.territories[cand.id] = cand
            g.territory_id = cand.id
            key = f"claimed_{g.id}"
            if now - region.flags.get(key, -10 ** 9) > 3 * TICKS_PER_DAY:
                world.history.record(now, "territory_claimed",
                                     f"The {g.name} ({sp.name}) claimed territory in {region.name}.",
                                     region.id, importance=1, group=g.id, territory=cand.id)
            region.flags[key] = now
    # strength and contests
    for t in list(region.territories.values()):
        g = world.groups.get(t.group_id)
        members = present.get(t.group_id, [])
        inside = sum(1 for c in members if t.contains(c.x, c.y))
        if g is None or g.territory_id != t.id:
            t.strength -= 0.03
        elif members:
            # drift the centre toward where the group actually lives
            mx = sum(c.x for c in members) / len(members)
            my = sum(c.y for c in members) / len(members)
            t.x += (mx - t.x) * (0.05 if inside else 0.12)
            t.y += (my - t.y) * (0.05 if inside else 0.12)
            t.strength = min(1.0, t.strength + 0.015 * inside) if inside else t.strength - 0.003
        else:
            t.strength -= 0.012
    terrs = sorted(region.territories.values(), key=lambda t: t.id)
    for i, a in enumerate(terrs):
        for b in terrs[i + 1:]:
            if a.strength <= 0 or b.strength <= 0 or not _overlap(a, b):
                continue
            sa = world.species(a.species_id)
            sb = world.species(b.species_id)
            if a.species_id != b.species_id and sa.trophic != sb.trophic:
                continue
            pa = a.strength * (1 + len(present.get(a.group_id, [])))
            pb = b.strength * (1 + len(present.get(b.group_id, [])))
            loser = b if pa >= pb else a
            loser.strength -= 0.025
            loser.__dict__["_pressed_by"] = (a if loser is b else b).group_id
    for t in list(region.territories.values()):
        if t.strength > 0:
            continue
        del region.territories[t.id]
        g = world.groups.get(t.group_id)
        if g is not None and g.territory_id == t.id:
            g.territory_id = None
        winner_gid = t.__dict__.get("_pressed_by")
        winner = world.groups.get(winner_gid) if winner_gid else None
        name = g.name if g else "a vanished group"
        if winner is not None:
            e = world.history.record(now, "territory_captured",
                                     f"The {winner.name} drove the {name} from their ground in {region.name}.",
                                     region.id, importance=2, group=winner.id, loser=t.group_id)
            for c in present.get(t.group_id, []):
                c.memory.add(now, "territory_lost", None, region.id, c.x, c.y, 0.7, winner.name)
                c.needs["insecurity"] = min(1.0, c.needs["insecurity"] + 0.3)
            for c in present.get(winner.id, []):
                c.memory.add(now, "territory_won", None, region.id, c.x, c.y, 0.6, name)
            sim.migration.invalidate()
            region.flags["_last_capture"] = e.id
        elif g is not None and now - region.flags.get(f"claimed_{g.id}", -10 ** 9) > TICKS_PER_DAY:
            world.history.record(now, "territory_abandoned",
                                 f"The {name} abandoned their territory in {region.name}.",
                                 region.id, importance=1, group=t.group_id)
    _dominance(sim, region)


def _dominance(sim, region) -> None:
    world = sim.world
    now = world.clock.tick
    best, best_v = None, 0.0
    for t in region.territories.values():
        v = t.radius * t.radius * t.strength
        if v > best_v:
            best, best_v = t, v
    gid = best.group_id if best is not None and best.strength > 0.5 else None
    prev = region.flags.get("dominant")
    if gid is not None and gid != prev:
        g = world.groups.get(gid)
        if g is not None:
            causes = [region.flags["_last_capture"]] if region.flags.get("_last_capture") else None
            world.history.record(now, "region_dominance",
                                 f"The {g.name} ({world.species(g.species_id).name}) became the dominant group of {region.name}.",
                                 region.id, importance=2, causes=causes, group=gid)
        region.flags["dominant"] = gid
    elif gid is None and prev is not None and best is None:
        region.flags["dominant"] = None


def abstract_territories(sim, region, creatures, dt: int) -> None:
    """Coarse territory upkeep for dormant regions."""
    counts: dict[int, int] = {}
    for c in creatures:
        if c.alive and c.group_id is not None:
            counts[c.group_id] = counts.get(c.group_id, 0) + 1
    hours = dt / 60.0
    for t in list(region.territories.values()):
        n = counts.get(t.group_id, 0)
        if n:
            t.strength = min(1.0, t.strength + 0.01 * n * hours * 0.5)
        else:
            t.strength -= 0.02 * hours
        if t.strength <= 0:
            del region.territories[t.id]
            g = sim.world.groups.get(t.group_id)
            if g is not None and g.territory_id == t.id:
                g.territory_id = None
