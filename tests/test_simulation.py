"""Whole-simulation properties: determinism, stability, no identity leaks."""

import pytest

from digitalworld.core.clock import TICKS_PER_DAY
from digitalworld.sim.simulation import Simulation

from conftest import small_config


def _fingerprint(sim):
    w = sim.world
    out = []
    for cid in sorted(w.all_creature_ids()):
        rec = w.find_creature_record(cid)
        if hasattr(rec, "x"):
            out.append((cid, rec.region_id, round(rec.x, 6), round(rec.y, 6), round(rec.needs["hunger"], 6)))
        else:
            out.append((cid, rec["region_id"], round(rec["x"], 6), round(rec["y"], 6), round(rec["needs"]["hunger"], 6)))
    return out, len(w.history.entries), w.next_id


def test_same_seed_same_world(tmp_path):
    a = Simulation.new(small_config(tmp_path, seed=21))
    b = Simulation.new(small_config(tmp_path, seed=21))
    a.run(400)
    b.run(400)
    assert _fingerprint(a) == _fingerprint(b)


def test_headless_simulation_runs_without_window(tmp_path):
    sim = Simulation.new(small_config(tmp_path, seed=2), with_player=False)
    assert sim.world.player is None
    sim.run(300)
    assert sim.errors == 0
    assert any(r.loaded for r in sim.world.regions.values())


def test_multi_day_stability(tmp_path):
    sim = Simulation.new(small_config(tmp_path, seed=8))
    w = sim.world
    start = w.total_population()
    for _ in range(3):
        sim.run(TICKS_PER_DAY // 2)
        ids = w.all_creature_ids()
        assert len(ids) == len(set(ids)), "no duplicated identities"
    sim.fast_forward(3 * TICKS_PER_DAY)
    ids = w.all_creature_ids()
    assert len(ids) == len(set(ids))
    assert sim.errors == 0
    total = w.total_population()
    assert total > 0, "the ecosystem must not collapse entirely"
    assert total < start * 4, "nor explode without bound"
    species_alive = {w.find_creature_record(i).species_id if hasattr(w.find_creature_record(i), "species_id")
                     else w.find_creature_record(i)["species"] for i in ids}
    assert len(species_alive) >= 4
    assert w.history.entries[-1].tick >= TICKS_PER_DAY


def test_creature_update_failure_is_contained(sim, monkeypatch):
    c = next(iter(sim.world.creatures.values()))
    from digitalworld.ai import brain

    orig = brain.Brain.update

    def flaky(self, cr, sp, region):
        if cr is c:
            raise RuntimeError("boom")
        return orig(self, cr, sp, region)

    monkeypatch.setattr(brain.Brain, "update", flaky)
    sim.run(20)
    assert sim.errors >= 1
    assert sim.world.clock.tick > 0 and len(sim.world.creatures) > 1
