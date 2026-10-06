"""Seeded fractal value noise (NumPy, no external deps)."""

from __future__ import annotations

import numpy as np


def _smooth(t: np.ndarray) -> np.ndarray:
    return t * t * (3 - 2 * t)


def value_noise(w: int, h: int, cell: float, rng: np.random.Generator) -> np.ndarray:
    gw = int(np.ceil(w / cell)) + 2
    gh = int(np.ceil(h / cell)) + 2
    grid = rng.random((gh, gw))
    ys = np.arange(h) / cell
    xs = np.arange(w) / cell
    y0 = np.floor(ys).astype(int)
    x0 = np.floor(xs).astype(int)
    ty = _smooth(ys - y0)[:, None]
    tx = _smooth(xs - x0)[None, :]
    a = grid[y0[:, None], x0[None, :]]
    b = grid[y0[:, None], x0[None, :] + 1]
    c = grid[y0[:, None] + 1, x0[None, :]]
    d = grid[y0[:, None] + 1, x0[None, :] + 1]
    top = a + (b - a) * tx
    bot = c + (d - c) * tx
    return top + (bot - top) * ty


def fractal_noise(w: int, h: int, seed: int, base_cell: float = 16.0, octaves: int = 4,
                  persistence: float = 0.5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    total = np.zeros((h, w))
    amp, norm, cell = 1.0, 0.0, base_cell
    for _ in range(octaves):
        total += value_noise(w, h, max(cell, 1.0), rng) * amp
        norm += amp
        amp *= persistence
        cell /= 2.0
    return total / norm
