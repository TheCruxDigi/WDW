"""Configurable, size-bounded logging."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


def setup_logging(level: str = "INFO", log_dir: Path | None = None, to_file: bool = True,
                  console: bool = True) -> None:
    root = logging.getLogger("digitalworld")
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
    if console:
        sh = logging.StreamHandler()
        sh.setFormatter(fmt)
        sh.setLevel(logging.WARNING)
        root.addHandler(sh)
    if to_file and log_dir is not None:
        try:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(Path(log_dir) / "digitalworld.log",
                                                      maxBytes=2_000_000, backupCount=3,
                                                      encoding="utf-8")
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except OSError:
            root.warning("could not open log file in %s", log_dir)
    root.propagate = False
