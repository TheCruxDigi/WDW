"""What a creature can perceive right now.

Perception range shrinks with signal noise (corruption), darkness for diurnal
species and sleep — so corruption has a direct behavioural cost.
"""

from __future__ import annotations

import math

from ..entities.memory import PLAYER_ID


class Percept:
    __slots__ = ("radius", "threats", "prey", "allies", "kin_in_danger", "friends", "rivals",
                 "intruders", "nodes", "anomalies", "player_dist", "player_threat", "own_territory",
                 "foreign_territory", "corruption", "max_danger", "mates", "others")

    def __init__(self):
        self.radius = 0.0
        self.threats: list = []          # (creature|None for player, danger, dist)
        self.prey: list = []             # (creature, dist)
        self.allies: list = []           # (creature, dist)
        self.kin_in_danger: list = []    # (ally, attacker)
        self.friends: list = []          # (creature, dist, relationship)
        self.rivals: list = []           # (creature, dist)
        self.intruders: list = []        # (creature|None for player, dist)
        self.nodes: dict = {}            # kind -> [(node, dist)]
        self.anomalies: list = []        # (anomaly, dist)
        self.player_dist: float | None = None
        self.player_threat = 0.0
        self.own_territory = None
        self.foreign_territory = None
        self.corruption = 0.0
        self.max_danger = 0.0
        self.mates: list = []
        self.others: list = []


def perception_radius(sim, c, sp, region) -> float:
    r = sp.perception * (1.0 - 0.4 * region.signal_noise)
    daylight = sim.world.clock.daylight()
    if sp.activity == "diurnal" and daylight < 0.3:
        r *= 0.7
    if c.sleeping:
        r *= 0.45
    return max(3.0, r)


def threat_level(c, sp, o, osp, now: int) -> float:
    d = 0.0
    if c.species_id in osp.prey:
        d = 0.7
        if o.action_name == "hunt":
            d += 0.3 if (o.action and o.action.get("target") == c.id) else 0.15
        if o.sleeping:
            d *= 0.25
        elif o.needs["hunger"] < 0.15:
            d *= 0.6
    rel = c.relations.get(o.id)
    if rel is not None:
        d += rel.fear * 0.6 + rel.hostility * 0.25
    if o.action_name == "defend_territory" and o.action and o.action.get("target") == c.id:
        d += 0.5
    if o.under_attack_by == c.id and now - o.under_attack_tick < 15:
        d += 0.2
    if o.taint > 0.65 and osp.temperament.get("aggression", 0) > 0.4 and o.species_id != c.species_id:
        d += 0.2
    if c.under_attack_by == o.id and now - c.under_attack_tick < 20:
        d += 0.5
    return d


def perceive(sim, c, sp, region) -> Percept:
    world = sim.world
    now = world.clock.tick
    p = Percept()
    p.radius = r = perception_radius(sim, c, sp, region)
    p.corruption = region.corruption_at(c.x, c.y)
    content = world.content
    my_group = c.group_id
    own_terr = None
    if my_group is not None:
        g = world.groups.get(my_group)
        if g and g.territory_id is not None:
            own_terr = region.territories.get(g.territory_id)
    elif sp.territorial:
        for t in region.territories.values():
            if t.group_id == -c.id:
                own_terr = t
    p.own_territory = own_terr
    ft = region.territory_at(c.x, c.y)
    if ft is not None and ft is not own_terr:
        p.foreign_territory = ft
    for o in world.nearby(region.id, c.x, c.y, r):
        if o is c:
            continue
        osp = content.species[o.species_id]
        dist = math.sqrt(c.dist2(o.x, o.y))
        p.others.append((o, dist))
        danger = threat_level(c, sp, o, osp, now)
        if danger > 0.15:
            danger *= 1.0 - 0.45 * (dist / r)
            if sp.has("mob"):
                danger *= 0.8
            p.threats.append((o, danger, dist))
        if o.species_id in sp.prey:
            p.prey.append((o, dist))
        rel = c.relations.get(o.id)
        same_group = my_group is not None and o.group_id == my_group
        if same_group or (rel is not None and o.species_id == c.species_id and rel.disposition > 0.3):
            p.allies.append((o, dist))
            if now - o.under_attack_tick < 15 and o.under_attack_by is not None:
                att = world.creatures.get(o.under_attack_by)
                if att is not None and att.alive and att is not c:
                    p.kin_in_danger.append((o, att))
        if rel is not None and rel.disposition > 0.25:
            p.friends.append((o, dist, rel))
        if o.species_id == c.species_id and not same_group:
            p.rivals.append((o, dist))
            if own_terr is not None and own_terr.contains(o.x, o.y):
                p.intruders.append((o, dist))
        elif own_terr is not None and own_terr.contains(o.x, o.y) and osp.territorial \
                and osp.trophic == sp.trophic and o.species_id != c.species_id:
            p.intruders.append((o, dist))
        if o.species_id == c.species_id and not o.sleeping:
            p.mates.append((o, dist))
    player = world.player
    if player is not None and player.region_id == region.id and not player.hidden:
        dist = math.sqrt(c.dist2(player.x, player.y))
        if dist <= r:
            p.player_dist = dist
            rel = c.relations.get(PLAYER_ID)
            fear = rel.fear if rel else 0.2
            if fear > 0.35:
                p.player_threat = fear * (1.0 if dist < 5 else 0.6)
                p.threats.append((None, p.player_threat, dist))
            if own_terr is not None and own_terr.contains(player.x, player.y):
                disp = rel.disposition if rel else 0.0
                if disp < 0.25:
                    p.intruders.append((None, dist))
    rr = r * r
    for n in region.resources.values():
        d2 = (n.x + 0.5 - c.x) ** 2 + (n.y + 0.5 - c.y) ** 2
        if d2 <= rr:
            p.nodes.setdefault(n.kind, []).append((n, math.sqrt(d2)))
    for a in region.anomalies.values():
        d2 = (a.x + 0.5 - c.x) ** 2 + (a.y + 0.5 - c.y) ** 2
        if d2 <= rr:
            p.anomalies.append((a, math.sqrt(d2)))
    p.threats.sort(key=lambda t: -t[1])
    p.max_danger = p.threats[0][1] if p.threats else 0.0
    return p
