"""Individual memory and relationships.

Only significant experiences are stored (never raw movement). Memories fade
with time; strongly emotional ones fade slower. Relationships summarise the
history with another individual (or with the player, id ``PLAYER_ID``) into
interpretable dimensions that the AI reads directly.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

PLAYER_ID = 0

# kind -> (default valence, base half-life in ticks)
MEMORY_KINDS = {
    "attacked_by": (-0.9, 4 * 1440),
    "attacked": (0.1, 1440),
    "helped_by": (0.8, 4 * 1440),
    "helped": (0.4, 2 * 1440),
    "fed_by_player": (0.8, 6 * 1440),
    "healed_by_player": (0.9, 6 * 1440),
    "scared_by_player": (-0.7, 5 * 1440),
    "lost_kin": (-0.8, 5 * 1440),
    "witnessed_kill": (-0.7, 2 * 1440),
    "displaced_by": (-0.5, 2 * 1440),
    "displaced": (0.2, 1440),
    "chased_off": (-0.5, 2 * 1440),
    "discovered": (0.5, 3 * 1440),
    "migrated": (0.1, 8 * 1440),
    "left_region": (-0.4, 6 * 1440),
    "hunt_failed": (-0.2, 720),
    "famine": (-0.6, 3 * 1440),
    "corruption_exposure": (-0.6, 3 * 1440),
    "born": (0.3, 20 * 1440),
    "became_leader": (0.6, 10 * 1440),
    "met_player": (0.2, 4 * 1440),
    "warned_player": (-0.1, 1440),
    "bonded": (0.7, 6 * 1440),
    "territory_lost": (-0.7, 4 * 1440),
    "territory_won": (0.6, 4 * 1440),
}


@dataclass
class MemoryRecord:
    tick: int
    kind: str
    subject: int | None = None      # another creature id, PLAYER_ID, or None
    region: int | None = None
    x: float = 0.0
    y: float = 0.0
    valence: float = 0.0
    intensity: float = 0.5
    detail: str = ""
    count: int = 1

    def salience(self, now: int) -> float:
        half = MEMORY_KINDS.get(self.kind, (0, 1440))[1] * (1.0 + abs(self.valence))
        age = max(0, now - self.tick)
        return self.intensity * math.exp(-0.693 * age / half) * (1 + 0.15 * min(self.count, 5))

    def to_list(self) -> list:
        return [self.tick, self.kind, self.subject, self.region, round(self.x, 2), round(self.y, 2),
                round(self.valence, 3), round(self.intensity, 3), self.detail, self.count]

    @classmethod
    def from_list(cls, v: list) -> "MemoryRecord":
        v = list(v) + [None] * (10 - len(v))
        return cls(int(v[0] or 0), str(v[1]), v[2], v[3], float(v[4] or 0), float(v[5] or 0),
                   float(v[6] or 0), float(v[7] if v[7] is not None else 0.5), v[8] or "", int(v[9] or 1))


class MemoryStore:
    CAPACITY = 40
    MERGE_WINDOW = 90

    def __init__(self, records: list[MemoryRecord] | None = None):
        self.records: list[MemoryRecord] = records or []

    def add(self, now: int, kind: str, subject=None, region=None, x=0.0, y=0.0,
            intensity: float = 0.6, detail: str = "", valence: float | None = None) -> MemoryRecord:
        if valence is None:
            valence = MEMORY_KINDS.get(kind, (0.0, 0))[0]
        for r in reversed(self.records[-8:]):
            if r.kind == kind and r.subject == subject and now - r.tick <= self.MERGE_WINDOW:
                r.count += 1
                r.tick = now
                r.intensity = min(1.0, max(r.intensity, intensity) + 0.05)
                if detail:
                    r.detail = detail
                return r
        rec = MemoryRecord(now, kind, subject, region, x, y, valence, min(1.0, intensity), detail)
        self.records.append(rec)
        if len(self.records) > self.CAPACITY:
            self.prune(now)
        return rec

    def prune(self, now: int) -> None:
        # Keep the most salient; life-defining kinds are sticky.
        sticky = {"born", "became_leader"}
        self.records.sort(key=lambda r: (r.kind in sticky, r.salience(now)), reverse=True)
        del self.records[self.CAPACITY:]
        self.records.sort(key=lambda r: r.tick)

    def recall(self, kind: str | None = None, subject=None, region=None, now: int | None = None,
               min_salience: float = 0.05) -> list[MemoryRecord]:
        out = []
        for r in self.records:
            if kind is not None and r.kind != kind:
                continue
            if subject is not None and r.subject != subject:
                continue
            if region is not None and r.region != region:
                continue
            if now is not None and r.salience(now) < min_salience:
                continue
            out.append(r)
        return out

    def danger_near(self, region: int, x: float, y: float, now: int, radius: float = 8.0) -> float:
        """How dangerous memory says this spot is (0..1)."""
        total = 0.0
        for r in self.records:
            if r.region != region or r.kind not in ("attacked_by", "witnessed_kill", "corruption_exposure"):
                continue
            d2 = (r.x - x) ** 2 + (r.y - y) ** 2
            if d2 <= radius * radius:
                total += r.salience(now)
        return min(1.0, total)

    def to_list(self) -> list:
        return [r.to_list() for r in self.records]

    @classmethod
    def from_list(cls, data) -> "MemoryStore":
        recs = []
        for v in data or []:
            try:
                recs.append(MemoryRecord.from_list(v))
            except (TypeError, ValueError, IndexError):
                continue  # tolerate damaged entries
        return cls(recs)


DIMENSIONS = ("trust", "affinity", "fear", "hostility", "familiarity", "rivalry")


class Relationship:
    __slots__ = DIMENSIONS + ("last_tick", "species")

    def __init__(self, species: str = "", **dims):
        for d in DIMENSIONS:
            setattr(self, d, float(dims.get(d, 0.0)))
        self.last_tick = int(dims.get("last_tick", 0))
        self.species = species

    def adjust(self, now: int, **deltas) -> None:
        for k, v in deltas.items():
            setattr(self, k, max(0.0, min(1.0, getattr(self, k) + v)))
        self.familiarity = min(1.0, self.familiarity + 0.02)
        self.last_tick = now

    def decay(self, dt: int) -> None:
        f = math.exp(-dt / (10 * 1440))
        for d in ("fear", "hostility", "rivalry"):
            setattr(self, d, getattr(self, d) * f)
        g = math.exp(-dt / (20 * 1440))
        for d in ("trust", "affinity", "familiarity"):
            setattr(self, d, getattr(self, d) * g)

    @property
    def weight(self) -> float:
        return self.familiarity + self.trust + self.affinity + self.fear + self.hostility + self.rivalry

    @property
    def disposition(self) -> float:
        """-1 (enemy) .. +1 (friend)."""
        return max(-1.0, min(1.0, (self.trust + self.affinity) * 0.5 - (self.hostility + self.fear * 0.5 + self.rivalry * 0.5)))

    def label(self) -> str:
        if self.hostility > 0.5:
            return "enemy"
        if self.fear > 0.5:
            return "feared"
        if self.rivalry > 0.4:
            return "rival"
        if self.trust > 0.5 and self.affinity > 0.4:
            return "friend"
        if self.affinity > 0.3:
            return "liked"
        if self.familiarity > 0.2:
            return "known"
        return "noticed"

    def to_dict(self) -> dict:
        d = {k: round(getattr(self, k), 4) for k in DIMENSIONS if getattr(self, k) > 0.0005}
        d["last_tick"] = self.last_tick
        d["species"] = self.species
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Relationship":
        d = dict(d)
        sp = d.pop("species", "")
        return cls(sp, **d)
