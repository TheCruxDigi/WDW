"""Simulation orchestrator — the authoritative world update.

Order of a tick:
  clock → spatial index → environment (weather, regrowth, corruption) →
  creatures (needs, utility AI, actions, movement) → migration evaluation →
  social/territory upkeep → deferred removals/additions/transfers →
  dormant regions (abstract LOD) → chronicle → population balance.

Everything here is headless; the renderer only reads state and calls the
public command methods (player actions, save/load via persistence).
"""

from __future__ import annotations

import logging
import math
import random

from ..ai.actions import ACTIONS_BY_NAME, strength
from ..ai.brain import Brain
from ..config import Config
from ..core.clock import TICKS_PER_DAY, TICKS_PER_HOUR
from ..core.rng import make_rng
from ..entities.creature import Creature
from ..entities.memory import PLAYER_ID
from ..nav.pathfinding import PathBudget
from ..world.generator import generate_world, choose_start_region, make_creature, spawn_group
from ..world.region import Region, Structure, CORR_CELL
from ..world.world import World
from . import ecology, corruption, social, chronicle
from .abstract import AbstractSimulator
from .migration import MigrationSystem
from .needs import update_needs, relax_relations

log = logging.getLogger("digitalworld.sim")


class Simulation:
    def __init__(self, world: World):
        self.world = world
        self.config: Config = world.config
        self.rng = random.Random(world.rng.random())
        self.brain = Brain(self)
        self.migration = MigrationSystem(self)
        self.abstract = AbstractSimulator(self)
        self.path_budget = PathBudget(self.config.path_budget_per_tick)
        self._dead: list[Creature] = []
        self._born: list[Creature] = []
        self._transfers: list[tuple] = []
        self._food_index_cache: dict = {}
        self.focus_region: int | None = None
        self.errors = 0
        self.ticks_run = 0

    # ================================================================ creation
    @classmethod
    def new(cls, config: Config, with_player: bool = True) -> "Simulation":
        world = World(config)
        generate_world(world)
        world.clock.tick = 7 * TICKS_PER_HOUR  # dawn of the first day
        for r in world.regions.values():
            r.last_sim_tick = world.clock.tick
        sim = cls(world)
        start = choose_start_region(world)
        sim.focus_region = start
        if with_player:
            from ..player.player import Player
            r = world.regions[start]
            x, y = r.nearest_walkable(r.width / 2, r.height / 2)
            world.player = Player(start, x + 0.5, y + 0.5)
            world.player.home = (start, x + 0.5, y + 0.5)
        # every region but the active neighbourhood becomes dormant immediately
        for r in world.regions.values():
            r.flags.setdefault("band", r.corruption_band())
        chronicle.world_census(sim)
        sim.update_lod(force_all=True)
        world.history.record(world.clock.tick, "genesis", f"The world awoke. {len(world.regions)} regions, "
                             f"{world.total_population()} creatures of {len(world.content.species)} species.",
                             None, importance=3)
        if world.player is not None:
            from ..player import interactions
            interactions.enter_region(sim, world.player.region_id, first=True)
        return sim

    def rng_for(self, *keys) -> random.Random:
        return make_rng(self.world.seed, *keys)

    # ================================================================ LOD
    def desired_active(self) -> set[int]:
        world = self.world
        focus = world.player.region_id if world.player is not None else self.focus_region
        if focus is None:
            return set()
        active = {focus}
        frontier = [focus]
        for _ in range(self.config.active_radius):
            nxt = []
            for rid in frontier:
                for nb in world.regions[rid].gates:
                    if nb not in active:
                        active.add(nb)
                        nxt.append(nb)
            frontier = nxt
        return active

    def update_lod(self, force_all: bool = False) -> None:
        want = self.desired_active()
        for r in list(self.world.regions.values()):
            if r.loaded and (r.id not in want or force_all and r.id not in want):
                self.unload_region(r.id)
        for rid in sorted(want):
            if not self.world.regions[rid].loaded:
                self.load_region(rid)

    def load_region(self, rid: int) -> None:
        world = self.world
        region = world.regions[rid]
        if region.loaded:
            return
        now = world.clock.tick
        self.abstract.advance(region, now)
        region.build_terrain()
        added = 0
        for d in region.dormant_creatures:
            try:
                c = Creature.from_dict(d)
            except (KeyError, TypeError, ValueError):
                log.warning("dropping unreadable creature record in region %s", rid)
                continue
            if c.id in world.creatures:
                log.error("duplicate creature id %s while loading region %s — skipped", c.id, rid)
                continue
            sp = world.species(c.species_id)
            x, y = region.nearest_walkable(c.x, c.y, sp.has("swim"))
            if (int(c.x), int(c.y)) != (x, y):
                c.x, c.y = x + 0.5, y + 0.5
            c.region_id = rid
            c.reset_runtime()
            c.next_decide_tick = now + (c.id % 7)
            world.creatures[c.id] = c
            added += 1
        region.dormant_creatures = []
        for n in region.resources.values():
            n.users = []
        region.loaded = True
        region.last_sim_tick = now
        world.invalidate_region_caches(rid)
        log.debug("loaded region %s (%s) with %d creatures", rid, region.name, added)

    def unload_region(self, rid: int) -> None:
        world = self.world
        region = world.regions[rid]
        if not region.loaded:
            return
        recs = []
        for c in list(world.creatures.values()):
            if c.region_id != rid:
                continue
            if c.action is not None:
                self.brain.interrupt(c)
            if c.alive:
                recs.append(c.to_dict())
            del world.creatures[c.id]
        region.dormant_creatures = recs
        region.release_terrain()
        region.loaded = False
        region.last_sim_tick = world.clock.tick
        world.spatial.pop(rid, None)
        world.invalidate_region_caches(rid)
        log.debug("unloaded region %s with %d creatures", rid, len(recs))

    # ================================================================ tick
    def step(self) -> None:
        world = self.world
        prof = world.profiler
        world.clock.advance(1)
        now = world.clock.tick
        self.ticks_run += 1
        self.path_budget.reset()
        if now % 30 == 0:
            self._food_index_cache.clear()
        loaded = world.loaded_regions()
        with prof.section("spatial"):
            world.rebuild_spatial()
        with prof.section("environment"):
            for r in loaded:
                if now % 10 == 0:
                    corruption.update_weather(self, r)
                if now % 5 == 0:
                    ecology.regenerate(world, r, 5)
                if now % 20 == 0:
                    wyrms = [(c.x, c.y) for c in world.creatures_in(r.id)
                             if world.species(c.species_id).has("emit_corruption")]
                    corruption.update_region(self, r, 20, wyrms)
            if now % 60 == 0:
                corruption.spread_between_regions(self, 60)
        with prof.section("creatures"):
            for c in list(world.creatures.values()):
                if not c.alive:
                    continue
                region = world.regions[c.region_id]
                sp = world.species(c.species_id)
                c.prev_x, c.prev_y = c.x, c.y
                try:
                    if (now + c.id) % 2 == 0:  # needs change slowly: update every other tick
                        update_needs(self, c, sp, region, 2)
                    if c.alive:
                        self.brain.update(c, sp, region)
                    if c.alive and sp.has("break_structures"):
                        self._erode_structures(c, sp, region)
                except Exception:
                    self.errors += 1
                    log.exception("creature %s update failed; resetting its behaviour", c.id)
                    c.reset_runtime()
        with prof.section("relations"):
            if now % 120 == 0:
                for c in world.creatures.values():
                    relax_relations(c, now, 120)
        with prof.section("migration"):
            self.migration.update()
        with prof.section("social"):
            if now % 30 == 0:
                for r in loaded:
                    social.update_territories(self, r)
            if now % 60 == 0:
                social.update_groups(self)
        with prof.section("bookkeeping"):
            self._flush()
        with prof.section("dormant"):
            if now % self.config.dormant_refresh_ticks == 0:
                self._advance_one_dormant()
        with prof.section("chronicle"):
            if now % 60 == 0:
                for r in world.regions.values():
                    chronicle.census(self, r)
            if now % (6 * TICKS_PER_HOUR) == 0:
                self._balance_populations()
        if world.player is not None:
            from ..player import interactions
            with prof.section("player"):
                interactions.player_tick(self)

    def run(self, ticks: int) -> None:
        for _ in range(ticks):
            self.step()

    def _advance_one_dormant(self) -> None:
        dormant = [r for r in self.world.regions.values() if not r.loaded]
        if not dormant:
            return
        r = min(dormant, key=lambda r: (r.last_sim_tick, r.id))
        self.abstract.advance(r, self.world.clock.tick)

    def _flush(self) -> None:
        world = self.world
        if self._dead:
            for c in self._dead:
                world.creatures.pop(c.id, None)
            self._dead.clear()
        if self._born:
            for c in self._born:
                region = world.regions[c.region_id]
                if region.loaded:
                    world.creatures[c.id] = c
                else:
                    region.dormant_creatures.append(c.to_dict())
            self._born.clear()
        if self._transfers:
            pending, self._transfers = self._transfers, []
            for c, target, reason in pending:
                if c.alive and c.id in world.creatures:
                    self.transfer(c, world.regions[c.region_id], world.regions[target], reason)

    # ================================================================ fast-forward
    def fast_forward(self, ticks: int, chunk: int = 360) -> list:
        """Advance the whole world abstractly (offline time, 'wait' command)."""
        world = self.world
        start = world.clock.tick
        hist_start = world.history.next_id
        for r in list(world.regions.values()):
            if r.loaded:
                self.unload_region(r.id)
        remaining = int(ticks)
        while remaining > 0:
            step = min(chunk, remaining)
            world.clock.advance(step)
            now = world.clock.tick
            for rid in sorted(world.regions):
                self.abstract.advance(world.regions[rid], now)
            corruption.spread_between_regions(self, step)
            social.update_groups(self)
            for r in world.regions.values():
                chronicle.census(self, r)
            self._balance_populations()
            remaining -= step
        self.update_lod()
        if world.player is not None:
            world.player.prev_x, world.player.prev_y = world.player.x, world.player.y
        log.info("fast-forwarded %d ticks (%s -> %s)", ticks, start, world.clock.tick)
        return [e for e in world.history.entries if e.id >= hist_start]

    # ================================================================ services for actions
    def consume(self, region, node, amount, consumer_id=None) -> float:
        return ecology.consume(self.world, region, node, amount, consumer_id)

    def release_node(self, c, node_id) -> None:
        if node_id is None:
            return
        region = self.world.regions.get(c.region_id)
        if region is None:
            return
        node = region.resources.get(node_id)
        if node is not None and c.id in node.users:
            node.users.remove(c.id)

    def food_index(self, region, species_id: str, pop_override: dict | None = None) -> float:
        key = (region.id, species_id)
        if pop_override is None:
            v = self._food_index_cache.get(key)
            if v is not None:
                return v
        world = self.world
        sp = world.species(species_id)
        pop = pop_override if pop_override is not None else world.population(region.id)
        food = ecology.food_totals(region)
        supply = sum(food.get(k, 0.0) * w for k, w in sp.diet.items() if k != "meat")
        if sp.is_predator:
            supply += sum(pop.get(p, 0) * world.species(p).meat_value for p in sp.prey) * 0.35
            supply += food.get("meat", 0.0)
        demand = 0.0
        for sid, n in pop.items():
            o = world.species(sid)
            shared = sum(min(o.diet.get(k, 0), sp.diet.get(k, 0)) for k in sp.diet if k != "meat")
            if sp.is_predator and o.is_predator:
                shared += 1.0
            if sid == species_id:
                shared = 1.0
            demand += n * o.appetite * min(1.0, shared)
        v = supply / (demand * 2.0 + sp.appetite)
        if pop_override is None:
            self._food_index_cache[key] = v
        return v

    def compete_for_node(self, c, sp, node) -> bool:
        """Try to displace the weakest feeder. Returns True if a slot was won."""
        world = self.world
        now = world.clock.tick
        users = [world.creatures[u] for u in node.users if u in world.creatures and u != c.id]
        if not users:
            node.users = []
            return True
        rivals = [u for u in users if c.group_id is None or u.group_id != c.group_id]
        if not rivals or c.traits["aggression"] < 0.2:
            return False
        weakest = min(rivals, key=lambda u: strength(u, world.species(u.species_id)))
        wsp = world.species(weakest.species_id)
        mine = strength(c, sp) * (0.5 + c.traits["aggression"]) * (0.6 + c.needs["hunger"])
        theirs = strength(weakest, wsp) * (0.5 + weakest.traits["boldness"])
        if mine <= theirs:
            c.relation(weakest.id, weakest.species_id).adjust(now, rivalry=0.08)
            return False
        node.users.remove(weakest.id)
        weakest.memory.add(now, "displaced_by", c.id, node and c.region_id, c.x, c.y, 0.5, node.kind)
        weakest.relation(c.id, c.species_id).adjust(now, hostility=0.12, rivalry=0.2, fear=0.05)
        c.memory.add(now, "displaced", weakest.id, c.region_id, c.x, c.y, 0.3, node.kind)
        c.relation(weakest.id, weakest.species_id).adjust(now, rivalry=0.1)
        self.brain.interrupt(weakest)
        weakest.ban(f"n{node.id}", now, 180)
        world.bus.publish("competition", now, winner=c.id, loser=weakest.id, node=node.id, region=c.region_id)
        return True

    def attack(self, att, tgt) -> bool:
        """One strike. Returns True if the target died."""
        world = self.world
        now = world.clock.tick
        asp = world.species(att.species_id)
        tsp = world.species(tgt.species_id)
        dmg = asp.attack * self.rng.uniform(0.6, 1.2) * (0.5 + 0.5 * att.health)
        dmg *= 1.0 - tsp.defense / (tsp.defense + 20.0)
        if tgt.sleeping:
            dmg *= 1.3
        tgt.health -= dmg / tsp.health
        first = now - tgt.under_attack_tick > 30 or tgt.under_attack_by != att.id
        tgt.under_attack_by = att.id
        tgt.under_attack_tick = now
        att.counters["fights"] += 1
        if first:
            tgt.memory.add(now, "attacked_by", att.id, tgt.region_id, tgt.x, tgt.y, 0.7, asp.name)
            tgt.relation(att.id, att.species_id).adjust(now, fear=0.2 * (1.2 - tgt.traits["boldness"]), hostility=0.15)
            att.memory.add(now, "attacked", tgt.id, att.region_id, att.x, att.y, 0.3, tsp.name)
            world.bus.publish("attack", now, attacker=att.id, target=tgt.id, region=tgt.region_id)
        if tgt.sleeping or (tgt.action is not None and tgt.action["name"] not in ("flee", "defend")):
            if tgt.action is not None:
                self.brain.interrupt(tgt)
            tgt.next_decide_tick = now
        if tgt.health <= 0:
            self.kill(tgt, f"killed by {asp.name} {att.name}", att)
            return True
        return False

    def kill(self, c, cause: str, killer=None) -> None:
        if not c.alive:
            return
        world = self.world
        now = world.clock.tick
        sp = world.species(c.species_id)
        region = world.regions[c.region_id]
        if c.action is not None:
            try:
                ACTIONS_BY_NAME[c.action["name"]].stop(self, c, c.action)
            except Exception:
                log.exception("stop on death failed")
            c.action = None
        c.alive = False
        c.death_cause = cause
        c.action_name = "dead"
        if killer is not None:
            killer.counters["kills"] += 1
            ecology.add_remains(world, region, c.x, c.y, sp.meat_value)
        elif cause == "corruption" and sum(1 for n in region.resources.values() if n.kind == "bytebloom") < 16:
            # bodies of the corrupted dissolve into bytebloom — corruption feeds itself
            from ..world.region import ResourceNode
            n = ResourceNode(world.new_id(), "bytebloom", int(c.x), int(c.y), 6.0, 18.0)
            region.resources[n.id] = n
        elif cause == "old age":
            ecology.add_remains(world, region, c.x, c.y, sp.meat_value * 0.4)
        else:
            ecology.add_remains(world, region, c.x, c.y, sp.meat_value * 0.6)
        # witnesses
        for o in world.nearby(region.id, c.x, c.y, 10):
            if o is c or not o.alive:
                continue
            kin = c.group_id is not None and o.group_id == c.group_id
            if kin or (c.id in o.relations and o.relations[c.id].affinity > 0.3):
                o.memory.add(now, "lost_kin", c.id, region.id, c.x, c.y, 0.8, f"{c.name} ({cause})")
                o.needs["insecurity"] = min(1.0, o.needs["insecurity"] + 0.25)
            elif killer is not None and o.species_id == c.species_id:
                o.memory.add(now, "witnessed_kill", killer.id, region.id, c.x, c.y, 0.5, cause)
            if killer is not None and o is not killer and (kin or o.species_id == c.species_id):
                o.relation(killer.id, killer.species_id).adjust(now, fear=0.2, hostility=0.15)
        self.record_death(c, sp, region, cause, killer, tick=now)
        self._dead.append(c)

    def record_death(self, c, sp, region, cause, killer, tick: int) -> None:
        """Bookkeeping shared by detailed and abstract deaths."""
        world = self.world
        stats = region.flags.setdefault("deaths", {})
        key = "predation" if cause.startswith("killed") else cause
        stats[key] = stats.get(key, 0) + 1
        g = world.groups.get(c.group_id) if c.group_id else None
        notable = 0
        what = []
        if g is not None:
            if c.id in g.members:
                g.members.remove(c.id)
            if g.leader_id == c.id:
                notable = max(notable, 2 if len(g.members) >= 4 or sp.is_predator else 1)
                what.append(f"leader of the {g.name}")
                g.leader_id = None
                g.__dict__["_prev_leader"] = c.id
        prel = c.relations.get(PLAYER_ID)
        if prel is not None and prel.trust > 0.5:
            notable = max(notable, 2)
            what.append("friend of the Wayfinder")
            if world.player is not None:
                world.player.note(tick, f"{c.name} the {sp.name}, who trusted you, has died ({cause}).")
        age = c.age_days(tick)
        if age > sp.lifespan_days * 0.9 and cause == "old age" and c.counters.get("children", 0) >= 2:
            notable = max(notable, 1)
            what.append(f"elder with {c.counters['children']} offspring")
        if notable:
            causes = []
            e = world.history.latest("resource_collapse", region.id, within=2 * TICKS_PER_DAY, now=tick)
            if e is not None and cause in ("starvation", "thirst"):
                causes.append(e.id)
            e = world.history.latest("corruption_rise", region.id, within=4 * TICKS_PER_DAY, now=tick)
            if e is not None and cause == "corruption":
                causes.append(e.id)
            entry = world.history.record(tick, "notable_death",
                                         f"{c.name} the {sp.name} ({', '.join(what)}) died in {region.name}: {cause}.",
                                         region.id, importance=notable, causes=causes, creature=c.id,
                                         species=sp.id, killer=killer.id if killer else None)
            if g is not None:
                g.__dict__["_leader_death_event"] = entry.id
        world.bus.publish("death", tick, creature=c.id, species=sp.id, cause=cause, region=region.id,
                          killer=killer.id if killer else None)

    def make_offspring(self, region, parent, partner, tick: int, rng) -> list[Creature]:
        world = self.world
        sp = world.species(parent.species_id)
        n = rng.randint(*sp.litter)
        parents = [parent] + ([partner] if partner is not None else [])
        kids = []
        for _ in range(n):
            x = parent.x + rng.uniform(-0.6, 0.6)
            y = parent.y + rng.uniform(-0.6, 0.6)
            if region.terrain is not None:
                tx, ty = region.nearest_walkable(x, y, sp.has("swim"))
                x, y = tx + 0.5, ty + 0.5
            k = make_creature(world, sp.id, region, x, y, tick, rng, parents)
            k.needs.update({"hunger": 0.2, "thirst": 0.2, "fatigue": 0.1})
            k.health = 0.8
            k.memory.add(tick, "born", parent.id, region.id, x, y, 0.5,
                         " & ".join(p.name for p in parents))
            k.known_nodes = {nid: list(v) for nid, v in list(parent.known_nodes.items())[-12:]}
            k.group_id = parent.group_id
            g = world.groups.get(parent.group_id) if parent.group_id else None
            if g is not None:
                g.members.append(k.id)
            for p in parents:
                r = k.relation(p.id, p.species_id)
                r.trust, r.affinity, r.familiarity = 0.7, 0.7, 0.8
                pr = p.relation(k.id, k.species_id)
                pr.trust, pr.affinity, pr.familiarity = 0.6, 0.75, 0.8
            kids.append(k)
        cool = int(sp.repro_cooldown_days * TICKS_PER_DAY)
        for p in parents:
            p.repro_ready_tick = tick + int(cool * rng.uniform(0.85, 1.2))
            p.counters["children"] = p.counters.get("children", 0) + n
            p.needs["hunger"] = min(1.0, p.needs["hunger"] + 0.12)
        births = region.flags.setdefault("births", {})
        births[sp.id] = births.get(sp.id, 0) + n
        world.bus.publish("birth", tick, parent=parent.id, partner=partner.id if partner else None,
                          species=sp.id, count=n, region=region.id)
        return kids

    def birth(self, parent, partner) -> None:
        region = self.world.regions[parent.region_id]
        kids = self.make_offspring(region, parent, partner, self.world.clock.tick, self.rng)
        self._born.extend(kids)

    def force_flee(self, c, threat) -> None:
        self.brain.interrupt(c)
        from ..ai.actions import Option
        flee = ACTIONS_BY_NAME["flee"]
        c.action = flee.start(self, c, self.world.species(c.species_id),
                              Option(flee, 1.0, threat.id if threat is not None else None, "forced"))
        c.action_name = "flee"
        c.next_decide_tick = self.world.clock.tick + 20

    def intimidate(self, c, sp, o, terr) -> None:
        world = self.world
        now = world.clock.tick
        osp = world.species(o.species_id)
        ratio = strength(o, osp) / max(1.0, strength(c, sp))
        world.bus.publish("territorial_clash", now, owner=c.id, intruder=o.id, region=o.region_id)
        if ratio > 1.3 and o.traits["boldness"] > 0.55:
            self.attack(c, o)
            if c.alive and o.alive:
                self.attack(o, c)
            return
        o.memory.add(now, "chased_off", c.id, o.region_id, o.x, o.y, 0.5, f"{sp.name} territory")
        o.relation(c.id, c.species_id).adjust(now, fear=0.2, hostility=0.12, rivalry=0.1)
        c.relation(o.id, o.species_id).adjust(now, hostility=0.05, rivalry=0.1)
        if terr is not None:
            o.ban(f"terr{terr.id}", now, TICKS_PER_DAY)
            terr.strength = min(1.0, terr.strength + 0.04)
        self.force_flee(o, c)

    def record_help(self, helper, ally) -> None:
        now = self.world.clock.tick
        ally.memory.add(now, "helped_by", helper.id, ally.region_id, ally.x, ally.y, 0.6)
        ally.relation(helper.id, helper.species_id).adjust(now, trust=0.2, affinity=0.15)
        helper.memory.add(now, "helped", ally.id, helper.region_id, helper.x, helper.y, 0.3)
        helper.relation(ally.id, ally.species_id).adjust(now, affinity=0.05)
        helper.counters["helped"] = helper.counters.get("helped", 0) + 1
        self.world.bus.publish("help", now, helper=helper.id, ally=ally.id, region=ally.region_id)

    def player_intimidated(self, c, sp) -> None:
        from ..player import interactions
        interactions.intimidated_by(self, c, sp)

    def scan_surroundings(self, c, sp, region) -> None:
        now = self.world.clock.tick
        p = c.percept_cache
        r2 = (p.radius if p else sp.perception) ** 2
        for n in region.resources.values():
            if (n.kind in sp.diet or n.kind in ("water", "shelter")) and n.amount > 1:
                if (n.x - c.x) ** 2 + (n.y - c.y) ** 2 <= r2:
                    if n.id not in c.known_nodes:
                        c.counters["discoveries"] = c.counters.get("discoveries", 0) + 1
                    c.remember_node(n, region.id, now)

    def share_knowledge(self, src, dst, n: int = 2) -> None:
        """Social learning: companions pass on where water and food are."""
        dsp = self.world.species(dst.species_id)
        shared = 0
        for nid, v in sorted(src.known_nodes.items(), key=lambda kv: -kv[1][5]):
            if shared >= n:
                break
            if nid in dst.known_nodes or v[0] != dst.region_id:
                continue
            if v[3] == "water" or v[3] in dsp.diet:
                dst.known_nodes[nid] = list(v)
                shared += 1

    def near_beacon(self, region, x, y, r: float = 7.0) -> bool:
        for s in region.structures.values():
            if s.kind == "beacon" and s.complete and (s.x - x) ** 2 + (s.y - y) ** 2 <= r * r:
                return True
        return False

    # ---------------------------------------------------------------- structures
    def find_beacon_site(self, c, region, terr):
        beacons = [s for s in region.structures.values() if s.kind == "beacon"]
        if len(beacons) >= 4:
            return None
        cx, cy = (terr.x, terr.y) if terr is not None else (c.x, c.y)
        f = region.corruption
        best, best_v = None, 0.0
        for y in range(f.shape[0]):
            for x in range(f.shape[1]):
                v = float(f[y, x])
                if v < 0.18:
                    continue
                tx, ty = x * CORR_CELL + 2, y * CORR_CELL + 2
                d = math.hypot(tx - cx, ty - cy)
                if d > 16:
                    continue
                if any((s.x - tx) ** 2 + (s.y - ty) ** 2 < 81 for s in beacons):
                    continue
                score = v - d * 0.01
                if score > best_v:
                    best, best_v = (tx, ty, v), score
        if best is None or region.terrain is None:
            return None
        x, y = region.nearest_walkable(best[0], best[1])
        return x, y, best[2]

    def begin_structure(self, region, kind, x, y, builder) -> Structure | None:
        if not region.is_walkable(x, y):
            return None
        for s in region.structures.values():
            if abs(s.x - x) + abs(s.y - y) < 3:
                return s if s.kind == kind else None
        s = Structure(self.world.new_id(), kind, x, y, 1.0, 0.0,
                      builder.group_id if builder is not None and builder != PLAYER_ID else None,
                      builder.id if hasattr(builder, "id") else PLAYER_ID, self.world.clock.tick)
        region.structures[s.id] = s
        return s

    def complete_structure(self, region, s, builder) -> None:
        world = self.world
        now = world.clock.tick
        s.progress = 1.0
        s.built_tick = now
        if builder is None or builder == PLAYER_ID:
            who = "The Wayfinder"
        else:
            g = world.groups.get(builder.group_id) if builder.group_id else None
            who = f"{builder.name} of the {g.name}" if g else builder.name
        cause = world.history.latest("corruption_rise", region.id, within=6 * TICKS_PER_DAY, now=now)
        world.history.record(now, "structure_built", f"{who} raised a lumen beacon in {region.name}.",
                             region.id, importance=2, causes=[cause.id] if cause else None,
                             structure=s.id, builder=getattr(builder, "id", PLAYER_ID))
        world.bus.publish("structure_built", now, region=region.id, structure=s.id)

    def destroy_structure(self, region, s, cause: str, by=None) -> None:
        world = self.world
        now = world.clock.tick
        region.structures.pop(s.id, None)
        g = world.groups.get(s.owner_group) if s.owner_group else None
        owner = f" of the {g.name}" if g else ""
        e = world.history.record(now, "structure_destroyed",
                                 f"A beacon{owner} in {region.name} fell: {cause}.", region.id,
                                 importance=2, structure=s.id, by=by)
        world.bus.publish("structure_destroyed", now, region=region.id, structure=s.id, cause=cause)
        if g is not None:
            for mid in g.members:
                m = world.creatures.get(mid)
                if m is not None and m.region_id == region.id:
                    m.memory.add(now, "territory_lost", by if isinstance(by, int) else None, region.id,
                                 s.x, s.y, 0.6, "our beacon fell")
                    if by is not None:
                        m.relation(by, "player" if by == PLAYER_ID else "").adjust(now, hostility=0.3, trust=-0.3)
        return e

    def _erode_structures(self, c, sp, region) -> None:
        for s in list(region.structures.values()):
            if s.kind == "beacon" and (s.x + 0.5 - c.x) ** 2 + (s.y + 0.5 - c.y) ** 2 < 4.0:
                s.integrity -= 0.008
                c.status_icon = "glitch"
                if s.integrity <= 0:
                    self.destroy_structure(region, s, f"shattered by the Glitchwyrm {c.name}", by=c.id)

    # ---------------------------------------------------------------- transfers
    def request_transfer(self, c, target: int, reason: str) -> None:
        self._transfers.append((c, target, reason))

    def transfer(self, c, src: Region, dst: Region, reason: str, tick: int | None = None,
                 abstract: bool = False) -> None:
        """Move a creature through the gate from src to dst, preserving identity."""
        world = self.world
        now = world.clock.tick if tick is None else tick
        if dst.id not in src.gates:
            log.warning("illegal transfer %s -> %s for creature %s", src.id, dst.id, c.id)
            return
        gx, gy = dst.gates[src.id]
        # step one tile inside the destination
        ix = gx + (1 if gx == 0 else -1 if gx == dst.width - 1 else 0)
        iy = gy + (1 if gy == 0 else -1 if gy == dst.height - 1 else 0)
        if c.action is not None and not abstract:
            self.brain.interrupt(c)
        c.region_id = dst.id
        c.x, c.y = ix + 0.5, iy + 0.5
        c.prev_x, c.prev_y = c.x, c.y
        c.path = []
        c.path_goal = None
        c.last_migration_tick = now
        c.migration_from = src.id
        c.migrate_target = None
        c.mig_urgency = 0.0
        c.counters["migrations"] = c.counters.get("migrations", 0) + 1
        c.memory.add(now, "left_region", None, src.id, c.x, c.y, 0.6, reason or "moved on")
        c.memory.add(now, "migrated", None, dst.id, c.x, c.y, 0.4, f"from {src.name}")
        if dst.loaded:
            if dst.terrain is not None:
                sp = world.species(c.species_id)
                tx, ty = dst.nearest_walkable(c.x, c.y, sp.has("swim"))
                c.x, c.y = tx + 0.5, ty + 0.5
            c.reset_runtime()
            world.creatures[c.id] = c
        else:
            world.creatures.pop(c.id, None)
            dst.dormant_creatures.append(c.to_dict())
        g = world.groups.get(c.group_id) if c.group_id else None
        if g is not None and g.leader_id == c.id:
            g.region_id = dst.id
        self.migration.record(c, src, dst, reason, now)
        self.migration.invalidate()
        world.bus.publish("migration", now, creature=c.id, species=c.species_id, src=src.id, dst=dst.id,
                          reason=reason)

    # ---------------------------------------------------------------- balance
    def _balance_populations(self) -> None:
        """Last-resort immigration from beyond the map edge ("the Outer Static").

        Prevents accidental world-wide extinction; it never tops up a species
        that still exists, and is recorded in history so it is never silent.
        """
        world = self.world
        now = world.clock.tick
        totals: dict[str, int] = {sid: 0 for sid in world.content.species}
        for r in world.regions.values():
            for sid, n in world.population(r.id).items():
                totals[sid] = totals.get(sid, 0) + n
        W, H = self.config.world_width, self.config.world_height
        for sid, n in sorted(totals.items()):
            sp = world.species(sid)
            minimum = 1 if sp.is_predator else 3
            if n >= minimum:
                continue
            key = f"immigration_{sid}"
            last = world.flags.get(key, -10 ** 9)
            if now - last < 3 * TICKS_PER_DAY:
                continue
            edge = [r for r in world.regions.values() if r.gx in (0, W - 1) or r.gy in (0, H - 1)]
            best = max(edge, key=lambda r: ecology.suitability(world, r, sid))
            if ecology.suitability(world, best, sid) < 0.15:
                continue
            world.flags[key] = now
            rng = self.rng_for("immigration", sid, now)
            size = rng.randint(*sp.group_size) if sp.social != "solitary" else rng.randint(1, 2)
            if best.loaded:
                members = spawn_group(world, best, sid, size, rng, now=now, founding=False)
                for c in members:
                    world.creatures[c.id] = c
            else:
                best.build_terrain()
                members = spawn_group(world, best, sid, size, rng, now=now, founding=False)
                best.release_terrain()
                for c in members:
                    best.dormant_creatures.append(c.to_dict())
            world.history.record(now, "immigration",
                                 f"{size} {sp.name}{'s' if size > 1 else ''} wandered in from the Outer Static into {best.name}.",
                                 best.id, importance=2, species=sid, count=size)
