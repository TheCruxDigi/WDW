"""Logical time. One tick is one in-world minute; simulation never depends on FPS."""

from __future__ import annotations

import math
import time

TICKS_PER_HOUR = 60
TICKS_PER_DAY = 24 * TICKS_PER_HOUR
DAYS_PER_CYCLE = 12  # "bloom cycle" — the world's season


class WorldClock:
    def __init__(self, tick: int = 0):
        self.tick = tick

    def advance(self, n: int = 1) -> None:
        self.tick += n

    @property
    def day(self) -> int:
        return self.tick // TICKS_PER_DAY

    @property
    def minute_of_day(self) -> int:
        return self.tick % TICKS_PER_DAY

    @property
    def hour(self) -> float:
        return self.minute_of_day / TICKS_PER_HOUR

    def daylight(self, tick: int | None = None) -> float:
        """0 at midnight, 1 at noon, smooth."""
        t = (self.tick if tick is None else tick) % TICKS_PER_DAY
        return 0.5 - 0.5 * math.cos(2 * math.pi * t / TICKS_PER_DAY)

    @property
    def is_night(self) -> bool:
        return self.daylight() < 0.25

    def season(self, tick: int | None = None) -> float:
        """-1..1 bloom factor; positive = growth season."""
        t = self.tick if tick is None else tick
        return math.sin(2 * math.pi * t / (TICKS_PER_DAY * DAYS_PER_CYCLE))

    def label(self) -> str:
        h = int(self.hour)
        m = self.minute_of_day % 60
        return f"Day {self.day + 1}  {h:02d}:{m:02d}"


def format_ticks(ticks: int) -> str:
    d, rem = divmod(int(ticks), TICKS_PER_DAY)
    h, m = divmod(rem, TICKS_PER_HOUR)
    if d:
        return f"{d}d {h}h"
    if h:
        return f"{h}h {m}m"
    return f"{m}m"


class FixedStepRunner:
    """Turns real elapsed time into a bounded number of logical ticks.

    The accumulator is clamped, and a wall-clock budget per frame stops a slow
    simulation from freezing the window (the backlog is dropped, not queued
    forever — the "spiral of death" guard).
    """

    SPEEDS = (0.5, 1, 2, 4, 8, 16)

    def __init__(self, tick_rate: float = 10.0, max_ticks_per_frame: int = 64,
                 budget_ms: float = 28.0):
        self.tick_rate = tick_rate
        self.max_ticks_per_frame = max_ticks_per_frame
        self.budget_ms = budget_ms
        self.speed = 1.0
        self.paused = False
        self.accumulator = 0.0
        self.dropped_ticks = 0

    def faster(self) -> None:
        bigger = [s for s in self.SPEEDS if s > self.speed]
        if bigger:
            self.speed = bigger[0]

    def slower(self) -> None:
        smaller = [s for s in self.SPEEDS if s < self.speed]
        if smaller:
            self.speed = smaller[-1]

    def run(self, real_dt: float, step_fn) -> int:
        """Call ``step_fn()`` once per due tick. Returns ticks executed."""
        if self.paused:
            return 0
        self.accumulator += min(real_dt, 0.25) * self.tick_rate * self.speed
        due = int(self.accumulator)
        if due > self.max_ticks_per_frame:
            self.dropped_ticks += due - self.max_ticks_per_frame
            due = self.max_ticks_per_frame
            self.accumulator = float(due)
        start = time.perf_counter()
        done = 0
        while done < due:
            step_fn()
            done += 1
            self.accumulator -= 1.0
            if (time.perf_counter() - start) * 1000.0 > self.budget_ms:
                # Out of budget: drop the rest of the backlog.
                self.dropped_ticks += int(self.accumulator)
                self.accumulator = min(self.accumulator, 1.0)
                break
        return done

    @property
    def alpha(self) -> float:
        """Interpolation factor between the last two ticks for rendering."""
        return max(0.0, min(1.0, self.accumulator))
