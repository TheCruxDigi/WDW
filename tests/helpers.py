"""Scenario helpers: build controlled situations inside a generated world."""
import random

from digitalworld.world.generator import make_creature
from digitalworld.world.region import ResourceNode


def lab_region(sim):
    """The player's (loaded) region, emptied of creatures."""
    world = sim.world
    rid = world.player.region_id if world.player else sim.focus_region
    region = world.regions[rid]
    for c in list(world.creatures.values()):
        if c.region_id == rid:
            del world.creatures[c.id]
    for g in world.groups.values():
        g.members = [m for m in g.members if m in world.creatures or world.find_creature_record(m) is not None]
    if world.player is not None:
        world.player.hidden = True  # keep the Wayfinder out of controlled experiments
    region.territories.clear()
    region.anomalies.clear()
    region.structures.clear()
    world.rebuild_spatial()
    return region


def open_spot(region, near=None, swim=False):
    x, y = near if near else (region.width // 2, region.height // 2)
    return region.nearest_walkable(x, y, swim)


def spawn(sim, region, species, x, y, **needs):
    world = sim.world
    rng = random.Random(len(world.creatures) + 1)
    c = make_creature(world, species, region, x + 0.5, y + 0.5, world.clock.tick - 5 * 1440, rng)
    c.needs.update({"hunger": 0.0, "thirst": 0.0, "fatigue": 0.0, "curiosity": 0.0, "loneliness": 0.0})
    c.needs.update(needs)
    c.repro_ready_tick = 10 ** 9
    world.creatures[c.id] = c
    world.rebuild_spatial()
    return c


def add_node(sim, region, kind, x, y, amount=None):
    rk = sim.world.content.resources[kind]
    n = ResourceNode(sim.world.new_id(), kind, x, y, rk.capacity if amount is None else amount, rk.capacity)
    region.resources[n.id] = n
    return n


def clear_nodes(region, kinds):
    for nid in [n.id for n in region.resources.values() if n.kind in kinds]:
        del region.resources[nid]
