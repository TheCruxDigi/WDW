"""Utility-AI decision maker.

Each decision scores every applicable action option, adds commitment to the
running action (hysteresis against oscillation), adds glitch noise for
heavily tainted creatures (corruption scrambles judgement), picks the best and
records a readable trace: the top options with their reasons.
"""

from __future__ import annotations

import logging

from ..entities.creature import Decision
from .actions import ALL_ACTIONS, ACTIONS_BY_NAME, URGENT, Ctx, RUNNING, SUCCESS
from .perception import perceive

log = logging.getLogger("digitalworld.ai")

COMMITMENT = 0.14
PREEMPTED = 3
MIN_COMMIT_TICKS = 5


class Brain:
    def __init__(self, sim):
        self.sim = sim
        self._by_species: dict[str, list] = {}
        self.decisions = 0
        self.action_failures: dict[str, int] = {}
        self.action_success: dict[str, int] = {}
        self.action_preempted: dict[str, int] = {}

    def actions_for(self, sp):
        acts = self._by_species.get(sp.id)
        if acts is None:
            acts = [a for a in ALL_ACTIONS if a.applies(sp)]
            self._by_species[sp.id] = acts
        return acts

    def quick_alarm(self, c, sp, region) -> bool:
        """Cheap per-tick check that forces a re-decision when danger appears."""
        world = self.sim.world
        r = sp.perception * 0.6
        for o in world.nearby(region.id, c.x, c.y, r):
            if c.species_id in world.content.species[o.species_id].prey and not o.sleeping:
                return True
        pl = world.player
        if pl is not None and pl.region_id == region.id:
            rel = c.relations.get(0)
            if rel is not None and rel.fear > 0.45 and c.dist2(pl.x, pl.y) < 16:
                return True
        return False

    def update(self, c, sp, region) -> None:
        sim = self.sim
        now = sim.world.clock.tick
        need = c.action is None or now >= c.next_decide_tick
        if not need and c.action["name"] not in URGENT and (now + c.id) % 3 == 0:
            if c.percept_cache is None or c.percept_cache.max_danger < 0.3:
                need = self.quick_alarm(c, sp, region)
        if need:
            self.decide(c, sp, region)
        if c.action is None:
            return
        action = ACTIONS_BY_NAME[c.action["name"]]
        try:
            status = action.tick(sim, c, sp, region, c.action)
        except Exception:
            log.exception("action %s failed for creature %s; resetting", action.name, c.id)
            status = 2
        if not c.alive:
            return
        if status != RUNNING:
            self._finish(c, action, status)

    def _finish(self, c, action, status) -> None:
        try:
            action.stop(self.sim, c, c.action)
        except Exception:
            log.exception("stop failed for %s", action.name)
        bucket = (self.action_success if status == SUCCESS else
                  self.action_preempted if status == PREEMPTED else self.action_failures)
        bucket[action.name] = bucket.get(action.name, 0) + 1
        c.action = None
        c.action_name = "idle"
        c.status_icon = ""
        c.sleeping = False
        c.next_decide_tick = self.sim.world.clock.tick + 1

    def interrupt(self, c) -> None:
        if c.action is not None:
            self._finish(c, ACTIONS_BY_NAME[c.action["name"]], PREEMPTED)
        c.next_decide_tick = self.sim.world.clock.tick

    def evaluate(self, c, sp, region):
        """Score all options without committing (used by the inspector too)."""
        sim = self.sim
        p = perceive(sim, c, sp, region)
        c.percept_cache = p
        ctx = Ctx(sim, c, sp, region, p)
        options = []
        for a in self.actions_for(sp):
            try:
                options.extend(a.options(ctx))
            except Exception:
                log.exception("scoring %s failed for creature %s", a.name, c.id)
        cur = c.action
        if c.taint > 0.3:
            rng = sim.rng
            for o in options:
                o.score += rng.uniform(-1, 1) * (c.taint - 0.3) * 0.5
        if cur is not None:
            for o in options:
                if o.action.name == cur["name"] and o.target == cur["target"]:
                    o.score += COMMITMENT
        options.sort(key=lambda o: -o.score)
        return options

    def decide(self, c, sp, region) -> None:
        sim = self.sim
        now = sim.world.clock.tick
        self.decisions += 1
        options = self.evaluate(c, sp, region)
        c.next_decide_tick = now + sim.config.decide_interval + (c.id % 3)
        c.last_scores = [(o.action.name, round(o.score, 3), o.reason) for o in options[:6]]
        if not options:
            return
        best = options[0]
        cur = c.action
        if cur is not None:
            same = best.action.name == cur["name"] and best.target == cur["target"]
            if same:
                return
            young = now - cur["start"] < MIN_COMMIT_TICKS
            if young and best.action.name not in URGENT and cur["name"] not in ("wander", "explore"):
                return
            self._finish(c, ACTIONS_BY_NAME[cur["name"]], PREEMPTED)
            c.next_decide_tick = now + sim.config.decide_interval + (c.id % 3)
        c.action = best.action.start(sim, c, sp, best)
        c.action_name = best.action.name
        c.action_started = now
        c.decisions.append(Decision(now, best.action.name, round(best.score, 3), best.reason,
                                    c.last_scores[:4]))
