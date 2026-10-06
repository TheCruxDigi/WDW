"""Tile terrain: types, passability and deterministic procedural generation.

Terrain is a pure function of (style, size, seed, gates) plus a sparse dict of
persistent modifications, so it never needs to be stored in the save.
"""

from __future__ import annotations

from collections import deque
from enum import IntEnum

import numpy as np

from .noise import fractal_noise


class Tile(IntEnum):
    GROUND = 0
    GRASS = 1
    DEEP = 2
    SHALLOW = 3
    TREE = 4
    ROCK = 5
    SAND = 6
    MUD = 7
    CAVE_FLOOR = 8
    CAVE_WALL = 9
    CRYSTAL = 10
    VOID = 11
    REED = 12
    GLITCH = 13


N_TILES = len(Tile)
INF = 1e9

# movement cost for walkers; INF = blocked
WALK_COST = np.full(N_TILES, INF)
for t, c in {Tile.GROUND: 1.0, Tile.GRASS: 1.0, Tile.SHALLOW: 2.2, Tile.SAND: 1.15,
             Tile.MUD: 1.8, Tile.CAVE_FLOOR: 1.0, Tile.REED: 1.6, Tile.GLITCH: 1.3}.items():
    WALK_COST[t] = c
SWIM_COST = WALK_COST.copy()
SWIM_COST[Tile.DEEP] = 1.6
SWIM_COST[Tile.SHALLOW] = 1.2

WATER_TILES = (Tile.DEEP, Tile.SHALLOW)


def passable_mask(terrain: np.ndarray, swim: bool = False) -> np.ndarray:
    table = SWIM_COST if swim else WALK_COST
    return table[terrain] < INF


def cost_grid(terrain: np.ndarray, swim: bool = False) -> np.ndarray:
    return (SWIM_COST if swim else WALK_COST)[terrain]


def _components(passable: np.ndarray) -> tuple[np.ndarray, int]:
    h, w = passable.shape
    labels = np.full((h, w), -1, dtype=np.int32)
    n = 0
    for sy in range(h):
        for sx in range(w):
            if not passable[sy, sx] or labels[sy, sx] >= 0:
                continue
            q = deque([(sx, sy)])
            labels[sy, sx] = n
            while q:
                x, y = q.popleft()
                for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                    if 0 <= nx < w and 0 <= ny < h and passable[ny, nx] and labels[ny, nx] < 0:
                        labels[ny, nx] = n
                        q.append((nx, ny))
            n += 1
    return labels, n


def _carve_line(t: np.ndarray, x0: int, y0: int, x1: int, y1: int, floor: int, width: int = 1) -> None:
    """Carve a 4-connected corridor (diagonal steps would not connect under
    4-neighbour flood fill and would later be filled back in)."""
    h, w = t.shape

    def open_at(x, y):
        for dy in range(-width + 1, width):
            for dx in range(-width + 1, width):
                xx, yy = x + dx, y + dy
                if 0 <= xx < w and 0 <= yy < h and WALK_COST[t[yy, xx]] >= INF:
                    t[yy, xx] = floor

    steps = max(abs(x1 - x0), abs(y1 - y0), 1)
    px, py = x0, y0
    open_at(px, py)
    for i in range(1, steps + 1):
        x = round(x0 + (x1 - x0) * i / steps)
        y = round(y0 + (y1 - y0) * i / steps)
        if x != px and y != py:
            open_at(x, py)
        open_at(x, y)
        px, py = x, y


