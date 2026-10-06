"""Resources: regeneration, consumption, degradation, and the region-level
ecological summaries used by migration and the abstract simulation.

Regeneration depends on biome fertility, the bloom season, current rain and
local corruption, so the same node can thrive, stagnate or collapse.
"""

from __future__ import annotations

import math

from ..core.clock import TICKS_PER_DAY
from ..world.region import Region, ResourceNode


def regen_rate(world, region: Region, node: ResourceNode, tick: int) -> float:
    rk = world.content.resources[node.kind]
    if node.kind == "shelter":
        return 0.0
    if rk.regen < 0:  # remains decay
        return rk.regen
    biome = world.biome(region)
    corr = region.corruption_at(node.x, node.y)
    rate = rk.regen
    if rk.corruption < 0 and node.kind != "water":
        rate *= biome.fertility
    if rk.season:
        rate *= max(0.05, 1.0 + rk.season * world.clock.season(tick))
    if rk.rain:
        rate *= max(0.05, 1.0 + rk.rain * (region.rain_now - 0.5) * 1.6)
    if rk.corruption < 0:
        rate *= max(0.0, 1.0 + rk.corruption * corr * 1.4)
    elif rk.corruption > 0:
        rate *= 0.15 + rk.corruption * corr * 4.0
    if node.amount < 0.08 * node.max_amount:
        rate *= 0.5  # regrowth from a depleted seed bank is slow
    return rate


def regenerate(world, region: Region, dt: int, tick: int | None = None) -> None:
    tick = world.clock.tick if tick is None else tick
    dead = []
    for node in region.resources.values():
        r = regen_rate(world, region, node, tick)
        if r < 0:
            node.amount += r * dt
            if node.amount <= 0.1:
                dead.append(node.id)
            continue
        if r:
            node.amount = min(node.max_amount, node.amount + r * dt)
        # ecological recovery of overused patches
        if node.fullness > 0.7 and node.health < 1.0:
            node.health = min(1.0, node.health + 0.05 * dt / TICKS_PER_DAY)
        # heavy corruption also erodes non-digital plants' long-term health
        rk = world.content.resources[node.kind]
        if rk.corruption < -0.5:
            c = region.corruption_at(node.x, node.y)
            if c > 0.6:
                node.health = max(0.3, node.health - (c - 0.6) * 0.08 * dt / TICKS_PER_DAY)
    for nid in dead:
        region.resources.pop(nid, None)


def consume(world, region: Region, node: ResourceNode, amount: float, consumer_id: int | None = None,
            tick: int | None = None) -> float:
    """Remove up to ``amount`` from a node; returns what was actually taken."""
    if node.kind == "shelter":
        return 0.0
    now = world.clock.tick if tick is None else tick
    taken = min(node.amount, amount)
    node.amount -= taken
    if node.amount < 0.5 and taken > 0 and node.kind not in ("meat",):
        if node.depleted_tick < 0 or now - node.depleted_tick > 120:
            node.depleted_count += 1
            node.depleted_tick = now
            node.health = max(0.3, node.health - 0.04)
            world.bus.publish("resource_depleted", now, region=region.id, node=node.id,
                              kind=node.kind, consumer=consumer_id)
    return taken


def add_remains(world, region: Region, x: float, y: float, amount: float) -> ResourceNode:
    ix, iy = int(x), int(y)
    for n in region.resources.values():
        if n.kind == "meat" and abs(n.x - ix) <= 1 and abs(n.y - iy) <= 1:
            n.amount = min(n.capacity, n.amount + amount)
            return n
    node = ResourceNode(world.new_id(), "meat", ix, iy, amount, max(amount, 20.0))
    region.resources[node.id] = node
    return node


# ---- summaries ---------------------------------------------------------------

def food_totals(region: Region) -> dict[str, float]:
    out: dict[str, float] = {}
    for n in region.resources.values():
        out[n.kind] = out.get(n.kind, 0.0) + n.amount
    return out


