"""Utility-AI actions.

Every action implements:
  * ``options(ctx)``  -> scored candidate options (score, target, reason)
  * ``tick(sim, c, sp, region, st)`` -> RUNNING / SUCCESS / FAILED
  * ``stop(sim, c, st)`` -> releases claimed resources

Actions validate their target every tick, have a time limit, and ban targets
that turned out to be impossible so the brain does not retry them in a loop.
"""

from __future__ import annotations

import math

from ..core.clock import TICKS_PER_DAY
from ..entities.memory import PLAYER_ID
from .movement import move_to, pick_walkable_near, ARRIVED, FAILED as MOVE_FAILED

RUNNING, SUCCESS, FAILED = 0, 1, 2


class Option:
    __slots__ = ("action", "score", "target", "reason", "data")

    def __init__(self, action, score: float, target=None, reason: str = "", data: dict | None = None):
        self.action = action
        self.score = score
        self.target = target
        self.reason = reason
        self.data = data or {}


class Ctx:
    __slots__ = ("sim", "world", "c", "sp", "region", "p", "now")

    def __init__(self, sim, c, sp, region, percept):
        self.sim = sim
        self.world = sim.world
        self.c = c
        self.sp = sp
        self.region = region
        self.p = percept
        self.now = sim.world.clock.tick


def strength(c, sp) -> float:
    return sp.attack * (0.4 + 0.6 * c.health) + sp.defense * 0.5


def _dist(c, x, y) -> float:
    return math.hypot(c.x - x, c.y - y)


class Action:
    name = "action"
    icon = ""
    max_ticks = 300
    interruptible = True

    def applies(self, sp) -> bool:
        return True

    def options(self, ctx: Ctx) -> list[Option]:
        return []

    def start(self, sim, c, sp, opt: Option) -> dict:
        return {"name": self.name, "target": opt.target, "start": sim.world.clock.tick,
                "reason": opt.reason, **opt.data}

    def tick(self, sim, c, sp, region, st) -> int:
        return SUCCESS

    def stop(self, sim, c, st) -> None:
        pass


# ---------------------------------------------------------------- food & water

class Eat(Action):
    name = "eat"
    icon = "food"
    max_ticks = 260

    def options(self, ctx):
        c, sp, p = ctx.c, ctx.sp, ctx.p
        h = c.needs["hunger"]
        if h < 0.18:
            return []
        best, best_cost = None, 1e9
        for kind, mult in sp.diet.items():
            for node, d in p.nodes.get(kind, ()):
                if node.amount < 1.0 or c.blacklisted(f"n{node.id}", ctx.now):
                    continue
                full = len(node.users) >= ctx.world.content.resources[kind].feeders and c.id not in node.users
                cost = d + (6.0 if full else 0.0) - mult * 3.0 - min(node.amount, 20) * 0.1
                cost += c.memory.danger_near(ctx.region.id, node.x, node.y, ctx.now) * 10
                ft = ctx.region.territory_at(node.x, node.y)
                if ft is not None and ft is not p.own_territory and ft.strength > 0.3:
                    owner = ctx.world.groups.get(ft.group_id)
                    if owner and (owner.species_id == c.species_id or ctx.world.species(owner.species_id).is_predator):
                        cost += 8 * (1.0 - c.traits["boldness"])
                if cost < best_cost:
                    best, best_cost = node, cost
        reason_src = "seen"
        if best is None:
            # fall back to remembered food in this region
            for nid, (rid, x, y, kind, frac, t) in c.known_nodes.items():
                if rid != ctx.region.id or kind not in sp.diet or frac < 0.15:
                    continue
                if c.blacklisted(f"n{nid}", ctx.now) or nid not in ctx.region.resources:
                    continue
                d = _dist(c, x, y)
                cost = d - sp.diet[kind] * 3 + (ctx.now - t) / 600.0
                if cost < best_cost:
                    best, best_cost = ctx.region.resources[nid], cost
                    reason_src = "remembered"
        if best is None:
            return []
        score = (h ** 1.3) * 1.15 + (0.1 if h > 0.6 else 0.0)
        if best_cost > 25:
            score *= 0.8
        return [Option(self, score, best.id,
                       f"hunger {h:.2f}; {reason_src} {best.kind} #{best.id} ({_dist(c, best.x, best.y):.0f} tiles)")]

    def tick(self, sim, c, sp, region, st):
        node = region.resources.get(st["target"])
        now = sim.world.clock.tick
        if node is None or node.amount < 0.3:
            if node is not None:
                c.remember_node(node, region.id, now)
            c.ban(f"n{st['target']}", now, 240)
            return FAILED
        if now - st["start"] > self.max_ticks:
            c.ban(f"n{node.id}", now, 200)
            return FAILED
        if _dist(c, node.x + 0.5, node.y + 0.5) > 1.3:
            r = move_to(sim, c, sp, region, node.x + 0.5, node.y + 0.5, arrive=1.1)
            if r == MOVE_FAILED:
                c.ban(f"n{node.id}", now, 400)
                return FAILED
            return RUNNING
        rk = sim.world.content.resources[node.kind]
        if c.id not in node.users:
            if len(node.users) >= rk.feeders:
                if not sim.compete_for_node(c, sp, node):
                    c.ban(f"n{node.id}", now, 150)
                    return FAILED
            node.users.append(c.id)
            st["feeding"] = True
            c.remember_node(node, region.id, now)
        mult = sp.diet.get(node.kind, 0.5)
        taken = sim.consume(region, node, sp.appetite / 22.0, c.id)
        if taken <= 0:
            c.remember_node(node, region.id, now)
            return FAILED
        c.needs["hunger"] = max(0.0, c.needs["hunger"] - taken * mult / sp.appetite)
        if node.kind == "bytebloom" and sp.corruption_affinity <= 0:
            c.taint = min(1.0, c.taint + 0.003)
        c.status_icon = "food"
        c.search_fail = 0
        if c.needs["hunger"] < 0.04:
            c.remember_node(node, region.id, now)
            c.counters["meals"] += 1
            return SUCCESS
        return RUNNING

    def stop(self, sim, c, st):
        sim.release_node(c, st.get("target"))


