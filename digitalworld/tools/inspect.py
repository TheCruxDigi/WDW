"""Diagnostics CLI. Read-only on saves.

    python -m digitalworld.tools.inspect summary
    python -m digitalworld.tools.inspect region "Lantern Thicket"
    python -m digitalworld.tools.inspect creature Velk
    python -m digitalworld.tools.inspect why Velk          # utility scores, migration & food analysis
    python -m digitalworld.tools.inspect history --region 7 --min 2
    python -m digitalworld.tools.inspect causes 123        # cause chain of a history entry
    python -m digitalworld.tools.inspect validate
    python -m digitalworld.tools.inspect profile --ticks 2000 --seed 3

Use --save PATH (or --data-dir / --save-name) to choose the save file.
"""

from __future__ import annotations

import argparse
import math
import sys
import time

from ..config import Config
from ..core.clock import TICKS_PER_DAY, format_ticks
from ..entities.creature import Creature
from ..entities.memory import PLAYER_ID
from ..persistence import savegame
from ..sim.ecology import suitability, dominant_pressure, food_totals


def _load(args):
    cfg = Config.load(None, **({"data_dir": args.data_dir} if args.data_dir else {}),
                      **({"save_name": args.save_name} if args.save_name else {}))
    path = args.save or cfg.save_path
    sim, report = savegame.load(path, cfg, offline=False)
    return sim


def _find_region(world, key):
    if key is None:
        return world.regions[world.player.region_id] if world.player else next(iter(world.regions.values()))
    if str(key).isdigit() and int(key) in world.regions:
        return world.regions[int(key)]
    for r in world.regions.values():
        if r.name.lower() == str(key).lower():
            return r
    raise SystemExit(f"no region {key!r}")


def _find_creature(world, key):
    if str(key).isdigit():
        rec = world.find_creature_record(int(key))
        if rec is not None:
            return rec
    for cid in world.all_creature_ids():
        rec = world.find_creature_record(cid)
        name = rec.name if hasattr(rec, "name") else rec["name"]
        if name.lower() == str(key).lower():
            return rec
    raise SystemExit(f"no creature {key!r}")


def _as_creature(rec) -> Creature:
    return rec if isinstance(rec, Creature) else Creature.from_dict(rec)


def cmd_summary(sim, args):
    w = sim.world
    print(f"{w.name}  seed {w.seed}  {w.clock.label()}  tick {w.clock.tick}")
    print(f"regions {len(w.regions)}  creatures {w.total_population()} (detailed {len(w.creatures)})  "
          f"groups {len(w.groups)}  history entries {len(w.history.entries)}")
    print(f"\n{'id':>3} {'region':<22}{'biome':<17}{'sim':<9}{'pop':>5}{'forage':>8}{'corr':>7}  band")
    totals = {}
    for r in w.regions.values():
        pop = w.population(r.id)
        for k, v in pop.items():
            totals[k] = totals.get(k, 0) + v
        st = r.stats
        print(f"{r.id:>3} {r.name:<22}{w.biome(r).name:<17}{'detailed' if r.loaded else 'abstract':<9}"
              f"{sum(pop.values()):>5}{st.get('plants', 0):>8.0%}{r.corruption_level:>7.0%}  {r.corruption_band()}")
    print("\nspecies totals:")
    for sid, n in sorted(totals.items(), key=lambda kv: -kv[1]):
        print(f"  {w.species(sid).name:<14}{n:>5}")


