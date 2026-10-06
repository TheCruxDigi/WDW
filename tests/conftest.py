import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")

from digitalworld.config import Config  # noqa: E402
from digitalworld.sim.simulation import Simulation  # noqa: E402


def small_config(tmp_path=None, seed=11, **kw) -> Config:
    cfg = Config()
    cfg.apply(dict(seed=seed, world_width=3, world_height=2, region_width=40, region_height=32,
                   audio=False, log_to_file=False, offline_progress=False))
    if tmp_path is not None:
        cfg.data_dir = str(tmp_path)
    cfg.apply(kw)
    return cfg


@pytest.fixture
def cfg(tmp_path):
    return small_config(tmp_path)


@pytest.fixture
def sim(cfg):
    return Simulation.new(cfg)
