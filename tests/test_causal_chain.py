"""End-to-end causal chain across systems and a restart:

consumption depletes food -> competition rises -> creatures search for
alternatives -> part of the population migrates -> regional distribution
changes -> relationships change -> history records causes -> save, restart ->
all consequences are still present.
"""

from digitalworld.entities.creature import Group
from digitalworld.persistence import savegame
from digitalworld.sim import chronicle
from digitalworld.sim.simulation import Simulation

from conftest import small_config
from helpers import lab_region, open_spot, spawn, add_node, clear_nodes


def test_full_causal_chain_survives_restart(tmp_path):
    cfg = small_config(tmp_path, seed=5)
    sim = Simulation.new(cfg)
    world = sim.world
    sid = "mossling"
    sp = world.species(sid)
    # pick a region that has a neighbour where mosslings can live
    pairs = [(r, world.regions[n]) for r in world.regions.values() for n in sorted(r.gates)
             if sp.habitat_score(world.regions[n].biome_id) >= 0.8]
    assert pairs, "world must offer a viable destination"
    home, best_nb = pairs[0]
    world.player.region_id = home.id
    sim.update_lod()
    region = lab_region(sim)
    assert region.id == home.id and best_nb.loaded
    for gx in range(3):
        x0, y0 = best_nb.nearest_walkable(best_nb.width // 2 + gx * 3, best_nb.height // 2)
        add_node(sim, best_nb, "grass", x0, y0)
    # 1) scarcity: one small patch for a crowded herd
    clear_nodes(region, ("grass", "fruit", "fungus", "sunbulb", "shoal", "bytebloom"))
    x, y = open_spot(region)
    patch = add_node(sim, region, "grass", *open_spot(region, (x + 2, y)), amount=25)
    herd = [spawn(sim, region, sid, *open_spot(region, (x + (i % 4), y + i // 4)), hunger=0.75)
            for i in range(8)]
    for i, c in enumerate(herd):
        c.traits["aggression"] = 0.7 if i % 2 else 0.1
        c.traits["wanderlust"] = 0.7
        c.traits["boldness"] = 0.6
    g = Group(world.new_id(), sid, "herd", "Hungry Chorus", herd[0].id, [c.id for c in herd], region.id)
    world.groups[g.id] = g
    for c in herd:
        c.group_id = g.id
    other = Group(world.new_id(), sid, "herd", "Rival Hum", None, [], region.id)
    # half the herd belongs to a rival group so feeding becomes real competition
    world.groups[other.id] = other
    for c in herd[4:]:
        c.group_id = other.id
        g.members.remove(c.id)
        other.members.append(c.id)
    other.leader_id = herd[4].id
    start_amount = patch.amount
    chronicle.census(sim, region)
    collapse = world.history.latest("resource_collapse", region.id)
    assert collapse is not None, "the chronicle notices the forage collapse"

    left = []
    for _ in range(3000):
        sim.step()
        left = [c for c in herd if c.alive and c.region_id != region.id]
        if len(left) >= 3:
            break

    # consumption happened and degraded the patch
    assert patch.amount < start_amount or patch.depleted_count > 0
    # competition for the patch
    assert world.bus.counts.get("competition", 0) > 0 or any(
        c.memory.recall("displaced_by") for c in herd), "feeders competed for the patch"
    # creatures looked for alternatives
    reasons = [d.reason for c in herd for d in c.decisions]
    assert any("searching for food" in r or "food scarcity" in r or "following" in r for r in reasons)
    # part of the population migrated along a real connection, identities intact
    assert len(left) >= 3, f"only {len(left)} migrated"
    assert all(c.region_id in region.gates for c in left)
    ids = world.all_creature_ids()
    assert len(ids) == len(set(ids))
    # regional distribution changed
    pop_here = world.population(region.id).get(sid, 0)
    pop_there = sum(world.population(r).get(sid, 0) for r in region.gates)
    assert pop_here < len(herd)
    # relationships changed (rivalry / hostility from displacement or kin bonds)
    assert any(any(r.rivalry > 0 or r.hostility > 0 for r in c.relations.values()) for c in herd)
    # history connects the migration to its cause
    wave = world.history.latest("migration_wave", region.id)
    group_mig = world.history.latest("group_migration", region.id)
    assert wave is not None or group_mig is not None
    if wave is not None:
        assert collapse.id in wave.causes
    print(f"\nchain: tick={world.clock.tick} migrated={len(left)} competition={world.bus.counts.get('competition', 0)} "
          f"patch={patch.amount:.1f}/{start_amount:.0f} depleted={patch.depleted_count} pop_here={pop_here} "
          f"pop_neighbours={pop_there} wave={wave.text if wave else None} causes={wave.causes if wave else None} "
          f"collapse={collapse.id}")
    migrant = left[0]
    migrant_id, migrant_region = migrant.id, migrant.region_id

    # ---- save, "close the application", restart ----
    savegame.save(sim, cfg.save_path)
    del sim, world
    sim2, report = savegame.load(cfg.save_path, small_config(tmp_path, seed=5), offline=False)
    w2 = sim2.world
    rec = w2.find_creature_record(migrant_id)
    assert rec is not None
    d = rec.to_dict() if hasattr(rec, "to_dict") else rec
    assert d["region_id"] == migrant_region
    mem_kinds = [m[1] for m in d["memory"]]
    assert "left_region" in mem_kinds and "migrated" in mem_kinds
    assert w2.history.latest("resource_collapse", region.id) is not None
    if wave is not None:
        w = w2.history.get(wave.id)
        assert w is not None and collapse.id in w.causes
    p2 = w2.regions[region.id].resources.get(patch.id)
    assert p2 is None or p2.depleted_count == patch.depleted_count
    # the world continues from here
    sim2.run(300)
    assert sim2.errors == 0
    ids2 = w2.all_creature_ids()
    assert len(ids2) == len(set(ids2))