def generate_terrain(style: str, w: int, h: int, seed: int, gates: list[tuple[int, int]],
                     coast_side: int = 0) -> np.ndarray:
    elev = fractal_noise(w, h, seed, 14.0, 4)
    moist = fractal_noise(w, h, seed + 101, 10.0, 3)
    detail = fractal_noise(w, h, seed + 202, 4.0, 2)
    t = np.zeros((h, w), dtype=np.uint8)
    floor = Tile.GROUND

    if style == "forest":
        t[:] = Tile.GRASS
        t[moist > 0.58] = Tile.GROUND
        t[(detail > 0.6) & (elev > 0.45)] = Tile.TREE
        t[elev > 0.72] = Tile.TREE
        t[(elev < 0.26)] = Tile.SHALLOW
        t[(elev < 0.21)] = Tile.DEEP
        t[(detail > 0.82) & (elev < 0.6)] = Tile.ROCK
        floor = Tile.GRASS
    elif style == "steppe":
        t[:] = Tile.GRASS
        t[moist > 0.6] = Tile.GROUND
        t[(detail > 0.8) & (elev > 0.55)] = Tile.ROCK
        t[(detail > 0.84) & (elev < 0.5)] = Tile.TREE
        t[elev < 0.22] = Tile.SHALLOW
        t[elev < 0.18] = Tile.DEEP
        floor = Tile.GRASS
    elif style == "coast":
        # gradient toward one side becomes sea
        xs = np.linspace(0, 1, w)[None, :].repeat(h, 0)
        ys = np.linspace(0, 1, h)[:, None].repeat(w, 1)
        grad = [xs, 1 - xs, ys, 1 - ys][coast_side % 4]
        sea = grad * 0.75 + elev * 0.35
        t[:] = Tile.GRASS
        t[sea > 0.56] = Tile.SAND
        t[sea > 0.68] = Tile.SHALLOW
        t[sea > 0.76] = Tile.DEEP
        t[(detail > 0.82) & (sea < 0.56)] = Tile.TREE
        t[(detail > 0.86) & (sea > 0.5) & (sea < 0.68)] = Tile.ROCK
        floor = Tile.SAND
    elif style == "marsh":
        t[:] = Tile.MUD
        t[moist > 0.5] = Tile.GRASS
        t[(elev < 0.42)] = Tile.REED
        t[(elev < 0.33)] = Tile.SHALLOW
        t[(elev < 0.24)] = Tile.DEEP
        t[(detail > 0.78) & (elev > 0.5)] = Tile.TREE
        floor = Tile.MUD
    elif style == "desert":
        t[:] = Tile.SAND
        t[moist > 0.66] = Tile.GROUND
        t[(elev > 0.7) & (detail > 0.45)] = Tile.ROCK
        t[(detail > 0.88)] = Tile.CRYSTAL
        oasis = (elev < 0.2) & (moist > 0.45)
        t[oasis] = Tile.SHALLOW
        t[(elev < 0.16) & (moist > 0.5)] = Tile.DEEP
        floor = Tile.SAND
    elif style == "caves":
        rng = np.random.default_rng(seed)
        walls = rng.random((h, w)) < 0.52
        for _ in range(4):  # cellular automata smoothing
            n = np.zeros((h, w), dtype=np.int32)
            pad = np.pad(walls, 1, constant_values=True)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx or dy:
                        n += pad[1 + dy:h + 1 + dy, 1 + dx:w + 1 + dx]
            walls = n >= 5
        t[:] = Tile.CAVE_FLOOR
        t[walls] = Tile.CAVE_WALL
        t[(~walls) & (elev < 0.25)] = Tile.SHALLOW
        t[(walls) & (detail > 0.75)] = Tile.CRYSTAL
        floor = Tile.CAVE_FLOOR
    elif style == "rift":
        t[:] = Tile.GLITCH
        t[moist > 0.55] = Tile.GROUND
        t[(elev > 0.7)] = Tile.CRYSTAL
        t[(detail > 0.84) & (elev < 0.55)] = Tile.VOID
        t[(elev < 0.2)] = Tile.SHALLOW
        floor = Tile.GLITCH
    else:
        t[:] = Tile.GROUND

    # keep a passable margin around gates and connect everything that matters
    cx, cy = w // 2, h // 2
    for (gx, gy) in gates:
        _carve_line(t, gx, gy, cx, cy, floor, 1)
    # make sure the centre is open
    t[cy - 1:cy + 2, cx - 1:cx + 2] = np.where(WALK_COST[t[cy - 1:cy + 2, cx - 1:cx + 2]] >= INF,
                                               floor, t[cy - 1:cy + 2, cx - 1:cx + 2])

    # walkable islands not connected to the main area are filled in
    walk = passable_mask(t)
    labels, n = _components(walk)
    if n > 1:
        main = labels[cy, cx]
        isolated = walk & (labels != main)
        big = []
        for lab in range(n):
            if lab == main:
                continue
            size = int((labels == lab).sum())
            if size >= 30:
                big.append(lab)
        # big islands get a connecting corridor, small ones are filled
        for lab in big:
            ys, xs = np.nonzero(labels == lab)
            i = len(xs) // 2
            _carve_line(t, int(xs[i]), int(ys[i]), cx, cy, floor, 1)
            isolated &= labels != lab
        obstacle = {"caves": Tile.CAVE_WALL, "rift": Tile.CRYSTAL, "desert": Tile.ROCK}.get(style, Tile.ROCK)
        t[isolated] = obstacle
    return t


def apply_mods(terrain: np.ndarray, mods: dict) -> None:
    for key, tile in mods.items():
        x, y = (int(v) for v in key.split(","))
        if 0 <= y < terrain.shape[0] and 0 <= x < terrain.shape[1]:
            terrain[y, x] = int(tile)


def water_adjacent_tiles(terrain: np.ndarray) -> list[tuple[int, int]]:
    """Walkable tiles next to water — shore spots for pools and shoals."""
    h, w = terrain.shape
    water = np.isin(terrain, [int(Tile.DEEP), int(Tile.SHALLOW)])
    walk = passable_mask(terrain) & ~water
    near = np.zeros_like(water)
    near[1:, :] |= water[:-1, :]
    near[:-1, :] |= water[1:, :]
    near[:, 1:] |= water[:, :-1]
    near[:, :-1] |= water[:, 1:]
    ys, xs = np.nonzero(walk & near)
    return list(zip(xs.tolist(), ys.tolist()))
