"""Original procedural naming (creatures, groups, regions)."""

from __future__ import annotations

import random

_ON = ["ka", "vel", "mi", "ro", "sy", "tel", "or", "zu", "ne", "qua", "fen", "lu", "da", "ix", "bryn",
       "cor", "ae", "pim", "sol", "vex", "ha", "nim", "os", "ri", "tam", "ul", "wen", "yo", "zel"]
_NU = ["a", "e", "i", "o", "u", "ae", "io", "y"]
_CO = ["n", "x", "l", "r", "th", "s", "k", "m", "sh", "v", "nd", "rk", ""]

_GROUP_ADJ = ["Ashen", "Lattice", "Silent", "Prism", "Hollow", "Bright", "Static", "Copper", "Moss",
              "Glass", "Ember", "Quiet", "Wandering", "Shimmer", "Low", "Cinder", "Echo", "Tidal"]
_GROUP_NOUN = {"pack": ["Fang", "Howl", "Shard", "Claw", "Pursuit"],
               "herd": ["Chorus", "Drift", "Hum", "Circle", "Bloom", "Tangle"],
               "solitary": ["Line"]}

_REGION_ADJ = {"forest": ["Whispering", "Braided", "Lantern", "Elder", "Tangled", "Gilded"],
               "coast": ["Copper", "Humming", "Shattered", "Salt", "Bright", "Foam"],
               "swamp": ["Sunken", "Drowned", "Murmuring", "Rotting", "Misted", "Mire"],
               "caves": ["Hollow", "Resonant", "Folded", "Deep", "Lightless", "Crystal"],
               "desert": ["Cinder", "Fused", "Mirror", "Burning", "Pale", "Singing"],
               "grassland": ["Open", "Crackling", "Windward", "Golden", "Restless", "Long"],
               "anomaly": ["Broken", "Null", "Inverted", "Forgotten", "Stuttering", "Fractured"]}
_REGION_NOUN = {"forest": ["Canopy", "Grove", "Lattice", "Thicket", "Weald"],
                "coast": ["Shore", "Strand", "Reach", "Bay", "Littoral"],
                "swamp": ["Fen", "Bog", "Mire", "Cache", "Slough"],
                "caves": ["Hollows", "Vaults", "Grotto", "Depths", "Echoes"],
                "desert": ["Dunes", "Expanse", "Flats", "Barrens", "Wastes"],
                "grassland": ["Steppe", "Plain", "Meadow", "Downs", "Prairie"],
                "anomaly": ["Rift", "Breach", "Fold", "Scar", "Null"]}


def creature_name(rng: random.Random) -> str:
    n = rng.choice([1, 2, 2, 2, 3])
    s = "".join(rng.choice(_ON) + (rng.choice(_NU) if i < n - 1 else "") for i in range(n))
    s += rng.choice(_CO)
    s = s[:9]
    return s.capitalize()


def group_name(rng: random.Random, social: str) -> str:
    nouns = _GROUP_NOUN.get(social, _GROUP_NOUN["herd"])
    return f"{rng.choice(_GROUP_ADJ)} {rng.choice(nouns)}"


def region_name(rng: random.Random, kind: str) -> str:
    adj = _REGION_ADJ.get(kind, _REGION_ADJ["grassland"])
    noun = _REGION_NOUN.get(kind, _REGION_NOUN["grassland"])
    return f"{rng.choice(adj)} {rng.choice(noun)}"
