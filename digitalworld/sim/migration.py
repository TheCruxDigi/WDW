"""Migration decisions, transfers between regions and migration-wave history.

A creature compares its current region with each *connected* neighbour using
the same ecological suitability model the abstract simulation uses. It only
considers leaving when the gain beats a personal threshold (wanderlust), and
memories of regions it recently fled make it reluctant to go straight back,
which prevents circular migrations.
"""

from __future__ import annotations

from collections import deque

from ..core.clock import TICKS_PER_DAY
from .ecology import suitability, dominant_pressure


class MigrationSystem:
    EVAL_INTERVAL = 240
    COOLDOWN = 2 * TICKS_PER_DAY

    def __init__(self, sim):
        self.sim = sim
        self.recent: deque = deque(maxlen=400)   # (tick, species, src, dst, reason, cid)
        self.total = 0
        self._pop_cache: dict[int, dict] = {}
        self._pop_tick = -1

    def pop(self, rid: int) -> dict:
        now = self.sim.world.clock.tick
        if self._pop_tick != now:
            self._pop_cache.clear()
            self._pop_tick = now
        p = self._pop_cache.get(rid)
        if p is None:
            p = self._pop_cache[rid] = self.sim.world.population(rid)
        return p

    def invalidate(self) -> None:
        self._pop_tick = -1

    def evaluate(self, c, sp, region, now: int, pop_here: dict | None = None, pop_fn=None):
        world = self.sim.world
        pop_fn = pop_fn or self.pop
        here, factors = suitability(world, region, sp.id, pop_here or pop_fn(region.id), explain=True)
        c.mig_factors = factors
        c.mig_eval_tick = now
        best, best_s = None, -1e9
        for nb in sorted(region.gates):
            r2 = world.regions[nb]
            s = suitability(world, r2, sp.id, pop_fn(nb), extra=1)
            if s < 0:
                continue
            left = [m for m in c.memory.recall("left_region", region=nb)]
            if left:
                s -= 0.3 * max(m.salience(now) for m in left)
            bad = c.memory.recall("attacked_by", region=nb)
            if bad:
                s -= 0.1
            if s > best_s:
                best, best_s = nb, s
        margin = 0.16 - 0.1 * c.traits["wanderlust"]
        gain = best_s - here
        if best is None or gain <= margin or best_s < 0.12:
            c.migrate_target = None
            c.mig_urgency = 0.0
            c.migrate_reason = ""
            return None
        urg = min(1.0, (gain - margin) * 1.5 + max(0.0, 0.25 - here) * 1.4) * (0.55 + 0.6 * c.traits["wanderlust"])
        if now - c.last_migration_tick < self.COOLDOWN and here > 0.0:
            urg *= 0.3
        c.migrate_target = best
        c.mig_urgency = urg
        c.mig_eval_tick = now
        c.migrate_reason = dominant_pressure(factors)
        return best

    def urgency(self, c) -> float:
        """Structural pull of a better region plus the creature's own distress:
        a starving animal in a barren region wants out far more than a fed one."""
        if c.migrate_target is None:
            return 0.0
        f = c.mig_factors or {}
        press = max(c.needs["hunger"] * (1.0 - f.get("food", 1.0)),
                    c.needs["thirst"] * (1.0 - f.get("water", 1.0)),
                    c.taint * min(1.0, -f.get("corruption", 0.0) * 3.0),
                    c.needs["insecurity"] * f.get("danger", 0.0))
        return min(1.3, c.mig_urgency + press * 0.9 + 0.1 * min(c.search_fail, 5))

    def update(self) -> None:
        """Staggered re-evaluation for loaded creatures."""
        sim = self.sim
        world = sim.world
        now = world.clock.tick
        k = self.EVAL_INTERVAL
        for c in list(world.creatures.values()):
            if not c.alive:
                continue
            due = (now + c.id) % k == 0
            distressed = (c.needs["hunger"] > 0.6 or c.needs["thirst"] > 0.6 or c.search_fail >= 2) \
                and now - c.mig_eval_tick >= 60
            if not due and not distressed:
                continue
            region = world.regions[c.region_id]
            g = world.groups.get(c.group_id) if c.group_id else None
            if g is not None and g.leader_id != c.id and g.leader_id in world.creatures:
                continue  # followers defer to the leader's judgement
            self.evaluate(c, world.species(c.species_id), region, now)

    # ---------------------------------------------------------------- history
    def record(self, c, src, dst, reason: str, tick: int) -> None:
        world = self.sim.world
        self.total += 1
        self.recent.append((tick, c.species_id, src.id, dst.id, reason, c.id))
        same = [r for r in self.recent if r[1] == c.species_id and r[2] == src.id and r[3] == dst.id
                and tick - r[0] <= TICKS_PER_DAY]
        sp = world.species(c.species_id)
        key = f"wave:{c.species_id}:{src.id}:{dst.id}"
        last = src.flags.get(key, -10 ** 9)
        if len(same) >= 3 and tick - last > TICKS_PER_DAY:
            src.flags[key] = tick
            causes = []
            for kind in ("resource_collapse", "corruption_rise", "species_arrived", "territory_captured"):
                e = world.history.latest(kind, src.id, within=3 * TICKS_PER_DAY, now=tick)
                if e is not None:
                    causes.append(e.id)
            reasons = {}
            for r in same:
                reasons[r[4]] = reasons.get(r[4], 0) + 1
            main = max(reasons.items(), key=lambda kv: kv[1])[0] if reasons else reason
            world.history.record(tick, "migration_wave",
                                 f"{len(same)} {sp.name}s left {src.name} for {dst.name} ({main}).",
                                 src.id, importance=2, causes=causes, species=c.species_id,
                                 dst=dst.id, count=len(same), reason=main)
        elif c.group_id is not None:
            g = world.groups.get(c.group_id)
            if g is not None and g.leader_id == c.id:
                world.history.record(tick, "group_migration",
                                     f"{c.name}, leader of the {g.name}, led them from {src.name} toward {dst.name} ({reason}).",
                                     src.id, importance=1, species=c.species_id, dst=dst.id, group=g.id)