class Drink(Action):
    name = "drink"
    icon = "water"
    max_ticks = 260

    def options(self, ctx):
        c, sp, p = ctx.c, ctx.sp, ctx.p
        t = c.needs["thirst"]
        if t < 0.2:
            return []
        best, best_cost = None, 1e9
        for node, d in p.nodes.get("water", ()):
            if node.amount < 2 or c.blacklisted(f"n{node.id}", ctx.now):
                continue
            tainted = ctx.region.corruption_at(node.x, node.y) - sp.corruption_tolerance
            cost = d + max(0.0, tainted) * 30
            if cost < best_cost:
                best, best_cost = node, cost
        src = "seen"
        if best is None:
            for nid, (rid, x, y, kind, frac, tk) in c.known_nodes.items():
                if kind == "water" and rid == ctx.region.id and nid in ctx.region.resources \
                        and not c.blacklisted(f"n{nid}", ctx.now):
                    d = _dist(c, x, y)
                    if d < best_cost:
                        best, best_cost = ctx.region.resources[nid], d
                        src = "remembered"
        if best is None and t > 0.55:
            # instinct: the smell of water carries further than sight
            reach = p.radius * 2.5
            for node in ctx.region.nodes_of("water"):
                d = _dist(c, node.x, node.y)
                if d < reach and node.amount > 2 and not c.blacklisted(f"n{node.id}", ctx.now) and d < best_cost:
                    best, best_cost = node, d
                    src = "scented"
        if best is None:
            return []
        return [Option(self, (t ** 1.3) * 1.2 + (0.1 if t > 0.6 else 0.0), best.id,
                       f"thirst {t:.2f}; {src} pool #{best.id}")]

    def tick(self, sim, c, sp, region, st):
        node = region.resources.get(st["target"])
        now = sim.world.clock.tick
        if node is None or node.amount < 1:
            c.ban(f"n{st['target']}", now, 300)
            return FAILED
        if now - st["start"] > self.max_ticks:
            c.ban(f"n{node.id}", now, 200)
            return FAILED
        if _dist(c, node.x + 0.5, node.y + 0.5) > 1.3:
            r = move_to(sim, c, sp, region, node.x + 0.5, node.y + 0.5, arrive=1.1)
            if r == MOVE_FAILED:
                c.ban(f"n{node.id}", now, 400)
                return FAILED
            return RUNNING
        taken = sim.consume(region, node, sp.drink / 10.0, c.id)
        if taken <= 0:
            return FAILED
        c.needs["thirst"] = max(0.0, c.needs["thirst"] - taken / sp.drink)
        over = region.corruption_at(node.x, node.y) - sp.corruption_tolerance
        if over > 0:
            c.taint = min(1.0, c.taint + over * 0.01)
        c.status_icon = "water"
        if c.needs["thirst"] < 0.04:
            c.remember_node(node, region.id, now)
            return SUCCESS
        return RUNNING


class Hunt(Action):
    name = "hunt"
    icon = "hunt"
    max_ticks = 170

    def applies(self, sp):
        return sp.is_predator

    def options(self, ctx):
        c, sp, p = ctx.c, ctx.sp, ctx.p
        h = c.needs["hunger"]
        if h < 0.3 or not p.prey or c.health < 0.3:
            return []
        # remains nearby are always preferred to a chase
        if any(n.amount > 5 for n, _ in p.nodes.get("meat", ())):
            h *= 0.5
        best, best_v = None, -1e9
        content = ctx.world.content
        pack_targets = {}
        for a, _ in p.allies:
            if a.action_name == "hunt" and a.action:
                pack_targets[a.action.get("target")] = pack_targets.get(a.action.get("target"), 0) + 1
        for prey, d in p.prey:
            if c.blacklisted(f"c{prey.id}", ctx.now):
                continue
            psp = content.species[prey.species_id]
            vuln = (1.2 - prey.health) * (1.3 if prey.sleeping else 1.0)
            if prey.age_days(ctx.now) < psp.maturity_days:
                vuln *= 1.3
            danger = strength(prey, psp) / max(1.0, strength(c, sp))
            v = psp.meat_value / (d + 6.0) * vuln - danger * 0.6 + pack_targets.get(prey.id, 0) * 0.6
            v -= sum(1 for o, _ in p.others if o.group_id is not None and o.group_id == prey.group_id) * 0.08 * (danger > 0.5)
            if v > best_v:
                best, best_v = prey, v
        if best is None:
            return []
        score = (h ** 1.2) * (0.8 + 0.4 * c.traits["aggression"]) + min(0.2, best_v * 0.1)
        return [Option(self, score, best.id,
                       f"hunger {h:.2f}; stalking {content.species[best.species_id].name} {best.name}")]

    def tick(self, sim, c, sp, region, st):
        world = sim.world
        now = world.clock.tick
        prey = world.creatures.get(st["target"])
        if prey is None or not prey.alive or prey.region_id != c.region_id:
            return SUCCESS if st.get("struck") else FAILED
        d = _dist(c, prey.x, prey.y)
        if d > sp.perception * 1.4 or now - st["start"] > self.max_ticks:
            c.ban(f"c{prey.id}", now, 360)
            c.memory.add(now, "hunt_failed", prey.id, region.id, c.x, c.y, 0.3, "escaped")
            world.bus.publish("hunt_failed", now, hunter=c.id, prey=prey.id, why="escaped")
            return FAILED
        # mobbing: several defenders drive a predator off
        defenders = [o for o in world.nearby(region.id, c.x, c.y, 2.8)
                     if o is not c and o.action and o.action.get("target") == c.id
                     and o.action_name in ("defend", "help_ally")]
        if len(defenders) >= 2:
            force = sum(strength(o, world.species(o.species_id)) for o in defenders)
            if force >= strength(c, sp) * 0.9:
                for o in defenders:
                    c.relation(o.id, o.species_id).adjust(now, fear=0.15, hostility=0.1)
                c.ban(f"c{prey.id}", now, 600)
                c.memory.add(now, "hunt_failed", prey.id, region.id, c.x, c.y, 0.6, "driven off by a mob")
                world.bus.publish("hunt_failed", now, hunter=c.id, prey=prey.id, why="mobbed")
                sim.force_flee(c, defenders[0])
                return FAILED
        reach = sp.size + world.species(prey.species_id).size + 0.5
        if d > reach:
            r = move_to(sim, c, sp, region, prey.x, prey.y, mult=1.25, arrive=reach * 0.9)
            c.needs["fatigue"] = min(1.0, c.needs["fatigue"] + 0.0012)
            if r == MOVE_FAILED:
                c.ban(f"c{prey.id}", now, 300)
                return FAILED
            return RUNNING
        if now - st.get("last_hit", -99) >= 4:
            st["last_hit"] = now
            st["struck"] = True
            killed = sim.attack(c, prey)
            if killed:
                c.next_decide_tick = now
                return SUCCESS
        return RUNNING


