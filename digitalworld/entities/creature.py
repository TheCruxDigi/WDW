"""Creature: per-individual logical state. Species-wide data lives in
``content.registry.Species``; nothing here references rendering."""

from __future__ import annotations

import random
from collections import deque
from dataclasses import dataclass, field

from ..core.clock import TICKS_PER_DAY
from .memory import MemoryStore, Relationship, PLAYER_ID

NEED_NAMES = ("hunger", "thirst", "fatigue", "insecurity", "loneliness", "curiosity", "territory")
TRAIT_NAMES = ("boldness", "sociability", "curiosity", "aggression", "wanderlust")


@dataclass
class Decision:
    tick: int
    action: str
    score: float
    reason: str
    top: list  # [(action, score, reason)]


class Creature:
    MAX_RELATIONS = 28
    MAX_KNOWN = 24

    def __init__(self, cid: int, species_id: str, name: str, region_id: int, x: float, y: float,
                 born_tick: int = 0, sex: str = "f"):
        self.id = cid
        self.species_id = species_id
        self.name = name
        self.region_id = region_id
        self.x, self.y = float(x), float(y)
        self.prev_x, self.prev_y = self.x, self.y
        self.born_tick = born_tick
        self.sex = sex
        self.health = 1.0                 # fraction of species max health
        self.needs = {n: 0.0 for n in NEED_NAMES}
        self.traits = {t: 0.5 for t in TRAIT_NAMES}
        self.taint = 0.0                  # accumulated corruption exposure 0..1
        self.group_id: int | None = None
        self.memory = MemoryStore()
        self.relations: dict[int, Relationship] = {}
        self.known_nodes: dict[int, list] = {}   # node id -> [region, x, y, kind, frac, tick]
        self.alive = True
        self.death_cause = ""
        self.repro_ready_tick = 0
        self.last_migration_tick = -10 ** 9
        self.migration_from: int | None = None
        self.carried = 0.0                # material carried (builders)
        self.generation = 0
        self.parents: list[int] = []
        self.counters = {"kills": 0, "children": 0, "meals": 0, "migrations": 0, "fights": 0,
                         "helped": 0, "discoveries": 0}
        self.under_attack_by: int | None = None
        self.under_attack_tick = -10 ** 9
        # runtime only (rebuilt after load)
        self.action = None
        self.action_name = "idle"
        self.action_started = 0
        self.next_decide_tick = 0
        self.decisions: deque[Decision] = deque(maxlen=12)
        self.blacklist: dict[str, int] = {}
        self.path: list[tuple[int, int]] = []
        self.path_goal: tuple[int, int] | None = None
        self.sleeping = False
        self.status_icon = ""
        self.last_scores: list = []
        self.migrate_target: int | None = None
        self.migrate_reason = ""
        self.mig_urgency = 0.0
        self.mig_factors: dict = {}
        self.mig_eval_tick = -10 ** 9
        self.search_fail = 0
        self.percept_cache = None

    # ---- derived -------------------------------------------------------
    def age_days(self, now: int) -> float:
        return (now - self.born_tick) / TICKS_PER_DAY

    def is_adult(self, now: int, species) -> bool:
        return self.age_days(now) >= species.maturity_days

    def dist2(self, x: float, y: float) -> float:
        return (self.x - x) ** 2 + (self.y - y) ** 2

    @property
    def tile(self) -> tuple[int, int]:
        return int(self.x), int(self.y)

    def relation(self, other_id: int, species: str = "", create: bool = True) -> Relationship | None:
        r = self.relations.get(other_id)
        if r is None and create:
            r = Relationship(species)
            self.relations[other_id] = r
            if len(self.relations) > self.MAX_RELATIONS:
                self._prune_relations()
                r = self.relations.get(other_id) or r
                self.relations[other_id] = r
        return r

    def _prune_relations(self) -> None:
        keep = sorted(self.relations.items(), key=lambda kv: (kv[0] == PLAYER_ID, kv[1].weight),
                      reverse=True)[: self.MAX_RELATIONS - 1]
        self.relations = dict(keep)

    def remember_node(self, node, region_id: int, now: int) -> None:
        self.known_nodes[node.id] = [region_id, node.x, node.y, node.kind, round(node.fullness, 3), now]
        if len(self.known_nodes) > self.MAX_KNOWN:
            oldest = min(self.known_nodes.items(), key=lambda kv: kv[1][5])[0]
            del self.known_nodes[oldest]

    def blacklisted(self, key: str, now: int) -> bool:
        t = self.blacklist.get(key)
        if t is None:
            return False
        if t <= now:
            del self.blacklist[key]
            return False
        return True

    def ban(self, key: str, now: int, duration: int = 240) -> None:
        self.blacklist[key] = now + duration
        if len(self.blacklist) > 40:
            for k in sorted(self.blacklist, key=self.blacklist.get)[:10]:
                del self.blacklist[k]

    def reset_runtime(self) -> None:
        self.action = None
        self.action_name = "idle"
        self.path = []
        self.path_goal = None
        self.sleeping = False
        self.status_icon = ""
        self.percept_cache = None
        self.prev_x, self.prev_y = self.x, self.y

    # ---- persistence ---------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "id": self.id, "species": self.species_id, "name": self.name, "region_id": self.region_id,
            "x": self.x, "y": self.y, "born_tick": self.born_tick, "sex": self.sex,
            "health": self.health, "needs": dict(self.needs),
            "traits": dict(self.traits), "taint": self.taint,
            "group_id": self.group_id, "memory": self.memory.to_list(),
            "relations": {str(k): r.to_dict() for k, r in self.relations.items()},
            "known_nodes": {str(k): v for k, v in self.known_nodes.items()},
            "repro_ready_tick": self.repro_ready_tick, "last_migration_tick": self.last_migration_tick,
            "migration_from": self.migration_from, "carried": self.carried,
            "generation": self.generation, "parents": list(self.parents),
            "counters": dict(self.counters), "last_action": self.action_name,
            "migrate_target": self.migrate_target, "migrate_reason": self.migrate_reason,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Creature":
        c = cls(int(d["id"]), d["species"], d.get("name", "?"), int(d["region_id"]),
                float(d.get("x", 1)), float(d.get("y", 1)), int(d.get("born_tick", 0)), d.get("sex", "f"))
        c.health = float(d.get("health", 1.0))
        for k, v in (d.get("needs") or {}).items():
            if k in c.needs:
                c.needs[k] = float(v)
        for k, v in (d.get("traits") or {}).items():
            if k in c.traits:
                c.traits[k] = float(v)
        c.taint = float(d.get("taint", 0.0))
        c.group_id = d.get("group_id")
        c.memory = MemoryStore.from_list(d.get("memory"))
        for k, v in (d.get("relations") or {}).items():
            try:
                c.relations[int(k)] = Relationship.from_dict(v)
            except (TypeError, ValueError):
                continue
        for k, v in (d.get("known_nodes") or {}).items():
            if isinstance(v, list) and len(v) >= 6:
                c.known_nodes[int(k)] = v
        c.repro_ready_tick = int(d.get("repro_ready_tick", 0))
        c.last_migration_tick = int(d.get("last_migration_tick", -10 ** 9))
        c.migration_from = d.get("migration_from")
        c.carried = float(d.get("carried", 0.0))
        c.generation = int(d.get("generation", 0))
        c.parents = list(d.get("parents", []))
        c.counters.update(d.get("counters") or {})
        c.action_name = d.get("last_action", "idle")
        c.migrate_target = d.get("migrate_target")
        c.migrate_reason = d.get("migrate_reason", "")
        return c


