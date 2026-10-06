"""Grid A* with a node-expansion cap and a per-tick request budget.

Corrupted tiles cost extra for creatures whose tolerance is below the local
corruption, so routes bend around fractured ground instead of ignoring it.
"""

from __future__ import annotations

import heapq
import math

import numpy as np

SQRT2 = math.sqrt(2.0)
NEIGHBOURS = ((1, 0, 1.0), (-1, 0, 1.0), (0, 1, 1.0), (0, -1, 1.0),
              (1, 1, SQRT2), (1, -1, SQRT2), (-1, 1, SQRT2), (-1, -1, SQRT2))


class PathBudget:
    def __init__(self, per_tick: int):
        self.per_tick = per_tick
        self.used = 0
        self.total_requests = 0
        self.total_failed = 0
        self.total_deferred = 0

    def reset(self) -> None:
        self.used = 0

    def take(self) -> bool:
        if self.used >= self.per_tick:
            self.total_deferred += 1
            return False
        self.used += 1
        self.total_requests += 1
        return True


def find_path(cost, start: tuple[int, int], goal: tuple[int, int],
              corruption=None, tolerance: float = 1.0,
              max_expand: int = 2500) -> list[tuple[int, int]] | None:
    """Return list of tiles from (excluding) start to goal, or None."""
    costl = cost if isinstance(cost, list) else cost.tolist()
    h, w = len(costl), len(costl[0])
    sx, sy = start
    gx, gy = goal
    if not (0 <= gx < w and 0 <= gy < h) or costl[gy][gx] >= 1e8:
        return None
    if (sx, sy) == (gx, gy):
        return []
    corr = None
    if corruption is not None and tolerance < 1.0:
        corr = corruption if isinstance(corruption, list) else corruption.tolist()
    open_heap = [(0.0, 0.0, sx, sy)]
    came: dict[tuple[int, int], tuple[int, int]] = {}
    g_best = {(sx, sy): 0.0}
    expanded = 0
    while open_heap:
        f, g, x, y = heapq.heappop(open_heap)
        if (x, y) == (gx, gy):
            path = [(x, y)]
            while (x, y) in came:
                x, y = came[(x, y)]
                path.append((x, y))
            path.pop()  # drop start
            path.reverse()
            return path
        if g > g_best.get((x, y), 1e18):
            continue
        expanded += 1
        if expanded > max_expand:
            return None
        for dx, dy, step in NEIGHBOURS:
            nx, ny = x + dx, y + dy
            if not (0 <= nx < w and 0 <= ny < h):
                continue
            c = costl[ny][nx]
            if c >= 1e8:
                continue
            if dx and dy and (costl[y][nx] >= 1e8 or costl[ny][x] >= 1e8):
                continue  # no corner cutting
            if corr is not None:
                over = corr[ny][nx] - tolerance
                if over > 0:
                    c += over * 6.0
            ng = g + c * step
            if ng < g_best.get((nx, ny), 1e18):
                g_best[(nx, ny)] = ng
                came[(nx, ny)] = (x, y)
                hx, hy = abs(nx - gx), abs(ny - gy)
                hcost = (hx + hy) + (SQRT2 - 2) * min(hx, hy)
                heapq.heappush(open_heap, (ng + hcost, ng, nx, ny))
    return None


def line_clear(cost: np.ndarray, x0: float, y0: float, x1: float, y1: float) -> bool:
    """Cheap visibility check along a segment (sampled)."""
    steps = int(max(abs(x1 - x0), abs(y1 - y0)) * 2) + 1
    h, w = cost.shape
    for i in range(steps + 1):
        t = i / steps
        x = int(x0 + (x1 - x0) * t)
        y = int(y0 + (y1 - y0) * t)
        if not (0 <= x < w and 0 <= y < h) or cost[y, x] >= 1e8:
            return False
    return True