# ---------------------------------------------------------------- rest & safety

class Rest(Action):
    name = "rest"
    icon = "sleep"
    max_ticks = 720

    def _circadian(self, sim, sp) -> float:
        day = sim.world.clock.daylight()
        if sp.activity == "diurnal" and day < 0.22:
            return 0.4
        if sp.activity == "nocturnal" and day > 0.7:
            return 0.4
        return 0.0

    def options(self, ctx):
        c, sp, p = ctx.c, ctx.sp, ctx.p
        f = c.needs["fatigue"]
        circ = self._circadian(ctx.sim, sp)
        score = (f ** 1.3) * 0.95 + circ * (0.5 + f) - c.needs["insecurity"] * 0.5 - p.max_danger * 0.6
        if f < 0.25 and circ == 0:
            return []
        if score <= 0.05:
            return []
        shelter = None
        best = 1e9
        for node, d in p.nodes.get("shelter", ()):
            if d > 16 or c.blacklisted(f"n{node.id}", ctx.now):
                continue
            if len(node.users) >= int(node.capacity) and c.id not in node.users:
                continue
            ft = ctx.region.territory_at(node.x, node.y)
            if ft is not None and ft is not p.own_territory and ft.strength > 0.4:
                continue
            if d < best:
                shelter, best = node, d
        where = f"hollow #{shelter.id}" if shelter else "in the open"
        return [Option(self, score, shelter.id if shelter else None,
                       f"fatigue {f:.2f}{' (night cycle)' if circ else ''}; sleeping {where}")]

    def tick(self, sim, c, sp, region, st):
        now = sim.world.clock.tick
        node = region.resources.get(st["target"]) if st["target"] is not None else None
        if st["target"] is not None and node is None:
            st["target"] = None
        if node is not None and not st.get("arrived"):
            if _dist(c, node.x + 0.5, node.y + 0.5) > 1.2:
                r = move_to(sim, c, sp, region, node.x + 0.5, node.y + 0.5, arrive=1.0)
                if r == MOVE_FAILED:
                    c.ban(f"n{node.id}", now, 600)
                    st["target"] = None
                return RUNNING
            if c.id not in node.users:
                if len(node.users) >= int(node.capacity):
                    st["target"] = None
                else:
                    node.users.append(c.id)
            st["arrived"] = True
        c.sleeping = True
        c.status_icon = "sleep"
        sheltered = st["target"] is not None
        c.needs["fatigue"] = max(0.0, c.needs["fatigue"] - (0.0075 if sheltered else 0.0055))
        if sheltered:
            c.needs["insecurity"] = max(0.0, c.needs["insecurity"] - 0.01)
        circ = self._circadian(sim, sp)
        if c.needs["fatigue"] <= 0.02 and circ == 0:
            return SUCCESS
        if now - st["start"] > self.max_ticks:
            return SUCCESS
        return RUNNING

    def stop(self, sim, c, st):
        c.sleeping = False
        sim.release_node(c, st.get("target"))


class Flee(Action):
    name = "flee"
    icon = "flee"
    max_ticks = 90

    def options(self, ctx):
        c, sp, p = ctx.c, ctx.sp, ctx.p
        if not p.threats:
            return []
        o, danger, d = p.threats[0]
        score = danger * (1.3 - 0.6 * c.traits["boldness"]) + (0.25 if c.health < 0.4 else 0.0)
        if score < 0.2:
            return []
        who = "the Wayfinder" if o is None else f"{ctx.world.species(o.species_id).name} {o.name}"
        return [Option(self, min(1.6, score), None if o is None else o.id,
                       f"danger {danger:.2f} from {who} at {d:.0f} tiles")]

    def tick(self, sim, c, sp, region, st):
        world = sim.world
        now = world.clock.tick
        p = c.percept_cache
        threats = [(t[0], t[1]) for t in p.threats] if p else []
        if not threats or (p and p.max_danger < 0.2):
            st["calm"] = st.get("calm", 0) + 1
            if st["calm"] > 8:
                return SUCCESS
        else:
            st["calm"] = 0
        if now - st["start"] > self.max_ticks:
            return SUCCESS
        if now - st.get("planned", -99) > 12 or not st.get("goal"):
            ax = ay = 0.0
            for o, w in threats:
                if o is None:
                    pl = world.player
                    ox, oy = pl.x, pl.y
                else:
                    ox, oy = o.x, o.y
                dx, dy = c.x - ox, c.y - oy
                n = math.hypot(dx, dy) or 1.0
                ax += dx / n * w
                ay += dy / n * w
            if ax == 0 and ay == 0:
                ax, ay = sim.rng.uniform(-1, 1), sim.rng.uniform(-1, 1)
            base = math.atan2(ay, ax)
            goal = None
            for off in (0, 0.5, -0.5, 1.0, -1.0, 1.5, -1.5):
                a = base + off
                for dist in (8, 5, 3):
                    tx, ty = int(c.x + math.cos(a) * dist), int(c.y + math.sin(a) * dist)
                    if region.is_walkable(tx, ty, sp.has("swim")):
                        goal = (tx + 0.5, ty + 0.5)
                        break
                if goal:
                    break
            if goal is None:
                st["cornered"] = True
                return FAILED
            st["goal"] = goal
            st["planned"] = now
        r = move_to(sim, c, sp, region, st["goal"][0], st["goal"][1], mult=1.35)
        c.needs["fatigue"] = min(1.0, c.needs["fatigue"] + 0.001)
        c.status_icon = "flee"
        if r in (ARRIVED, MOVE_FAILED):
            st["goal"] = None
        return RUNNING


