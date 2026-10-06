"""Digital corruption, weather and anomalies.

Corruption is a coarse per-region field (one cell = 4x4 tiles). It diffuses,
relaxes toward the biome baseline according to the region's resistance, is
fed by anomalies, Glitchwyrms and data storms, is pushed back by beacons and
healthy vegetation, and leaks across gates into neighbouring regions.

Systemic effects (handled elsewhere, reading this field): plant regrowth,
bytebloom growth, creature taint and health, path costs, perception range
(signal noise), migration pressure and structure decay.
"""

from __future__ import annotations

import numpy as np

from ..core.clock import TICKS_PER_HOUR
from ..world.region import Region, Anomaly, CORR_CELL
from .ecology import plant_fullness

WEATHER_TABLE = {
    # kind: (weight in normal regions, weight scaling with corruption)
    "clear": (5.0, 0.0),
    "rain": (2.5, 0.0),
    "drought": (1.0, 0.0),
    "datastorm": (0.25, 4.0),
}


def update_weather(sim, region: Region, now: int | None = None) -> None:
    world = sim.world
    now = world.clock.tick if now is None else now
    if now < region.weather_until:
        return
    biome = world.biome(region)
    rng = sim.rng_for(region.id, "weather", now // 60)
    weights = {}
    for kind, (w, wc) in WEATHER_TABLE.items():
        weights[kind] = w + wc * region.corruption_level
    weights["rain"] *= 0.4 + biome.rain * 1.4
    weights["drought"] *= 1.6 - biome.rain
    kinds = sorted(weights)
    pick = rng.choices(kinds, [weights[k] for k in kinds])[0]
    prev = region.weather
    region.weather = pick
    region.weather_until = now + rng.randint(3, 12) * TICKS_PER_HOUR
    region.rain_now = {"rain": min(1.0, biome.rain + 0.4), "drought": biome.rain * 0.2,
                       "datastorm": biome.rain, "clear": biome.rain}[pick]
    if pick == "datastorm" and prev != "datastorm":
        sim.world.bus.publish("weather", now, region=region.id, weather=pick)
        if region.corruption_level > 0.3 or region.loaded:
            world.history.record(now, "data_storm", f"A data storm broke over {region.name}.",
                                 region.id, importance=1)


def _diffuse(f: np.ndarray, d: float) -> np.ndarray:
    p = np.pad(f, 1, mode="edge")
    nb = (p[:-2, 1:-1] + p[2:, 1:-1] + p[1:-1, :-2] + p[1:-1, 2:]) * 0.25
    return f * (1 - d) + nb * d


def _add_disc(f: np.ndarray, cx: int, cy: int, r: int, amount: float) -> None:
    h, w = f.shape
    for y in range(max(0, cy - r), min(h, cy + r + 1)):
        for x in range(max(0, cx - r), min(w, cx + r + 1)):
            d2 = (x - cx) ** 2 + (y - cy) ** 2
            if d2 <= r * r:
                f[y, x] += amount * (1.0 - d2 / (r * r + 1))


def update_region(sim, region: Region, dt: int, wyrm_positions: list | None = None,
                  now: int | None = None) -> None:
    world = sim.world
    now = world.clock.tick if now is None else now
    biome = world.biome(region)
    hours = dt / TICKS_PER_HOUR
    f = region.corruption.astype(np.float64)
    beacons = [s for s in region.structures.values() if s.kind == "beacon" and s.complete]
    veg = plant_fullness(world, region)
    region.resistance = min(1.0, biome.resistance * 0.6 + veg * 0.3 + min(0.3, len(beacons) * 0.08))
    # relaxation toward the biome's natural level
    k = min(0.5, 0.012 * hours * (0.5 + region.resistance))
    f += (biome.base_corruption - f) * k
    f = _diffuse(f, min(0.5, 0.08 * hours))
    # sources
    for a in region.anomalies.values():
        cx, cy = a.x // CORR_CELL, a.y // CORR_CELL
        if a.kind == "glitch_bloom":
            _add_disc(f, cx, cy, 2, 0.02 * a.intensity * hours)
        elif a.kind == "static_vortex":
            _add_disc(f, cx, cy, 1, 0.012 * a.intensity * hours)
    if region.weather == "datastorm":
        f += 0.004 * hours * (1.0 - region.resistance)
    for (x, y) in wyrm_positions or []:
        _add_disc(f, int(x) // CORR_CELL, int(y) // CORR_CELL, 1, 0.012 * hours)
    # sinks
    for b in beacons:
        _add_disc(f, b.x // CORR_CELL, b.y // CORR_CELL, 2, -0.035 * b.integrity * hours)
    np.clip(f, 0.0, 1.0, out=f)
    region.corruption = f.astype(np.float32)
    region.flags["_corr_version"] = region.flags.get("_corr_version", 0) + 1
    level = float(f.mean())
    storm = 0.45 if region.weather == "datastorm" else 0.0
    target_noise = min(1.0, level * 0.6 + storm + 0.04 * len(region.anomalies))
    region.signal_noise += (target_noise - region.signal_noise) * min(1.0, 0.25 * hours)
    region.anomaly_intensity = min(1.0, level * 0.7 + region.signal_noise * 0.3)
    region.spread = 0.25 + level * 0.6
    _structures(sim, region, f, hours)
    _anomalies(sim, region, hours, now)


def _structures(sim, region: Region, f: np.ndarray, hours: float) -> None:
    world = sim.world
    for s in list(region.structures.values()):
        c = float(f[min(s.y // CORR_CELL, f.shape[0] - 1), min(s.x // CORR_CELL, f.shape[1] - 1)])
        decay = (max(0.0, c - 0.35) * 0.03 + 0.001) * hours
        s.integrity -= decay
        if s.integrity <= 0:
            sim.destroy_structure(region, s, cause="eroded by corruption" if c > 0.35 else "weathered away")


def _anomalies(sim, region: Region, hours: float, now: int) -> None:
    world = sim.world
    for a in list(region.anomalies.values()):
        if a.kind == "glitch_bloom":
            _seed_bytebloom(sim, region, a, hours, now)
        if now - a.born_tick > a.lifetime:
            del region.anomalies[a.id]
            world.bus.publish("anomaly_faded", now, region=region.id, anomaly=a.id)
            if a.discovered or a.kind == "glitch_bloom":
                world.history.record(now, "anomaly_faded", f"The {a.name} in {region.name} dissolved.",
                                     region.id, importance=1)
    if len(region.anomalies) >= 4:
        return
    rng = sim.rng_for(region.id, "anomaly", now // 60)
    p = region.anomaly_intensity * 0.02 * hours
    if rng.random() >= p:
        return
    level = region.corruption_level
    kinds = ["echo_pillar", "glitch_bloom", "static_vortex"]
    weights = [max(0.1, 1.0 - level * 1.5), 0.2 + level, 0.1 + level * 0.8]
    kind = rng.choices(kinds, weights)[0]
    ch, cw = region.corruption.shape
    # emerge where corruption is highest-ish
    cells = [(float(region.corruption[y, x]) + rng.random() * 0.2, x, y) for y in range(ch) for x in range(cw)]
    cells.sort(reverse=True)
    _, cx, cy = cells[rng.randrange(min(6, len(cells)))]
    x = min(region.width - 2, max(1, cx * CORR_CELL + rng.randint(0, CORR_CELL - 1)))
    y = min(region.height - 2, max(1, cy * CORR_CELL + rng.randint(0, CORR_CELL - 1)))
    if region.terrain is not None:
        x, y = region.nearest_walkable(x, y)
    a = Anomaly(world.new_id(), kind, x, y, min(1.0, 0.3 + level), now, rng.randint(2, 7) * 1440)
    region.anomalies[a.id] = a
    world.bus.publish("anomaly_emerged", now, region=region.id, anomaly=a.id, kind=kind)
    cause = world.history.latest("corruption_rise", region.id, within=3 * 1440, now=now)
    world.history.record(now, "anomaly_emerged", f"A {a.name} flickered into being in {region.name}.",
                         region.id, importance=1 if kind == "echo_pillar" else 2,
                         causes=[cause.id] if cause else None, anomaly=a.id, kind=kind)


def _seed_bytebloom(sim, region: Region, a: Anomaly, hours: float, now: int) -> None:
    """Glitch blooms sprout bytebloom — corruption literally grows food for digital life."""
    near = [n for n in region.resources.values()
            if n.kind == "bytebloom" and abs(n.x - a.x) + abs(n.y - a.y) <= 6]
    if len(near) >= 3 or sum(1 for n in region.resources.values() if n.kind == "bytebloom") >= 16:
        return
    rng = sim.rng_for(region.id, "bloomseed", a.id, now // 60)
    if rng.random() > 0.05 * hours * a.intensity:
        return
    x = min(region.width - 2, max(1, a.x + rng.randint(-4, 4)))
    y = min(region.height - 2, max(1, a.y + rng.randint(-4, 4)))
    if region.terrain is not None and not region.is_walkable(x, y):
        return
    from ..world.region import ResourceNode
    n = ResourceNode(sim.world.new_id(), "bytebloom", x, y, 4.0, 24.0)
    region.resources[n.id] = n


def spread_between_regions(sim, dt: int) -> None:
    """Leak corruption across gates, proportional to the gradient."""
    world = sim.world
    hours = dt / TICKS_PER_HOUR
    done = set()
    for a in world.regions.values():
        for bid, (gx, gy) in a.gates.items():
            key = (min(a.id, bid), max(a.id, bid))
            if key in done:
                continue
            done.add(key)
            b = world.regions[bid]
            bx, by = b.gates[a.id]
            ca = a.corruption_at(gx, gy)
            cb = b.corruption_at(bx, by)
            if ca == cb:
                continue
            src, dst, sx, sy, dx, dy, diff = (a, b, gx, gy, bx, by, ca - cb) if ca > cb else (b, a, bx, by, gx, gy, cb - ca)
            flow = 0.03 * diff * src.spread * (1.0 - dst.resistance * 0.8) * hours
            if flow <= 0:
                continue
            fd = dst.corruption
            _add_disc_f32(fd, dx // CORR_CELL, dy // CORR_CELL, 2, flow)
            fs = src.corruption
            _add_disc_f32(fs, sx // CORR_CELL, sy // CORR_CELL, 1, -flow * 0.3)
            dst.flags["_corr_version"] = dst.flags.get("_corr_version", 0) + 1
            dst.flags["_last_inflow_from"] = src.id


def _add_disc_f32(f: np.ndarray, cx: int, cy: int, r: int, amount: float) -> None:
    g = f.astype(np.float64)
    _add_disc(g, min(cx, f.shape[1] - 1), min(cy, f.shape[0] - 1), r, amount)
    np.clip(g, 0, 1, out=g)
    f[:] = g.astype(np.float32)


def stabilize_area(region: Region, x: float, y: float, strength: float, radius_cells: int = 2) -> float:
    """Player/beacon pulse. Returns how much corruption was removed."""
    f = region.corruption.astype(np.float64)
    before = float(f.sum())
    _add_disc(f, int(x) // CORR_CELL, int(y) // CORR_CELL, radius_cells, -strength)
    np.clip(f, 0, 1, out=f)
    region.corruption = f.astype(np.float32)
    region.flags["_corr_version"] = region.flags.get("_corr_version", 0) + 1
    return before - float(f.sum())
