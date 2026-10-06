"""Loads and validates the data-driven content (species, biomes, resources).

New species or biomes are added by editing the JSON files in ``content/data``;
no code change is needed unless a new ability keyword is introduced.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"

TROPHIC = {"herbivore", "predator", "omnivore", "scavenger"}
SOCIAL = {"solitary", "herd", "pack"}
ACTIVITY = {"diurnal", "nocturnal", "any"}
KNOWN_ABILITIES = {"swim", "mob", "asexual", "emit_corruption", "break_structures", "build", "stabilize"}


@dataclass(frozen=True)
class ResourceKind:
    id: str
    name: str
    capacity: float
    regen: float
    corruption: float     # >0 thrives on corruption, <0 suffers from it
    season: float
    rain: float
    feeders: int
    food: bool
    color: tuple


@dataclass(frozen=True)
class Biome:
    id: str
    name: str
    kind: str
    description: str
    terrain: dict
    resources: dict
    fertility: float
    rain: float
    base_corruption: float
    resistance: float
    palette: dict
    ambience: dict


@dataclass(frozen=True)
class Species:
    id: str
    name: str
    trophic: str
    description: str
    shape: str
    color: tuple
    accent: tuple
    size: float
    speed: float
    perception: float
    diet: dict
    prey: tuple
    appetite: float
    drink: float
    meat_value: float
    rates: dict
    health: float
    attack: float
    defense: float
    lifespan_days: float
    maturity_days: float
    repro_cooldown_days: float
    litter: tuple
    social: str
    group_size: tuple
    territorial: bool
    territory_radius: float
    habitat: dict
    corruption_tolerance: float
    corruption_affinity: float
    activity: str
    abilities: frozenset
    player: dict
    temperament: dict = field(default_factory=dict)

    def has(self, ability: str) -> bool:
        return ability in self.abilities

    @property
    def is_predator(self) -> bool:
        return bool(self.prey)

    def eats(self, kind: str) -> float:
        return float(self.diet.get(kind, 0.0))

    def habitat_score(self, biome_id: str) -> float:
        return float(self.habitat.get(biome_id, 0.0))


class ContentError(ValueError):
    pass


def _load_json(name: str) -> dict:
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return json.load(f)


class Content:
    def __init__(self, species: dict, biomes: dict, resources: dict):
        self.resources: dict[str, ResourceKind] = {}
        for rid, r in resources.items():
            self.resources[rid] = ResourceKind(rid, r["name"], float(r["capacity"]), float(r["regen"]),
                                               float(r["corruption"]), float(r.get("season", 0)),
                                               float(r.get("rain", 0)), int(r.get("feeders", 2)),
                                               bool(r.get("food", False)), tuple(r.get("color", (200, 200, 200))))
        self.biomes: dict[str, Biome] = {}
        for bid, b in biomes.items():
            for k in b["resources"]:
                if k not in self.resources:
                    raise ContentError(f"biome {bid}: unknown resource {k}")
            self.biomes[bid] = Biome(bid, b["name"], b["kind"], b.get("description", ""), b["terrain"],
                                     dict(b["resources"]), float(b["fertility"]), float(b["rain"]),
                                     float(b["base_corruption"]), float(b["resistance"]),
                                     {k: tuple(v) for k, v in b["palette"].items()}, dict(b.get("ambience", {})))
        self.species: dict[str, Species] = {}
        for sid, s in species.items():
            self.species[sid] = self._make_species(sid, s)
        for sp in self.species.values():
            for p in sp.prey:
                if p not in self.species:
                    raise ContentError(f"species {sp.id}: unknown prey {p}")

    def _make_species(self, sid: str, s: dict) -> Species:
        if s["trophic"] not in TROPHIC:
            raise ContentError(f"{sid}: bad trophic {s['trophic']}")
        if s["social"] not in SOCIAL:
            raise ContentError(f"{sid}: bad social {s['social']}")
        if s["activity"] not in ACTIVITY:
            raise ContentError(f"{sid}: bad activity")
        for k in s["diet"]:
            if k not in self.resources:
                raise ContentError(f"{sid}: unknown diet item {k}")
        for b in s["habitat"]:
            if b not in self.biomes:
                raise ContentError(f"{sid}: unknown biome {b}")
        unknown = set(s.get("abilities", [])) - KNOWN_ABILITIES
        if unknown:
            raise ContentError(f"{sid}: unknown abilities {unknown}")
        temperament = {"boldness": 0.5, "sociability": 0.5, "curiosity": 0.5, "aggression": 0.3,
                       "wanderlust": 0.4}
        temperament.update(s.get("temperament", {}))
        return Species(
            id=sid, name=s["name"], trophic=s["trophic"], description=s.get("description", ""),
            shape=s.get("shape", "blob"), color=tuple(s["color"]), accent=tuple(s["accent"]),
            size=float(s["size"]), speed=float(s["speed"]), perception=float(s["perception"]),
            diet=dict(s["diet"]), prey=tuple(s.get("prey", [])), appetite=float(s["appetite"]),
            drink=float(s["drink"]), meat_value=float(s["meat_value"]), rates=dict(s["rates"]),
            health=float(s["health"]), attack=float(s["attack"]), defense=float(s["defense"]),
            lifespan_days=float(s["lifespan_days"]), maturity_days=float(s["maturity_days"]),
            repro_cooldown_days=float(s["repro_cooldown_days"]), litter=tuple(s["litter"]),
            social=s["social"], group_size=tuple(s["group_size"]), territorial=bool(s["territorial"]),
            territory_radius=float(s["territory_radius"]), habitat=dict(s["habitat"]),
            corruption_tolerance=float(s["corruption_tolerance"]),
            corruption_affinity=float(s["corruption_affinity"]), activity=s["activity"],
            abilities=frozenset(s.get("abilities", [])), player=dict(s.get("player", {})),
            temperament=temperament)

    def predators_of(self, species_id: str) -> list[str]:
        return [s.id for s in self.species.values() if species_id in s.prey]

    def food_kinds(self) -> list[str]:
        return [k for k, r in self.resources.items() if r.food]


@lru_cache(maxsize=1)
def load_content() -> Content:
    return Content(_load_json("species.json"), _load_json("biomes.json"), _load_json("resources.json"))
