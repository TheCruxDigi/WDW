from digitalworld.entities.memory import PLAYER_ID
from digitalworld.player import interactions as act
from digitalworld.world.region import Territory
from digitalworld.entities.creature import Group

from helpers import lab_region, open_spot, spawn, add_node


def _place_player(sim, x, y):
    p = sim.world.player
    p.hidden = False
    p.x, p.y = x + 0.5, y + 0.5
    return p


def test_feed_changes_needs_trust_memory_and_witnesses(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    a = spawn(sim, region, "mossling", x, y, hunger=0.7)
    b = spawn(sim, region, "mossling", *open_spot(region, (x + 2, y)))
    g = Group(sim.world.new_id(), "mossling", "herd", "Witnesses", a.id, [a.id, b.id], region.id)
    sim.world.groups[g.id] = g
    a.group_id = b.group_id = g.id
    p = _place_player(sim, x + 1, y)
    p.inventory["seeds"] = 2
    t_b = b.relation(PLAYER_ID, "player").trust
    msg = act.feed(sim, a)
    assert "fed" in msg
    assert a.needs["hunger"] < 0.4
    assert a.memory.recall("fed_by_player")
    assert b.relations[PLAYER_ID].trust > t_b, "the herd notices kindness"
    assert p.inventory["seeds"] == 1


def test_repeated_feeding_forms_a_bond_recorded_in_history(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    c = spawn(sim, region, "mossling", x, y, hunger=0.9)
    p = _place_player(sim, x + 1, y)
    p.inventory["seeds"] = 10
    for _ in range(6):
        act.feed(sim, c)
    assert c.memory.recall("bonded", subject=PLAYER_ID)
    assert sim.world.history.latest("player_bond") is not None


def test_bonded_creature_approaches_player(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    c = spawn(sim, region, "mossling", x, y)
    rel = c.relation(PLAYER_ID, "player")
    rel.trust, rel.affinity, rel.fear = 0.9, 0.8, 0.0
    _place_player(sim, *open_spot(region, (x + 6, y)))
    seen = False
    for _ in range(80):
        sim.step()
        if c.action_name == "approach_player":
            seen = True
            break
    assert seen


def test_scare_creates_fear_and_flight(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    c = spawn(sim, region, "glimmerhare", x, y)
    c.traits["boldness"] = 0.1
    _place_player(sim, x + 1, y)
    act.scare(sim, c)
    assert c.relations[PLAYER_ID].fear > 0.3
    assert c.action_name == "flee"
    assert c.memory.recall("scared_by_player")


def test_heal_restores_health_and_taint(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    c = spawn(sim, region, "fennwisp", x, y)
    c.health, c.taint = 0.3, 0.8
    p = _place_player(sim, x + 1, y)
    p.energy = 100
    act.heal(sim, c)
    assert c.health > 0.6 and c.taint < 0.4 and p.energy == 80


def test_gather_depletes_real_resources(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    n = add_node(sim, region, "fruit", x, y, amount=12)
    p = _place_player(sim, x, y)
    seeds = p.inventory["seeds"]
    act.gather(sim)
    assert n.amount < 12 and p.inventory["seeds"] > seeds


def test_build_and_dismantle_beacon_have_consequences(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    keeper = spawn(sim, region, "lumen_keeper", *open_spot(region, (x + 3, y)))
    g = Group(sim.world.new_id(), "lumen_keeper", "herd", "Archivists", keeper.id, [keeper.id], region.id)
    sim.world.groups[g.id] = g
    keeper.group_id = g.id
    p = _place_player(sim, x, y)
    p.inventory["shards"] = 5
    msg = act.build_beacon(sim)
    assert "raised" in msg
    s = next(iter(region.structures.values()))
    s.owner_group = g.id
    assert sim.world.history.latest("structure_built", region.id) is not None
    hostility = keeper.relation(PLAYER_ID, "player").hostility
    p.x, p.y = s.x + 0.5, s.y + 1.0
    act.dismantle(sim)
    assert not region.structures
    assert keeper.relations[PLAYER_ID].hostility > hostility
    assert sim.world.history.latest("structure_destroyed", region.id) is not None


def test_entering_territory_triggers_warning_then_force(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    owner = spawn(sim, region, "thornhorn", x, y)
    owner.traits["aggression"] = 0.9
    g = Group(sim.world.new_id(), "thornhorn", "herd", "Guardians", owner.id, [owner.id], region.id)
    sim.world.groups[g.id] = g
    owner.group_id = g.id
    t = Territory(sim.world.new_id(), g.id, "thornhorn", owner.x, owner.y, 10, 0.9, 0)
    region.territories[t.id] = t
    g.territory_id = t.id
    p = _place_player(sim, *open_spot(region, (x + 3, y)))
    p.integrity = 100
    warned = hit = False
    for _ in range(600):
        sim.step()
        warned |= bool(owner.memory.recall("warned_player"))
        if p.integrity < 100:
            hit = True
            break
        # the player stubbornly stays inside the territory
        p.x, p.y = owner.x + 1.5, owner.y
    assert warned and hit


def test_player_collision_and_region_travel(sim):
    world = sim.world
    p = world.player
    region = world.regions[p.region_id]
    nb, (gx, gy) = sorted(region.gates.items())[0]
    p.x, p.y = gx + 0.5, gy + 0.5
    dx = -1 if gx == 0 else 1 if gx == region.width - 1 else 0
    dy = -1 if gy == 0 else 1 if gy == region.height - 1 else 0
    for _ in range(20):
        act.move_player(sim, dx, dy, 0.1)
        if p.region_id != region.id:
            break
    assert p.region_id == nb
    assert world.regions[nb].is_walkable(int(p.x), int(p.y))
