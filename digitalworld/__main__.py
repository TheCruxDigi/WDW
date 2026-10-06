"""Entry point.

    python -m digitalworld                 # play (title screen)
    python -m digitalworld --new --seed 42 # start a fresh world immediately
    python -m digitalworld --headless --ticks 4320 --save   # simulate without a window
    python -m digitalworld --screenshot shot.png --frames 120
    python -m digitalworld.tools.inspect --help              # diagnostics
"""

from __future__ import annotations

import argparse
import os
import sys
import time


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="digitalworld", description="Digital World — persistent autonomous world")
    ap.add_argument("--config", help="path to a JSON config file")
    ap.add_argument("--data-dir", help="directory for saves and logs (default: per-user)")
    ap.add_argument("--save-name", help="save slot name (default: world)")
    ap.add_argument("--new", action="store_true", help="start a new world (overwrites the slot on first save)")
    ap.add_argument("--seed", type=int, help="world seed for --new / --headless")
    ap.add_argument("--headless", action="store_true", help="run the simulation without a window")
    ap.add_argument("--ticks", type=int, default=1440, help="ticks to simulate in headless mode (1 tick = 1 minute)")
    ap.add_argument("--save", action="store_true", help="headless: save at the end")
    ap.add_argument("--load", action="store_true", help="headless: continue the saved world instead of a new one")
    ap.add_argument("--screenshot", help="render N frames and write a PNG, then exit")
    ap.add_argument("--frames", type=int, default=90)
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--fullscreen", action="store_true")
    ap.add_argument("--log-level", default=None)
    return ap


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    from .config import Config
    from .core.logging_setup import setup_logging
    overrides = {}
    if args.data_dir:
        overrides["data_dir"] = args.data_dir
    if args.save_name:
        overrides["save_name"] = args.save_name
    if args.log_level:
        overrides["log_level"] = args.log_level
    if args.no_audio:
        overrides["audio"] = False
    if args.fullscreen:
        overrides["fullscreen"] = True
    cfg = Config.load(args.config, **overrides)
    setup_logging(cfg.log_level, cfg.logs_dir, cfg.log_to_file)
    if args.headless:
        return run_headless(cfg, args)
    if args.screenshot:
        os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    from .render.app import App
    app = App(cfg, new_world=args.new, seed=args.seed, screenshot=args.screenshot, frames=args.frames)
    app.run()
    return 0


def run_headless(cfg, args) -> int:
    from .persistence import savegame
    from .sim.simulation import Simulation
    from .core.clock import TICKS_PER_DAY
    if args.seed is not None:
        cfg.seed = args.seed
    t0 = time.perf_counter()
    if args.load:
        sim, report = savegame.load(cfg.save_path, cfg)
        print(f"loaded {report['path']} (offline ticks simulated: {report['offline_ticks']})")
    else:
        sim = Simulation.new(cfg)
        print(f"new world seed={cfg.seed}: {len(sim.world.regions)} regions, {sim.world.total_population()} creatures")
    start_hist = sim.world.history.next_id
    t1 = time.perf_counter()
    step = max(1, args.ticks // 10)
    for i in range(args.ticks):
        sim.step()
        if (i + 1) % step == 0:
            w = sim.world
            print(f"  {w.clock.label():>16}  loaded {len(w.creatures):4d}  world {w.total_population():4d}  "
                  f"{(time.perf_counter() - t1) / (i + 1) * 1000:.2f} ms/tick")
    dt = time.perf_counter() - t1
    w = sim.world
    print(f"simulated {args.ticks} ticks ({args.ticks / TICKS_PER_DAY:.1f} days) in {dt:.1f}s "
          f"= {dt / max(1, args.ticks) * 1000:.2f} ms/tick (setup {t1 - t0:.1f}s), errors: {sim.errors}")
    print(w.profiler.format())
    new = [e for e in w.history.entries if e.id >= start_hist and e.importance >= 2]
    print(f"\n{len(new)} notable history entries, latest:")
    for e in new[-15:]:
        print(f"  day {e.tick // TICKS_PER_DAY + 1}: {e.text}")
    if args.save:
        p = savegame.save(sim, cfg.save_path)
        print(f"saved to {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
