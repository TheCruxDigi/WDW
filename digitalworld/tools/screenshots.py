"""Drive the real application offscreen and capture screenshots.

    python -m digitalworld.tools.screenshots --out docs/screenshots --seed 7

Uses SDL's dummy video driver, so it works on machines without a display.
"""

from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/screenshots")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--warmup", type=int, default=900, help="ticks to simulate before capturing")
    args = ap.parse_args(argv)
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    import pygame
    from ..config import Config
    from ..render.app import App
    from ..player import interactions as act

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="dw_shots_")
    cfg = Config.load(None, data_dir=tmp, audio=False, seed=args.seed)
    app = App(cfg, new_world=True, seed=args.seed)
    sim = app.sim
    world = sim.world

    def frames(n=3):
        for _ in range(n):
            app.dt = 1 / 30
            app.update_game()
            app.draw_game()
            pygame.display.flip()

    def shot(name):
        frames(2)
        pygame.image.save(app.screen, str(out / f"{name}.png"))
        print("wrote", out / f"{name}.png")

    app.runner.paused = True
    sim.run(args.warmup)
    p = world.player
    # stand near a group of creatures and inspect one
    cs = world.creatures_in(p.region_id)
    if cs:
        target = max(cs, key=lambda c: sum(1 for o in cs if (o.x - c.x) ** 2 + (o.y - c.y) ** 2 < 64))
        p.x, p.y = target.x + 1.5, target.y + 0.5
        p.prev_x, p.prev_y = p.x, p.y
        app.view.cam.snap(p.x, p.y, world.regions[p.region_id])
        app.view.selected = target.id
        sim.brain.decide(target, world.species(target.species_id), world.regions[p.region_id])
    frames(10)
    shot("01_explore_inspector")
    app.view.selected = None
    app.view.show_territories = True
    app.view.debug = True
    shot("02_debug_territories")
    app.view.debug = False
    app.view.show_territories = False
    app.overlay = "map"
    app.map_hover = p.region_id
    for r in world.regions.values():
        r.discovered = True
    shot("03_world_map")
    app.overlay = "chronicle"
    app.chron_state = {"mode": 2, "sel": 0}
    shot("04_chronicle")
    app.overlay = None
    # visit the most corrupted region
    rift = max(world.regions.values(), key=lambda r: r.corruption_level)
    p.region_id = rift.id
    sim.update_lod()
    x, y = rift.nearest_walkable(rift.width / 2, rift.height / 2)
    p.x, p.y = x + 0.5, y + 0.5
    p.prev_x, p.prev_y = p.x, p.y
    act.enter_region(sim, rift.id)
    app.view.cam.snap(p.x, p.y, rift)
    sim.run(30)
    shot("05_corrupted_region")
    app.overlay = "journal"
    for sid in world.content.species:
        p.discovered_species.setdefault(sid, world.clock.tick)
    shot("06_journal")
    app.overlay = None
    app.show_profiler = True
    shot("07_profiler")
    pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
