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
    """Return list of tiles from (excluding) start to goal, or None.

    Flat-array A* (index = y * w + x): roughly 3x faster than dict bookkeeping.
    """
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
    n = w * h
    INFV = 1e18
    g_best = [INFV] * n
    came = [-1] * n
    s_idx = sy * w + sx
    g_idx = gy * w + gx
    g_best[s_idx] = 0.0
    open_heap = [(0.0, 0.0, s_idx)]
    push, pop = heapq.heappush, heapq.heappop
    expanded = 0
    diag_k = SQRT2 - 2
    while open_heap:
        f, g, idx = pop(open_heap)
        if idx == g_idx:
            path = []
            while idx != s_idx:
                path.append((idx % w, idx // w))
                idx = came[idx]
            path.reverse()
            return path
        if g > g_best[idx]:
            continue
        expanded += 1
        if expanded > max_expand:
            return None
        y, x = divmod(idx, w)
        row = costl[y]
        for dx, dy, step in NEIGHBOURS:
            nx, ny = x + dx, y + dy
            if nx < 0 or ny < 0 or nx >= w or ny >= h:
                continue
            c = costl[ny][nx]
            if c >= 1e8:
                continue
            if dx and dy and (row[nx] >= 1e8 or costl[ny][x] >= 1e8):
                continue  # no corner cutting
            if corr is not None:
                over = corr[ny][nx] - tolerance
                if over > 0:
                    c += over * 6.0
            ng = g + c * step
            nidx = ny * w + nx
            if ng < g_best[nidx]:
                g_best[nidx] = ng
                came[nidx] = idx
                hx = nx - gx if nx > gx else gx - nx
                hy = ny - gy if ny > gy else gy - ny
                push(open_heap, (ng + hx + hy + diag_k * (hx if hx < hy else hy), ng, nidx))
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