def cmd_region(sim, args):
    w = sim.world
    r = _find_region(w, args.target)
    b = w.biome(r)
    print(f"[{r.id}] {r.name} — {b.name} ({r.style}); grid {r.gx},{r.gy}; neighbours: "
          + ", ".join(f"{w.regions[n].name} [{n}]" for n in sorted(r.gates)))
    print(f"corruption {r.corruption_level:.1%} ({r.corruption_band()}), stability {r.stability:.2f}, "
          f"noise {r.signal_noise:.2f}, resistance {r.resistance:.2f}, weather {r.weather}")
    print("population:", {w.species(k).name: v for k, v in w.population(r.id).items()})
    ft = food_totals(r)
    print("resources:", {k: round(v) for k, v in sorted(ft.items())})
    print(f"births {r.flags.get('births', {})}  deaths {r.flags.get('deaths', {})}")
    if r.territories:
        print("territories:")
        for t in r.territories.values():
            g = w.groups.get(t.group_id)
            print(f"  {g.name if g else t.group_id} ({t.species_id}) r={t.radius:.0f} strength {t.strength:.2f}")
    if r.structures:
        print("structures:", [(s.kind, s.x, s.y, f"{s.integrity:.0%}") for s in r.structures.values()])
    if r.anomalies:
        print("anomalies:", [(a.name, a.x, a.y) for a in r.anomalies.values()])
    samples = r.flags.get("samples", [])
    if samples:
        print("\nchange over time (tick, population, forage, corruption):")
        step = max(1, len(samples) // 12)
        for s in samples[::step]:
            print(f"  day {s[0] // TICKS_PER_DAY + 1:>3} {s[0] % TICKS_PER_DAY // 60:02d}h  pop {s[1]:>4}  forage {s[2]:.0%}  corr {s[3]:.0%}")
    since = r.last_player_tick
    entries = [e for e in w.history.entries if e.region_id == r.id]
    if since is not None:
        absent = [e for e in entries if e.tick > since]
        print(f"\nwhile the Wayfinder was away ({format_ticks(w.clock.tick - since)}): {len(absent)} events")
        for e in absent[-15:]:
            print(f"  day {e.tick // TICKS_PER_DAY + 1}: {e.text}")
    else:
        print("\nhistory:")
        for e in entries[-15:]:
            print(f"  day {e.tick // TICKS_PER_DAY + 1}: {e.text}")


def _creature_text(w, c: Creature) -> None:
    sp = w.species(c.species_id)
    now = w.clock.tick
    r = w.regions[c.region_id]
    g = w.groups.get(c.group_id) if c.group_id else None
    print(f"#{c.id} {c.name}, {sp.name} ({c.sex}), age {c.age_days(now):.1f}d, gen {c.generation}, in {r.name}")
    if g:
        print(f"  group: {g.name} ({'leader' if g.leader_id == c.id else 'member'}, {len(g.members)} members)")
    print(f"  health {c.health:.0%}  taint {c.taint:.0%}  position ({c.x:.1f},{c.y:.1f})  last action: {c.action_name}")
    print("  needs: " + "  ".join(f"{k} {v:.2f}" for k, v in c.needs.items()))
    print("  traits: " + "  ".join(f"{k} {v:.2f}" for k, v in c.traits.items()))
    print("  counters:", c.counters)
    print("  memories (most salient first):")
    for m in sorted(c.memory.records, key=lambda m: -m.salience(now))[:12]:
        who = "Wayfinder" if m.subject == PLAYER_ID else (f"#{m.subject}" if m.subject is not None else "")
        where = w.regions[m.region].name if m.region in w.regions else ""
        print(f"    {format_ticks(now - m.tick):>8} ago  {m.kind:<20} {who:<8} {where:<20} {m.detail}  (x{m.count}, salience {m.salience(now):.2f})")
    print("  relationships:")
    for oid, rel in sorted(c.relations.items(), key=lambda kv: -kv[1].weight)[:10]:
        name = "Wayfinder" if oid == PLAYER_ID else f"#{oid}"
        o = w.find_creature_record(oid) if oid != PLAYER_ID else None
        if o is not None:
            name += " " + (o.name if hasattr(o, "name") else o["name"])
        print(f"    {name:<22} {rel.label():<8} trust {rel.trust:.2f} affinity {rel.affinity:.2f} fear {rel.fear:.2f} "
              f"hostility {rel.hostility:.2f} rivalry {rel.rivalry:.2f} familiarity {rel.familiarity:.2f}")


def cmd_creature(sim, args):
    _creature_text(sim.world, _as_creature(_find_creature(sim.world, args.target)))


def cmd_why(sim, args):
    w = sim.world
    rec = _find_creature(w, args.target)
    cid = rec.id if hasattr(rec, "id") else rec["id"]
    rid = rec.region_id if hasattr(rec, "region_id") else rec["region_id"]
    if not w.regions[rid].loaded:
        sim.focus_region = rid
        if w.player is not None:
            w.player.region_id = rid
        sim.update_lod()
    c = w.creatures[cid]
    sp = w.species(c.species_id)
    region = w.regions[rid]
    _creature_text(w, c)
    print("\nWHY — utility evaluation right now:")
    for o in sim.brain.evaluate(c, sp, region)[:10]:
        print(f"  {o.score:6.3f}  {o.action.name:<18} {o.reason}")
    print("\nWHY — migration analysis (suitability; higher is better):")
    here, f = suitability(w, region, sp.id, explain=True)
    print(f"  here  {region.name:<22} {here:6.3f}  {f}  -> main pressure: {dominant_pressure(f)}")
    for nb in sorted(region.gates):
        r2 = w.regions[nb]
        s, f2 = suitability(w, r2, sp.id, extra=1, explain=True)
        print(f"  gate  {r2.name:<22} {s:6.3f}  {f2}")
    sim.migration.evaluate(c, sp, region, w.clock.tick)
    tgt = w.regions[c.migrate_target].name if c.migrate_target else "none"
    print(f"  decision: target {tgt}, urgency {sim.migration.urgency(c):.2f}, reason '{c.migrate_reason}'")
    past = c.memory.recall("left_region")
    if past:
        print("  past migrations:")
        for m in past:
            print(f"    left {w.regions[m.region].name if m.region in w.regions else m.region} "
                  f"{format_ticks(w.clock.tick - m.tick)} ago because: {m.detail}")
    print("\nWHY — food situation:")
    for kind, mult in sp.diet.items():
        nodes = [n for n in region.resources.values() if n.kind == kind]
        if not nodes:
            print(f"  {kind:<10} none in this region")
            continue
        best = sorted(nodes, key=lambda n: math.hypot(n.x - c.x, n.y - c.y))[:3]
        desc = ", ".join(f"#{n.id} {n.amount:.0f}/{n.max_amount:.0f} at {math.hypot(n.x - c.x, n.y - c.y):.0f} tiles"
                         f"{' (crowded)' if len(n.users) >= w.content.resources[kind].feeders else ''}" for n in best)
        print(f"  {kind:<10} {len(nodes)} patches, total {sum(n.amount for n in nodes):.0f}; nearest: {desc}")
    known = [v for v in c.known_nodes.values() if v[3] in sp.diet]
    print(f"  remembers {len(known)} food places; perception radius "
          f"{c.percept_cache.radius if c.percept_cache else sp.perception:.1f} (signal noise {region.signal_noise:.2f})")
    if sp.is_predator:
        prey = {p: w.population(rid).get(p, 0) for p in sp.prey}
        print(f"  prey present: {prey}")


def cmd_history(sim, args):
    w = sim.world
    rid = _find_region(w, args.region).id if args.region else None
    es = [e for e in w.history.entries if e.importance >= args.min and (rid is None or e.region_id == rid)
          and (args.kind is None or e.kind == args.kind)]
    for e in es[-args.limit:]:
        cause = f"  <- {e.causes}" if e.causes else ""
        print(f"[{e.id:>5}] day {e.tick // TICKS_PER_DAY + 1:>3} {e.tick % TICKS_PER_DAY // 60:02d}h  {e.kind:<20} {e.text}{cause}")


def cmd_causes(sim, args):
    w = sim.world
    chain = w.history.cause_chain(int(args.target), depth=10)
    if not chain:
        raise SystemExit("no such entry")
    for i, e in enumerate(chain):
        print(("   " * i) + ("<- " if i else "") + f"[{e.id}] day {e.tick // TICKS_PER_DAY + 1}: {e.text}")


def cmd_validate(args):
    cfg = Config.load(None, **({"data_dir": args.data_dir} if args.data_dir else {}))
    path = args.save or cfg.save_path
    problem = savegame.verify(path)
    if problem:
        print(f"INVALID: {problem}")
        return 1
    sim, rep = savegame.load(path, cfg, offline=False)
    ids = sim.world.all_creature_ids()
    print(f"OK: {path}  tick {sim.world.clock.tick}  creatures {len(ids)} (unique {len(set(ids))})  "
          f"regions {len(sim.world.regions)}  history {len(sim.world.history.entries)}")
    return 0 if len(ids) == len(set(ids)) else 1


def cmd_profile(args):
    from ..sim.simulation import Simulation
    cfg = Config.load(None, seed=args.seed, audio=False, log_to_file=False)
    if args.big:
        cfg.apply({"world_width": 8, "world_height": 6})
    sim = Simulation.new(cfg)
    w = sim.world
    print(f"world {cfg.world_width}x{cfg.world_height} regions, {w.total_population()} creatures, "
          f"{len(w.creatures)} detailed in {sum(1 for r in w.regions.values() if r.loaded)} active regions")
    t0 = time.perf_counter()
    sim.run(args.ticks)
    dt = time.perf_counter() - t0
    print(f"{args.ticks} ticks in {dt:.2f}s -> {dt / args.ticks * 1000:.2f} ms/tick "
          f"(max sustainable speed at 10 ticks/s: {1000 / max(1e-6, dt / args.ticks * 1000) / 10:.1f}x)")
    print(w.profiler.format())
    print(f"\npaths requested {sim.path_budget.total_requests}, failed {sim.path_budget.total_failed}, "
          f"deferred {sim.path_budget.total_deferred}; decisions {sim.brain.decisions}; abstract steps {sim.abstract.steps_run}")
    acts = sorted(set(sim.brain.action_success) | set(sim.brain.action_failures) | set(sim.brain.action_preempted))
    print(f"\n{'action':<18}{'success':>9}{'failed':>8}{'preempted':>11}")
    for a in acts:
        print(f"{a:<18}{sim.brain.action_success.get(a, 0):>9}{sim.brain.action_failures.get(a, 0):>8}"
              f"{sim.brain.action_preempted.get(a, 0):>11}")
    t1 = time.perf_counter()
    sim.fast_forward(TICKS_PER_DAY)
    print(f"\nabstract fast-forward of 1 day for the whole world: {(time.perf_counter() - t1) * 1000:.0f} ms")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="digitalworld.tools.inspect", description="Digital World diagnostics")
    ap.add_argument("command", choices=["summary", "region", "creature", "why", "history", "causes", "validate", "profile"])
    ap.add_argument("target", nargs="?")
    ap.add_argument("--save")
    ap.add_argument("--data-dir")
    ap.add_argument("--save-name")
    ap.add_argument("--region")
    ap.add_argument("--kind")
    ap.add_argument("--min", type=int, default=1)
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--ticks", type=int, default=1440)
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--big", action="store_true", help="profile: 8x6 regions world")
    args = ap.parse_args(argv)
    if args.command == "validate":
        return cmd_validate(args)
    if args.command == "profile":
        return cmd_profile(args)
    sim = _load(args)
    {"summary": cmd_summary, "region": cmd_region, "creature": cmd_creature, "why": cmd_why,
     "history": cmd_history, "causes": cmd_causes}[args.command](sim, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
