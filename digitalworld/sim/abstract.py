"""Abstract (low level-of-detail) simulation for dormant regions.

Instead of replaying every tick, a dormant region is advanced in coarse steps
(default one in-world hour). The same rates and the same suitability model as
the detailed simulation are applied in aggregate, but each creature keeps its
identity: specific individuals eat, starve, are hunted, give birth, age and
migrate, and their memories/relationships are updated with what happened.
"""

from __future__ import annotations

import math

from ..core.clock import TICKS_PER_DAY
from ..entities.creature import Creature
from ..entities.memory import PLAYER_ID
from . import ecology, corruption
from .needs import relax_relations
from .social import abstract_territories

MAX_STEPS = 120


class AbstractSimulator:
    def __init__(self, sim):
        self.sim = sim
        self.steps_run = 0
        self.regions_advanced = 0

    def advance(self, region, to_tick: int) -> None:
        if region.loaded:
            return
        dt = to_tick - region.last_sim_tick
        if dt <= 0:
            return
        step = self.sim.config.abstract_step_ticks
        n = math.ceil(dt / step)
        if n > MAX_STEPS:
            step = math.ceil(dt / MAX_STEPS)
            n = math.ceil(dt / step)
        creatures: list[Creature] = []
        seen = set()
        for d in region.dormant_creatures:
            try:
                c = Creature.from_dict(d)
            except (KeyError, TypeError, ValueError):
                continue
            if c.id in seen:
                continue  # never duplicate an identity
            seen.add(c.id)
            creatures.append(c)
        t = region.last_sim_tick
        for _ in range(n):
            s = min(step, to_tick - t)
            if s <= 0:
                break
            t += s
            self._step(region, creatures, t, s)
            self.steps_run += 1
        region.dormant_creatures = [c.to_dict() for c in creatures if c.alive and c.region_id == region.id]
        region.last_sim_tick = to_tick
        self.regions_advanced += 1

    # ------------------------------------------------------------------
    def _step(self, region, creatures: list[Creature], t: int, s: int) -> None:
        sim = self.sim
        world = sim.world
        rng = sim.rng_for(region.id, "abstract", t)
        corruption.update_weather(sim, region, now=t)
        ecology.regenerate(world, region, s, tick=t)
        wyrms = [(c.x, c.y) for c in creatures if c.alive and world.species(c.species_id).has("emit_corruption")]
        corruption.update_region(sim, region, s, wyrms, now=t)
        alive = [c for c in creatures if c.alive and c.region_id == region.id]
        rng.shuffle(alive)
        nodes_by_kind: dict[str, list] = {}
        for n in region.resources.values():
            nodes_by_kind.setdefault(n.kind, []).append(n)
        for lst in nodes_by_kind.values():
            lst.sort(key=lambda n: n.id)
        pop: dict[str, int] = {}
        for c in alive:
            pop[c.species_id] = pop.get(c.species_id, 0) + 1
        heat = 1.35 if region.biome_id == "glass_dunes" else 1.0
        for c in alive:
            if not c.alive:
                continue
            sp = world.species(c.species_id)
            n = c.needs
            n["hunger"] = min(1.0, n["hunger"] + sp.rates["hunger"] * s)
            n["thirst"] = min(1.0, n["thirst"] + sp.rates["thirst"] * s * heat)
            n["fatigue"] = 0.2
            n["loneliness"] *= 0.5
            n["curiosity"] = min(1.0, n["curiosity"] + 0.1)
            fed_at = None
            # foraging
            if n["hunger"] > 0.2:
                want = n["hunger"] * sp.appetite
                for kind, mult in sorted(sp.diet.items(), key=lambda kv: -kv[1]):
                    if kind == "meat" and sp.is_predator:
                        continue
                    for node in nodes_by_kind.get(kind, ()):
                        if want <= 0:
                            break
                        got = ecology.consume(world, region, node, min(node.amount, want / mult), c.id, tick=t)
                        if got > 0:
                            want -= got * mult
                            n["hunger"] = max(0.0, n["hunger"] - got * mult / sp.appetite)
                            fed_at = node
            if sp.is_predator and n["hunger"] > 0.35:
                self._hunt(region, c, sp, alive, rng, t, s, nodes_by_kind)
            if n["thirst"] > 0.2:
                for node in nodes_by_kind.get("water", ()):
                    got = ecology.consume(world, region, node, n["thirst"] * sp.drink, c.id, tick=t)
                    if got > 0:
                        n["thirst"] = max(0.0, n["thirst"] - got / sp.drink)
                        over = region.corruption_at(node.x, node.y) - sp.corruption_tolerance
                        if over > 0:
                            c.taint = min(1.0, c.taint + over * 0.05)
                        break
            if fed_at is not None:
                c.x = fed_at.x + 0.5 + rng.uniform(-1.5, 1.5)
                c.y = fed_at.y + 0.5 + rng.uniform(-1.5, 1.5)
                c.remember_node(fed_at, region.id, t)
            # corruption exposure
            corr = region.corruption_at(c.x, c.y)
            over = corr - sp.corruption_tolerance
            if sp.corruption_affinity > 0:
                c.taint = max(0.0, c.taint - 0.0005 * s)
            elif over > 0:
                c.taint = min(1.0, c.taint + over * 0.0025 * s)
                if c.taint > 0.5:
                    c.memory.add(t, "corruption_exposure", None, region.id, c.x, c.y, 0.4)
            else:
                c.taint = max(0.0, c.taint - 0.0006 * s)
            # health
            dmg = 0.0
            if n["hunger"] > 0.93:
                dmg += 0.0012 * s
            if n["thirst"] > 0.93:
                dmg += 0.0016 * s
            if c.taint > 0.7:
                dmg += (c.taint - 0.7) * 0.003 * s
            if dmg:
                c.health -= dmg
                if n["hunger"] > 0.9:
                    c.memory.add(t, "famine", None, region.id, c.x, c.y, 0.5)
                if c.health <= 0:
                    cause = "starvation" if n["hunger"] > 0.93 else "thirst" if n["thirst"] > 0.93 else "corruption"
                    self._die(region, c, sp, cause, None, alive, t)
                    continue
            elif n["hunger"] < 0.6 and n["thirst"] < 0.6:
                c.health = min(1.0, c.health + 0.0008 * s)
            age = c.age_days(t)
            if age > sp.lifespan_days:
                excess = (age - sp.lifespan_days) / max(1.0, sp.lifespan_days * 0.2)
                if rng.random() < min(0.9, 0.0004 * s * (1 + excess * 4)):
                    self._die(region, c, sp, "old age", None, alive, t)
                    continue
            self._reproduce(region, c, sp, alive, creatures, rng, t, s)
            relax_relations(c, t, s)
        abstract_territories(sim, region, alive, s)
        # migrations every ~6 in-world hours
        if (t // 360) != ((t - s) // 360) or s >= 360:
            self._migrations(region, creatures, rng, t)

    def _hunt(self, region, c, sp, alive, rng, t, s, nodes_by_kind) -> None:
        world = self.sim.world
        meat = [n for n in nodes_by_kind.get("meat", ()) if n.amount > 1]
        for node in meat:
            got = ecology.consume(world, region, node, c.needs["hunger"] * sp.appetite, c.id, tick=t)
            c.needs["hunger"] = max(0.0, c.needs["hunger"] - got / sp.appetite)
            if c.needs["hunger"] < 0.35:
                return
        prey = [p for p in alive if p.alive and p.species_id in sp.prey and p is not c]
        if not prey:
            return
        p_success = min(0.7, 0.06 + 0.5 * len(prey) / (len(prey) + 15.0)) * min(1.0, s / 60.0) * c.needs["hunger"]
        # herds that mob predators make hunting harder
        mobbers = sum(1 for p in prey if world.species(p.species_id).has("mob"))
        p_success *= 1.0 / (1.0 + mobbers * 0.15)
        if rng.random() >= p_success:
            return
        weights = []
        for p in prey:
            psp = world.species(p.species_id)
            w = (1.25 - p.health) * (1.6 if p.age_days(t) < psp.maturity_days else 1.0)
            weights.append(max(0.05, w))
        victim = rng.choices(prey, weights)[0]
        vsp = world.species(victim.species_id)
        self._die(region, victim, vsp, f"killed by {sp.name} {c.name}", c, alive, t)
        eaten = min(vsp.meat_value, c.needs["hunger"] * sp.appetite)
        c.needs["hunger"] = max(0.0, c.needs["hunger"] - eaten / sp.appetite)
        c.counters["kills"] += 1
        left = vsp.meat_value - eaten
        if c.group_id is not None:  # the pack shares the kill
            for mate in alive:
                if left <= 1:
                    break
                if mate is not c and mate.alive and mate.group_id == c.group_id and mate.needs["hunger"] > 0.2:
                    share = min(left, mate.needs["hunger"] * sp.appetite)
                    mate.needs["hunger"] -= share / sp.appetite
                    left -= share
        if left > 3:
            ecology.add_remains(world, region, victim.x, victim.y, left)

    def _die(self, region, c, sp, cause, killer, alive, t) -> None:
        sim = self.sim
        c.alive = False
        c.death_cause = cause
        # witnesses / kin within the same region remember
        for o in alive:
            if not o.alive or o is c:
                continue
            same_group = c.group_id is not None and o.group_id == c.group_id
            if same_group:
                o.memory.add(t, "lost_kin", c.id, region.id, c.x, c.y, 0.7, f"{c.name} ({cause})")
                if killer is not None:
                    o.relation(killer.id, killer.species_id).adjust(t, fear=0.25, hostility=0.2)
        if cause.startswith("corruption"):
            pass
        elif killer is None and cause != "old age":
            ecology.add_remains(sim.world, region, c.x, c.y, sp.meat_value * 0.6)
        sim.record_death(c, sp, region, cause, killer, tick=t)

    def _reproduce(self, region, c, sp, alive, creatures, rng, t, s) -> None:
        if t < c.repro_ready_tick or not c.is_adult(t, sp):
            return
        if c.needs["hunger"] > 0.45 or c.needs["thirst"] > 0.5 or c.health < 0.6 or c.taint > 0.6:
            return
        index = self.sim.food_index(region, sp.id, pop_override=self._pop(alive))
        fertility = min(1.0, max(0.0, (index - 0.35) / 0.6))
        if fertility <= 0:
            return
        partner = None
        if sp.has("asexual"):
            if region.corruption_at(c.x, c.y) < 0.25:
                return
        else:
            for o in alive:
                if (o.alive and o.species_id == c.species_id and o.sex != c.sex and t >= o.repro_ready_tick
                        and o.is_adult(t, sp) and o.health > 0.5 and o.id not in c.parents and c.id not in o.parents):
                    partner = o
                    break
            if partner is None:
                return
        if rng.random() > 0.3 * fertility * min(1.0, s / 60.0):
            return
        kids = self.sim.make_offspring(region, c, partner, t, rng)
        for k in kids:
            creatures.append(k)
            alive.append(k)

    @staticmethod
    def _pop(alive) -> dict:
        pop: dict[str, int] = {}
        for c in alive:
            if c.alive:
                pop[c.species_id] = pop.get(c.species_id, 0) + 1
        return pop

    def _migrations(self, region, creatures, rng, t) -> None:
        sim = self.sim
        world = sim.world
        alive = [c for c in creatures if c.alive and c.region_id == region.id]
        here_pop = self._pop(alive)

        def pop_fn(rid):
            return here_pop if rid == region.id else world.population(rid)

        moved = set()
        for c in sorted(alive, key=lambda c: c.id):
            if c.id in moved or not c.alive:
                continue
            g = world.groups.get(c.group_id) if c.group_id else None
            if g is not None and g.leader_id != c.id and any(o.id == g.leader_id for o in alive):
                continue  # the leader decides for the group
            sp = world.species(c.species_id)
            target = sim.migration.evaluate(c, sp, region, t, pop_here=here_pop, pop_fn=pop_fn)
            if target is None or rng.random() > c.mig_urgency * 1.5:
                continue
            party = [c]
            if g is not None:
                party += [o for o in alive if o.group_id == g.id and o is not c and o.alive]
                g.migrate_to, g.migrate_reason, g.migrate_tick = target, c.migrate_reason, t
            for m in party:
                moved.add(m.id)
                sim.transfer(m, region, world.regions[target], c.migrate_reason, tick=t, abstract=True)
                here_pop[m.species_id] = here_pop.get(m.species_id, 1) - 1
