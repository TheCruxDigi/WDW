"""Path following and steering. Logical positions are floats in tile units."""

from __future__ import annotations

import math

from ..nav.pathfinding import find_path, line_clear

ARRIVED, MOVING, FAILED = "arrived", "moving", "failed"


def creature_speed(sim, c, sp, region, mult: float = 1.0) -> float:
    cost = (region.swim_cost if sp.has("swim") else region.walk_cost)[int(c.y), int(c.x)]
    if cost >= 1e8:
        cost = 1.0
    s = sp.speed * mult / max(1.0, float(cost) ** 0.7)
    if c.health < 0.3:
        s *= 0.6
    if c.needs["fatigue"] > 0.85:
        s *= 0.7
    if c.age_days(sim.world.clock.tick) < sp.maturity_days * 0.5:
        s *= 0.8
    return s


def _advance(c, tx: float, ty: float, step: float) -> bool:
    dx, dy = tx - c.x, ty - c.y
    d = math.hypot(dx, dy)
    if d <= step or d < 1e-6:
        c.x, c.y = tx, ty
        return True
    c.x += dx / d * step
    c.y += dy / d * step
    return False


def move_to(sim, c, sp, region, gx: float, gy: float, mult: float = 1.0, arrive: float = 0.6) -> str:
    dist = math.hypot(gx - c.x, gy - c.y)
    if dist <= arrive:
        c.path = []
        return ARRIVED
    swim = sp.has("swim")
    cost = region.swim_cost if swim else region.walk_cost
    speed = creature_speed(sim, c, sp, region, mult)
    goal_tile = (int(gx), int(gy))
    # short, unobstructed hops need no path search
    if dist < 6 and line_clear(cost, c.x, c.y, gx, gy):
        c.path = []
        c.path_goal = None
        _advance(c, gx, gy, speed)
        return MOVING
    if c.path_goal != goal_tile or not c.path:
        if not sim.path_budget.take():
            return MOVING  # deferred to a later tick; keep current position
        walk_l, swim_l = sim.world.cost_lists(region)
        corr = None
        tol = sp.corruption_tolerance
        if tol < 0.9:
            corr = sim.world.corruption_tiles(region)
        path = find_path(swim_l if swim else walk_l, c.tile, goal_tile, corr, tol)
        if path is None:
            sim.path_budget.total_failed += 1
            c.path = []
            c.path_goal = None
            return FAILED
        c.path = path
        c.path_goal = goal_tile
    if not c.path:
        _advance(c, gx, gy, speed)
        return MOVING
    nx, ny = c.path[0]
    if cost[ny, nx] >= 1e8:  # world changed under us: repath next tick
        c.path = []
        c.path_goal = None
        return MOVING
    remaining = speed
    while remaining > 0 and c.path:
        nx, ny = c.path[0]
        tx, ty = nx + 0.5, ny + 0.5
        d = math.hypot(tx - c.x, ty - c.y)
        if d <= remaining:
            c.x, c.y = tx, ty
            remaining -= d
            c.path.pop(0)
        else:
            _advance(c, tx, ty, remaining)
            remaining = 0
    return MOVING


def pick_walkable_near(region, x: float, y: float, rng, radius: float, swim: bool = False,
                       tries: int = 12):
    for _ in range(tries):
        a = rng.random() * math.tau
        d = rng.uniform(radius * 0.4, radius)
        tx, ty = int(x + math.cos(a) * d), int(y + math.sin(a) * d)
        if region.is_walkable(tx, ty, swim):
            return tx + 0.5, ty + 0.5
    return None
