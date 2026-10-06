"""Versioned SQLite persistence of the logical world state.

Guarantees:
  * Saves are written to ``<name>.db.tmp`` in one transaction, verified
    (integrity check + row counts) and only then atomically swapped in; the
    previous good save is kept as ``<name>.db.bak``. A failed or partial save
    never replaces a valid one.
  * Identity: every creature id appears exactly once; a save with duplicates
    is refused instead of written.
  * Loading tolerates missing/extra fields, migrates older format versions,
    skips unreadable rows (logged) and falls back to the backup file.
  * No rendering objects are ever serialized.
"""

from __future__ import annotations

import json
import logging
import os
import random
import sqlite3
import time
from pathlib import Path

from .. import __version__
from ..config import Config
from ..core.events import HistoryEntry
from ..entities.creature import Group
from ..world.region import Region
from ..world.world import World

log = logging.getLogger("digitalworld.save")

FORMAT_VERSION = 2

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE regions (id INTEGER PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE creatures (id INTEGER PRIMARY KEY, region_id INTEGER NOT NULL, species TEXT NOT NULL,
                        loaded INTEGER NOT NULL DEFAULT 0, data TEXT NOT NULL);
CREATE TABLE groups_ (id INTEGER PRIMARY KEY, data TEXT NOT NULL);
CREATE TABLE history (id INTEGER PRIMARY KEY, tick INTEGER NOT NULL, kind TEXT NOT NULL,
                      region_id INTEGER, importance INTEGER NOT NULL, data TEXT NOT NULL);
CREATE TABLE player (id INTEGER PRIMARY KEY CHECK (id = 0), data TEXT NOT NULL);
CREATE INDEX creatures_region ON creatures(region_id);
CREATE INDEX history_tick ON history(tick);
"""


class SaveError(RuntimeError):
    pass


class LoadError(RuntimeError):
    pass


def _rng_state(r: random.Random) -> list:
    v, state, gauss = r.getstate()
    return [v, list(state), gauss]


def _set_rng_state(r: random.Random, s) -> None:
    try:
        r.setstate((s[0], tuple(s[1]), s[2]))
    except (TypeError, ValueError, IndexError):
        log.warning("rng state unreadable; continuing with a fresh stream")


# ------------------------------------------------------------------ snapshot

def snapshot(sim) -> dict:
    """Collect the complete logical state as plain data."""
    world = sim.world
    creatures = []
    seen: set[int] = set()
    dupes = []
    for c in world.creatures.values():
        if not c.alive:
            continue
        if c.id in seen:
            dupes.append(c.id)
        seen.add(c.id)
        creatures.append((c.id, c.region_id, c.species_id, 1, c.to_dict()))
    for r in world.regions.values():
        for d in r.dormant_creatures:
            cid = int(d["id"])
            if cid in seen:
                dupes.append(cid)
            seen.add(cid)
            creatures.append((cid, int(d["region_id"]), d["species"], 0, d))
    if dupes:
        raise SaveError(f"refusing to save: duplicate creature ids {sorted(set(dupes))[:10]}")
    meta = {
        "format_version": FORMAT_VERSION,
        "game_version": __version__,
        "seed": world.seed,
        "tick": world.clock.tick,
        "next_id": world.next_id,
        "history_next_id": world.history.next_id,
        "saved_at": time.time(),
        "world_name": world.name,
        "dims": {"world_width": world.config.world_width, "world_height": world.config.world_height,
                 "region_width": world.config.region_width, "region_height": world.config.region_height},
        "rng": _rng_state(world.rng),
        "sim_rng": _rng_state(sim.rng),
        "flags": world.flags,
        "creature_count": len(creatures),
        "focus_region": sim.focus_region,
    }
    return {
        "meta": meta,
        "regions": [_region_record(r, world.clock.tick) for r in world.regions.values()],
        "creatures": creatures,
        "groups": [g.to_dict() for g in world.groups.values()],
        "history": [e.to_dict() for e in world.history.entries],
        "player": world.player.to_dict() if world.player is not None else None,
    }


def _region_record(r, now: int) -> dict:
    d = r.to_dict()
    if r.loaded:
        d["last_sim_tick"] = now  # loaded regions are simulated in detail up to now
    return d


def save(sim, path: str | os.PathLike) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    snap = snapshot(sim)
    tmp = path.with_suffix(path.suffix + ".tmp")
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(SCHEMA)
        with conn:
            conn.executemany("INSERT INTO meta(key, value) VALUES (?, ?)",
                             [(k, json.dumps(v)) for k, v in snap["meta"].items()])
            conn.executemany("INSERT INTO regions(id, data) VALUES (?, ?)",
                             [(r["id"], json.dumps(r, separators=(",", ":"))) for r in snap["regions"]])
            conn.executemany("INSERT INTO creatures(id, region_id, species, loaded, data) VALUES (?, ?, ?, ?, ?)",
                             [(cid, rid, sp, ld, json.dumps(d, separators=(",", ":")))
                              for cid, rid, sp, ld, d in snap["creatures"]])
            conn.executemany("INSERT INTO groups_(id, data) VALUES (?, ?)",
                             [(g["id"], json.dumps(g)) for g in snap["groups"]])
            conn.executemany("INSERT INTO history(id, tick, kind, region_id, importance, data) VALUES (?, ?, ?, ?, ?, ?)",
                             [(e["id"], e["tick"], e["kind"], e["region_id"], e["importance"], json.dumps(e))
                              for e in snap["history"]])
            if snap["player"] is not None:
                conn.execute("INSERT INTO player(id, data) VALUES (0, ?)", (json.dumps(snap["player"]),))
    except Exception as exc:
        conn.close()
        tmp.unlink(missing_ok=True)
        raise SaveError(f"writing save failed: {exc}") from exc
    conn.close()
    problem = verify(tmp, expected_creatures=len(snap["creatures"]))
    if problem:
        tmp.unlink(missing_ok=True)
        raise SaveError(f"verification of new save failed: {problem}")
    if path.exists():
        os.replace(path, path.with_suffix(path.suffix + ".bak"))
    os.replace(tmp, path)
    log.info("saved %s (%d creatures, %d history) in %.0f ms", path, len(snap["creatures"]),
             len(snap["history"]), (time.perf_counter() - t0) * 1000)
    return path


def verify(path: str | os.PathLike, expected_creatures: int | None = None) -> str | None:
    """Return None if the file is a structurally valid save, else a reason."""
    try:
        conn = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        return f"cannot open: {exc}"
    try:
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok":
            return f"integrity check: {ok}"
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"meta", "regions", "creatures"} <= tables:
            return "missing tables"
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        if "format_version" not in meta or "tick" not in meta:
            return "missing meta"
        nreg = conn.execute("SELECT COUNT(*) FROM regions").fetchone()[0]
        if nreg == 0:
            return "no regions"
        if expected_creatures is not None:
            n = conn.execute("SELECT COUNT(*) FROM creatures").fetchone()[0]
            if n != expected_creatures:
                return f"creature count {n} != {expected_creatures}"
    except sqlite3.Error as exc:
        return f"sqlite error: {exc}"
    finally:
        conn.close()
    return None


# ------------------------------------------------------------------ loading

def _read_raw(path: Path) -> dict:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        meta = {k: json.loads(v) for k, v in conn.execute("SELECT key, value FROM meta")}
        raw = {"meta": meta, "regions": [], "creatures": [], "groups": [], "history": [], "player": None}
        for (data,) in conn.execute("SELECT data FROM regions ORDER BY id"):
            raw["regions"].append(json.loads(data))
        cols = [r[1] for r in conn.execute("PRAGMA table_info(creatures)")]
        for row in conn.execute("SELECT * FROM creatures ORDER BY id"):
            rec = dict(zip(cols, row))
            raw["creatures"].append(rec)
        if "groups_" in tables:
            for (data,) in conn.execute("SELECT data FROM groups_ ORDER BY id"):
                raw["groups"].append(json.loads(data))
        if "history" in tables:
            for (data,) in conn.execute("SELECT data FROM history ORDER BY id"):
                raw["history"].append(json.loads(data))
        if "player" in tables:
            row = conn.execute("SELECT data FROM player WHERE id = 0").fetchone()
            if row:
                raw["player"] = json.loads(row[0])
        return raw
    finally:
        conn.close()


def _migrate_1_to_2(raw: dict) -> dict:
    """v1 kept the player inside meta and had no group territory/migration fields."""
    if raw.get("player") is None and "player" in raw["meta"]:
        raw["player"] = raw["meta"].pop("player")
    for g in raw["groups"]:
        g.setdefault("territory_id", None)
        g.setdefault("migrate_to", None)
    for r in raw["regions"]:
        r.setdefault("flags", {})
        r.setdefault("signal_noise", 0.0)
    raw["meta"]["format_version"] = 2
    return raw


MIGRATIONS = {1: _migrate_1_to_2}


def migrate(raw: dict) -> dict:
    v = int(raw["meta"].get("format_version", 1))
    if v > FORMAT_VERSION:
        raise LoadError(f"save format {v} is newer than this build ({FORMAT_VERSION})")
    while v < FORMAT_VERSION:
        fn = MIGRATIONS.get(v)
        if fn is None:
            raise LoadError(f"no migration from format {v}")
        log.info("migrating save format %d -> %d", v, v + 1)
        raw = fn(raw)
        v = int(raw["meta"]["format_version"])
    return raw


def build_simulation(raw: dict, config: Config | None = None):
    from ..sim.simulation import Simulation
    from ..player.player import Player
    meta = raw["meta"]
    cfg = config or Config()
    cfg.seed = int(meta.get("seed", cfg.seed))
    dims = meta.get("dims") or {}
    cfg.apply({k: dims[k] for k in ("world_width", "world_height", "region_width", "region_height") if k in dims})
    world = World(cfg)
    world.clock.tick = int(meta["tick"])
    world.name = meta.get("world_name", world.name)
    world.flags = dict(meta.get("flags") or {})
    if meta.get("rng"):
        _set_rng_state(world.rng, meta["rng"])
    for rd in raw["regions"]:
        try:
            r = Region.from_dict(rd)
        except (KeyError, TypeError, ValueError) as exc:
            raise LoadError(f"region record unreadable: {exc}") from exc
        r.loaded = False
        r.dormant_creatures = []
        world.regions[r.id] = r
    if not world.regions:
        raise LoadError("save has no regions")
    seen: set[int] = set()
    max_id = 0
    skipped = 0
    for rec in raw["creatures"]:
        try:
            d = rec["data"] if isinstance(rec.get("data"), dict) else json.loads(rec["data"])
            cid = int(d["id"])
            rid = int(d.get("region_id", rec.get("region_id")))
        except (KeyError, TypeError, ValueError):
            skipped += 1
            continue
        if cid in seen or rid not in world.regions or d.get("species") not in world.content.species:
            skipped += 1
            continue
        seen.add(cid)
        max_id = max(max_id, cid)
        world.regions[rid].dormant_creatures.append(d)
    if skipped:
        log.warning("skipped %d invalid/duplicate creature records", skipped)
    for gd in raw["groups"]:
        try:
            g = Group.from_dict(gd)
        except (KeyError, TypeError, ValueError):
            continue
        g.members = [m for m in g.members if m in seen]
        world.groups[g.id] = g
        max_id = max(max_id, g.id)
    for hd in raw["history"]:
        try:
            world.history.entries.append(HistoryEntry.from_dict(hd))
        except (KeyError, TypeError, ValueError):
            continue
    world.history.entries.sort(key=lambda e: e.id)
    world.history.next_id = max(int(meta.get("history_next_id", 1)),
                                (world.history.entries[-1].id + 1) if world.history.entries else 1)
    # ids must never be reused, even if the stored counter is damaged
    for r in world.regions.values():
        for coll in (r.resources, r.structures, r.anomalies, r.territories):
            if coll:
                max_id = max(max_id, max(coll))
    world.next_id = max(int(meta.get("next_id", 1000)), max_id + 1)
    sim = Simulation(world)
    if meta.get("sim_rng"):
        _set_rng_state(sim.rng, meta["sim_rng"])
    sim.focus_region = meta.get("focus_region")
    if raw.get("player"):
        try:
            world.player = Player.from_dict(raw["player"])
            if world.player.region_id not in world.regions:
                raise ValueError("player region missing")
        except (KeyError, TypeError, ValueError):
            log.warning("player record unreadable; placing a new Wayfinder")
            world.player = None
    if world.player is None and raw.get("player") is not None:
        rid = sim.focus_region or min(world.regions)
        world.player = Player(rid, world.config.region_width / 2, world.config.region_height / 2)
    sim.update_lod()
    if world.player is not None:
        region = world.regions[world.player.region_id]
        x, y = region.nearest_walkable(world.player.x, world.player.y)
        if (int(world.player.x), int(world.player.y)) != (x, y):
            world.player.x, world.player.y = x + 0.5, y + 0.5
    return sim


def load(path: str | os.PathLike, config: Config | None = None, offline: bool | None = None):
    """Load a save (falling back to the backup). Returns (sim, report dict)."""
    path = Path(path)
    errors = []
    for candidate in (path, path.with_suffix(path.suffix + ".bak")):
        if not candidate.exists():
            errors.append(f"{candidate.name}: missing")
            continue
        problem = verify(candidate)
        if problem:
            errors.append(f"{candidate.name}: {problem}")
            log.error("save %s invalid: %s", candidate, problem)
            continue
        try:
            raw = migrate(_read_raw(candidate))
            sim = build_simulation(raw, config)
        except (LoadError, sqlite3.Error, ValueError, KeyError) as exc:
            errors.append(f"{candidate.name}: {exc}")
            log.exception("loading %s failed", candidate)
            continue
        report = {"path": str(candidate), "used_backup": candidate != path, "errors": errors,
                  "offline_ticks": 0, "offline_events": []}
        cfg = sim.config
        do_offline = cfg.offline_progress if offline is None else offline
        saved_at = raw["meta"].get("saved_at")
        if do_offline and saved_at:
            elapsed = max(0.0, time.time() - float(saved_at))
            rate = cfg.offline_ticks_per_real_second or cfg.tick_rate
            ticks = int(min(cfg.offline_max_ticks, elapsed * rate))
            if ticks >= 10:
                report["offline_ticks"] = ticks
                report["offline_events"] = sim.fast_forward(ticks)
        return sim, report
    raise LoadError("no loadable save: " + "; ".join(errors))


def list_saves(saves_dir: str | os.PathLike) -> list[dict]:
    out = []
    d = Path(saves_dir)
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.db")):
        info = {"name": p.stem, "path": str(p), "valid": verify(p) is None}
        if info["valid"]:
            try:
                conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
                meta = {k: json.loads(v) for k, v in conn.execute("SELECT key, value FROM meta")}
                conn.close()
                info.update(tick=meta.get("tick"), saved_at=meta.get("saved_at"), seed=meta.get("seed"))
            except sqlite3.Error:
                info["valid"] = False
        out.append(info)
    return out
