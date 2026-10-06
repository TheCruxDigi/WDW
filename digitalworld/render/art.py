"""Procedural, original placeholder art generated at runtime.

Everything is drawn with pygame primitives so the game needs no asset files;
any sprite can later be replaced by an image of the same name in
``assets/sprites/<species>.png`` (loaded if present).
"""

from __future__ import annotations

import math
import random
from pathlib import Path

import pygame

from ..world.terrain import Tile

ASSET_DIR = Path(__file__).resolve().parents[2] / "assets"


_fonts: dict = {}


def font(size: int) -> pygame.font.Font:
    """Cached default font (cache is reset by the App on each pygame init)."""
    f = _fonts.get(size)
    if f is None:
        f = _fonts[size] = pygame.font.Font(None, size)
    return f


def reset_caches() -> None:
    _fonts.clear()


def shade(c, f: float):
    return tuple(max(0, min(255, int(v * f))) for v in c[:3])


def mix(a, b, t: float):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


TILE_BASE = {
    Tile.DEEP: (28, 70, 130),
    Tile.SHALLOW: (52, 112, 170),
    Tile.TREE: (52, 110, 58),
    Tile.ROCK: (104, 104, 112),
    Tile.SAND: (206, 186, 130),
    Tile.MUD: (92, 78, 56),
    Tile.CAVE_FLOOR: (70, 66, 80),
    Tile.CAVE_WALL: (34, 30, 42),
    Tile.CRYSTAL: (90, 200, 220),
    Tile.VOID: (8, 4, 16),
    Tile.REED: (96, 120, 66),
    Tile.GLITCH: (52, 34, 76),
}


