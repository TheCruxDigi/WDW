"""World view: draws the player's region from simulation state (read-only)."""

from __future__ import annotations

import math
import random

import pygame

from ..world.region import CORR_CELL
from ..world.terrain import Tile
from .art import TerrainPainter, SpriteBank, draw_icon, shade, mix


class Camera:
    def __init__(self, w: int, h: int, ts: int):
        self.w, self.h = w, h
        self.ts = ts
        self.x = self.y = 0.0  # world tile coords of the screen centre

    def follow(self, tx: float, ty: float, region, dt: float) -> None:
        k = min(1.0, dt * 6.0)
        self.x += (tx - self.x) * k
        self.y += (ty - self.y) * k
        self.clamp(region)

    def snap(self, tx: float, ty: float, region) -> None:
        self.x, self.y = tx, ty
        self.clamp(region)

    def clamp(self, region) -> None:
        half_w = self.w / self.ts / 2
        half_h = self.h / self.ts / 2
        if region.width <= half_w * 2:
            self.x = region.width / 2
        else:
            self.x = min(max(self.x, half_w), region.width - half_w)
        if region.height <= half_h * 2:
            self.y = region.height / 2
        else:
            self.y = min(max(self.y, half_h), region.height - half_h)

    def to_screen(self, x: float, y: float) -> tuple[int, int]:
        return (int((x - self.x) * self.ts + self.w / 2), int((y - self.y) * self.ts + self.h / 2))

    def to_world(self, sx: float, sy: float) -> tuple[float, float]:
        return ((sx - self.w / 2) / self.ts + self.x, (sy - self.h / 2) / self.ts + self.y)

    @property
    def offset(self) -> tuple[int, int]:
        return (int(self.w / 2 - self.x * self.ts), int(self.h / 2 - self.y * self.ts))


class Particles:
    def __init__(self):
        self.items: list[list] = []   # x, y, vx, vy, life, max_life, color, size (world coords)

    def emit(self, x, y, color, n=6, speed=1.5, life=0.8, size=2, rng=random):
        for _ in range(n):
            a = rng.random() * math.tau
            v = speed * (0.4 + rng.random())
            self.items.append([x, y, math.cos(a) * v, math.sin(a) * v, life, life, color, size])
        if len(self.items) > 600:
            del self.items[:200]

    def update(self, dt: float) -> None:
        for p in self.items:
            p[0] += p[2] * dt
            p[1] += p[3] * dt
            p[3] -= 0.5 * dt
            p[4] -= dt
        self.items = [p for p in self.items if p[4] > 0]

    def draw(self, surf, cam: Camera) -> None:
        for x, y, _, _, life, ml, col, size in self.items:
            sx, sy = cam.to_screen(x, y)
            a = life / ml
            pygame.draw.circle(surf, shade(col, 0.5 + 0.5 * a), (sx, sy), max(1, int(size * a + 0.5)))