def roll_traits(rng: random.Random, temperament: dict, parents: list["Creature"] | None = None) -> dict:
    out = {}
    for t in TRAIT_NAMES:
        base = temperament.get(t, 0.5)
        if parents:
            base = 0.5 * base + 0.5 * sum(p.traits.get(t, base) for p in parents) / len(parents)
        out[t] = max(0.0, min(1.0, rng.gauss(base, 0.12)))
    return out


@dataclass
class Group:
    id: int
    species_id: str
    kind: str
    name: str
    leader_id: int | None = None
    members: list = field(default_factory=list)
    region_id: int | None = None
    founded_tick: int = 0
    territory_id: int | None = None
    migrate_to: int | None = None
    migrate_reason: str = ""
    migrate_tick: int = 0

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if not k.startswith("_")}
        d["members"] = list(self.members)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Group":
        g = cls(int(d["id"]), d["species_id"], d.get("kind", "herd"), d.get("name", "?"))
        g.leader_id = d.get("leader_id")
        g.members = [int(m) for m in d.get("members", [])]
        g.region_id = d.get("region_id")
        g.founded_tick = int(d.get("founded_tick", 0))
        g.territory_id = d.get("territory_id")
        g.migrate_to = d.get("migrate_to")
        g.migrate_reason = d.get("migrate_reason", "")
        g.migrate_tick = int(d.get("migrate_tick", 0))
        return g