class Shelter(Action):
    """Hide out a data storm or a frightening spell in a hollow."""
    name = "shelter"
    icon = "shelter"
    max_ticks = 360

    def options(self, ctx):
        c, sp, p = ctx.c, ctx.sp, ctx.p
        storm = ctx.region.weather == "datastorm" and sp.corruption_tolerance < 0.6
        ins = c.needs["insecurity"]
        if not storm and ins < 0.45:
            return []
        nodes = [(n, d) for n, d in p.nodes.get("shelter", ())
                 if (len(n.users) < int(n.capacity) or c.id in n.users) and not c.blacklisted(f"n{n.id}", ctx.now)]
        if not nodes:
            return []
        node, d = min(nodes, key=lambda t: t[1])
        score = (0.55 if storm else 0.0) + ins * 0.5
        return [Option(self, score, node.id, "data storm" if storm else f"insecurity {ins:.2f}")]

    def tick(self, sim, c, sp, region, st):
        now = sim.world.clock.tick
        node = region.resources.get(st["target"])
        if node is None:
            return FAILED
        if _dist(c, node.x + 0.5, node.y + 0.5) > 1.2:
            if move_to(sim, c, sp, region, node.x + 0.5, node.y + 0.5, arrive=1.0) == MOVE_FAILED:
                c.ban(f"n{node.id}", now, 500)
                return FAILED
            return RUNNING
        if c.id not in node.users:
            if len(node.users) >= int(node.capacity):
                return FAILED
            node.users.append(c.id)
        c.status_icon = "shelter"
        c.needs["insecurity"] = max(0.0, c.needs["insecurity"] - 0.012)
        c.needs["fatigue"] = max(0.0, c.needs["fatigue"] - 0.002)
        if region.weather != "datastorm" and c.needs["insecurity"] < 0.15:
            return SUCCESS
        if now - st["start"] > self.max_ticks:
            return SUCCESS
        return RUNNING

    def stop(self, sim, c, st):
        sim.release_node(c, st.get("target"))


class AvoidCorruption(Action):
    name = "avoid_corruption"
    icon = "glitch"
    max_ticks = 150

    def applies(self, sp):
        return sp.corruption_tolerance < 0.9

    def options(self, ctx):
        c, sp = ctx.c, ctx.sp
        over = ctx.p.corruption - sp.corruption_tolerance
        if over <= 0.05:
            return []
        return [Option(self, min(1.2, over * 2.2 + c.taint * 0.3), None,
                       f"local corruption {ctx.p.corruption:.2f} > tolerance {sp.corruption_tolerance:.2f}")]

    def tick(self, sim, c, sp, region, st):
        now = sim.world.clock.tick
        if not st.get("goal"):
            # search the coarse field for the nearest clearly cleaner cell
            best, best_s = None, 1e9
            f = region.corruption
            ch, cw = f.shape
            for y in range(ch):
                for x in range(cw):
                    v = float(f[y, x])
                    if v > sp.corruption_tolerance - 0.05:
                        continue
                    tx, ty = x * 4 + 2, y * 4 + 2
                    s = math.hypot(tx - c.x, ty - c.y) + v * 20
                    if s < best_s:
                        best, best_s = (tx, ty), s
            if best is None:
                return FAILED
            gx, gy = region.nearest_walkable(best[0], best[1], sp.has("swim"))
            st["goal"] = (gx + 0.5, gy + 0.5)
        r = move_to(sim, c, sp, region, st["goal"][0], st["goal"][1], mult=1.1)
        if r == ARRIVED:
            return SUCCESS
        if r == MOVE_FAILED or now - st["start"] > self.max_ticks:
            return FAILED
        return RUNNING


# ---------------------------------------------------------------- curiosity

class Explore(Action):
    name = "explore"
    icon = "explore"
    max_ticks = 260

    def options(self, ctx):
        c, sp, p = ctx.c, ctx.sp, ctx.p
        cur = c.needs["curiosity"]
        score = 0.12 + cur * 0.55 * (0.5 + c.traits["curiosity"])
        reason = f"curiosity {cur:.2f}"
        rid = ctx.region.id
        food_seen = any(p.nodes.get(k) for k in sp.diet) or (sp.is_predator and p.prey) or any(
            v[0] == rid and v[3] in sp.diet and v[4] > 0.15 and not c.blacklisted(f"n{k}", ctx.now)
            for k, v in c.known_nodes.items())
        frustration = max(0.3, 1.0 - 0.2 * c.search_fail)
        if c.needs["hunger"] > 0.35 and not food_seen:
            score += c.needs["hunger"] * 0.75 * frustration
            reason = f"searching for food (hunger {c.needs['hunger']:.2f})"
        if c.needs["thirst"] > 0.35 and not p.nodes.get("water"):
            known_water = any(v[3] == "water" and v[0] == ctx.region.id for v in c.known_nodes.values())
            if not known_water:
                score += c.needs["thirst"] * 0.7 * frustration
                reason = f"searching for water (thirst {c.needs['thirst']:.2f})"
        return [Option(self, score, None, reason, {"search": reason.startswith("searching")})]

    def tick(self, sim, c, sp, region, st):
        now = sim.world.clock.tick
        world = sim.world
        if not st.get("goal"):
            anchor_x, anchor_y = c.x, c.y
            g = world.groups.get(c.group_id) if c.group_id else None
            leader = world.creatures.get(g.leader_id) if g and g.leader_id != c.id else None
            if leader is not None and leader.region_id == c.region_id and not st.get("search"):
                anchor_x, anchor_y = leader.x, leader.y
                radius = 10
            else:
                radius = 24
            goal = None
            for _ in range(6):
                cand = pick_walkable_near(region, anchor_x, anchor_y, sim.rng, radius, sp.has("swim"))
                if cand and c.memory.danger_near(region.id, cand[0], cand[1], now) < 0.3:
                    goal = cand
                    break
            if goal is None:
                return FAILED
            st["goal"] = goal
        r = move_to(sim, c, sp, region, st["goal"][0], st["goal"][1])
        if (now - st["start"]) % 6 == 0:
            sim.scan_surroundings(c, sp, region)
        if r == ARRIVED:
            c.needs["curiosity"] = max(0.0, c.needs["curiosity"] - 0.35)
            if st.get("search"):
                rid = region.id
                found = any(v[0] == rid and (v[3] in sp.diet or v[3] == "water") and v[4] > 0.15
                            for v in c.known_nodes.values())
                if not found:
                    c.search_fail += 1  # frustration builds; leaving starts to look better
            return SUCCESS
        if r == MOVE_FAILED:
            st["goal"] = None
            st["fails"] = st.get("fails", 0) + 1
            return FAILED if st["fails"] > 2 else RUNNING
        if now - st["start"] > self.max_ticks:
            return SUCCESS
        return RUNNING


