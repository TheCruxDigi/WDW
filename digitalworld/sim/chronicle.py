"""Turns measured state changes into persistent history entries.

Runs for every region (loaded or dormant), so the chronicle keeps growing
while the player is elsewhere. Each entry links to earlier entries that
plausibly caused it (same region, recent, causally related kinds).
"""

from __future__ import annotations

from ..core.clock import TICKS_PER_DAY
from .ecology import plant_fullness

BANDS = ["stable", "flickering", "corrupted", "fractured"]
MAX_SAMPLES = 96


def _causes(world, region, kinds, now, within=3 * TICKS_PER_DAY, neighbours=False):
    out = []
    rids = [region.id] + (sorted(region.gates) if neighbours else [])
    for kind in kinds:
        for rid in rids:
            e = world.history.latest(kind, rid, within=within, now=now)
            if e is not None:
                out.append(e.id)
                break
    return out


def census(sim, region, now: int | None = None) -> None:
    world = sim.world
    now = world.clock.tick if now is None else now
    pop = world.population(region.id)
    # forage relative to the region's historical carrying capacity, so lost
    # patches count as loss (not just how full the surviving patches are)
    amount, cap = 0.0, 0.0
    for n in region.resources.values():
        rk = world.content.resources[n.kind]
        if rk.food and n.kind != "meat":
            amount += n.amount
            cap += n.capacity
    base = max(region.flags.get("plant_capacity", 0.0), cap)
    region.flags["plant_capacity"] = base * 0.999 + cap * 0.001 if base > cap else cap
    plants = amount / base if base else 0.0
    corr = region.corruption_level
    total = sum(pop.values())
    region.stats = {"tick": now, "pop": pop, "total": total, "plants": round(plants, 3),
                    "corruption": round(corr, 3), "noise": round(region.signal_noise, 3),
                    "weather": region.weather, "structures": len(region.structures),
                    "anomalies": len(region.anomalies), "territories": len(region.territories)}
    samples = region.flags.setdefault("samples", [])
    if not samples or now - samples[-1][0] >= 120:
        samples.append([now, total, round(plants, 3), round(corr, 3), dict(pop)])
        if len(samples) > MAX_SAMPLES:
            del samples[: len(samples) - MAX_SAMPLES]
    f = region.flags
    # corruption bands
    band = region.corruption_band()
    prev = f.get("band")
    if prev is None:
        f["band"] = band
    elif band != prev:
        worse = BANDS.index(band) > BANDS.index(prev)
        f["band"] = band
        if worse:
            causes = _causes(world, region, ["data_storm", "anomaly_emerged", "species_arrived",
                                             "structure_destroyed"], now)
            src = f.get("_last_inflow_from")
            if src is not None:
                causes += _causes(world, world.regions[src], ["corruption_rise"], now, within=6 * TICKS_PER_DAY)
            world.history.record(now, "corruption_rise",
                                 f"Corruption in {region.name} rose to '{band}' ({corr:.0%}).",
                                 region.id, importance=2, causes=causes, band=band, level=round(corr, 3))
        else:
            causes = _causes(world, region, ["structure_built", "player_stabilize"], now, within=5 * TICKS_PER_DAY)
            world.history.record(now, "corruption_recede",
                                 f"Corruption in {region.name} receded to '{band}' ({corr:.0%}).",
                                 region.id, importance=2, causes=causes, band=band, level=round(corr, 3))
    # food base
    if plants < 0.22 and not f.get("collapse"):
        f["collapse"] = True
        consumers = sorted(pop.items(), key=lambda kv: -kv[1])[:2]
        who = ", ".join(f"{n} {world.species(s).name}s" for s, n in consumers) or "no one"
        causes = _causes(world, region, ["corruption_rise", "migration_wave", "data_storm"], now)
        world.history.record(now, "resource_collapse",
                             f"Forage in {region.name} collapsed to {plants:.0%} of capacity (grazers present: {who}).",
                             region.id, importance=2, causes=causes, plants=round(plants, 3))
    elif plants > 0.5 and f.get("collapse"):
        f["collapse"] = False
        causes = _causes(world, region, ["resource_collapse", "migration_wave"], now, within=10 * TICKS_PER_DAY)
        world.history.record(now, "resource_recovered", f"Forage in {region.name} recovered to {plants:.0%}.",
                             region.id, importance=1, causes=causes)
    # species presence
    present = f.setdefault("present", {})
    for sid, n in pop.items():
        if n > 0 and not present.get(sid):
            first = f"seen_{sid}" not in f
            f[f"seen_{sid}"] = True
            present[sid] = now
            if not first:
                causes = _causes(world, region, ["migration_wave", "immigration"], now, neighbours=True)
                world.history.record(now, "species_arrived",
                                     f"{world.species(sid).name}s appeared in {region.name}.",
                                     region.id, importance=2, causes=causes, species=sid)
    for sid in list(present):
        if pop.get(sid, 0) == 0 and present.get(sid):
            present[sid] = 0
            causes = _causes(world, region, ["migration_wave", "resource_collapse", "corruption_rise",
                                             "territory_captured", "notable_death"], now)
            world.history.record(now, "species_vanished",
                                 f"No {world.species(sid).name}s remain in {region.name}.",
                                 region.id, importance=2, causes=causes, species=sid)


def world_census(sim, now: int | None = None) -> dict:
    world = sim.world
    total: dict[str, int] = {}
    for r in world.regions.values():
        census(sim, r, now)
        for sid, n in r.stats["pop"].items():
            total[sid] = total.get(sid, 0) + n
    return total
