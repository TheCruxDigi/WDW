import numpy as np

from digitalworld.sim import ecology, corruption, chronicle
from digitalworld.world.region import Structure
from digitalworld.player import interactions as act

from helpers import lab_region, open_spot, add_node, clear_nodes


def test_consumption_and_bounded_regeneration(sim):
    region = lab_region(sim)
    region.corruption[:] = 0.0
    n = add_node(sim, region, "grass", *open_spot(region), amount=10)
    taken = ecology.consume(sim.world, region, n, 4)
    assert taken == 4 and abs(n.amount - 6) < 1e-9
    ecology.regenerate(sim.world, region, 10 ** 6)
    assert n.amount <= n.max_amount + 1e-9
    assert n.amount == n.max_amount


def test_depletion_degrades_patch_health(sim):
    region = lab_region(sim)
    n = add_node(sim, region, "grass", *open_spot(region), amount=5)
    events = []
    sim.world.bus.subscribe("resource_depleted", events.append)
    ecology.consume(sim.world, region, n, 10)
    assert n.amount == 0 and n.depleted_count == 1 and n.health < 1.0
    assert events and events[0].data["node"] == n.id


def test_corruption_suppresses_plants_and_feeds_bytebloom(sim):
    region = lab_region(sim)
    g = add_node(sim, region, "grass", *open_spot(region), amount=0)
    b = add_node(sim, region, "bytebloom", *open_spot(region, (5, 5)), amount=0)
    region.corruption[:] = 0.0
    clean_g = ecology.regen_rate(sim.world, region, g, sim.world.clock.tick)
    clean_b = ecology.regen_rate(sim.world, region, b, sim.world.clock.tick)
    region.corruption[:] = 0.8
    dirty_g = ecology.regen_rate(sim.world, region, g, sim.world.clock.tick)
    dirty_b = ecology.regen_rate(sim.world, region, b, sim.world.clock.tick)
    assert dirty_g < clean_g * 0.2
    assert dirty_b > clean_b * 5


def test_corruption_spreads_through_gates(sim):
    world = sim.world
    a = next(iter(world.regions.values()))
    nb = world.regions[next(iter(a.gates))]
    for r in world.regions.values():
        r.corruption[:] = 0.05
        r.resistance = 0.3
    a.corruption[:] = 0.95
    a.spread = 0.9
    before = nb.corruption_at(*nb.gates[a.id])
    for _ in range(24):
        corruption.spread_between_regions(sim, 60)
    after = nb.corruption_at(*nb.gates[a.id])
    assert after > before + 0.05
    # it is a leak, not a teleport: the far side of the neighbour is still clean-ish
    assert nb.corruption_level < a.corruption_level


def test_beacons_and_stabilization_push_corruption_back(sim):
    region = lab_region(sim)
    region.corruption[:] = 0.6
    x, y = open_spot(region)
    s = Structure(sim.world.new_id(), "beacon", x, y, 1.0, 1.0)
    region.structures[s.id] = s
    before = region.corruption_at(x, y)
    for _ in range(12):
        corruption.update_region(sim, region, 60)
    assert region.corruption_at(x, y) < before - 0.2
    p = sim.world.player
    p.x, p.y = x + 8.5, y + 0.5
    v0 = region.corruption_at(p.x, p.y)
    msg = act.stabilize(sim)
    assert "reduced" in msg and region.corruption_at(p.x, p.y) < v0


def test_corruption_band_change_is_recorded_with_history(sim):
    region = lab_region(sim)
    chronicle.census(sim, region)
    region.corruption[:] = 0.75
    chronicle.census(sim, region)
    e = sim.world.history.latest("corruption_rise", region.id)
    assert e is not None and "fractured" in e.text


def test_high_corruption_erodes_structures(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    s = Structure(sim.world.new_id(), "beacon", x, y, 0.2, 1.0)
    region.structures[s.id] = s
    region.corruption[:] = 1.0
    for _ in range(40):
        corruption.update_region(sim, region, 60)
        region.corruption[:] = 1.0
    assert s.id not in region.structures
    assert sim.world.history.latest("structure_destroyed", region.id) is not None


def test_weather_changes_and_datastorm_raises_noise(sim):
    region = lab_region(sim)
    region.weather = "datastorm"
    region.weather_until = sim.world.clock.tick + 10 ** 6
    region.signal_noise = 0.0
    corruption.update_region(sim, region, 60)
    assert region.signal_noise > 0.05