ANOMALY_DANGER = {"static_vortex": 0.8, "glitch_bloom": 0.35, "echo_pillar": 0.0}


class Investigate(Action):
    name = "investigate"
    icon = "investigate"
    max_ticks = 200

    def options(self, ctx):
        c, sp, p = ctx.c, ctx.sp, ctx.p
        out = []
        for a, d in p.anomalies:
            if c.blacklisted(f"a{a.id}", ctx.now):
                continue
            danger = ANOMALY_DANGER.get(a.kind, 0.3) * (1.0 - sp.corruption_tolerance)
            score = c.traits["curiosity"] * (0.25 + c.needs["curiosity"] * 0.7) * (1 - danger * (1 - c.traits["boldness"]))
            if score > 0.1:
                out.append(Option(self, score, a.id, f"curious about {a.name} ({d:.0f} tiles)"))
        return out[:2]

    def tick(self, sim, c, sp, region, st):
        now = sim.world.clock.tick
        a = region.anomalies.get(st["target"])
        if a is None:
            return FAILED
        if _dist(c, a.x + 0.5, a.y + 0.5) > 2.0:
            if move_to(sim, c, sp, region, a.x + 0.5, a.y + 0.5, arrive=1.8) == MOVE_FAILED:
                c.ban(f"a{a.id}", now, TICKS_PER_DAY)
                return FAILED
            return RUNNING if now - st["start"] < self.max_ticks else FAILED
        st["watch"] = st.get("watch", 0) + 1
        c.status_icon = "investigate"
        if a.kind == "static_vortex" and sp.corruption_tolerance < 0.8:
            c.taint = min(1.0, c.taint + 0.004)
        if st["watch"] >= 25:
            c.needs["curiosity"] = 0.0
            c.ban(f"a{a.id}", now, 2 * TICKS_PER_DAY)
            c.memory.add(now, "discovered", None, region.id, a.x, a.y, 0.5, a.name)
            c.counters["discoveries"] += 1
            sim.world.bus.publish("investigated", now, creature=c.id, anomaly=a.id, region=region.id)
            return SUCCESS
        return RUNNING


# ---------------------------------------------------------------- conflict

class Defend(Action):
    name = "defend"
    icon = "fight"
    max_ticks = 80

    def options(self, ctx):
        c, sp, p = ctx.c, ctx.sp, ctx.p
        if c.under_attack_by is None or ctx.now - c.under_attack_tick > 12:
            return []
        att = ctx.world.creatures.get(c.under_attack_by)
        if att is None or not att.alive or att.region_id != c.region_id or _dist(c, att.x, att.y) > 5:
            return []
        asp = ctx.world.species(att.species_id)
        ratio = strength(c, sp) / max(1.0, strength(att, asp))
        allies = sum(1 for a, d in p.allies if d < 5)
        score = (0.25 * c.traits["boldness"] + 0.25 * c.traits["aggression"] + min(0.6, ratio * 0.35)
                 + allies * 0.08 + (0.35 if sp.has("mob") else 0.0))
        if c.health < 0.3:
            score *= 0.5
        return [Option(self, score, att.id, f"fighting back against {asp.name} {att.name} (strength ratio {ratio:.2f})")]

    def tick(self, sim, c, sp, region, st):
        world = sim.world
        now = world.clock.tick
        att = world.creatures.get(st["target"])
        if att is None or not att.alive or att.region_id != c.region_id:
            return SUCCESS
        d = _dist(c, att.x, att.y)
        if d > 6 or now - st["start"] > self.max_ticks:
            return SUCCESS
        if c.health < 0.2:
            return FAILED
        reach = sp.size + world.species(att.species_id).size + 0.5
        if d > reach:
            move_to(sim, c, sp, region, att.x, att.y, mult=1.1, arrive=reach * 0.9)
            return RUNNING
        if now - st.get("last_hit", -99) >= 4:
            st["last_hit"] = now
            if sim.attack(c, att):
                return SUCCESS
        return RUNNING


class HelpAlly(Action):
    name = "help_ally"
    icon = "help"
    max_ticks = 90

    def options(self, ctx):
        c, sp, p = ctx.c, ctx.sp, ctx.p
        out = []
        if c.health < 0.35:
            return out
        for ally, att in p.kin_in_danger:
            asp = ctx.world.species(att.species_id)
            rel = c.relations.get(ally.id)
            aff = rel.affinity if rel else 0.0
            ratio = strength(c, sp) / max(1.0, strength(att, asp))
            score = 0.25 + c.traits["sociability"] * 0.3 + aff * 0.3 + c.traits["boldness"] * 0.15
            score += 0.35 if sp.has("mob") else 0.0
            if ratio < 0.3 and not sp.has("mob"):
                score -= 0.4
            out.append(Option(self, score, att.id, f"helping {ally.name} against {asp.name} {att.name}",
                              {"ally": ally.id}))
        for ally, d in p.allies:
            if ally.health < 0.4 and sp.social != "solitary" and not c.blacklisted(f"tend{ally.id}", ctx.now):
                rel = c.relations.get(ally.id)
                score = 0.2 + c.traits["sociability"] * 0.35 + (rel.affinity if rel else 0) * 0.2
                out.append(Option(self, score, None, f"tending injured {ally.name}",
                                  {"ally": ally.id, "tend": True}))
                break
        return out

    def tick(self, sim, c, sp, region, st):
        world = sim.world
        now = world.clock.tick
        ally = world.creatures.get(st.get("ally"))
        if ally is None or not ally.alive or ally.region_id != c.region_id:
            return FAILED
        if now - st["start"] > self.max_ticks:
            return SUCCESS
        if st.get("tend"):
            if _dist(c, ally.x, ally.y) > 1.6:
                if move_to(sim, c, sp, region, ally.x, ally.y, arrive=1.4) == MOVE_FAILED:
                    return FAILED
                return RUNNING
            st["n"] = st.get("n", 0) + 1
            ally.health = min(1.0, ally.health + 0.002)
            ally.needs["loneliness"] = 0.0
            c.status_icon = "help"
            if st["n"] >= 25:
                sim.record_help(c, ally)
                c.ban(f"tend{ally.id}", now, 600)
                return SUCCESS
            return RUNNING
        att = world.creatures.get(st["target"])
        if att is None or not att.alive or now - ally.under_attack_tick > 25:
            sim.record_help(c, ally)
            return SUCCESS
        reach = sp.size + world.species(att.species_id).size + 0.6
        if _dist(c, att.x, att.y) > reach:
            move_to(sim, c, sp, region, att.x, att.y, mult=1.2, arrive=reach * 0.9)
            return RUNNING
        if now - st.get("last_hit", -99) >= 5:
            st["last_hit"] = now
            sim.attack(c, att)
        return RUNNING


