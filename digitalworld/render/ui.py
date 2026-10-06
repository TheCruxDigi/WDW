"""HUD, panels and overlays. Pure presentation over simulation state."""

from __future__ import annotations

import math

import pygame

from ..core.clock import format_ticks, TICKS_PER_DAY
from ..entities.creature import NEED_NAMES, TRAIT_NAMES
from ..entities.memory import PLAYER_ID
from ..sim.ecology import suitability
from .art import shade, mix

BG = (12, 14, 24)
FG = (225, 232, 245)
DIM = (140, 150, 170)
ACC = (110, 240, 220)
WARN = (255, 120, 90)
MAG = (255, 70, 200)

_fonts: dict = {}


def font(size: int) -> pygame.font.Font:
    f = _fonts.get(size)
    if f is None:
        f = _fonts[size] = pygame.font.Font(None, size)
    return f


def text(surf, s: str, pos, size=18, color=FG, center=False, right=False) -> pygame.Rect:
    img = font(size).render(str(s), True, color)
    r = img.get_rect()
    if center:
        r.center = pos
    elif right:
        r.topright = pos
    else:
        r.topleft = pos
    surf.blit(img, r)
    return r


def wrap(s: str, width_px: int, size=16) -> list[str]:
    f = font(size)
    words = s.split()
    lines, cur = [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if f.size(t)[0] > width_px and cur:
            lines.append(cur)
            cur = w
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def panel(surf, rect, alpha=200, border=(60, 80, 110)) -> pygame.Rect:
    rect = pygame.Rect(rect)
    s = pygame.Surface(rect.size, pygame.SRCALPHA)
    s.fill((*BG, alpha))
    surf.blit(s, rect.topleft)
    pygame.draw.rect(surf, border, rect, 1)
    return rect


def bar(surf, rect, v: float, color, bg=(30, 34, 48), label: str | None = None) -> None:
    rect = pygame.Rect(rect)
    pygame.draw.rect(surf, bg, rect)
    w = int(rect.w * max(0.0, min(1.0, v)))
    if w:
        pygame.draw.rect(surf, color, (rect.x, rect.y, w, rect.h))
    if label:
        text(surf, label, (rect.x + 3, rect.y + 1), 14, FG)


NEED_COLORS = {"hunger": (240, 170, 80), "thirst": (90, 170, 255), "fatigue": (170, 150, 255),
               "insecurity": (255, 90, 90), "loneliness": (255, 200, 140), "curiosity": (140, 255, 200),
               "territory": (255, 220, 90)}


# ------------------------------------------------------------------ HUD

def draw_hud(surf, sim, runner, fps: float, app) -> None:
    world = sim.world
    p = world.player
    region = world.regions[p.region_id]
    biome = world.biome(region)
    w, h = surf.get_size()
    r = panel(surf, (8, 8, 330, 96))
    text(surf, region.name, (18, 14), 26, ACC)
    text(surf, f"{biome.name}  ·  {region.weather}", (18, 38), 17, DIM)
    band = region.corruption_band()
    bcol = {"stable": (120, 230, 160), "flickering": (230, 220, 120), "corrupted": (255, 140, 90), "fractured": MAG}[band]
    text(surf, f"Corruption: {band} {region.corruption_level:.0%}   noise {region.signal_noise:.0%}", (18, 56), 16, bcol)
    speed = "PAUSED" if runner.paused else f"{runner.speed:g}x"
    text(surf, f"{world.clock.label()}   {speed}", (18, 76), 18, FG)
    if app.cfg.show_fps:
        text(surf, f"{fps:.0f} fps", (318, 80), 14, DIM, right=True)
    # player panel
    pr = panel(surf, (8, h - 78, 330, 70))
    bar(surf, (18, h - 70, 150, 14), p.integrity / 100, (90, 220, 255), label=f"Integrity {p.integrity:.0f}")
    bar(surf, (18, h - 52, 150, 14), p.energy / 100, (120, 255, 170), label=f"Energy {p.energy:.0f}")
    text(surf, f"Seeds {p.inventory.get('seeds', 0)}   Shards {p.inventory.get('shards', 0)}", (180, h - 70), 17, FG)
    text(surf, f"Species {len(p.discovered_species)}/{len(world.content.species)}  Regions {len(p.discovered_regions)}/{len(world.regions)}",
         (180, h - 52), 15, DIM)
    text(surf, "F feed C mend X startle Q scan G gather R stabilize B beacon  [F1 help]", (18, h - 30), 14, DIM)
    draw_minimap(surf, sim, (w - 178, 8, 170, 136))
    draw_toasts(surf, p, app.dt)
    draw_ticker(surf, sim, (w - 420, h - 112, 412, 104))


def draw_minimap(surf, sim, rect) -> None:
    world = sim.world
    rect = panel(surf, rect)
    W, H = world.config.world_width, world.config.world_height
    cw = (rect.w - 10) / W
    ch = (rect.h - 10) / H
    p = world.player
    for r in world.regions.values():
        x = rect.x + 5 + r.gx * cw
        y = rect.y + 5 + r.gy * ch
        b = world.biome(r)
        col = b.palette["ground"] if r.discovered else (40, 44, 56)
        col = mix(col, (200, 40, 180), min(1.0, r.corruption_level * 0.9)) if r.discovered else col
        pygame.draw.rect(surf, col, (x + 1, y + 1, cw - 2, ch - 2))
        if r.loaded:
            pygame.draw.rect(surf, (200, 220, 255), (x + 1, y + 1, cw - 2, ch - 2), 1)
        for nb in r.gates:
            o = world.regions[nb]
            if o.id > r.id:
                pygame.draw.line(surf, (230, 230, 230), (x + cw / 2, y + ch / 2),
                                 (rect.x + 5 + o.gx * cw + cw / 2, rect.y + 5 + o.gy * ch + ch / 2), 1)
        if r.id == p.region_id:
            pygame.draw.circle(surf, ACC, (int(x + cw / 2), int(y + ch / 2)), 4)


def draw_toasts(surf, p, dt) -> None:
    w, h = surf.get_size()
    y = h - 150
    for t in list(p.toasts)[-3:]:
        t[1] -= dt
        if t[1] <= 0:
            continue
        a = min(1.0, t[1])
        lines = wrap(t[0], 640, 18)[:2]
        y -= 24 * (len(lines) - 1)
        for line in lines:
            img = font(18).render(line, True, mix((40, 40, 40), FG, a))
            rr = img.get_rect(center=(w // 2, y))
            panel(surf, rr.inflate(14, 6), int(170 * a))
            surf.blit(img, rr)
            y += 24
        y -= 24 * (len(lines) + 1) + 6
    while p.toasts and p.toasts[0][1] <= 0:
        p.toasts.popleft()


def draw_ticker(surf, sim, rect) -> None:
    world = sim.world
    rid = world.player.region_id
    entries = [e for e in world.history.entries[-200:] if e.importance >= 2 or e.region_id == rid][-5:]
    rect = panel(surf, rect, 170)
    text(surf, "Chronicle  [J]", (rect.x + 6, rect.y + 4), 15, DIM)
    y = rect.y + 20
    for e in reversed(entries):
        col = FG if e.region_id == rid else DIM
        line = f"d{e.tick // TICKS_PER_DAY + 1} " + e.text
        for ln in wrap(line, rect.w - 12, 15)[:1]:
            text(surf, ln, (rect.x + 6, y), 15, col)
        y += 16


# ------------------------------------------------------------------ inspector

def draw_inspector(surf, sim, cid: int) -> bool:
    world = sim.world
    c = world.creatures.get(cid)
    if c is None or not c.alive:
        return False
    sp = world.species(c.species_id)
    now = world.clock.tick
    w, h = surf.get_size()
    rect = panel(surf, (w - 372, 152, 364, h - 272), 225)
    x, y = rect.x + 10, rect.y + 8
    g = world.groups.get(c.group_id) if c.group_id else None
    text(surf, f"{c.name}", (x, y), 26, ACC)
    text(surf, f"{sp.name} · {'female' if c.sex == 'f' else 'male'} · {c.age_days(now):.1f}d · gen {c.generation}", (x + 150, y + 6), 15, DIM)
    y += 24
    role = ""
    if g:
        role = f"{'Leader' if g.leader_id == c.id else 'Member'} of the {g.name} ({len(g.members)})"
    text(surf, role or "Solitary", (x, y), 15, FG)
    y += 18
    bar(surf, (x, y, 165, 12), c.health, (90, 220, 120), label=f"health {c.health:.0%}")
    bar(surf, (x + 175, y, 165, 12), c.taint, MAG, label=f"taint {c.taint:.0%}")
    y += 18
    for i, n in enumerate(NEED_NAMES):
        if n == "territory" and not sp.territorial:
            continue
        col = i % 2
        bx = x + col * 175
        bar(surf, (bx, y, 165, 11), c.needs[n], NEED_COLORS[n], label=f"{n} {c.needs[n]:.2f}")
        if col == 1:
            y += 14
    y += 18
    text(surf, "Traits: " + "  ".join(f"{t[:4]} {c.traits[t]:.2f}" for t in TRAIT_NAMES), (x, y), 14, DIM)
    y += 18
    reason = c.action.get("reason", "") if c.action else ""
    text(surf, f"Doing: {c.action_name}", (x, y), 18, (255, 230, 140))
    y += 16
    for ln in wrap(reason, 340, 15)[:2]:
        text(surf, ln, (x, y), 15, FG)
        y += 15
    y += 4
    text(surf, "Utility scores (last decision):", (x, y), 15, DIM)
    y += 15
    for name, score, why in c.last_scores[:5]:
        bar(surf, (x, y + 2, 60, 9), max(0, score) / 1.5, ACC)
        text(surf, f"{score:5.2f} {name}", (x + 64, y), 15, FG)
        text(surf, why[:38], (x + 180, y), 13, DIM)
        y += 14
    y += 4
    if c.migrate_target is not None or c.mig_factors:
        dest = world.regions[c.migrate_target].name if c.migrate_target else "stay"
        text(surf, f"Migration: {dest}  urgency {c.mig_urgency:.2f}  {c.migrate_reason}", (x, y), 14, (180, 220, 255))
        y += 14
        if c.mig_factors:
            text(surf, "  " + " ".join(f"{k[:4]} {v}" for k, v in c.mig_factors.items()), (x, y), 13, DIM)
            y += 14
    y += 4
    text(surf, "Memories:", (x, y), 15, DIM)
    y += 15
    mems = sorted(c.memory.records, key=lambda m: -m.salience(now))[:6]
    for m in mems:
        who = ""
        if m.subject == PLAYER_ID:
            who = "Wayfinder"
        elif m.subject is not None:
            o = world.find_creature_record(m.subject)
            who = (o.name if hasattr(o, "name") else o.get("name", "?")) if o is not None else f"#{m.subject}"
        col = (140, 255, 170) if m.valence > 0 else (255, 150, 140) if m.valence < 0 else FG
        line = f"{format_ticks(now - m.tick)} ago: {m.kind.replace('_', ' ')} {who} {m.detail}"
        text(surf, line[:58], (x, y), 14, col)
        y += 14
    y += 4
    text(surf, "Relationships:", (x, y), 15, DIM)
    y += 15
    rels = sorted(c.relations.items(), key=lambda kv: -kv[1].weight)[:5]
    for oid, rel in rels:
        if oid == PLAYER_ID:
            name = "Wayfinder (you)"
        else:
            o = world.find_creature_record(oid)
            name = (o.name if hasattr(o, "name") else o.get("name", "?")) if o is not None else f"#{oid} (gone)"
        text(surf, f"{name[:16]:<16} {rel.label():<8} tr {rel.trust:.2f} af {rel.affinity:.2f} fe {rel.fear:.2f} ho {rel.hostility:.2f}",
             (x, y), 13, FG)
        y += 14
    cs = c.counters
    text(surf, f"meals {cs.get('meals', 0)} kills {cs.get('kills', 0)} young {cs.get('children', 0)} "
               f"migrations {cs.get('migrations', 0)} helped {cs.get('helped', 0)}", (x, rect.bottom - 18), 14, DIM)
    return True


# ------------------------------------------------------------------ world map

def draw_world_map(surf, sim, hover_rid: int | None) -> dict:
    world = sim.world
    w, h = surf.get_size()
    rect = panel(surf, (40, 30, w - 80, h - 60), 235)
    text(surf, "WORLD MAP", (rect.x + 14, rect.y + 10), 30, ACC)
    text(surf, "Click a region for details · M to close · bright border = simulated in detail", (rect.x + 200, rect.y + 16), 16, DIM)
    W, H = world.config.world_width, world.config.world_height
    gw = min((rect.w * 0.62) / W, (rect.h - 70) / H)
    ox, oy = rect.x + 16, rect.y + 50
    hit = {}
    p = world.player
    for r in world.regions.values():
        x = ox + r.gx * gw
        y = oy + r.gy * gw
        cell = pygame.Rect(x + 4, y + 4, gw - 8, gw - 8)
        hit[r.id] = cell
        b = world.biome(r)
        known = r.discovered
        col = b.palette["ground"] if known else (36, 40, 52)
        pygame.draw.rect(surf, col, cell)
        if known:
            ov = pygame.Surface(cell.size, pygame.SRCALPHA)
            ov.fill((230, 30, 190, int(min(200, r.corruption_level * 260))))
            surf.blit(ov, cell.topleft)
        for nb, _ in r.gates.items():
            o = world.regions[nb]
            if o.id > r.id:
                pygame.draw.line(surf, (220, 220, 230), cell.center,
                                 (ox + o.gx * gw + gw / 2, oy + o.gy * gw + gw / 2), 2)
        pygame.draw.rect(surf, (230, 240, 255) if r.loaded else (70, 80, 100), cell, 2 if r.loaded else 1)
        if hover_rid == r.id:
            pygame.draw.rect(surf, (255, 240, 120), cell, 3)
        if known:
            text(surf, r.name, (cell.x + 4, cell.y + 4), 15, FG)
            text(surf, b.name, (cell.x + 4, cell.y + 18), 13, DIM)
            pop = r.stats.get("total", 0)
            text(surf, f"pop {pop}  corr {r.corruption_level:.0%}", (cell.x + 4, cell.bottom - 16), 13, FG)
        else:
            text(surf, "unexplored", (cell.x + 4, cell.y + 4), 14, DIM)
        if r.id == p.region_id:
            pygame.draw.circle(surf, ACC, cell.center, 7)
            pygame.draw.circle(surf, (0, 0, 0), cell.center, 7, 2)
    # detail panel
    rid = hover_rid if hover_rid is not None else p.region_id
    r = world.regions[rid]
    dx = ox + W * gw + 20
    dr = pygame.Rect(dx, oy, rect.right - dx - 16, rect.bottom - oy - 14)
    panel(surf, dr, 200)
    x, y = dr.x + 10, dr.y + 8
    b = world.biome(r)
    text(surf, r.name if r.discovered else "Unexplored region", (x, y), 24, ACC)
    y += 24
    if not r.discovered:
        text(surf, "Travel there to learn about it.", (x, y), 16, DIM)
        return hit
    text(surf, f"{b.name} · {r.corruption_band()} · {r.weather} · {'detailed' if r.loaded else 'abstract'} sim", (x, y), 15, DIM)
    y += 18
    for ln in wrap(b.description, dr.w - 20, 15)[:2]:
        text(surf, ln, (x, y), 15, FG)
        y += 15
    st = r.stats
    y += 4
    text(surf, f"Population {st.get('total', 0)}  forage {st.get('plants', 0):.0%}  corruption {r.corruption_level:.0%}  "
               f"beacons {sum(1 for s in r.structures.values() if s.kind == 'beacon')}", (x, y), 15, FG)
    y += 18
    for sid, n in sorted(st.get("pop", {}).items(), key=lambda kv: -kv[1]):
        name = world.species(sid).name if sid in p.discovered_species else "unknown species"
        text(surf, f"  {n:3d}  {name}", (x, y), 15, FG)
        y += 15
    # population / forage / corruption history graph
    samples = r.flags.get("samples", [])
    if len(samples) >= 2:
        gr = pygame.Rect(x, y + 8, dr.w - 20, 90)
        pygame.draw.rect(surf, (20, 24, 36), gr)
        text(surf, "history: population (white) forage (green) corruption (magenta)", (gr.x + 2, gr.y + 2), 13, DIM)
        mp = max(1, max(s[1] for s in samples))
        for idx, col, scale in ((1, (230, 230, 230), mp), (2, (120, 230, 120), 1.0), (3, MAG, 1.0)):
            pts = []
            for i, s in enumerate(samples):
                px = gr.x + i * (gr.w - 1) / max(1, len(samples) - 1)
                py = gr.bottom - 4 - (s[idx] / scale) * (gr.h - 20)
                pts.append((px, py))
            pygame.draw.lines(surf, col, False, pts, 2)
        t0 = samples[0][0]
        text(surf, f"since day {t0 // TICKS_PER_DAY + 1}", (gr.right - 90, gr.bottom - 14), 13, DIM)
        y = gr.bottom + 6
    # what happened while away
    since = r.last_player_tick
    text(surf, "Recent history" + (f" (you were last here {format_ticks(world.clock.tick - since)} ago)" if since else ""),
         (x, y), 15, DIM)
    y += 16
    for e in [e for e in world.history.entries if e.region_id == r.id][-9:][::-1]:
        for ln in wrap(f"d{e.tick // TICKS_PER_DAY + 1}: {e.text}", dr.w - 20, 14)[:2]:
            if y > dr.bottom - 16:
                break
            text(surf, ln, (x, y), 14, FG if e.importance >= 2 else DIM)
            y += 14
    return hit


# ------------------------------------------------------------------ chronicle

def draw_chronicle(surf, sim, state: dict) -> None:
    world = sim.world
    w, h = surf.get_size()
    rect = panel(surf, (60, 40, w - 120, h - 80), 238)
    modes = ["all", "region", "notable"]
    mode = modes[state.get("mode", 0) % 3]
    text(surf, "CHRONICLE OF THE WORLD", (rect.x + 14, rect.y + 10), 30, ACC)
    text(surf, f"filter: {mode} (TAB)   Up/Down scroll   ENTER causes   J close", (rect.x + 380, rect.y + 18), 16, DIM)
    rid = world.player.region_id
    entries = world.history.entries
    if mode == "region":
        entries = [e for e in entries if e.region_id == rid]
    elif mode == "notable":
        entries = [e for e in entries if e.importance >= 2]
    entries = sorted(entries, key=lambda e: (e.tick, e.id), reverse=True)
    sel = max(0, min(state.get("sel", 0), len(entries) - 1))
    state["sel"] = sel
    rows = (rect.h - 200) // 18
    start = max(0, sel - rows // 2)
    y = rect.y + 50
    for i, e in enumerate(entries[start:start + rows]):
        idx = start + i
        col = (255, 240, 140) if idx == sel else (FG if e.importance >= 2 else DIM)
        rname = world.regions[e.region_id].name if e.region_id in world.regions else "world"
        line = f"Day {e.tick // TICKS_PER_DAY + 1:>3} {e.tick % TICKS_PER_DAY // 60:02d}h  [{rname[:16]:<16}] {e.text}"
        text(surf, line[:150], (rect.x + 14, y), 17, col)
        y += 18
    if entries:
        e = entries[sel]
        cr = panel(surf, (rect.x + 10, rect.bottom - 140, rect.w - 20, 130), 220)
        text(surf, "Cause chain of selected entry:", (cr.x + 8, cr.y + 6), 16, DIM)
        yy = cr.y + 24
        for depth, c in enumerate(world.history.cause_chain(e.id)[:6]):
            text(surf, ("   " * depth) + ("<- caused by: " if depth else "") + f"d{c.tick // TICKS_PER_DAY + 1}: {c.text}"[:140],
                 (cr.x + 8, yy), 15, FG if depth == 0 else (180, 220, 255))
            yy += 16
    state["count"] = len(entries)


# ------------------------------------------------------------------ journal / bestiary

def draw_journal(surf, sim) -> None:
    world = sim.world
    p = world.player
    w, h = surf.get_size()
    rect = panel(surf, (60, 40, w - 120, h - 80), 238)
    text(surf, "FIELD JOURNAL", (rect.x + 14, rect.y + 10), 30, ACC)
    text(surf, "K to close", (rect.right - 90, rect.y + 16), 16, DIM)
    x, y = rect.x + 16, rect.y + 50
    text(surf, "Bestiary", (x, y), 22, (255, 230, 140))
    y += 24
    totals: dict[str, int] = {}
    for r in world.regions.values():
        for sid, n in r.stats.get("pop", {}).items():
            totals[sid] = totals.get(sid, 0) + n
    for sid, sp in world.content.species.items():
        if sid not in p.discovered_species:
            text(surf, "??? — not yet observed", (x, y), 16, DIM)
            y += 34
            continue
        pygame.draw.circle(surf, sp.color, (x + 8, y + 10), 8)
        text(surf, f"{sp.name}  ({sp.trophic}, {sp.social}{', territorial' if sp.territorial else ''})  world population ~{totals.get(sid, 0)}",
             (x + 22, y), 17, FG)
        text(surf, sp.description[:110], (x + 22, y + 16), 14, DIM)
        y += 34
    jx = rect.x + rect.w // 2 + 20
    jy = rect.y + 50
    text(surf, "Journal", (jx, jy), 22, (255, 230, 140))
    jy += 24
    for tick, msg in list(p.journal)[::-1][:28]:
        for ln in wrap(f"d{tick // TICKS_PER_DAY + 1}: {msg}", rect.right - jx - 20, 15)[:2]:
            text(surf, ln, (jx, jy), 15, FG)
            jy += 15
        if jy > rect.bottom - 30:
            break
    st = p.stats
    text(surf, f"fed {st['fed']}  mended {st['healed']}  startled {st['scared']}  gathered {st['gathered']}  "
               f"beacons {st['beacons']}  stabilized {st['stabilized']}  distance {st.get('distance', 0):.0f} tiles",
         (x, rect.bottom - 24), 15, DIM)


HELP = [
    ("Move", "WASD / arrows (Shift = run)"),
    ("Interact (nearest creature)", "F feed seeds · C mend (20 energy) · X startle · Q scan"),
    ("World", "G gather from a plant / shard deposit · R stabilization pulse (30 energy)"),
    ("Build", "B raise a lumen beacon (5 shards) · V dismantle a nearby beacon"),
    ("Time", "Space pause · [ / ] slower / faster · N rest 6 hours (whole world advances)"),
    ("Views", "M world map · J chronicle · K journal & bestiary · L territories"),
    ("Inspect", "Left-click a creature to inspect needs, utility scores, memory and relationships"),
    ("Debug", "Tab debug overlay (paths, actions, territories) · F3 profiler · mouse wheel zoom"),
    ("Save", "F5 save · F9 load last save · Esc menu (autosaves on quit)"),
]


def draw_help(surf) -> None:
    w, h = surf.get_size()
    rect = panel(surf, (w // 2 - 380, h // 2 - 200, 760, 400), 240)
    text(surf, "HOW TO PLAY", (rect.x + 16, rect.y + 12), 30, ACC)
    y = rect.y + 56
    for k, v in HELP:
        text(surf, k, (rect.x + 20, y), 18, (255, 230, 140))
        text(surf, v, (rect.x + 250, y), 18, FG)
        y += 28
    text(surf, "The world lives without you: creatures remember what you do, regions change while you are away.",
         (rect.x + 20, rect.bottom - 40), 16, DIM)


def draw_profiler(surf, sim, runner, fps) -> None:
    prof = sim.world.profiler
    rows = prof.report()[:12]
    rect = panel(surf, (350, 8, 380, 40 + 16 * len(rows) + 40), 220)
    text(surf, f"PROFILER  fps {fps:.0f}  ticks {sim.ticks_run}  dropped {runner.dropped_ticks}", (rect.x + 8, rect.y + 6), 16, ACC)
    y = rect.y + 26
    text(surf, f"{'system':<14} avg ms  max ms", (rect.x + 8, y), 15, DIM)
    y += 16
    for name, avg, tot, calls, mx in rows:
        text(surf, f"{name:<14} {avg:6.2f}  {mx:6.1f}", (rect.x + 8, y), 15, FG)
        y += 16
    w = sim.world
    text(surf, f"loaded creatures {len(w.creatures)}  total {w.total_population()}  paths {sim.path_budget.total_requests}",
         (rect.x + 8, y + 4), 14, DIM)
    text(surf, f"decisions {sim.brain.decisions}  abstract steps {sim.abstract.steps_run}  errors {sim.errors}",
         (rect.x + 8, y + 20), 14, DIM)