def plant_fullness(world, region: Region) -> float:
    tot, cap = 0.0, 0.0
    for n in region.resources.values():
        rk = world.content.resources[n.kind]
        if rk.food and n.kind != "meat":
            tot += n.amount
            cap += n.capacity
    return tot / cap if cap else 0.0


def suitability(world, region: Region, species_id: str, pop: dict[str, int] | None = None,
                extra: int = 0, explain: bool = False):
    """How good a region is for one more member of a species (higher = better).

    Returns (score, factors) where factors has interpretable components.
    """
    sp = world.species(species_id)
    habitat = sp.habitat_score(region.biome_id)
    if habitat <= 0:
        return (-1.0, {"habitat": 0.0}) if explain else -1.0
    if pop is None:
        pop = world.population(region.id)
    food = food_totals(region)
    content = world.content
    # supply for this diet
    supply = sum(food.get(k, 0.0) * w for k, w in sp.diet.items() if k != "meat")
    if sp.is_predator:
        prey_biomass = sum(pop.get(p, 0) * content.species[p].meat_value for p in sp.prey)
        supply += prey_biomass * 0.5 + food.get("meat", 0.0)
    # demand from everyone eating overlapping food
    demand = 0.0
    for sid, n in pop.items():
        o = content.species[sid]
        if sid == species_id:
            demand += (n + extra) * o.appetite
            continue
        overlap = sum(min(o.diet.get(k, 0), sp.diet.get(k, 0)) for k in sp.diet)
        if sp.is_predator and o.is_predator:
            overlap += len(set(o.prey) & set(sp.prey)) / max(len(sp.prey), 1)
        demand += n * o.appetite * min(1.0, overlap) * 0.6
    if extra == 0 and pop.get(species_id, 0) == 0:
        demand += sp.appetite
    food_ratio = supply / (demand * 1.5 + sp.appetite * 2)
    food_score = min(1.0, food_ratio)
    water = food.get("water", 0.0)
    water_score = min(1.0, water / (sum(pop.values()) * 4 + 30))
    corr = region.corruption_level
    over = max(0.0, corr - sp.corruption_tolerance)
    corr_term = -over * 1.8 + (corr * sp.corruption_affinity * 0.6)
    danger = 0.0
    for pid in content.predators_of(species_id):
        danger += pop.get(pid, 0) * content.species[pid].attack
    danger = min(1.0, danger / (12.0 * (pop.get(species_id, 0) + 3)))
    total_pop = sum(pop.values()) + extra
    crowd = max(0.0, total_pop / 70.0 - 0.6)
    score = habitat * 0.35 + food_score * 0.45 + water_score * 0.1 + corr_term - danger * 0.3 - crowd * 0.2
    if explain:
        return score, {"habitat": round(habitat, 2), "food": round(food_score, 2),
                       "water": round(water_score, 2), "corruption": round(-over, 2),
                       "danger": round(danger, 2), "crowding": round(crowd, 2)}
    return score


def dominant_pressure(factors: dict) -> str:
    """Which factor is pushing a creature out (for readable migration reasons)."""
    pressures = {
        "food scarcity": 1.0 - factors.get("food", 1.0),
        "corruption": -factors.get("corruption", 0.0) * 1.5,
        "predators": factors.get("danger", 0.0),
        "overcrowding": factors.get("crowding", 0.0),
        "thirst": (1.0 - factors.get("water", 1.0)) * 0.8,
        "poor habitat": (1.0 - factors.get("habitat", 1.0)) * 0.6,
    }
    return max(pressures.items(), key=lambda kv: kv[1])[0]


def daily_rate(rate_per_tick: float) -> float:
    return rate_per_tick * TICKS_PER_DAY


def clamp01(v: float) -> float:
    return 0.0 if v < 0 else 1.0 if v > 1 else v


def sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))
