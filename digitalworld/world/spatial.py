"""Uniform grid spatial hash for neighbourhood queries."""

from __future__ import annotations

from collections import defaultdict


class SpatialHash:
    def __init__(self, cell: float = 6.0):
        self.cell = cell
        self.cells: dict[tuple[int, int], list[int]] = defaultdict(list)
        self.pos: dict[int, tuple[float, float]] = {}

    def clear(self) -> None:
        self.cells.clear()
        self.pos.clear()

    def insert(self, oid: int, x: float, y: float) -> None:
        c = self.cell
        self.cells[(int(x // c), int(y // c))].append(oid)
        self.pos[oid] = (x, y)

    def query(self, x: float, y: float, r: float) -> list[int]:
        c = self.cell
        r2 = r * r
        out = []
        cx0, cx1 = int((x - r) // c), int((x + r) // c)
        cy0, cy1 = int((y - r) // c), int((y + r) // c)
        cells = self.cells
        pos = self.pos
        for cy in range(cy0, cy1 + 1):
            for cx in range(cx0, cx1 + 1):
                bucket = cells.get((cx, cy))
                if not bucket:
                    continue
                for oid in bucket:
                    px, py = pos[oid]
                    dx, dy = px - x, py - y
                    if dx * dx + dy * dy <= r2:
                        out.append(oid)
        return out
