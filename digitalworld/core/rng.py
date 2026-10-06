"""Deterministic randomness.

Python's ``hash()`` of strings is salted per process, so derived seeds use
CRC32 to stay reproducible across runs and machines.
"""

from __future__ import annotations

import random
import zlib


def derive_seed(seed: int, *keys) -> int:
    text = ":".join([str(seed)] + [str(k) for k in keys])
    return zlib.crc32(text.encode("utf-8")) & 0x7FFFFFFF


def make_rng(seed: int, *keys) -> random.Random:
    return random.Random(derive_seed(seed, *keys))
