"""Behaviour tests: needs drive decisions, actions progress and recover."""

from helpers import lab_region, open_spot, spawn, add_node, clear_nodes


def run_until(sim, cond, max_ticks=600):
    for _ in range(max_ticks):
        sim.step()
        if cond():
            return True
    return False


def test_needs_grow_over_time(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    c = spawn(sim, region, "mossling", x, y)
    clear_nodes(region, ("grass", "fruit", "water"))
    before = dict(c.needs)
    sim.run(200)
    assert c.needs["hunger"] > before["hunger"]
    assert c.needs["thirst"] > before["thirst"]


def test_hungry_creature_eats_and_food_is_consumed(sim):
    region = lab_region(sim)
    clear_nodes(region, ("grass", "fruit"))
    x, y = open_spot(region)
    node = add_node(sim, region, "grass", *open_spot(region, (x + 3, y)))
    c = spawn(sim, region, "mossling", x, y, hunger=0.8)
    amount0 = node.amount
    assert run_until(sim, lambda: c.needs["hunger"] < 0.1, 400)
    assert node.amount < amount0
    assert any(d.action == "eat" for d in c.decisions)
    assert node.id in c.known_nodes  # it remembers where it ate


def test_thirsty_creature_drinks(sim):
    region = lab_region(sim)
    water = region.nodes_of("water")
    assert water
    w = water[0]
    x, y = open_spot(region, (w.x + 2, w.y))
    c = spawn(sim, region, "mossling", x, y, thirst=0.85)
    assert run_until(sim, lambda: c.needs["thirst"] < 0.1, 500)


def test_tired_creature_rests_and_recovers(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    c = spawn(sim, region, "lumen_keeper", x, y, fatigue=0.95)
    assert run_until(sim, lambda: c.action_name == "rest", 50)
    assert run_until(sim, lambda: c.needs["fatigue"] < 0.3, 400)


def test_prey_flees_and_predator_hunts(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    prey = spawn(sim, region, "mossling", x, y)
    hx, hy = open_spot(region, (x + 4, y))
    hunter = spawn(sim, region, "shardfang", hx, hy, hunger=0.9)
    saw_flee = saw_hunt = False
    for _ in range(300):
        sim.step()
        saw_flee |= prey.action_name == "flee"
        saw_hunt |= hunter.action_name == "hunt"
        if not prey.alive:
            break
    assert saw_hunt
    assert saw_flee or not prey.alive
    if not prey.alive:
        assert prey.death_cause.startswith("killed by Shardfang")
        assert any(n.kind == "meat" for n in region.resources.values())
    else:
        assert prey.memory.recall("attacked_by") or prey.relations.get(hunter.id) is not None


def test_victim_remembers_attacker_and_fears_it(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    a = spawn(sim, region, "shardfang", x, y)
    b = spawn(sim, region, "thornhorn", *open_spot(region, (x + 1, y)))
    sim.attack(a, b)
    assert b.memory.recall("attacked_by", subject=a.id)
    rel = b.relations[a.id]
    assert rel.fear > 0 and rel.hostility > 0


def test_invalid_target_recovers_without_loop(sim):
    region = lab_region(sim)
    clear_nodes(region, ("grass", "fruit"))
    x, y = open_spot(region)
    node = add_node(sim, region, "grass", *open_spot(region, (x + 8, y)))
    c = spawn(sim, region, "mossling", x, y, hunger=0.7)
    assert run_until(sim, lambda: c.action_name == "eat", 30)
    del region.resources[node.id]          # the food vanishes mid-action
    sim.run(5)
    assert c.action_name != "eat" or c.action["target"] != node.id
    assert c.blacklisted(f"n{node.id}", sim.world.clock.tick)
    sim.run(100)                            # keeps living, no exception storm
    assert sim.errors == 0


def test_decisions_do_not_oscillate(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    cs = [spawn(sim, region, sp, *open_spot(region, (x + i * 2, y)), hunger=0.5, thirst=0.5)
          for i, sp in enumerate(["mossling", "glimmerhare", "lumen_keeper", "thornhorn"])]
    sim.run(720)
    for c in cs:
        if not c.alive:
            continue
        ds = list(c.decisions)
        # count A->B->A flips within 10 ticks
        flips = sum(1 for a, b, d in zip(ds, ds[1:], ds[2:])
                    if a.action == d.action and a.action != b.action and d.tick - a.tick < 10)
        assert flips <= 2, [(d.tick, d.action) for d in ds]


def test_utility_scores_and_reasons_are_recorded(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    c = spawn(sim, region, "glimmerhare", x, y, hunger=0.6)
    sim.run(3)
    assert c.last_scores and all(len(s) == 3 for s in c.last_scores)
    assert c.decisions and c.decisions[-1].reason


def test_taint_accumulates_in_corruption_and_heals_outside(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    c = spawn(sim, region, "mossling", x, y)
    region.corruption[:] = 0.9
    from digitalworld.sim.needs import update_needs
    sp = sim.world.species("mossling")
    for _ in range(200):
        update_needs(sim, c, sp, region)
    assert c.taint > 0.3
    region.corruption[:] = 0.0
    t = c.taint
    for _ in range(200):
        update_needs(sim, c, sp, region)
    assert c.taint < t


def test_corrupted_creature_avoids_corruption(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    c = spawn(sim, region, "mossling", x, y)
    region.corruption[:] = 0.05
    cy, cx = int(c.y) // 4, int(c.x) // 4
    region.corruption[max(0, cy - 1):cy + 2, max(0, cx - 1):cx + 2] = 0.9
    sim.world.invalidate_region_caches(region.id)
    assert run_until(sim, lambda: c.action_name == "avoid_corruption", 30)
    assert run_until(sim, lambda: region.corruption_at(c.x, c.y) < 0.5, 200)


def test_herd_mobs_predator(sim):
    region = lab_region(sim)
    x, y = open_spot(region)
    herd = [spawn(sim, region, "thornhorn", *open_spot(region, (x + dx, y + dy)))
            for dx, dy in ((0, 0), (1, 0), (0, 1))]
    g = sim.world.groups[next(iter(sim.world.groups))]
    from digitalworld.entities.creature import Group
    grp = Group(sim.world.new_id(), "thornhorn", "herd", "Test Herd", herd[0].id, [h.id for h in herd], region.id)
    sim.world.groups[grp.id] = grp
    for h in herd:
        h.group_id = grp.id
    wolf = spawn(sim, region, "shardfang", *open_spot(region, (x + 3, y)), hunger=0.95)
    sim.attack(wolf, herd[1])
    helped = False
    for _ in range(120):
        sim.step()
        if any(h.action_name in ("help_ally", "defend") for h in herd if h.alive):
            helped = True
            break
    assert helped
