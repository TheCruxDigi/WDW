import json
import sqlite3
import time

import pytest

from digitalworld.persistence import savegame
from digitalworld.entities.memory import PLAYER_ID
from digitalworld.player import interactions as act

from conftest import small_config
from helpers import lab_region, open_spot, spawn


def _state(sim):
    w = sim.world
    out = {}
    for cid in w.all_creature_ids():
        rec = w.find_creature_record(cid)
        d = rec.to_dict() if hasattr(rec, "to_dict") else rec
        out[cid] = (d["species"], d["region_id"], d["name"], d["born_tick"], round(d["traits"]["boldness"], 6),
                    len(d["memory"]), sorted(d["relations"]))
    return out


def test_roundtrip_preserves_world(sim, cfg):
    sim.run(300)
    w = sim.world
    before = _state(sim)
    hist = [e.text for e in w.history.entries]
    path = savegame.save(sim, cfg.save_path)
    sim2, report = savegame.load(path, cfg, offline=False)
    w2 = sim2.world
    assert _state(sim2) == before
    assert [e.text for e in w2.history.entries] == hist
    assert w2.clock.tick == w.clock.tick and w2.next_id >= w.next_id
    assert {g.id for g in w2.groups.values()} == {g.id for g in w.groups.values()}
    for r in w.regions.values():
        r2 = w2.regions[r.id]
        assert r2.name == r.name and abs(r2.corruption_level - r.corruption_level) < 1e-3
        assert set(r2.resources) == set(r.resources)
        assert set(r2.structures) == set(r.structures)
    assert w2.player.region_id == w.player.region_id
    sim2.run(200)
    assert sim2.errors == 0


def test_save_is_atomic_and_backup_is_used(sim, cfg):
    p = savegame.save(sim, cfg.save_path)
    sim.run(50)
    savegame.save(sim, cfg.save_path)
    bak = p.with_suffix(".db.bak")
    assert bak.exists()
    # corrupt the main file: loading must fall back to the backup
    p.write_bytes(b"this is not a database" * 100)
    sim2, report = savegame.load(p, cfg, offline=False)
    assert report["used_backup"]
    assert sim2.world.total_population() > 0


def test_refuses_to_save_duplicates(sim, cfg):
    savegame.save(sim, cfg.save_path)
    good = cfg.save_path.read_bytes()
    w = sim.world
    c = next(iter(w.creatures.values()))
    dormant = next(r for r in w.regions.values() if not r.loaded)
    dormant.dormant_creatures.append(c.to_dict())
    with pytest.raises(savegame.SaveError):
        savegame.save(sim, cfg.save_path)
    assert cfg.save_path.read_bytes() == good, "a refused save must not touch the existing file"


def test_load_skips_damaged_rows_and_fills_missing_fields(sim, cfg):
    path = savegame.save(sim, cfg.save_path)
    conn = sqlite3.connect(path)
    cid, data = conn.execute("SELECT id, data FROM creatures LIMIT 1").fetchone()
    d = json.loads(data)
    for k in ("traits", "memory", "relations", "known_nodes", "counters"):
        d.pop(k, None)
    conn.execute("UPDATE creatures SET data=? WHERE id=?", (json.dumps(d), cid))
    other = conn.execute("SELECT id FROM creatures WHERE id != ? LIMIT 1", (cid,)).fetchone()[0]
    conn.execute("UPDATE creatures SET data='{broken' WHERE id=?", (other,))
    conn.commit()
    conn.close()
    sim2, _ = savegame.load(path, cfg, offline=False)
    rec = sim2.world.find_creature_record(cid)
    assert rec is not None
    assert sim2.world.find_creature_record(other) is None
    sim2.run(100)
    assert sim2.errors == 0


def test_migrates_v1_saves(sim, cfg):
    path = savegame.save(sim, cfg.save_path)
    conn = sqlite3.connect(path)
    pdata = conn.execute("SELECT data FROM player").fetchone()[0]
    conn.execute("DROP TABLE player")
    conn.execute("INSERT INTO meta(key, value) VALUES ('player', ?)", (pdata,))
    conn.execute("UPDATE meta SET value='1' WHERE key='format_version'")
    conn.commit()
    conn.close()
    sim2, _ = savegame.load(path, cfg, offline=False)
    assert sim2.world.player is not None
    assert sim2.world.player.region_id == sim.world.player.region_id


def test_rejects_future_format(sim, cfg):
    path = savegame.save(sim, cfg.save_path)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value='99' WHERE key='format_version'")
    conn.commit()
    conn.close()
    with pytest.raises(savegame.LoadError):
        savegame.load(path, cfg, offline=False)


def test_offline_progression_simulates_absence(sim, cfg):
    path = savegame.save(sim, cfg.save_path)
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value=? WHERE key='saved_at'", (json.dumps(time.time() - 3 * 3600),))
    conn.commit()
    conn.close()
    cfg.offline_progress = True
    cfg.offline_ticks_per_real_second = 0.2
    sim2, report = savegame.load(path, cfg)
    assert report["offline_ticks"] > 1500
    assert sim2.world.clock.tick >= sim.world.clock.tick + report["offline_ticks"]
    ids = sim2.world.all_creature_ids()
    assert len(ids) == len(set(ids))


def test_player_relationship_survives_restart(sim, cfg):
    region = lab_region(sim)
    p = sim.world.player
    p.hidden = False
    x, y = open_spot(region)
    c = spawn(sim, region, "mossling", x, y, hunger=0.6)
    p.x, p.y = c.x + 1, c.y
    p.inventory["seeds"] = 5
    for _ in range(4):
        act.feed(sim, c)
    trust = c.relations[PLAYER_ID].trust
    assert trust > 0.4
    savegame.save(sim, cfg.save_path)
    sim2, _ = savegame.load(cfg.save_path, cfg, offline=False)
    c2 = sim2.world.find_creature_record(c.id)
    c2 = c2 if hasattr(c2, "relations") else None
    assert c2 is not None
    assert abs(c2.relations[PLAYER_ID].trust - trust) < 1e-3
    assert c2.memory.recall("fed_by_player", subject=PLAYER_ID)