class DefendTerritory(Action):
    name = "defend_territory"
    icon = "territory"
    max_ticks = 120

    def applies(self, sp):
        return sp.territorial

    def options(self, ctx):
        c, sp, p = ctx.c, ctx.sp, ctx.p
        if not p.intruders or p.own_territory is None or c.health < 0.4:
            return []
        out = []
        for o, d in p.intruders[:3]:
            if o is None:
                rel = c.relations.get(PLAYER_ID)
                hostile = rel.hostility if rel else 0.0
                if (c.traits["aggression"] < 0.4 and hostile < 0.3) or c.blacklisted("player_terr", ctx.now):
                    continue
                score = 0.25 + c.traits["aggression"] * 0.4 + hostile * 0.4 + c.needs["territory"] * 0.2
                out.append(Option(self, score, PLAYER_ID, "driving the Wayfinder out of the territory"))
                continue
            osp = ctx.world.species(o.species_id)
            ratio = strength(o, osp) / max(1.0, strength(c, sp))
            score = 0.3 + c.traits["aggression"] * 0.45 + c.needs["territory"] * 0.3 - max(0.0, ratio - 1.2) * 0.4
            out.append(Option(self, score, o.id, f"intruder {osp.name} {o.name} in our territory"))
        return out

    def tick(self, sim, c, sp, region, st):
        world = sim.world
        now = world.clock.tick
        terr = c.percept_cache.own_territory if c.percept_cache else None
        if now - st["start"] > self.max_ticks:
            return SUCCESS
        if st["target"] == PLAYER_ID:
            pl = world.player
            if pl is None or pl.region_id != region.id or (terr and not terr.contains(pl.x, pl.y)):
                return SUCCESS
            if _dist(c, pl.x, pl.y) > 1.6:
                if move_to(sim, c, sp, region, pl.x, pl.y, mult=1.2, arrive=1.4) == MOVE_FAILED:
                    return FAILED
                return RUNNING
            sim.player_intimidated(c, sp)
            c.needs["territory"] = 0.0
            return SUCCESS
        o = world.creatures.get(st["target"])
        if o is None or not o.alive or o.region_id != region.id:
            return SUCCESS
        if terr is not None and not terr.contains(o.x, o.y) and _dist(c, o.x, o.y) > 3:
            c.needs["territory"] = max(0.0, c.needs["territory"] - 0.3)
            return SUCCESS
        if _dist(c, o.x, o.y) > 1.8:
            if move_to(sim, c, sp, region, o.x, o.y, mult=1.15, arrive=1.6) == MOVE_FAILED:
                return FAILED
            return RUNNING
        sim.intimidate(c, sp, o, terr)
        c.needs["territory"] = 0.0
        return SUCCESS


# ---------------------------------------------------------------- social

class Socialize(Action):
    name = "socialize"
    icon = "social"
    max_ticks = 120

    def applies(self, sp):
        return sp.social != "solitary" or sp.rates.get("social", 0) > 0

    def options(self, ctx):
        c, sp, p = ctx.c, ctx.sp, ctx.p
        lon = c.needs["loneliness"]
        if lon < 0.3:
            return []
        cands = [(o, d, c.relations.get(o.id)) for o, d in p.allies] + [(o, d, r) for o, d, r in p.friends]
        if not cands:
            return []
        o, d, rel = max(cands, key=lambda t: (t[2].affinity if t[2] else 0) - t[1] * 0.02)
        score = lon * (0.4 + c.traits["sociability"] * 0.6) * 0.85
        return [Option(self, score, o.id, f"loneliness {lon:.2f}; seeking {o.name}")]

    def tick(self, sim, c, sp, region, st):
        world = sim.world
        now = world.clock.tick
        o = world.creatures.get(st["target"])
        if o is None or not o.alive or o.region_id != region.id:
            return FAILED
        if _dist(c, o.x, o.y) > 2.0:
            if move_to(sim, c, sp, region, o.x, o.y, arrive=1.8) == MOVE_FAILED:
                c.ban(f"soc{o.id}", now, 300)
                return FAILED
            return RUNNING if now - st["start"] < self.max_ticks else FAILED
        st["n"] = st.get("n", 0) + 1
        c.status_icon = "social"
        c.needs["loneliness"] = max(0.0, c.needs["loneliness"] - 0.04)
        o.needs["loneliness"] = max(0.0, o.needs["loneliness"] - 0.02)
        if st["n"] % 5 == 0:
            c.relation(o.id, o.species_id).adjust(now, affinity=0.02, trust=0.01)
            o.relation(c.id, c.species_id).adjust(now, affinity=0.015)
            sim.share_knowledge(o, c)
        if st["n"] >= 20 or c.needs["loneliness"] <= 0.02:
            return SUCCESS
        return RUNNING


