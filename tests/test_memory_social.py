from digitalworld.entities.memory import MemoryStore, Relationship, PLAYER_ID
from digitalworld.sim import social
from digitalworld.entities.creature import Group

from helpers import lab_region, open_spot, spawn


def test_memory_merges_repeats_and_prunes_by_salience():
    m = MemoryStore()
    m.add(0, "attacked_by", 5, 1, 0, 0, 0.7)
    m.add(10, "attacked_by", 5, 1, 0, 0, 0.7)
    assert len(m.records) == 1 and m.records[0].count == 2
    for i in range(100):
        m.add(i * 200, "discovered", None, 1, i, i, 0.1)
    m.add(20000, "lost_kin", 9, 1, 0, 0, 0.9)
    assert len(m.records) <= MemoryStore.CAPACITY
    assert m.recall("lost_kin")              # strong memories survive pruning


def test_memory_fades_with_time():
    m = MemoryStore()
    r = m.add(0, "displaced_by", 3, 1, intensity=0.6)
    assert r.salience(0) > r.salience(5 * 1440) > 0


def test_memory_roundtrip_tolerates_garbage():
    m = MemoryStore()
    m.add(5, "helped_by", 7, 2, 1.0, 2.0, 0.5, "x")
    data = m.to_list() + [["bad"], None, "junk"]
    m2 = MemoryStore.from_list(data)
    assert len(m2.recall("helped_by")) == 1


def test_relationship_dimensions_and_labels():
    r = Relationship("x")
    r.adjust(0, trust=0.7, affinity=0.6)
    assert r.label() == "friend" and r.disposition > 0.5
    r.adjust(1, hostility=0.8)
    assert r.label() == "enemy"
    r.adjust(2, trust=5.0)
    assert r.trust == 1.0  # clamped
    d = Relationship.from_dict(r.to_dict())
    assert abs(d.hostility - r.hostility) < 1e-3


def test_help_creates_trust(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    a = spawn(sim, region, "mossling", x, y)
    b = spawn(sim, region, "mossling", *open_spot(region, (x + 1, y)))
    sim.record_help(a, b)
    assert b.relations[a.id].trust > 0.15
    assert b.memory.recall("helped_by", subject=a.id)


def test_leader_succession_and_history(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    a = spawn(sim, region, "mossling", x, y)
    b = spawn(sim, region, "mossling", *open_spot(region, (x + 1, y)))
    c = spawn(sim, region, "mossling", *open_spot(region, (x + 2, y)))
    g = Group(sim.world.new_id(), "mossling", "herd", "Test Hum", a.id, [a.id, b.id, c.id], region.id)
    sim.world.groups[g.id] = g
    for m in (a, b, c):
        m.group_id = g.id
    sim.kill(a, "old age")
    sim._flush()
    social.update_groups(sim)
    assert g.leader_id in (b.id, c.id)
    assert sim.world.history.latest("leader_changed") is not None
    new_leader = sim.world.creatures[g.leader_id]
    assert new_leader.memory.recall("became_leader")


def test_territory_claim_and_capture(sim):
    region = lab_region(sim)
    world = sim.world
    x, y = open_spot(region)
    strong = [spawn(sim, region, "shardfang", *open_spot(region, (x + i, y))) for i in range(4)]
    weak = [spawn(sim, region, "shardfang", *open_spot(region, (x + 3, y + 2)))]
    gs = Group(world.new_id(), "shardfang", "pack", "Strong", strong[0].id, [c.id for c in strong], region.id)
    gw = Group(world.new_id(), "shardfang", "pack", "Weak", weak[0].id, [c.id for c in weak], region.id)
    world.groups[gs.id] = gs
    world.groups[gw.id] = gw
    for c in strong:
        c.group_id = gs.id
    for c in weak:
        c.group_id = gw.id
    # the weak pack claims first; then the strong pack moves in
    for c in strong:
        c.x, c.y = 2.5, 2.5
    world.rebuild_spatial()
    social.update_territories(sim, region)
    assert gw.territory_id is not None
    for c in strong:
        c.x, c.y = weak[0].x + 0.2, weak[0].y + 0.2
    world.rebuild_spatial()
    gs.territory_id = None
    from digitalworld.world.region import Territory
    t = Territory(world.new_id(), gs.id, "shardfang", weak[0].x, weak[0].y, 14, 0.9, 0)
    region.territories[t.id] = t
    gs.territory_id = t.id
    for _ in range(80):
        social.update_territories(sim, region)
        if gw.territory_id is None:
            break
    assert gw.territory_id is None
    e = world.history.latest("territory_captured", region.id)
    assert e is not None and "Strong" in e.text
    assert weak[0].memory.recall("territory_lost")
