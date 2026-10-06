"""Runtime configuration.

Values can be overridden by a JSON file (``config.json`` next to the save
directory or passed with ``--config``). Unknown keys are ignored so old
config files never break newer builds.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict, fields
from pathlib import Path


def default_data_dir() -> Path:
    """Per-user writable directory (saves, logs). Windows: %APPDATA%."""
    env = os.environ.get("DIGITALWORLD_HOME")
    if env:
        return Path(env)
    if os.name == "nt" and os.environ.get("APPDATA"):
        return Path(os.environ["APPDATA"]) / "DigitalWorld"
    return Path.home() / ".digitalworld"


@dataclass
class Config:
    # world generation
    seed: int = 1337
    world_width: int = 5          # regions horizontally
    world_height: int = 4         # regions vertically
    region_width: int = 64        # tiles
    region_height: int = 48       # tiles

    # simulation
    tick_rate: float = 10.0       # logical ticks per real second at 1x
    max_ticks_per_frame: int = 64 # protects the frame from spiralling catch-up
    frame_sim_budget_ms: float = 28.0
    decide_interval: int = 6      # ticks between routine re-decisions
    path_budget_per_tick: int = 14
    active_radius: int = 1        # regions around the player simulated in detail
    abstract_step_ticks: int = 60 # granularity of dormant region simulation
    dormant_refresh_ticks: int = 120  # how often one dormant region is advanced
    offline_progress: bool = True
    # 0 = time keeps flowing while the game is closed at the same pace as at 1x
    offline_ticks_per_real_second: float = 0.0
    offline_max_ticks: int = 1440 * 7

    # persistence
    data_dir: str = ""
    save_name: str = "world"
    autosave_minutes: float = 4.0

    # presentation
    window_width: int = 1280
    window_height: int = 720
    fullscreen: bool = False
    tile_size: int = 20
    audio: bool = True
    volume: float = 0.5
    show_fps: bool = True

    # diagnostics
    log_level: str = "INFO"
    log_to_file: bool = True
    debug_overlay: bool = False
    decision_trace: int = 12      # decisions kept per creature for inspection

    extra: dict = field(default_factory=dict)

    @property
    def data_path(self) -> Path:
        return Path(self.data_dir) if self.data_dir else default_data_dir()

    @property
    def saves_dir(self) -> Path:
        return self.data_path / "saves"

    @property
    def logs_dir(self) -> Path:
        return self.data_path / "logs"

    @property
    def save_path(self) -> Path:
        return self.saves_dir / f"{self.save_name}.db"

    @classmethod
    def load(cls, path: str | os.PathLike | None = None, **overrides) -> "Config":
        cfg = cls()
        candidates = [Path(path)] if path else [cfg.data_path / "config.json"]
        for p in candidates:
            if p.is_file():
                try:
                    raw = json.loads(p.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    raw = {}
                cfg.apply(raw)
        cfg.apply(overrides)
        return cfg

    def apply(self, values: dict) -> None:
        names = {f.name: f for f in fields(self)}
        for k, v in values.items():
            if k in names and v is not None:
                setattr(self, k, v)

    def to_dict(self) -> dict:
        return asdict(self)
