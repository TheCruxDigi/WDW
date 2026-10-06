"""Migration, level-of-detail transitions and simulation during absence."""

from digitalworld.core.clock import TICKS_PER_DAY
from digitalworld.sim.ecology import suitability

from helpers import lab_region, open_spot, spawn, clear_nodes


def _neighbour(sim, region):
    return sim.world.regions[sorted(region.gates)[0]]


def test_food_collapse_makes_neighbour_attractive_and_creature_migrates(sim):
    world = sim.world
    region = lab_region(sim)
    sp = "mossling"
    nb = max((world.regions[n] for n in region.gates), key=lambda r: world.species(sp).habitat_score(r.biome_id))
    # make sure the neighbour can feed mosslings
    if world.species(sp).habitat_score(nb.biome_id) <= 0:
        sp = next(s for s in world.content.species if world.species(s).habitat_score(nb.biome_id) > 0.5
                  and world.species(s).habitat_score(region.biome_id) > 0 and not world.species(s).is_predator)
    clear_nodes(region, tuple(world.species(sp).diet))
    x, y = open_spot(region)
    c = spawn(sim, region, sp, x, y, hunger=0.6)
    c.traits["wanderlust"] = 0.8
    here, f = suitability(world, region, sp, explain=True)
    there = suitability(world, nb, sp, extra=1)
    assert f["food"] < 0.2 and there > here
    target = sim.migration.evaluate(c, world.species(sp), region, world.clock.tick)
    assert target is not None and c.migrate_reason == "food scarcity"
    cid = c.id
    for _ in range(1500):
        sim.step()
        if c.region_id != region.id:
            break
    assert c.region_id != region.id, "creature should have migrated"
    assert c.region_id in region.gates                      # moved along a real connection
    assert world.all_creature_ids().count(cid) == 1         # identity preserved, no duplicate
    assert c.memory.recall("left_region", region=region.id)
    assert c.counters["migrations"] == 1


def test_recently_left_region_is_penalised(sim):
    world = sim.world
    region = lab_region(sim)
    nb = _neighbour(sim, region)
    x, y = open_spot(region)
    c = spawn(sim, region, "mossling", x, y)
    base = None
    sim.migration.evaluate(c, world.species("mossling"), region, world.clock.tick)
    c.memory.add(world.clock.tick, "left_region", None, nb.id, 0, 0, 0.9, "food scarcity")
    # the neighbour it just fled is no longer chosen unless something else is clearly better
    for other in region.gates:
        if other == nb.id:
            continue
    t = sim.migration.evaluate(c, world.species("mossling"), region, world.clock.tick)
    assert t != nb.id or len(region.gates) == 1


def test_unload_load_preserves_identity_memory_and_relations(sim):
    world = sim.world
    region = lab_region(sim)
    x, y = open_spot(region)
    a = spawn(sim, region, "mossling", x, y)
    b = spawn(sim, region, "mossling", *open_spot(region, (x + 1, y)))
    a.memory.add(world.clock.tick, "helped_by", b.id, region.id, a.x, a.y, 0.8)
    a.relation(b.id, "mossling").adjust(world.clock.tick, trust=0.6, affinity=0.5)
    traits = dict(a.traits)
    sim.focus_region = region.id
    world.player.region_id = region.id
    ids_before = sorted(world.all_creature_ids())
    for _ in range(3):
        sim.unload_region(region.id)
        assert not region.loaded and a.id not in world.creatures
        sim.load_region(region.id)
    ids_after = sorted(world.all_creature_ids())
    assert ids_before == ids_after
    a2 = world.creatures[a.id]
    assert a2 is not a                                   # really rebuilt from data
    assert a2.traits == traits
    assert a2.relations[b.id].trust >= 0.59
    assert a2.memory.recall("helped_by", subject=b.id)


def test_dormant_region_changes_during_absence(sim):
    world = sim.world
    region = lab_region(sim)
    x, y = open_spot(region)
    cs = [spawn(sim, region, "mossling", *open_spot(region, (x + i, y)), hunger=0.3) for i in range(5)]
    food = [n for n in region.resources.values() if n.kind in ("grass", "fruit")]
    for n in food:
        n.amount = n.max_amount * 0.2
    total_before = sum(n.amount for n in food)
    hunger_before = [c.needs["hunger"] for c in cs]
    sim.unload_region(region.id)
    t0 = world.clock.tick
    world.clock.advance(TICKS_PER_DAY)  # a day passes while nobody watches
    sim.abstract.advance(region, world.clock.tick)
    assert region.last_sim_tick == world.clock.tick
    assert sum(n.amount for n in food if n.id in region.resources) != total_before
    recs = {d["id"]: d for d in region.dormant_creatures}
    survivors = [recs[c.id] for c in cs if c.id in recs]
    assert survivors, "some of the herd should still exist"
    assert any(abs(d["needs"]["hunger"] - h) > 0.01 for d, h in zip(survivors, hunger_before))
    sim.load_region(region.id)
    for d in survivors:
        c = world.creatures[d["id"]]
        assert region.is_walkable(int(c.x), int(c.y))     # reconstructed at a valid position


def test_lod_follows_player(sim):
    world = sim.world
    p = world.player
    start = p.region_id
    loaded = {r.id for r in world.loaded_regions()}
    assert start in loaded and set(world.regions[start].gates) <= loaded
    from digitalworld.player import interactions as act
    target = sorted(world.regions[start].gates)[0]
    act.travel(sim, target)
    loaded2 = {r.id for r in world.loaded_regions()}
    assert target in loaded2 and set(world.regions[target].gates) <= loaded2
    far = [r for r in world.regions.values() if r.id not in loaded2]
    assert all(not r.loaded for r in far)
    assert world.regions[target].discovered


def test_fast_forward_produces_history_and_keeps_population_consistent(sim):
    world = sim.world
    ids = world.all_creature_ids()
    assert len(ids) == len(set(ids))
    entries = sim.fast_forward(2 * TICKS_PER_DAY)
    ids2 = world.all_creature_ids()
    assert len(ids2) == len(set(ids2))
    assert world.clock.tick >= 2 * TICKS_PER_DAY
    assert all(r.last_sim_tick == world.clock.tick for r in world.regions.values())
    assert world.total_population() > 0
    assert entries  # something happened while time passed