def _radial(radius: int, color, alpha: int, additive: bool = True) -> pygame.Surface:
    """Radial gradient. Additive glows store intensity in RGB (premultiplied),
    light masks store it in alpha (used to cut holes in the darkness layer)."""
    s = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
    step = max(1, radius // 16)
    for r in range(radius, 0, -step):
        k = (1 - r / radius) ** 1.6
        a = int(alpha * k)
        if additive:
            col = tuple(int(c * a / 255) for c in color[:3])
            pygame.draw.circle(s, (*col, 255), (radius, radius), r)
        else:
            pygame.draw.circle(s, (0, 0, 0, a), (radius, radius), r)
    return s


class WorldView:
    def __init__(self, content, w: int, h: int, ts: int):
        self.content = content
        self.cam = Camera(w, h, ts)
        self.terrain = TerrainPainter()
        self.sprites = SpriteBank(content)
        self.particles = Particles()
        self._corr_cache: dict = {}
        self._glow_cache: dict = {}
        self.rng = random.Random(7)
        self.time = 0.0
        self.show_territories = False
        self.debug = False
        self.selected = None
        self.dark = pygame.Surface((w, h), pygame.SRCALPHA)

    def resize(self, w, h):
        self.cam.w, self.cam.h = w, h
        self.dark = pygame.Surface((w, h), pygame.SRCALPHA)

    def set_tile_size(self, ts: int):
        self.cam.ts = ts
        self._glow_cache.clear()

    def glow(self, radius: int, color, alpha=120, additive: bool = True) -> pygame.Surface:
        key = (radius, tuple(color[:3]), alpha, additive)
        g = self._glow_cache.get(key)
        if g is None:
            g = self._glow_cache[key] = _radial(max(2, radius), color, alpha, additive)
        return g

    def shadow(self, size: int) -> pygame.Surface:
        key = ("shadow", size)
        g = self._glow_cache.get(key)
        if g is None:
            g = pygame.Surface((max(2, size * 2 // 3), max(2, size // 6)), pygame.SRCALPHA)
            pygame.draw.ellipse(g, (0, 0, 0, 80), g.get_rect())
            self._glow_cache[key] = g
        return g

    def corruption_overlay(self, region, ts: int) -> pygame.Surface:
        ver = region.flags.get("_corr_version", 0)
        key = (region.id, ts)
        cached = self._corr_cache.get(key)
        if cached and cached[0] == ver:
            return cached[1]
        f = region.corruption
        ch, cw = f.shape
        s = pygame.Surface((region.width * ts, region.height * ts), pygame.SRCALPHA)
        cs = CORR_CELL * ts
        for y in range(ch):
            for x in range(cw):
                v = float(f[y, x])
                if v < 0.12:
                    continue
                a = int(min(115, (v - 0.12) * 150))
                col = mix((140, 40, 200), (255, 30, 140), min(1.0, v))
                s.fill((*col, a), (x * cs, y * cs, cs, cs))
        self._corr_cache[key] = (ver, s)
        return s

    # ------------------------------------------------------------------
    def draw(self, surf: pygame.Surface, sim, alpha: float, dt: float) -> None:
        self.time += dt
        world = sim.world
        p = world.player
        region = world.regions[p.region_id]
        biome = world.biome(region)
        cam = self.cam
        ts = cam.ts
        if region.terrain is None:
            surf.fill((0, 0, 0))
            return
        px = p.prev_x + (p.x - p.prev_x) * 1.0
        py = p.prev_y + (p.y - p.prev_y) * 1.0
        cam.follow(px, py, region, dt)
        surf.fill((6, 6, 12))
        terr = self.terrain.get(region, biome, ts)
        ox, oy = cam.offset
        surf.blit(terr, (ox, oy))
        self._water_shimmer(surf, region, cam)
        surf.blit(self.corruption_overlay(region, ts), (ox, oy))
        if self.show_territories or self.debug:
            self._territories(surf, sim, region, cam)
        self._nodes(surf, sim, region, cam)
        self._structures(surf, sim, region, cam)
        self._anomalies(surf, region, cam)
        self._creatures(surf, sim, region, cam, alpha)
        self._player(surf, sim, cam)
        self.particles.update(dt)
        self.particles.draw(surf, cam)
        self._lighting(surf, sim, region, cam)
        self._glitch(surf, sim, region, cam)

    def _visible(self, cam, x, y, margin=2) -> bool:
        hw = cam.w / cam.ts / 2 + margin
        hh = cam.h / cam.ts / 2 + margin
        return abs(x - cam.x) < hw and abs(y - cam.y) < hh

    def _water_shimmer(self, surf, region, cam):
        ts = cam.ts
        t = self.time
        rng = random.Random(int(t * 3))
        terr = region.terrain
        x0 = max(0, int(cam.x - cam.w / ts / 2))
        y0 = max(0, int(cam.y - cam.h / ts / 2))
        x1 = min(region.width, int(cam.x + cam.w / ts / 2) + 1)
        y1 = min(region.height, int(cam.y + cam.h / ts / 2) + 1)
        for _ in range(40):
            x = rng.randrange(x0, max(x0 + 1, x1))
            y = rng.randrange(y0, max(y0 + 1, y1))
            if terr[y, x] in (Tile.DEEP, Tile.SHALLOW):
                sx, sy = cam.to_screen(x + rng.random(), y + rng.random())
                pygame.draw.line(surf, (150, 200, 255), (sx, sy), (sx + ts // 3, sy), 1)

    def _territories(self, surf, sim, region, cam):
        world = sim.world
        for t in region.territories.values():
            g = world.groups.get(t.group_id)
            col = world.species(t.species_id).accent
            sx, sy = cam.to_screen(t.x, t.y)
            r = int(t.radius * cam.ts)
            s = pygame.Surface((r * 2 + 4, r * 2 + 4), pygame.SRCALPHA)
            pygame.draw.circle(s, (*col, int(25 + 50 * t.strength)), (r + 2, r + 2), r)
            pygame.draw.circle(s, (*col, 160), (r + 2, r + 2), r, 2)
            surf.blit(s, (sx - r - 2, sy - r - 2))
            if g is not None:
                f = pygame.font.Font(None, 16)
                surf.blit(f.render(f"{g.name} {t.strength:.0%}", True, col), (sx - 30, sy - r - 12))

    def _nodes(self, surf, sim, region, cam):
        ts = cam.ts
        t = self.time
        for n in region.resources.values():
            if not self._visible(cam, n.x, n.y):
                continue
            sx, sy = cam.to_screen(n.x + 0.5, n.y + 0.5)
            full = max(0.0, min(1.0, n.fullness))
            k = n.kind
            rk = self.content.resources[k]
            col = rk.color
            if k == "grass":
                for i in range(1 + int(full * 5)):
                    a = i * 1.3
                    bx = sx + int(math.cos(a) * ts * 0.3)
                    by = sy + int(math.sin(a) * ts * 0.25)
                    pygame.draw.line(surf, shade(col, 0.8 + 0.4 * full), (bx, by + 3), (bx + int(math.sin(t + i) * 2), by - ts // 3), 2)
                if full < 0.1:
                    pygame.draw.circle(surf, (110, 90, 60), (sx, sy), ts // 4, 1)
            elif k in ("fruit", "sunbulb"):
                pygame.draw.circle(surf, (40, 100, 50) if k == "fruit" else (150, 140, 70), (sx, sy), int(ts * 0.42))
                for i in range(int(full * 6)):
                    a = i * 1.05
                    pygame.draw.circle(surf, col, (sx + int(math.cos(a) * ts * 0.25), sy + int(math.sin(a) * ts * 0.22)), max(2, ts // 8))
            elif k == "fungus":
                for i in range(1 + int(full * 3)):
                    bx = sx + (i - 1) * ts // 4
                    pygame.draw.rect(surf, (220, 210, 200), (bx - 1, sy, 3, ts // 4))
                    pygame.draw.ellipse(surf, col, (bx - ts // 6, sy - ts // 8, ts // 3, ts // 5))
            elif k == "bytebloom":
                g = self.glow(int(ts * 0.9), col, 70)
                surf.blit(g, (sx - g.get_width() // 2, sy - g.get_height() // 2), special_flags=pygame.BLEND_ADD)
                for i in range(1 + int(full * 4)):
                    a = i * 1.6 + t
                    bx = sx + int(math.cos(a) * ts * 0.25)
                    by = sy + int(math.sin(a) * ts * 0.25)
                    pygame.draw.rect(surf, col, (bx - 2, by - 2, 4, 4))
            elif k == "shoal":
                for i in range(int(full * 5) + 1):
                    a = t * 1.5 + i * 1.3
                    pygame.draw.ellipse(surf, col, (sx + int(math.cos(a) * ts * 0.3) - 3, sy + int(math.sin(a) * ts * 0.2) - 1, 6, 3))
            elif k == "water":
                pygame.draw.ellipse(surf, shade(col, 0.6 + 0.5 * full), (sx - ts * 0.45, sy - ts * 0.3, ts * 0.9, ts * 0.6))
                c = region.corruption_at(n.x, n.y)
                if c > 0.4:
                    pygame.draw.ellipse(surf, (200, 60, 200), (sx - ts * 0.45, sy - ts * 0.3, ts * 0.9, ts * 0.6), 1)
            elif k == "material":
                for i in range(1 + int(full * 3)):
                    bx = sx + (i - 1) * ts // 4
                    pygame.draw.polygon(surf, col, [(bx - 3, sy + ts // 4), (bx, sy - ts // 4), (bx + 3, sy + ts // 4)])
            elif k == "shelter":
                pygame.draw.ellipse(surf, (30, 24, 20), (sx - ts * 0.4, sy - ts * 0.25, ts * 0.8, ts * 0.55))
                pygame.draw.arc(surf, (140, 110, 80), (sx - ts * 0.45, sy - ts * 0.3, ts * 0.9, ts * 0.7), 0, math.pi, 2)
            elif k == "meat":
                pygame.draw.ellipse(surf, col, (sx - ts * 0.3, sy - ts * 0.15, ts * 0.6, ts * 0.3))
                pygame.draw.line(surf, (230, 220, 200), (sx - ts * 0.2, sy), (sx + ts * 0.2, sy), 1)

    def _structures(self, surf, sim, region, cam):
        ts = cam.ts
        for s in region.structures.values():
            if not self._visible(cam, s.x, s.y):
                continue
            sx, sy = cam.to_screen(s.x + 0.5, s.y + 0.5)
            if s.kind == "beacon":
                hgt = int(ts * 1.4 * max(0.2, s.progress))
                pygame.draw.rect(surf, (60, 70, 90), (sx - ts // 4, sy - hgt + ts // 3, ts // 2, hgt))
                pygame.draw.rect(surf, (180, 240, 255), (sx - ts // 4, sy - hgt + ts // 3, ts // 2, hgt), 1)
                if s.complete:
                    pulse = 0.5 + 0.5 * math.sin(self.time * 2 + s.id)
                    col = mix((255, 80, 80), (120, 255, 210), s.integrity)
                    g = self.glow(int(ts * 1.4), col, int(90 + 60 * pulse))
                    surf.blit(g, (sx - g.get_width() // 2, sy - hgt + ts // 3 - g.get_height() // 2), special_flags=pygame.BLEND_ADD)
                    r = int(ts * (2 + 5 * ((self.time * 0.4 + s.id * 0.1) % 1.0)))
                    pygame.draw.circle(surf, shade(col, 0.6), (sx, sy), r, 1)
                else:
                    pygame.draw.line(surf, (200, 200, 120), (sx - ts // 2, sy + ts // 3), (sx + ts // 2, sy - hgt), 1)

    def _anomalies(self, surf, region, cam):
        ts = cam.ts
        t = self.time
        for a in region.anomalies.values():
            if not self._visible(cam, a.x, a.y, 4):
                continue
            sx, sy = cam.to_screen(a.x + 0.5, a.y + 0.5)
            if a.kind == "glitch_bloom":
                g = self.glow(int(ts * 2.2), (255, 40, 200), 110)
                surf.blit(g, (sx - g.get_width() // 2, sy - g.get_height() // 2), special_flags=pygame.BLEND_ADD)
                for i in range(8):
                    ang = t * 0.8 + i * math.tau / 8
                    r = ts * (0.6 + 0.25 * math.sin(t * 3 + i))
                    pygame.draw.rect(surf, (255, 60, 210) if i % 2 else (60, 255, 230),
                                     (sx + math.cos(ang) * r - 3, sy + math.sin(ang) * r - 3, 6, 6))
            elif a.kind == "echo_pillar":
                s = pygame.Surface((ts, ts * 3), pygame.SRCALPHA)
                s.fill((180, 200, 255, 70))
                surf.blit(s, (sx - ts // 2, sy - ts * 2.5))
                for i in range(3):
                    yy = sy - ts * 2.5 + ((t * 20 + i * ts) % (ts * 3))
                    pygame.draw.line(surf, (220, 230, 255), (sx - ts // 2, yy), (sx + ts // 2, yy), 1)
            elif a.kind == "static_vortex":
                for i in range(30):
                    ang = t * 2 + i * 0.4
                    r = ts * 0.15 * i ** 0.6
                    col = (200, 200, 220) if i % 3 else (255, 60, 160)
                    surf.set_at((int(sx + math.cos(ang) * r), int(sy + math.sin(ang) * r)), col)
                    pygame.draw.circle(surf, col, (int(sx + math.cos(ang) * r), int(sy + math.sin(ang) * r)), 1)

    def _creatures(self, surf, sim, region, cam, alpha):
        world = sim.world
        ts = cam.ts
        t = self.time
        font = None
        sel = self.selected
        for c in sorted(world.creatures_in(region.id), key=lambda c: c.y):
            x = c.prev_x + (c.x - c.prev_x) * alpha
            y = c.prev_y + (c.y - c.prev_y) * alpha
            if not self._visible(cam, x, y):
                continue
            sp = world.species(c.species_id)
            young = c.age_days(world.clock.tick) < sp.maturity_days
            size = max(10, int(ts * sp.size * 2.4 * (0.65 if young else 1.0)))
            moving = abs(c.x - c.prev_x) + abs(c.y - c.prev_y) > 0.01
            frame = int(t * 6 + c.id) % 2 if moving else 0
            flip = (c.x - c.prev_x) < -0.005 or (not moving and c.id % 2 == 0)
            spr = self.sprites.get(c.species_id, size, frame, flip)
            sx, sy = cam.to_screen(x, y)
            if c.taint > 0.45:
                jit = int((c.taint - 0.4) * 6)
                sx += self.rng.randint(-jit, jit)
            if c.sleeping:
                spr = pygame.transform.rotate(spr, 90 if flip else -90)
            surf.blit(self.shadow(size), (sx - size // 3, sy + size // 3))
            if sp.id == "fennwisp" or sp.id == "lumen_keeper":
                g = self.glow(int(size * 0.8), sp.accent, 50)
                surf.blit(g, (sx - g.get_width() // 2, sy - g.get_height() // 2), special_flags=pygame.BLEND_ADD)
            surf.blit(spr, (sx - size // 2, sy - size // 2))
            if c.taint > 0.35:
                tint = spr.copy()
                tint.fill((255, 0, 180, 0), special_flags=pygame.BLEND_RGBA_MAX)
                tint.set_alpha(int(min(160, c.taint * 160)))
                surf.blit(tint, (sx - size // 2 + 2, sy - size // 2))
            if c.status_icon:
                draw_icon(surf, c.status_icon, sx + size // 3, sy - size // 2 - 4, t)
            if c.health < 0.5:
                pygame.draw.rect(surf, (40, 0, 0), (sx - size // 2, sy - size // 2 - 6, size, 3))
                pygame.draw.rect(surf, (230, 60, 60), (sx - size // 2, sy - size // 2 - 6, int(size * max(0, c.health)), 3))
            if sel is not None and sel == c.id:
                pygame.draw.circle(surf, (255, 255, 160), (sx, sy), size // 2 + 4, 2)
                pr = int((c.percept_cache.radius if c.percept_cache else sp.perception) * ts)
                pygame.draw.circle(surf, (255, 255, 160), (sx, sy), pr, 1)
                if c.path:
                    pts = [cam.to_screen(px + 0.5, py + 0.5) for px, py in c.path]
                    if len(pts) > 1:
                        pygame.draw.lines(surf, (255, 240, 120), False, [(sx, sy)] + pts, 1)
                if c.action and c.action.get("goal"):
                    gx, gy = c.action["goal"]
                    pygame.draw.circle(surf, (255, 200, 80), cam.to_screen(gx, gy), 4, 1)
            elif self.debug and c.path:
                pts = [cam.to_screen(px + 0.5, py + 0.5) for px, py in c.path[:20]]
                if len(pts) > 1:
                    pygame.draw.lines(surf, (120, 120, 160), False, [(sx, sy)] + pts, 1)
            if self.debug:
                if font is None:
                    font = pygame.font.Font(None, 14)
                surf.blit(font.render(c.action_name, True, (230, 230, 230)), (sx - 14, sy + size // 2))

    def _player(self, surf, sim, cam):
        p = sim.world.player
        ts = cam.ts
        sx, sy = cam.to_screen(p.x, p.y)
        g = self.glow(int(ts * 1.6), (90, 230, 255), 90)
        surf.blit(g, (sx - g.get_width() // 2, sy - g.get_height() // 2), special_flags=pygame.BLEND_ADD)
        fx, fy = p.facing
        r = int(ts * 0.45)
        pts = [(sx + fx * r * 1.2, sy + fy * r * 1.2), (sx - fy * r * 0.8 - fx * r * 0.6, sy + fx * r * 0.8 - fy * r * 0.6),
               (sx + fy * r * 0.8 - fx * r * 0.6, sy - fx * r * 0.8 - fy * r * 0.6)]
        pygame.draw.polygon(surf, (40, 90, 120), pts)
        pygame.draw.polygon(surf, (140, 250, 255), pts, 2)
        pulse = 0.5 + 0.5 * math.sin(self.time * 4)
        pygame.draw.circle(surf, mix((120, 255, 240), (255, 255, 255), pulse), (sx, sy), max(2, ts // 6))
        e = p.energy / 100
        pygame.draw.arc(surf, (120, 255, 200), (sx - r - 4, sy - r - 4, 2 * r + 8, 2 * r + 8), math.pi / 2, math.pi / 2 + math.tau * e, 2)

    def _lighting(self, surf, sim, region, cam):
        world = sim.world
        day = world.clock.daylight()
        darkness = int(max(0, (0.5 - day) / 0.5) * 150)
        if region.style == "caves":
            darkness = max(darkness, 110)
        if region.weather == "datastorm":
            darkness = max(darkness, 60)
        if darkness <= 0:
            return
        dark = self.dark
        tint = (10, 12, 40) if region.style != "rift" else (30, 0, 40)
        dark.fill((*tint, darkness))
        ts = cam.ts

        def punch(x, y, radius, strength=255):
            sx, sy = cam.to_screen(x, y)
            g = self.glow(int(radius), (0, 0, 0), strength, additive=False)
            # subtracting alpha "cuts" light into the darkness layer
            dark.blit(g, (sx - g.get_width() // 2, sy - g.get_height() // 2), special_flags=pygame.BLEND_RGBA_SUB)

        p = world.player
        punch(p.x, p.y, ts * 5.5, 230)
        for s in region.structures.values():
            if s.kind == "beacon" and s.complete:
                punch(s.x + 0.5, s.y + 0.5, ts * 6 * s.integrity, 220)
        for a in region.anomalies.values():
            punch(a.x + 0.5, a.y + 0.5, ts * 3.5, 200)
        for c in world.creatures_in(region.id):
            if c.species_id in ("fennwisp", "lumen_keeper", "bitmite") and self._visible(cam, c.x, c.y):
                punch(c.x, c.y, ts * 2.2, 160)
        for n in region.resources.values():
            if n.kind == "bytebloom" and self._visible(cam, n.x, n.y):
                punch(n.x + 0.5, n.y + 0.5, ts * 1.5, 120)
        surf.blit(dark, (0, 0))

    def _glitch(self, surf, sim, region, cam):
        """Visual-only corruption: scanlines and displaced blocks where it is strong."""
        p = sim.world.player
        local = region.corruption_at(p.x, p.y)
        level = max(region.corruption_level, local)
        noise = region.signal_noise
        if level < 0.25 and noise < 0.3:
            return
        rng = self.rng
        w, h = surf.get_size()
        n = int((level - 0.2) * 8 + noise * 6)
        for _ in range(max(0, n)):
            bw, bh = rng.randint(20, 160), rng.randint(2, 10)
            x, y = rng.randint(0, w - bw), rng.randint(0, h - bh)
            dx = rng.randint(-12, 12)
            try:
                chunk = surf.subsurface((x, y, bw, bh)).copy()
                surf.blit(chunk, (x + dx, y))
            except ValueError:
                pass
        if level > 0.45 and int(self.time * 12) % 3 == 0:
            for y in range(0, h, 6):
                if rng.random() < (level - 0.45) * 0.12:
                    pygame.draw.line(surf, (255, 0, 160) if rng.random() < 0.5 else (0, 255, 220), (0, y), (w, y), 1)
