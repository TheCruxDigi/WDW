"""Decoupled event bus and persistent history.

* Transient events (``EventBus.publish``) are delivered synchronously to
  subscribers and then forgotten — births, bites, attacks, etc.
* Historical entries (``History.record``) persist in the save, carry a stable
  id and may reference the ids of the entries that caused them, so causes and
  consequences can be reconstructed later.
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger("digitalworld.events")


@dataclass
class Event:
    kind: str
    tick: int
    data: dict = field(default_factory=dict)


class EventBus:
    def __init__(self):
        self._subs: dict[str, list[Callable[[Event], None]]] = defaultdict(list)
        self._any: list[Callable[[Event], None]] = []
        self.counts: dict[str, int] = defaultdict(int)
        self.recent: deque[Event] = deque(maxlen=200)

    def subscribe(self, kind: str, fn: Callable[[Event], None]) -> None:
        if kind == "*":
            self._any.append(fn)
        else:
            self._subs[kind].append(fn)

    def publish(self, kind: str, tick: int, /, **data) -> Event:
        ev = Event(kind, tick, data)
        self.counts[kind] += 1
        self.recent.append(ev)
        for fn in self._subs.get(kind, []) + self._any:
            try:
                fn(ev)
            except Exception:  # a broken listener must not break the world
                log.exception("event subscriber failed for %s", kind)
        return ev


# Importance: 1 = minor/local, 2 = notable, 3 = major world change.
@dataclass
class HistoryEntry:
    id: int
    tick: int
    kind: str
    text: str
    region_id: int | None = None
    importance: int = 1
    causes: list[int] = field(default_factory=list)
    data: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"id": self.id, "tick": self.tick, "kind": self.kind, "text": self.text,
                "region_id": self.region_id, "importance": self.importance,
                "causes": list(self.causes), "data": dict(self.data)}

    @classmethod
    def from_dict(cls, d: dict) -> "HistoryEntry":
        return cls(id=int(d["id"]), tick=int(d.get("tick", 0)), kind=d.get("kind", "unknown"),
                   text=d.get("text", ""), region_id=d.get("region_id"),
                   importance=int(d.get("importance", 1)), causes=list(d.get("causes", [])),
                   data=dict(d.get("data", {})))


class History:
    """Chronicle of the world. Kept fully in memory and persisted."""

    MAX_ENTRIES = 20000

    def __init__(self):
        self.entries: list[HistoryEntry] = []
        self.next_id = 1
        self.listeners: list[Callable[[HistoryEntry], None]] = []

    def record(self, tick: int, kind: str, text: str, /, region_id: int | None = None,
               importance: int = 1, causes: list[int] | None = None, **data) -> HistoryEntry:
        e = HistoryEntry(self.next_id, tick, kind, text, region_id, importance,
                         [c for c in (causes or []) if c], data)
        self.next_id += 1
        self.entries.append(e)
        if len(self.entries) > self.MAX_ENTRIES:
            # Forget the least important old entries first.
            keep = [x for x in self.entries[: len(self.entries) // 2] if x.importance >= 2]
            self.entries = keep + self.entries[len(self.entries) // 2:]
        log.info("[history d%d] %s", tick // 1440 + 1, text)
        for fn in self.listeners:
            try:
                fn(e)
            except Exception:
                log.exception("history listener failed")
        return e

    def get(self, entry_id: int) -> HistoryEntry | None:
        # entries are ordered by id; binary search
        lo, hi = 0, len(self.entries) - 1
        while lo <= hi:
            mid = (lo + hi) // 2
            e = self.entries[mid]
            if e.id == entry_id:
                return e
            if e.id < entry_id:
                lo = mid + 1
            else:
                hi = mid - 1
        return None

    def since(self, tick: int, region_id: int | None = None, min_importance: int = 1):
        return [e for e in self.entries if e.tick >= tick and e.importance >= min_importance
                and (region_id is None or e.region_id == region_id)]

    def latest(self, kind: str, region_id: int | None = None, within: int | None = None,
               now: int | None = None) -> HistoryEntry | None:
        # entries from dormant regions can arrive slightly out of tick order,
        # so scan a bounded window instead of stopping at the first old entry
        for e in reversed(self.entries[-1500:]):
            if e.kind != kind or (region_id is not None and e.region_id != region_id):
                continue
            if within is not None and now is not None and (now - e.tick > within or e.tick > now):
                continue
            return e
        return None

    def cause_chain(self, entry_id: int, depth: int = 6) -> list[HistoryEntry]:
        out, frontier, seen = [], [entry_id], set()
        while frontier and depth > 0:
            nxt = []
            for eid in frontier:
                if eid in seen:
                    continue
                seen.add(eid)
                e = self.get(eid)
                if e:
                    out.append(e)
                    nxt.extend(e.causes)
            frontier = nxt
            depth -= 1
        return out
