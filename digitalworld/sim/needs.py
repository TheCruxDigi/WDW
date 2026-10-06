"""Per-tick need dynamics, health, taint and ageing for loaded creatures."""

from __future__ import annotations

from ..core.clock import TICKS_PER_DAY


def update_needs(sim, c, sp, region, dt: int = 1) -> None:
    n = c.needs
    r = sp.rates
    moving = c.action_name in ("hunt", "flee", "migrate", "explore", "defend", "help_ally")
    heat = 1.35 if region.biome_id == "glass_dunes" and region.weather != "rain" else 1.0
    n["hunger"] = min(1.0, n["hunger"] + r["hunger"] * dt * (1.25 if moving else 1.0))
    n["thirst"] = min(1.0, n["thirst"] + r["thirst"] * dt * heat * (1.2 if moving else 1.0))
    if not c.sleeping:
        n["fatigue"] = min(1.0, n["fatigue"] + r["energy"] * dt * (1.6 if moving else 1.0))
    soc = r.get("social", 0.0) * c.traits["sociability"] * 2
    p = c.percept_cache
    near_ally = bool(p and any(d < 6 for _, d in p.allies))
    if near_ally:
        n["loneliness"] = max(0.0, n["loneliness"] - 0.002 * dt)
    else:
        n["loneliness"] = min(1.0, n["loneliness"] + soc * dt)
    if not c.sleeping:
        n["curiosity"] = min(1.0, n["curiosity"] + r.get("curiosity", 0.0) * dt * (0.5 + c.traits["curiosity"]))
    # insecurity follows perceived danger, corruption and exposure
    corr = region.corruption_at(c.x, c.y)
    over = max(0.0, corr - sp.corruption_tolerance)
    danger = p.max_danger if p else 0.0
    exposed = 0.15 if (c.sleeping and c.action and c.action.get("target") is None and sp.trophic != "predator") else 0.0
    target = min(1.0, danger + over * 0.6 + exposed + (0.2 if c.health < 0.35 else 0.0))
    n["insecurity"] += (target - n["insecurity"]) * min(1.0, 0.06 * dt)
    if sp.territorial:
        terr = p.own_territory if p else None
        inside = terr is not None and terr.contains(c.x, c.y)
        if terr is not None:
            n["territory"] = max(0.0, n["territory"] - 0.003 * dt) if inside else min(1.0, n["territory"] + 0.0015 * dt)
    # taint: corruption exposure accumulates beyond tolerance
    if sp.corruption_affinity > 0:
        c.taint = max(0.0, c.taint - 0.0005 * dt)
        if corr > 0.3 and c.health < 1.0:
            c.health = min(1.0, c.health + corr * sp.corruption_affinity * 0.0006 * dt)
    elif over > 0:
        c.taint = min(1.0, c.taint + over * 0.0025 * dt)
    else:
        c.taint = max(0.0, c.taint - 0.0006 * dt * (2.0 if sim.near_beacon(region, c.x, c.y) else 1.0))
    # health
    dmg = 0.0
    if n["hunger"] > 0.93:
        dmg += 0.0012 * dt
    if n["thirst"] > 0.93:
        dmg += 0.0016 * dt
    if c.taint > 0.7:
        dmg += (c.taint - 0.7) * 0.003 * dt
    if dmg:
        c.health -= dmg
        if c.health <= 0:
            cause = "starvation" if n["hunger"] > 0.93 else "thirst" if n["thirst"] > 0.93 else "corruption"
            sim.kill(c, cause)
            return
    elif n["hunger"] < 0.6 and n["thirst"] < 0.6 and c.health < 1.0:
        c.health = min(1.0, c.health + (0.0012 if c.sleeping else 0.0005) * dt)
    # old age
    age = c.age_days(sim.world.clock.tick)
    if age > sp.lifespan_days:
        excess = (age - sp.lifespan_days) / max(1.0, sp.lifespan_days * 0.2)
        if sim.rng.random() < min(0.5, 0.0004 * dt * (1 + excess * 4)):
            sim.kill(c, "old age")


def relax_relations(c, now: int, dt: int) -> None:
    for rel in c.relations.values():
        rel.decay(dt)
    if len(c.relations) > c.MAX_RELATIONS:
        c._prune_relations()


DAY = TICKS_PER_DAY
