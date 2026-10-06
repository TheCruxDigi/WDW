"""Desktop application (pygame-ce).

The App owns a Simulation and a FixedStepRunner. Each frame: input → player
commands → run due logical ticks → draw. Rendering never mutates simulation
state except through the public command functions in ``player.interactions``.
"""

from __future__ import annotations

import logging
import math
import os
import random
import time
from pathlib import Path

import pygame

from ..config import Config
from ..core.clock import FixedStepRunner, TICKS_PER_DAY
from ..persistence import savegame
from ..player import interactions as act
from ..sim.simulation import Simulation
from . import ui
from .view import WorldView

log = logging.getLogger("digitalworld.app")

ZOOMS = (14, 18, 22, 28, 34)


class App:
    def __init__(self, cfg: Config, new_world: bool = False, seed: int | None = None,
                 screenshot: str | None = None, frames: int = 0, script: list | None = None):
        self.cfg = cfg
        self.screenshot = screenshot
        self.max_frames = frames
        self.script = script or []
        pygame.init()
        from .art import reset_caches
        reset_caches()
        flags = pygame.FULLSCREEN if cfg.fullscreen else pygame.RESIZABLE
        self.screen = pygame.display.set_mode((cfg.window_width, cfg.window_height), flags)
        pygame.display.set_caption("Digital World")
        try:
            icon = pygame.Surface((32, 32), pygame.SRCALPHA)
            pygame.draw.circle(icon, (90, 240, 220), (16, 16), 12)
            pygame.draw.rect(icon, (255, 60, 200), (10, 14, 12, 4))
            pygame.display.set_icon(icon)
        except pygame.error:
            pass
        self.clock = pygame.time.Clock()
        self.dt = 0.016
        self.fps = 0.0
        self.state = "title"
        self.sim: Simulation | None = None
        self.view: WorldView | None = None
        self.runner = FixedStepRunner(cfg.tick_rate, cfg.max_ticks_per_frame, cfg.frame_sim_budget_ms)
        self.overlay = None              # None | map | chronicle | journal | help
        self.chron_state = {"mode": 0, "sel": 0}
        self.map_hover = None
        self.map_hit = {}
        self.show_profiler = False
        self.zoom_i = ZOOMS.index(cfg.tile_size) if cfg.tile_size in ZOOMS else 2
        self.last_autosave = time.time()
        self.menu_i = 0
        self.message = ""
        self.report = None
        self.audio = None
        self.frame = 0
        self._region_seen = None
        self.title_t = 0.0
        if cfg.audio and not screenshot:
            from ..audio.synth import Audio
            self.audio = Audio(True, cfg.volume)
        if new_world:
            self.new_world(seed)
        elif screenshot:
            if not self.try_continue():
                self.new_world(seed)

    # ------------------------------------------------------------------ lifecycle
    def new_world(self, seed: int | None = None) -> None:
        cfg = self.cfg
        cfg.seed = seed if seed is not None else random.randrange(1, 2 ** 31)
        self._splash("Generating a new world...")
        self.sim = Simulation.new(cfg)
        self._start_game()
        self.sim.world.player.note(0, "You are the Wayfinder. Explore, observe, and decide what kind of presence to be. (F1 for help)")
        self.save(quiet=True)

    def try_continue(self) -> bool:
        path = self.cfg.save_path
        if not path.exists() and not path.with_suffix(".db.bak").exists():
            return False
        self._splash("Loading world... (simulating the time you were away)")
        try:
            sim, report = savegame.load(path, self.cfg)
        except savegame.LoadError as exc:
            self.message = f"Could not load save: {exc}"
            log.error(self.message)
            return False
        self.sim = sim
        self.report = report
        self._start_game()
        if report.get("offline_ticks"):
            imp = [e for e in report["offline_events"] if e.importance >= 2]
            self.sim.world.player.note(self.sim.world.clock.tick,
                                       f"While you were away {report['offline_ticks'] // 60} hours passed: "
                                       f"{len(report['offline_events'])} events, {len(imp)} notable. (J for the chronicle)")
        if report.get("used_backup"):
            self.sim.world.player.note(self.sim.world.clock.tick, "The main save was damaged; restored from backup.")
        return True

    def _start_game(self) -> None:
        w, h = self.screen.get_size()
        self.view = WorldView(self.sim.world.content, w, h, ZOOMS[self.zoom_i])
        self.view.debug = self.cfg.debug_overlay
        p = self.sim.world.player
        self.view.cam.snap(p.x, p.y, self.sim.world.regions[p.region_id])
        self.state = "game"
        self.overlay = None
        self.runner.paused = False
        self.last_autosave = time.time()
        sub = self.sim.world.bus.subscribe
        sub("attack", self._on_attack)
        sub("birth", self._on_near_sound("chirp_small"))
        sub("structure_built", lambda e: self._play("beacon"))
        sub("structure_destroyed", lambda e: self._play("glitch"))
        sub("player_hit", lambda e: self._play("hit"))
        sub("player_warned", lambda e: self._play("warn"))
        sub("anomaly_emerged", lambda e: self._play("glitch", 0.6))
        self.sim.world.history.listeners.append(self._on_history)

    def _splash(self, msg: str) -> None:
        self.screen.fill((6, 6, 14))
        w, h = self.screen.get_size()
        ui.text(self.screen, "DIGITAL WORLD", (w // 2, h // 2 - 30), 64, ui.ACC, center=True)
        ui.text(self.screen, msg, (w // 2, h // 2 + 30), 22, ui.DIM, center=True)
        pygame.display.flip()

    def save(self, quiet: bool = False) -> bool:
        if self.sim is None:
            return False
        try:
            savegame.save(self.sim, self.cfg.save_path)
            self.last_autosave = time.time()
            if not quiet:
                self._toast(f"World saved ({self.sim.world.clock.label()}).")
            return True
        except savegame.SaveError as exc:
            log.error("save failed: %s", exc)
            self._toast(f"SAVE FAILED: {exc}")
            return False

    # ------------------------------------------------------------------ helpers
    def _toast(self, msg: str) -> None:
        if self.sim is not None and self.sim.world.player is not None:
            self.sim.world.player.toasts.append([msg, 5.0])
        else:
            self.message = msg

    def _play(self, name, vol=1.0):
        if self.audio:
            self.audio.play(name, vol)

    def _near(self, x, y, rid) -> float:
        p = self.sim.world.player
        if rid != p.region_id:
            return 0.0
        d = math.hypot(x - p.x, y - p.y)
        return max(0.0, 1.0 - d / 18.0)

    def _on_attack(self, e) -> None:
        c = self.sim.world.creatures.get(e.data.get("target"))
        if c is not None:
            v = self._near(c.x, c.y, c.region_id)
            if v > 0:
                self._play("hit", v * 0.7)
                if self.view:
                    self.view.particles.emit(c.x, c.y, (255, 90, 90), 6, 2.0, 0.5)

    def _on_near_sound(self, name):
        def fn(e):
            c = self.sim.world.creatures.get(e.data.get("parent"))
            if c is not None:
                v = self._near(c.x, c.y, c.region_id)
                if v > 0:
                    self._play(name, v)
                    if self.view:
                        self.view.particles.emit(c.x, c.y, (255, 160, 220), 8, 1.2, 1.0)
        return fn

    def _on_history(self, entry) -> None:
        p = self.sim.world.player
        if entry.region_id == p.region_id and entry.importance >= 2 and entry.kind not in ("player_discovery",):
            p.toasts.append([entry.text, 6.0])
        if entry.kind in ("player_discovery", "species_discovered", "anomaly_discovered", "player_bond"):
            self._play("discover")

    # ------------------------------------------------------------------ main loop
    def run(self) -> None:
        running = True
        while running:
            self.dt = min(0.1, self.clock.tick(60) / 1000.0)
            self.fps = self.clock.get_fps()
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    running = False
                elif ev.type == pygame.VIDEORESIZE and not self.cfg.fullscreen:
                    self.screen = pygame.display.set_mode((ev.w, ev.h), pygame.RESIZABLE)
                    if self.view:
                        self.view.resize(ev.w, ev.h)
                else:
                    r = self.handle(ev)
                    if r == "quit":
                        running = False
            if self.script:
                self._run_script()
            if self.state == "game":
                self.update_game()
                self.draw_game()
            elif self.state == "title":
                self.draw_title()
            elif self.state == "menu":
                self.draw_game(update_view=False)
                self.draw_menu()
            pygame.display.flip()
            self.frame += 1
            if self.screenshot and self.frame >= self.max_frames:
                pygame.image.save(self.screen, self.screenshot)
                log.info("screenshot written to %s", self.screenshot)
                running = False
        if self.sim is not None and not self.screenshot:
            self.save(quiet=True)
        pygame.quit()

    def _run_script(self) -> None:
        """Deterministic input script for automated screenshots/tests:
        list of (frame, action, arg)."""
        while self.script and self.script[0][0] <= self.frame:
            _, action, arg = self.script.pop(0)
            if action == "key":
                for t in (pygame.KEYDOWN, pygame.KEYUP):
                    self.handle(pygame.event.Event(t, key=arg, mod=0, unicode=""))
            elif action == "select":
                if self.view and self.sim:
                    cs = self.sim.world.creatures_in(self.sim.world.player.region_id)
                    if cs:
                        p = self.sim.world.player
                        c = min(cs, key=lambda c: (c.x - p.x) ** 2 + (c.y - p.y) ** 2) if arg is None else \
                            next((c for c in cs if c.species_id == arg), cs[0])
                        self.view.selected = c.id
            elif action == "teleport_to":
                p = self.sim.world.player
                cs = [c for c in self.sim.world.creatures_in(p.region_id) if arg is None or c.species_id == arg]
                if cs:
                    c = cs[0]
                    p.x, p.y = c.x + 1.0, c.y
                    p.prev_x, p.prev_y = p.x, p.y
                    self.view.cam.snap(p.x, p.y, self.sim.world.regions[p.region_id])
            elif action == "run_ticks":
                self.sim.run(arg)
            elif action == "call":
                arg(self)

    # ------------------------------------------------------------------ input
    def handle(self, ev):
        if self.state == "title":
            return self.handle_title(ev)
        if self.state == "menu":
            return self.handle_menu(ev)
        if ev.type == pygame.KEYDOWN:
            k = ev.key
            if self.overlay == "chronicle":
                if k in (pygame.K_UP, pygame.K_w):
                    self.chron_state["sel"] = max(0, self.chron_state["sel"] - 1)
                    return
                if k in (pygame.K_DOWN, pygame.K_s):
                    self.chron_state["sel"] += 1
                    return
                if k == pygame.K_PAGEUP:
                    self.chron_state["sel"] = max(0, self.chron_state["sel"] - 20)
                    return
                if k == pygame.K_PAGEDOWN:
                    self.chron_state["sel"] += 20
                    return
                if k == pygame.K_TAB:
                    self.chron_state["mode"] += 1
                    self.chron_state["sel"] = 0
                    return
            if k == pygame.K_ESCAPE:
                if self.overlay:
                    self.overlay = None
                elif self.view and self.view.selected:
                    self.view.selected = None
                else:
                    self.state = "menu"
                    self.menu_i = 0
                return
            if k == pygame.K_SPACE:
                self.runner.paused = not self.runner.paused
            elif k in (pygame.K_RIGHTBRACKET, pygame.K_PLUS, pygame.K_KP_PLUS, pygame.K_EQUALS):
                self.runner.faster()
            elif k in (pygame.K_LEFTBRACKET, pygame.K_MINUS, pygame.K_KP_MINUS):
                self.runner.slower()
            elif k == pygame.K_m:
                self.overlay = None if self.overlay == "map" else "map"
            elif k == pygame.K_j:
                self.overlay = None if self.overlay == "chronicle" else "chronicle"
            elif k == pygame.K_k:
                self.overlay = None if self.overlay == "journal" else "journal"
            elif k in (pygame.K_F1, pygame.K_h):
                self.overlay = None if self.overlay == "help" else "help"
            elif k == pygame.K_TAB:
                self.view.debug = not self.view.debug
            elif k == pygame.K_F3:
                self.show_profiler = not self.show_profiler
            elif k == pygame.K_l:
                self.view.show_territories = not self.view.show_territories
            elif k == pygame.K_z:
                self.zoom_i = (self.zoom_i + 1) % len(ZOOMS)
                self.view.set_tile_size(ZOOMS[self.zoom_i])
            elif k == pygame.K_F5:
                self.save()
            elif k == pygame.K_F9:
                if self.try_continue():
                    self._toast("Save loaded.")
            elif self.overlay is None:
                self.command(k, ev.mod)
        elif ev.type == pygame.MOUSEWHEEL and self.view:
            self.zoom_i = max(0, min(len(ZOOMS) - 1, self.zoom_i + (1 if ev.y > 0 else -1)))
            self.view.set_tile_size(ZOOMS[self.zoom_i])
        elif ev.type == pygame.MOUSEBUTTONDOWN and self.view:
            if self.overlay == "map":
                for rid, rect in self.map_hit.items():
                    if rect.collidepoint(ev.pos):
                        self.map_hover = rid
                return
            if ev.button == 1:
                self.select_at(ev.pos)
            elif ev.button == 3:
                self.view.selected = None

    def select_at(self, pos) -> None:
        wx, wy = self.view.cam.to_world(*pos)
        world = self.sim.world
        best, bd = None, 1.5
        for c in world.creatures_in(world.player.region_id):
            d = math.hypot(c.x - wx, c.y - wy)
            if d < bd:
                best, bd = c, d
        self.view.selected = best.id if best else None
        if best:
            self._play("blip", 0.6)

    def command(self, k, mod) -> None:
        sim = self.sim
        msg = None
        sound = None
        if k == pygame.K_f:
            msg, sound = act.feed(sim), "feed"
        elif k == pygame.K_c:
            msg, sound = act.heal(sim), "heal"
        elif k == pygame.K_x:
            msg, sound = act.scare(sim), "scare"
        elif k == pygame.K_q:
            msg, sound = act.scan(sim), "blip"
        elif k == pygame.K_g:
            msg, sound = act.gather(sim), "blip"
        elif k == pygame.K_r:
            msg, sound = act.stabilize(sim), "stabilize"
            p = sim.world.player
            self.view.particles.emit(p.x, p.y, (120, 255, 220), 30, 4.0, 1.0, 3)
        elif k == pygame.K_b:
            msg = act.build_beacon(sim)
        elif k == pygame.K_v:
            msg, sound = act.dismantle(sim), "glitch"
        elif k == pygame.K_n:
            self._splash("Resting... the world moves on without you.")
            msg = act.wait(sim, 6)
            self.view.cam.snap(sim.world.player.x, sim.world.player.y, sim.world.regions[sim.world.player.region_id])
        if msg:
            self._toast(msg)
            if sound:
                self._play(sound)

    def handle_title(self, ev):
        has_save = self.cfg.save_path.exists() or self.cfg.save_path.with_suffix(".db.bak").exists()
        items = (["Continue"] if has_save else []) + ["New World", "Quit"]
        if ev.type == pygame.KEYDOWN:
            if ev.key in (pygame.K_UP, pygame.K_w):
                self.menu_i = (self.menu_i - 1) % len(items)
            elif ev.key in (pygame.K_DOWN, pygame.K_s):
                self.menu_i = (self.menu_i + 1) % len(items)
            elif ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                return self._title_choose(items[self.menu_i])
            elif ev.key == pygame.K_ESCAPE:
                return "quit"
        elif ev.type == pygame.MOUSEBUTTONDOWN:
            w, h = self.screen.get_size()
            for i, it in enumerate(items):
                r = pygame.Rect(w // 2 - 120, h // 2 + 20 + i * 44, 240, 36)
                if r.collidepoint(ev.pos):
                    return self._title_choose(it)
        self.title_items = items

    def _title_choose(self, item):
        if item == "Continue":
            if not self.try_continue():
                self.state = "title"
        elif item == "New World":
            self.new_world()
        elif item == "Quit":
            return "quit"

    def handle_menu(self, ev):
        items = ["Resume", "Save", "Save & return to title", "Save & quit"]
        if ev.type == pygame.KEYDOWN:
            if ev.key in (pygame.K_UP, pygame.K_w):
                self.menu_i = (self.menu_i - 1) % len(items)
            elif ev.key in (pygame.K_DOWN, pygame.K_s):
                self.menu_i = (self.menu_i + 1) % len(items)
            elif ev.key == pygame.K_ESCAPE:
                self.state = "game"
            elif ev.key in (pygame.K_RETURN, pygame.K_SPACE):
                return self._menu_choose(items[self.menu_i])
        elif ev.type == pygame.MOUSEBUTTONDOWN:
            w, h = self.screen.get_size()
            for i, it in enumerate(items):
                r = pygame.Rect(w // 2 - 160, h // 2 - 60 + i * 44, 320, 36)
                if r.collidepoint(ev.pos):
                    return self._menu_choose(it)

    def _menu_choose(self, item):
        if item == "Resume":
            self.state = "game"
        elif item == "Save":
            self.save()
            self.state = "game"
        elif item.startswith("Save & return"):
            self.save()
            self.sim = None
            self.state = "title"
            self.menu_i = 0
        elif item == "Save & quit":
            self.save()
            self.sim = None
            return "quit"

    # ------------------------------------------------------------------ update/draw
    def update_game(self) -> None:
        sim = self.sim
        keys = pygame.key.get_pressed()
        if self.overlay is None:
            vx = (keys[pygame.K_d] or keys[pygame.K_RIGHT]) - (keys[pygame.K_a] or keys[pygame.K_LEFT])
            vy = (keys[pygame.K_s] or keys[pygame.K_DOWN]) - (keys[pygame.K_w] or keys[pygame.K_UP])
            p = sim.world.player
            p.prev_x, p.prev_y = p.x, p.y
            if vx or vy:
                rid = p.region_id
                act.move_player(sim, vx, vy, self.dt, keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT])
                if p.region_id != rid:
                    self.view.cam.snap(p.x, p.y, sim.world.regions[p.region_id])
                    self.view.selected = None
        try:
            self.runner.run(self.dt, sim.step)
        except Exception:
            log.exception("simulation step failed")
            self._toast("A simulation error occurred (see log); continuing.")
        if time.time() - self.last_autosave > self.cfg.autosave_minutes * 60:
            self.save(quiet=True)
        region = sim.world.regions[sim.world.player.region_id]
        if self.audio:
            self.audio.ensure_ambient(sim.world.biome(region))
            self.audio.corruption_level(region.corruption_at(sim.world.player.x, sim.world.player.y))

    def draw_game(self, update_view: bool = True) -> None:
        s = self.screen
        sim = self.sim
        if sim is None:
            return
        self.view.draw(s, sim, self.runner.alpha if update_view else 1.0, self.dt if update_view else 0.0)
        ui.draw_hud(s, sim, self.runner, self.fps, self)
        if self.view.selected is not None:
            if not ui.draw_inspector(s, sim, self.view.selected):
                self.view.selected = None
        if self.show_profiler:
            ui.draw_profiler(s, sim, self.runner, self.fps)
        if self.overlay == "map":
            self.map_hit = ui.draw_world_map(s, sim, self.map_hover)
        elif self.overlay == "chronicle":
            ui.draw_chronicle(s, sim, self.chron_state)
        elif self.overlay == "journal":
            ui.draw_journal(s, sim)
        elif self.overlay == "help":
            ui.draw_help(s)

    def draw_title(self) -> None:
        s = self.screen
        w, h = s.get_size()
        self.title_t += self.dt
        s.fill((6, 6, 16))
        rng = random.Random(int(self.title_t * 8))
        for i in range(70):
            x = (i * 97 + self.title_t * (20 + i % 7 * 6)) % w
            y = (i * 53) % h
            pygame.draw.rect(s, (40 + i % 5 * 20, 30, 90 + i % 3 * 50), (x, y, 2 + i % 3, 2))
        title = "DIGITAL WORLD"
        for dx, col in ((-3, (255, 40, 200)), (3, (40, 255, 230)), (0, (235, 240, 255))):
            jit = rng.randint(-2, 2) if rng.random() < 0.15 else 0
            ui.text(s, title, (w // 2 + dx + jit, h // 2 - 120), 96, col, center=True)
        ui.text(s, "a persistent world of autonomous digital creatures", (w // 2, h // 2 - 50), 24, ui.DIM, center=True)
        has_save = self.cfg.save_path.exists() or self.cfg.save_path.with_suffix(".db.bak").exists()
        items = (["Continue"] if has_save else []) + ["New World", "Quit"]
        self.menu_i %= len(items)
        for i, it in enumerate(items):
            r = pygame.Rect(w // 2 - 120, h // 2 + 20 + i * 44, 240, 36)
            ui.panel(s, r, 220, ui.ACC if i == self.menu_i else (60, 80, 110))
            ui.text(s, it, r.center, 26, ui.FG if i == self.menu_i else ui.DIM, center=True)
        if self.message:
            ui.text(s, self.message, (w // 2, h - 60), 18, ui.WARN, center=True)
        ui.text(s, f"saves: {self.cfg.saves_dir}", (10, h - 22), 14, ui.DIM)

    def draw_menu(self) -> None:
        s = self.screen
        w, h = s.get_size()
        ui.panel(s, (w // 2 - 200, h // 2 - 110, 400, 260), 235)
        ui.text(s, "PAUSED", (w // 2, h // 2 - 90), 34, ui.ACC, center=True)
        items = ["Resume", "Save", "Save & return to title", "Save & quit"]
        for i, it in enumerate(items):
            r = pygame.Rect(w // 2 - 160, h // 2 - 60 + i * 44, 320, 36)
            ui.panel(s, r, 220, ui.ACC if i == self.menu_i else (60, 80, 110))
            ui.text(s, it, r.center, 22, ui.FG if i == self.menu_i else ui.DIM, center=True)