class TerrainPainter:
    """Renders a whole region's terrain into one cached surface."""

    def __init__(self):
        self.cache: dict[tuple, pygame.Surface] = {}

    def get(self, region, biome, ts: int) -> pygame.Surface:
        key = (region.id, region.version, ts)
        surf = self.cache.get(key)
        if surf is None:
            for k in [k for k in self.cache if k[0] == region.id]:
                del self.cache[k]
            surf = self._paint(region, biome, ts)
            self.cache[key] = surf
        return surf

    def _paint(self, region, biome, ts: int) -> pygame.Surface:
        t = region.terrain
        h, w = t.shape
        surf = pygame.Surface((w * ts, h * ts))
        rng = random.Random(region.seed)
        g1 = biome.palette.get("ground", (90, 120, 70))
        g2 = biome.palette.get("ground2", (80, 100, 60))
        acc = biome.palette.get("accent", (200, 220, 120))
        tl = t.tolist()
        for y in range(h):
            row = tl[y]
            for x in range(w):
                tile = row[x]
                r = rng.random()
                rect = pygame.Rect(x * ts, y * ts, ts, ts)
                if tile in (Tile.GROUND, Tile.GRASS):
                    base = g1 if tile == Tile.GRASS else g2
                    col = shade(base, 0.92 + r * 0.16)
                    surf.fill(col, rect)
                    if tile == Tile.GRASS and r > 0.55:
                        for _ in range(2):
                            px = x * ts + rng.randint(2, ts - 3)
                            py = y * ts + rng.randint(3, ts - 2)
                            pygame.draw.line(surf, shade(base, 1.25), (px, py), (px + rng.randint(-1, 1), py - ts // 5), 1)
                    elif r > 0.9:
                        pygame.draw.circle(surf, shade(base, 0.8), (x * ts + rng.randint(3, ts - 3), y * ts + rng.randint(3, ts - 3)), max(1, ts // 10))
                    continue
                base = TILE_BASE.get(Tile(tile), (100, 100, 100))
                if tile == Tile.TREE:
                    surf.fill(shade(g1, 0.75), rect)
                    cx, cy = x * ts + ts // 2, y * ts + ts // 2
                    pygame.draw.rect(surf, (70, 50, 34), (cx - ts // 10, cy, max(2, ts // 5), ts // 2))
                    rad = int(ts * (0.5 + r * 0.12))
                    pygame.draw.circle(surf, shade(base, 0.55), (cx + 1, cy - ts // 8 + 2), rad)
                    pygame.draw.circle(surf, shade(base, 0.85 + r * 0.3), (cx, cy - ts // 8), rad)
                    pygame.draw.circle(surf, shade(base, 1.3), (cx - rad // 3, cy - ts // 8 - rad // 3), max(2, rad // 2))
                    if r > 0.8:
                        pygame.draw.circle(surf, acc, (cx + rad // 3, cy - ts // 6), max(1, ts // 12))
                elif tile == Tile.ROCK:
                    surf.fill(shade(g2, 0.85), rect)
                    pts = [(x * ts + ts * a, y * ts + ts * b) for a, b in ((0.12, 0.8), (0.25, 0.25), (0.6, 0.1), (0.9, 0.45), (0.85, 0.9))]
                    pygame.draw.polygon(surf, shade(base, 0.9 + r * 0.2), pts)
                    pygame.draw.line(surf, shade(base, 1.3), pts[1], pts[2], 1)
                elif tile in (Tile.DEEP, Tile.SHALLOW):
                    surf.fill(shade(base, 0.95 + r * 0.1), rect)
                    if r > 0.7:
                        yy = y * ts + rng.randint(2, ts - 3)
                        pygame.draw.line(surf, shade(base, 1.3), (x * ts + 2, yy), (x * ts + ts // 2, yy), 1)
                elif tile == Tile.CAVE_WALL:
                    surf.fill(shade(base, 0.9 + r * 0.2), rect)
                    pygame.draw.line(surf, shade(base, 1.5), (x * ts, y * ts + ts - 1), (x * ts + ts, y * ts + ts - 1), 1)
                elif tile == Tile.CRYSTAL:
                    surf.fill(shade(g2, 0.7), rect)
                    cx, cy = x * ts + ts // 2, y * ts + ts // 2
                    for k in range(3):
                        ox = (k - 1) * ts // 4
                        hgt = int(ts * (0.4 + rng.random() * 0.45))
                        pygame.draw.polygon(surf, shade(acc if region.style == "rift" else base, 0.8 + 0.4 * rng.random()),
                                            [(cx + ox - ts // 8, cy + ts // 3), (cx + ox, cy + ts // 3 - hgt), (cx + ox + ts // 8, cy + ts // 3)])
                elif tile == Tile.VOID:
                    surf.fill(base, rect)
                    if r > 0.5:
                        surf.set_at((x * ts + rng.randint(0, ts - 1), y * ts + rng.randint(0, ts - 1)), (200, 180, 255))
                elif tile == Tile.REED:
                    surf.fill(shade(TILE_BASE[Tile.MUD], 1.1), rect)
                    for k in range(3):
                        px = x * ts + 3 + k * (ts - 6) // 2
                        pygame.draw.line(surf, shade(base, 1.0 + 0.3 * rng.random()), (px, y * ts + ts - 2), (px + rng.randint(-2, 2), y * ts + 3), 2)
                elif tile == Tile.GLITCH:
                    surf.fill(shade(base, 0.9 + r * 0.2), rect)
                    if r > 0.6:
                        pygame.draw.rect(surf, shade(acc, 0.5), rect, 1)
                    if r > 0.93:
                        surf.fill(shade(acc, 0.8), (x * ts + rng.randint(0, ts - 4), y * ts + rng.randint(0, ts - 4), 3, 2))
                else:
                    surf.fill(shade(base, 0.92 + r * 0.16), rect)
        # gates: glowing thresholds
        for nb, (gx, gy) in region.gates.items():
            rect = pygame.Rect(gx * ts, gy * ts, ts, ts)
            pygame.draw.rect(surf, (230, 240, 255), rect, 2)
        return surf


# ------------------------------------------------------------------ creatures

def _eye(s, x, y, r, col=(20, 20, 30)):
    pygame.draw.circle(s, (250, 250, 250), (x, y), r)
    pygame.draw.circle(s, col, (x + max(1, r // 3), y), max(1, r // 2))


def draw_species(sp, size_px: int, frame: int = 0) -> pygame.Surface:
    """Side view facing right. size_px is the sprite box side."""
    custom = ASSET_DIR / "sprites" / f"{sp.id}.png"
    if custom.is_file():
        try:
            img = pygame.image.load(str(custom)).convert_alpha()
            return pygame.transform.smoothscale(img, (size_px, size_px))
        except pygame.error:
            pass
    S = size_px
    s = pygame.Surface((S, S), pygame.SRCALPHA)
    c, a = sp.color, sp.accent
    dark = shade(c, 0.6)
    bob = (1 if frame else 0)
    leg = (2 if frame else -2)
    cx, cy = S // 2, S // 2
    shape = sp.shape
    if shape == "blob":  # mossling
        pygame.draw.ellipse(s, dark, (S * 0.16, S * 0.36 + bob, S * 0.7, S * 0.5))
        pygame.draw.ellipse(s, c, (S * 0.14, S * 0.32 + bob, S * 0.7, S * 0.48))
        for k in range(4):
            pygame.draw.circle(s, a, (int(S * (0.25 + k * 0.15)), int(S * 0.36 + bob)), max(2, S // 12))
        _eye(s, int(S * 0.7), int(S * 0.52 + bob), max(2, S // 11))
        pygame.draw.line(s, dark, (S * 0.3, S * 0.8), (S * 0.3 + leg, S * 0.9), 2)
        pygame.draw.line(s, dark, (S * 0.6, S * 0.8), (S * 0.6 - leg, S * 0.9), 2)
    elif shape == "hare":
        pygame.draw.ellipse(s, c, (S * 0.18, S * 0.45 + bob, S * 0.6, S * 0.34))
        pygame.draw.ellipse(s, c, (S * 0.6, S * 0.32 + bob, S * 0.28, S * 0.26))
        pygame.draw.ellipse(s, a, (S * 0.62, S * 0.02 + bob, S * 0.1, S * 0.36))
        pygame.draw.ellipse(s, c, (S * 0.72, S * 0.04 + bob, S * 0.1, S * 0.34))
        _eye(s, int(S * 0.78), int(S * 0.42 + bob), max(2, S // 13))
        pygame.draw.circle(s, a, (int(S * 0.18), int(S * 0.55 + bob)), max(2, S // 10))
        pygame.draw.line(s, dark, (S * 0.3, S * 0.75), (S * 0.22 + leg, S * 0.92), 3)
        pygame.draw.line(s, dark, (S * 0.6, S * 0.75), (S * 0.66 - leg, S * 0.92), 2)
    elif shape == "quadruped":  # thornhorn
        pygame.draw.rect(s, dark, (S * 0.18, S * 0.66, S * 0.08, S * 0.26))
        pygame.draw.rect(s, dark, (S * 0.62, S * 0.66, S * 0.08, S * 0.26))
        pygame.draw.ellipse(s, c, (S * 0.1, S * 0.36 + bob, S * 0.66, S * 0.4))
        for k in range(4):  # plates
            pygame.draw.polygon(s, shade(c, 1.3), [(S * (0.18 + k * 0.13), S * 0.4 + bob), (S * (0.24 + k * 0.13), S * 0.26 + bob), (S * (0.3 + k * 0.13), S * 0.4 + bob)])
        pygame.draw.ellipse(s, c, (S * 0.66, S * 0.42 + bob, S * 0.28, S * 0.24))
        pygame.draw.polygon(s, a, [(S * 0.82, S * 0.44 + bob), (S * 0.98, S * 0.22 + bob), (S * 0.9, S * 0.48 + bob)])
        pygame.draw.polygon(s, a, [(S * 0.74, S * 0.44 + bob), (S * 0.8, S * 0.2 + bob), (S * 0.84, S * 0.46 + bob)])
        _eye(s, int(S * 0.84), int(S * 0.52 + bob), max(2, S // 16))
    elif shape == "canine":  # shardfang
        pygame.draw.line(s, dark, (S * 0.26, S * 0.62), (S * 0.22 + leg, S * 0.9), 3)
        pygame.draw.line(s, dark, (S * 0.62, S * 0.62), (S * 0.66 - leg, S * 0.9), 3)
        pygame.draw.ellipse(s, c, (S * 0.14, S * 0.4 + bob, S * 0.6, S * 0.28))
        pygame.draw.polygon(s, c, [(S * 0.62, S * 0.42 + bob), (S * 0.96, S * 0.5 + bob), (S * 0.66, S * 0.62 + bob)])
        pygame.draw.polygon(s, a, [(S * 0.66, S * 0.42 + bob), (S * 0.7, S * 0.22 + bob), (S * 0.76, S * 0.44 + bob)])
        pygame.draw.polygon(s, a, [(S * 0.86, S * 0.56 + bob), (S * 0.9, S * 0.66 + bob), (S * 0.93, S * 0.55 + bob)])
        pygame.draw.polygon(s, shade(c, 1.2), [(S * 0.16, S * 0.48 + bob), (S * 0.0, S * 0.3 + bob), (S * 0.1, S * 0.56 + bob)])
        for k in range(3):
            pygame.draw.line(s, a, (S * (0.3 + k * 0.12), S * 0.4 + bob), (S * (0.34 + k * 0.12), S * 0.3 + bob), 2)
        _eye(s, int(S * 0.78), int(S * 0.47 + bob), max(2, S // 16), (200, 30, 60))
    elif shape == "wisp":  # fennwisp
        pygame.draw.circle(s, shade(a, 0.9), (int(S * 0.16), int(S * 0.3 + bob)), max(3, S // 8))
        pygame.draw.line(s, c, (S * 0.18, S * 0.32 + bob), (S * 0.42, S * 0.55), 3)
        pygame.draw.ellipse(s, c, (S * 0.34, S * 0.38 - bob, S * 0.5, S * 0.42))
        pygame.draw.ellipse(s, shade(c, 1.3), (S * 0.42, S * 0.44 - bob, S * 0.3, S * 0.18))
        _eye(s, int(S * 0.72), int(S * 0.52 - bob), max(2, S // 12))
        pygame.draw.circle(s, a, (int(S * 0.16), int(S * 0.3 + bob)), max(2, S // 14))
    elif shape == "feline":  # coralynx
        pygame.draw.line(s, dark, (S * 0.26, S * 0.62), (S * 0.24 + leg, S * 0.9), 3)
        pygame.draw.line(s, dark, (S * 0.6, S * 0.62), (S * 0.62 - leg, S * 0.9), 3)
        pygame.draw.ellipse(s, c, (S * 0.14, S * 0.42 + bob, S * 0.56, S * 0.26))
        pygame.draw.circle(s, c, (int(S * 0.74), int(S * 0.46 + bob)), int(S * 0.15))
        for k in range(3):
            pygame.draw.circle(s, a, (int(S * (0.66 + k * 0.07)), int(S * (0.28 - (k % 2) * 0.05) + bob)), max(2, S // 14))
        pygame.draw.arc(s, c, (S * -0.05, S * 0.2, S * 0.3, S * 0.5), 0.5, 2.6, 3)
        _eye(s, int(S * 0.8), int(S * 0.44 + bob), max(2, S // 15))
    elif shape == "mite":  # bitmite
        for k in range(3):
            y = S * (0.5 + k * 0.1)
            pygame.draw.line(s, a, (S * 0.25, y), (S * 0.1, y + leg), 1)
            pygame.draw.line(s, a, (S * 0.75, y), (S * 0.9, y - leg), 1)
        pygame.draw.rect(s, c, (S * 0.25, S * 0.4 + bob, S * 0.5, S * 0.4))
        pygame.draw.rect(s, shade(c, 1.4), (S * 0.3, S * 0.45 + bob, S * 0.15, S * 0.12))
        pygame.draw.rect(s, a, (S * 0.6, S * 0.48 + bob, S * 0.1, S * 0.08))
    elif shape == "serpent":  # glitchwyrm
        pts = []
        for k in range(7):
            t = k / 6
            pts.append((S * (0.08 + t * 0.78), S * (0.55 + math.sin(t * 6 + frame * 1.5) * 0.15)))
        for i, (x, y) in enumerate(pts):
            r = int(S * (0.07 + 0.06 * (i / 6)))
            pygame.draw.circle(s, c if i % 2 else shade(c, 1.6), (int(x), int(y)), r)
            if i % 2 == 0:
                pygame.draw.rect(s, a, (x - r // 2, y - r - 2, r, 3))
        hx, hy = pts[-1]
        pygame.draw.polygon(s, c, [(hx, hy - S * 0.12), (hx + S * 0.16, hy), (hx, hy + S * 0.12)])
        pygame.draw.circle(s, a, (int(hx + S * 0.05), int(hy - S * 0.03)), max(2, S // 16))
    elif shape == "keeper":  # lumen keeper
        pygame.draw.ellipse(s, shade(c, 0.85), (S * 0.22, S * 0.24 + bob, S * 0.56, S * 0.64))
        pygame.draw.ellipse(s, c, (S * 0.26, S * 0.22 + bob, S * 0.5, S * 0.58))
        pygame.draw.circle(s, a, (int(S * 0.51), int(S * 0.62 + bob)), max(3, S // 9))
        for ex in (0.4, 0.62):
            pygame.draw.circle(s, (255, 230, 150), (int(S * ex), int(S * 0.4 + bob)), max(3, S // 9))
            pygame.draw.circle(s, (30, 30, 40), (int(S * ex + 1), int(S * 0.4 + bob)), max(1, S // 20))
        pygame.draw.polygon(s, shade(c, 0.7), [(S * 0.28, S * 0.24 + bob), (S * 0.34, S * 0.08 + bob), (S * 0.4, S * 0.24 + bob)])
        pygame.draw.polygon(s, shade(c, 0.7), [(S * 0.6, S * 0.24 + bob), (S * 0.68, S * 0.08 + bob), (S * 0.72, S * 0.24 + bob)])
        pygame.draw.line(s, dark, (S * 0.42, S * 0.86), (S * 0.42, S * 0.95), 2)
        pygame.draw.line(s, dark, (S * 0.6, S * 0.86), (S * 0.6, S * 0.95), 2)
    elif shape == "strider":
        pygame.draw.line(s, dark, (S * 0.36, S * 0.5), (S * 0.3 + leg, S * 0.96), 2)
        pygame.draw.line(s, dark, (S * 0.6, S * 0.5), (S * 0.66 - leg, S * 0.96), 2)
        pygame.draw.ellipse(s, c, (S * 0.22, S * 0.3 + bob, S * 0.52, S * 0.24))
        pygame.draw.circle(s, a, (int(S * 0.46), int(S * 0.26 + bob)), max(3, S // 9))
        pygame.draw.line(s, c, (S * 0.7, S * 0.38 + bob), (S * 0.84, S * 0.16 + bob), 3)
        pygame.draw.circle(s, c, (int(S * 0.86), int(S * 0.14 + bob)), max(3, S // 12))
        _eye(s, int(S * 0.88), int(S * 0.13 + bob), max(1, S // 20))
    else:
        pygame.draw.circle(s, c, (cx, cy), S // 3)
    return s


class SpriteBank:
    def __init__(self, content):
        self.content = content
        self.cache: dict[tuple, pygame.Surface] = {}

    def get(self, species_id: str, size_px: int, frame: int, flip: bool) -> pygame.Surface:
        key = (species_id, size_px, frame, flip)
        s = self.cache.get(key)
        if s is None:
            base = self.cache.get((species_id, size_px, frame, False))
            if base is None:
                base = draw_species(self.content.species[species_id], size_px, frame)
                self.cache[(species_id, size_px, frame, False)] = base
            s = pygame.transform.flip(base, True, False) if flip else base
            self.cache[key] = s
        return s


# ------------------------------------------------------------------ icons

def draw_icon(surf: pygame.Surface, kind: str, x: int, y: int, t: float) -> None:
    if not kind:
        return
    if kind == "sleep":
        f = font(16)
        surf.blit(f.render("z", True, (200, 220, 255)), (x + int(math.sin(t * 2) * 2), y - 4))
    elif kind == "flee":
        pygame.draw.rect(surf, (255, 90, 70), (x, y - 6, 3, 7))
        pygame.draw.rect(surf, (255, 90, 70), (x, y + 3, 3, 2))
    elif kind == "food":
        pygame.draw.ellipse(surf, (120, 220, 90), (x - 3, y - 3, 7, 5))
    elif kind == "water":
        pygame.draw.circle(surf, (90, 170, 255), (x, y), 3)
        pygame.draw.polygon(surf, (90, 170, 255), [(x - 2, y - 1), (x, y - 6), (x + 2, y - 1)])
    elif kind == "heart":
        pygame.draw.circle(surf, (255, 110, 160), (x - 2, y - 1), 2)
        pygame.draw.circle(surf, (255, 110, 160), (x + 2, y - 1), 2)
        pygame.draw.polygon(surf, (255, 110, 160), [(x - 4, y), (x, y + 4), (x + 4, y)])
    elif kind == "investigate":
        f = font(16)
        surf.blit(f.render("?", True, (255, 240, 140)), (x - 3, y - 6))
    elif kind in ("fight", "hunt", "territory"):
        col = {"fight": (255, 80, 80), "hunt": (255, 150, 60), "territory": (255, 200, 60)}[kind]
        pygame.draw.line(surf, col, (x - 3, y - 3), (x + 3, y + 3), 2)
        pygame.draw.line(surf, col, (x + 3, y - 3), (x - 3, y + 3), 2)
    elif kind == "migrate":
        pygame.draw.polygon(surf, (180, 240, 255), [(x - 4, y - 3), (x + 4, y), (x - 4, y + 3)])
    elif kind == "build":
        pygame.draw.rect(surf, (140, 255, 210), (x - 3, y - 3, 6, 6), 1)
    elif kind in ("social", "help", "group"):
        for k in (-3, 0, 3):
            pygame.draw.circle(surf, (255, 220, 160), (x + k, y), 1)
    elif kind == "player":
        pygame.draw.circle(surf, (120, 255, 240), (x, y), 3, 1)
    elif kind == "glitch":
        pygame.draw.rect(surf, (255, 40, 200), (x - 3, y - 2, 4, 2))
        pygame.draw.rect(surf, (60, 255, 220), (x - 1, y + 1, 4, 2))
    elif kind == "shelter":
        pygame.draw.arc(surf, (200, 180, 140), (x - 4, y - 3, 8, 8), 0, math.pi, 2)
    elif kind == "explore":
        pygame.draw.circle(surf, (220, 220, 220), (x, y), 2, 1)
