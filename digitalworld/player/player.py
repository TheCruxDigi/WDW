"""The Wayfinder — the player's avatar. Pure logical state."""

from __future__ import annotations

from collections import deque


class Player:
    SPEED = 5.5          # tiles per real second (walking)
    RADIUS = 0.3

    def __init__(self, region_id: int, x: float, y: float):
        self.region_id = region_id
        self.x, self.y = float(x), float(y)
        self.prev_x, self.prev_y = self.x, self.y
        self.energy = 100.0
        self.integrity = 100.0
        self.inventory = {"seeds": 4, "shards": 0}
        self.discovered_species: dict[str, int] = {}
        self.discovered_regions: dict[int, int] = {}
        self.discovered_anomalies: dict[int, int] = {}
        self.scanned: dict[int, dict] = {}       # creature id -> {name, species, tick}
        self.journal: deque = deque(maxlen=300)  # (tick, text)
        self.stats = {"fed": 0, "healed": 0, "scared": 0, "gathered": 0, "beacons": 0,
                      "stabilized": 0, "dismantled": 0, "collapses": 0, "distance": 0.0}
        self.hidden = False
        self.facing = (1.0, 0.0)
        self.home = (region_id, x, y)
        # runtime
        self.toasts: deque = deque(maxlen=6)     # (text, ttl seconds)
        self.last_hit_tick = -999

    def note(self, tick: int, text: str, toast: bool = True) -> None:
        self.journal.append((tick, text))
        if toast:
            self.toasts.append([text, 5.0])

    def to_dict(self) -> dict:
        return {"region_id": self.region_id, "x": round(self.x, 3), "y": round(self.y, 3),
                "energy": round(self.energy, 2), "integrity": round(self.integrity, 2),
                "inventory": dict(self.inventory),
                "discovered_species": dict(self.discovered_species),
                "discovered_regions": {str(k): v for k, v in self.discovered_regions.items()},
                "discovered_anomalies": {str(k): v for k, v in self.discovered_anomalies.items()},
                "scanned": {str(k): v for k, v in self.scanned.items()},
                "journal": [list(j) for j in self.journal], "stats": dict(self.stats),
                "home": list(self.home)}

    @classmethod
    def from_dict(cls, d: dict) -> "Player":
        p = cls(int(d["region_id"]), float(d.get("x", 32)), float(d.get("y", 24)))
        p.energy = float(d.get("energy", 100))
        p.integrity = float(d.get("integrity", 100))
        p.inventory.update(d.get("inventory") or {})
        p.discovered_species = dict(d.get("discovered_species") or {})
        p.discovered_regions = {int(k): v for k, v in (d.get("discovered_regions") or {}).items()}
        p.discovered_anomalies = {int(k): v for k, v in (d.get("discovered_anomalies") or {}).items()}
        p.scanned = {int(k): v for k, v in (d.get("scanned") or {}).items()}
        for j in d.get("journal") or []:
            if isinstance(j, (list, tuple)) and len(j) == 2:
                p.journal.append((int(j[0]), str(j[1])))
        p.stats.update(d.get("stats") or {})
        h = d.get("home")
        if h and len(h) == 3:
            p.home = (int(h[0]), float(h[1]), float(h[2]))
        return p