class Regroup(Action):
    name = "regroup"
    icon = "group"
    max_ticks = 200

    def applies(self, sp):
        return sp.social != "solitary"

    def options(self, ctx):
        c = ctx.c
        if c.group_id is None:
            return []
        g = ctx.world.groups.get(c.group_id)
        if g is None or g.leader_id in (None, c.id):
            return []
        leader = ctx.world.creatures.get(g.leader_id)
        if leader is None or leader.region_id != c.region_id:
            return []
        d = _dist(c, leader.x, leader.y)
        if d < 9:
            return []
        score = 0.15 + c.traits["sociability"] * 0.3 * min(1.5, d / 15.0)
        return [Option(self, score, leader.id, f"{d:.0f} tiles from leader {leader.name}")]

    def tick(self, sim, c, sp, region, st):
        leader = sim.world.creatures.get(st["target"])
        if leader is None or not leader.alive or leader.region_id != region.id:
            return FAILED
        r = move_to(sim, c, sp, region, leader.x, leader.y, arrive=4.0)
        if r == ARRIVED:
            sim.share_knowledge(leader, c)
            return SUCCESS
        if r == MOVE_FAILED or sim.world.clock.tick - st["start"] > self.max_ticks:
            return FAILED
        return RUNNING


class ApproachPlayer(Action):
    name = "approach_player"
    icon = "player"
    max_ticks = 90

    def options(self, ctx):
        c, sp, p = ctx.c, ctx.sp, ctx.p
        if p.player_dist is None or p.player_threat > 0.3 or c.blacklisted("player", ctx.now):
            return []
        rel = c.relations.get(PLAYER_ID)
        trust = rel.trust if rel else 0.0
        aff = rel.affinity if rel else 0.0
        fear = rel.fear if rel else 0.2
        fam = rel.familiarity if rel else 0.0
        score = (trust * 0.55 + aff * 0.35) * (0.6 + c.traits["sociability"] * 0.4)
        score += c.traits["curiosity"] * 0.3 * (1.0 - fear) * (1.0 - fam)
        score -= fear * 0.6
        if c.needs["hunger"] > 0.4 and rel and rel.trust > 0.4 and ctx.world.player.inventory.get("seeds", 0) > 0:
            score += 0.2  # it has learned the Wayfinder carries food
        if score < 0.2:
            return []
        why = "trusts the Wayfinder" if trust > 0.45 else "curious about the stranger"
        return [Option(self, score, PLAYER_ID, f"{why} (trust {trust:.2f}, fear {fear:.2f})")]

    def tick(self, sim, c, sp, region, st):
        world = sim.world
        now = world.clock.tick
        pl = world.player
        if pl is None or pl.region_id != region.id:
            return FAILED
        if now - st["start"] > self.max_ticks:
            c.ban("player", now, 300)
            return SUCCESS
        d = _dist(c, pl.x, pl.y)
        if d > 2.2:
            if move_to(sim, c, sp, region, pl.x, pl.y, arrive=2.0) == MOVE_FAILED:
                c.ban("player", now, 400)
                return FAILED
            return RUNNING
        c.status_icon = "player"
        if not st.get("met"):
            st["met"] = True
            rel = c.relation(PLAYER_ID, "player")
            if rel.familiarity < 0.05:
                c.memory.add(now, "met_player", PLAYER_ID, region.id, c.x, c.y, 0.4)
            rel.adjust(now, familiarity=0.05)
        c.needs["curiosity"] = max(0.0, c.needs["curiosity"] - 0.01)
        c.needs["loneliness"] = max(0.0, c.needs["loneliness"] - 0.01)
        return RUNNING


# ---------------------------------------------------------------- life cycle

class Mate(Action):
    name = "mate"
    icon = "heart"
    max_ticks = 160

    def options(self, ctx):
        c, sp, p, now = ctx.c, ctx.sp, ctx.p, ctx.now
        if now < c.repro_ready_tick or not c.is_adult(now, sp):
            return []
        if c.needs["hunger"] > 0.45 or c.needs["thirst"] > 0.5 or c.health < 0.6 or c.taint > 0.6:
            return []
        if p.max_danger > 0.3:
            return []
        index = ctx.sim.food_index(ctx.region, sp.id)
        if index < 0.8:
            return []
        score = 0.35 + min(0.3, (index - 0.8) * 0.3)
        if sp.has("asexual"):
            if ctx.p.corruption < 0.25:
                return []
            return [Option(self, score, None, f"budding (food index {index:.2f})")]
        best, best_v = None, -1e9
        for o, d in p.mates:
            if o.sex == c.sex or not o.alive or now < o.repro_ready_tick or not o.is_adult(now, sp):
                continue
            if o.needs["hunger"] > 0.5 or o.health < 0.5 or c.blacklisted(f"mate{o.id}", now):
                continue
            if o.id in c.parents or c.id in o.parents:
                continue
            rel = c.relations.get(o.id)
            v = (rel.affinity if rel else 0.0) - d * 0.03
            if v > best_v:
                best, best_v = o, v
        if best is None:
            return []
        return [Option(self, score + max(0.0, best_v) * 0.2, best.id,
                       f"courting {best.name} (food index {index:.2f})")]

    def tick(self, sim, c, sp, region, st):
        world = sim.world
        now = world.clock.tick
        if now - st["start"] > self.max_ticks:
            if st["target"] is not None:
                c.ban(f"mate{st['target']}", now, 600)
            return FAILED
        c.status_icon = "heart"
        if st["target"] is None:
            st["n"] = st.get("n", 0) + 1
            if st["n"] >= 20:
                sim.birth(c, None)
                return SUCCESS
            return RUNNING
        o = world.creatures.get(st["target"])
        if o is None or not o.alive or o.region_id != region.id or now < o.repro_ready_tick:
            return FAILED
        if _dist(c, o.x, o.y) > 1.5:
            if move_to(sim, c, sp, region, o.x, o.y, arrive=1.3) == MOVE_FAILED:
                c.ban(f"mate{o.id}", now, 600)
                return FAILED
            return RUNNING
        st["n"] = st.get("n", 0) + 1
        if st["n"] >= 15:
            sim.birth(c, o)
            return SUCCESS
        return RUNNING


