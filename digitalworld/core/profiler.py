"""Lightweight per-system timing used by the debug overlay and the bench tool."""

from __future__ import annotations

import time
from collections import defaultdict
from contextlib import contextmanager


class Profiler:
    def __init__(self, smoothing: float = 0.05):
        self.smoothing = smoothing
        self.avg_ms: dict[str, float] = defaultdict(float)
        self.total_ms: dict[str, float] = defaultdict(float)
        self.calls: dict[str, int] = defaultdict(int)
        self.max_ms: dict[str, float] = defaultdict(float)
        self.enabled = True

    @contextmanager
    def section(self, name: str):
        if not self.enabled:
            yield
            return
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.add(name, (time.perf_counter() - t0) * 1000.0)

    def add(self, name: str, ms: float) -> None:
        a = self.smoothing
        self.avg_ms[name] = self.avg_ms[name] * (1 - a) + ms * a if self.calls[name] else ms
        self.total_ms[name] += ms
        self.calls[name] += 1
        if ms > self.max_ms[name]:
            self.max_ms[name] = ms

    def report(self) -> list[tuple[str, float, float, int, float]]:
        """(name, avg_ms, total_ms, calls, max_ms) sorted by total cost."""
        rows = [(k, self.avg_ms[k], self.total_ms[k], self.calls[k], self.max_ms[k])
                for k in self.total_ms]
        rows.sort(key=lambda r: -r[2])
        return rows

    def reset(self) -> None:
        self.avg_ms.clear()
        self.total_ms.clear()
        self.calls.clear()
        self.max_ms.clear()

    def format(self) -> str:
        lines = [f"{'system':<22}{'avg ms':>9}{'max ms':>9}{'total ms':>11}{'calls':>8}"]
        for name, avg, tot, calls, mx in self.report():
            lines.append(f"{name:<22}{avg:>9.3f}{mx:>9.2f}{tot:>11.1f}{calls:>8}")
        return "\n".join(lines)