class Migrate(Action):
    name = "migrate"
    icon = "migrate"
    max_ticks = 600

    def options(self, ctx):
        c = ctx.c
        out = []
        g = ctx.world.groups.get(c.group_id) if c.group_id else None
        if g is not None and g.migrate_to is not None and g.leader_id != c.id \
                and g.migrate_to in ctx.region.gates and ctx.now - g.migrate_tick < TICKS_PER_DAY:
            dest = ctx.world.regions[g.migrate_to]
            out.append(Option(self, 0.5 + c.traits["sociability"] * 0.35, g.migrate_to,
                              f"following the {g.name} to {dest.name} ({g.migrate_reason})",
                              {"why": g.migrate_reason}))
        if c.migrate_target is not None and c.migrate_target in ctx.region.gates:
            urg = ctx.sim.migration.urgency(c)
            if urg > 0.05 and not c.blacklisted(f"mig{c.migrate_target}", ctx.now):
                dest = ctx.world.regions[c.migrate_target]
                out.append(Option(self, urg, c.migrate_target, f"{c.migrate_reason} -> {dest.name}",
                                  {"why": c.migrate_reason}))
        return out

    def start(self, sim, c, sp, opt):
        st = super().start(sim, c, sp, opt)
        g = sim.world.groups.get(c.group_id) if c.group_id else None
        if g is not None and g.leader_id == c.id and g.migrate_to != opt.target:
            g.migrate_to = opt.target
            g.migrate_reason = opt.data.get("why", "")
            g.migrate_tick = sim.world.clock.tick
        return st

    def tick(self, sim, c, sp, region, st):
        now = sim.world.clock.tick
        target = st["target"]
        gate = region.gates.get(target)
        if gate is None:
            return FAILED
        gx, gy = gate[0] + 0.5, gate[1] + 0.5
        r = move_to(sim, c, sp, region, gx, gy, arrive=0.8)
        c.status_icon = "migrate"
        if r == ARRIVED:
            sim.request_transfer(c, target, st.get("why", ""))
            return SUCCESS
        if r == MOVE_FAILED or now - st["start"] > self.max_ticks:
            c.ban(f"mig{target}", now, TICKS_PER_DAY)
            return FAILED
        return RUNNING


class Build(Action):
    name = "build"
    icon = "build"
    max_ticks = 500

    def applies(self, sp):
        return sp.has("build")

    def options(self, ctx):
        c, sp, p, region = ctx.c, ctx.sp, ctx.p, ctx.region
        if c.needs["hunger"] > 0.6 or c.needs["thirst"] > 0.6 or not c.is_adult(ctx.now, sp):
            return []
        if c.blacklisted("build", ctx.now):
            return []
        # repair a damaged beacon first
        for s in region.structures.values():
            if s.kind == "beacon" and (s.integrity < 0.55 or not s.complete) and _dist(c, s.x, s.y) < 20:
                return [Option(self, 0.35 + (1 - s.integrity) * 0.5, s.id, f"tending beacon #{s.id}",
                               {"site": (s.x, s.y)})]
        site = ctx.sim.find_beacon_site(c, region, p.own_territory)
        if site is None:
            return []
        x, y, corr = site
        score = 0.2 + corr * 0.9 + c.traits["curiosity"] * 0.1
        return [Option(self, score, None, f"corruption {corr:.2f} near ({x},{y}); raising a beacon",
                       {"site": (x, y)})]

    def tick(self, sim, c, sp, region, st):
        world = sim.world
        now = world.clock.tick
        if now - st["start"] > self.max_ticks:
            c.ban("build", now, 300)
            return FAILED
        need = 3.0
        if c.carried < need:
            node = region.resources.get(st.get("node")) if st.get("node") else None
            if node is None or node.amount < 1:
                cands = [n for n in region.resources.values() if n.kind == "material" and n.amount >= 1
                         and not c.blacklisted(f"n{n.id}", now)]
                if not cands:
                    c.ban("build", now, TICKS_PER_DAY)
                    return FAILED
                node = min(cands, key=lambda n: _dist(c, n.x, n.y))
                st["node"] = node.id
            if _dist(c, node.x + 0.5, node.y + 0.5) > 1.3:
                if move_to(sim, c, sp, region, node.x + 0.5, node.y + 0.5, arrive=1.1) == MOVE_FAILED:
                    c.ban(f"n{node.id}", now, 600)
                    st["node"] = None
                return RUNNING
            if now % 4 == 0:
                c.carried += sim.consume(region, node, 0.5, c.id)
            c.status_icon = "build"
            return RUNNING
        sx, sy = st["site"]
        if _dist(c, sx + 0.5, sy + 0.5) > 1.5:
            if move_to(sim, c, sp, region, sx + 0.5, sy + 0.5, arrive=1.3) == MOVE_FAILED:
                c.ban("build", now, 600)
                return FAILED
            return RUNNING
        s = region.structures.get(st["target"]) if st["target"] is not None else None
        if s is None:
            s = sim.begin_structure(region, "beacon", sx, sy, c)
            if s is None:
                c.ban("build", now, 300)
                return FAILED
            st["target"] = s.id
        c.status_icon = "build"
        if not s.complete:
            s.progress = min(1.0, s.progress + 0.025)
            if s.complete:
                sim.complete_structure(region, s, c)
                c.carried = 0.0
                return SUCCESS
        else:
            s.integrity = min(1.0, s.integrity + 0.02)
            if s.integrity >= 0.98:
                c.carried = max(0.0, c.carried - 2)
                return SUCCESS
        return RUNNING


class Wander(Action):
    name = "wander"
    icon = ""
    max_ticks = 80

    def options(self, ctx):
        return [Option(self, 0.1, None, "nothing pressing")]

    def tick(self, sim, c, sp, region, st):
        now = sim.world.clock.tick
        if "goal" not in st:
            if sim.rng.random() < 0.45:
                st["goal"] = None
                st["until"] = now + sim.rng.randint(10, 30)
            else:
                st["goal"] = pick_walkable_near(region, c.x, c.y, sim.rng, 5, sp.has("swim"))
        if st["goal"] is None:
            return SUCCESS if now >= st.get("until", now) else RUNNING
        r = move_to(sim, c, sp, region, st["goal"][0], st["goal"][1], mult=0.6)
        if r != "moving":
            return SUCCESS
        return RUNNING if now - st["start"] < self.max_ticks else SUCCESS


ALL_ACTIONS: list[Action] = [Eat(), Drink(), Hunt(), Rest(), Flee(), Shelter(), AvoidCorruption(),
                             Explore(), Investigate(), Defend(), HelpAlly(), DefendTerritory(),
                             Socialize(), Regroup(), ApproachPlayer(), Mate(), Migrate(), Build(), Wander()]
ACTIONS_BY_NAME = {a.name: a for a in ALL_ACTIONS}
URGENT = {"flee", "defend", "help_ally"}
