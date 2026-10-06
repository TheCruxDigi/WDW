from collections import deque

import pytest

from digitalworld.content.registry import load_content, Content, ContentError, _load_json
from digitalworld.world.terrain import passable_mask, _components
from digitalworld.world.world import World
from digitalworld.world.generator import generate_world

from conftest import small_config


def test_content_is_valid_and_rich():
    c = load_content()
    assert len(c.species) >= 10
    assert len(c.biomes) >= 7
    trophic = {s.trophic for s in c.species.values()}
    assert {"herbivore", "predator", "omnivore", "scavenger"} <= trophic
    # every species has at least one predator or prey relation (food web is connected)
    for s in c.species.values():
        assert s.prey or c.predators_of(s.id) or s.trophic == "scavenger"


def test_content_validation_rejects_bad_data():
    sp = _load_json("species.json")
    sp["mossling"]["diet"]["plastic"] = 1.0
    with pytest.raises(ContentError):
        Content(sp, _load_json("biomes.json"), _load_json("resources.json"))


def test_generated_world_is_connected_and_consistent(tmp_path):
    w = World(small_config(tmp_path, world_width=4, world_height=3))
    generate_world(w)
    # region graph connected
    start = next(iter(w.regions))
    seen, q = {start}, deque([start])
    while q:
        r = q.popleft()
        for nb in w.regions[r].gates:
            assert r in w.regions[nb].gates, "gates must be symmetric"
            if nb not in seen:
                seen.add(nb)
                q.append(nb)
    assert seen == set(w.regions)
    ids = set()
    for r in w.regions.values():
        walk = passable_mask(r.terrain)
        labels, n = _components(walk)
        main = labels[r.height // 2, r.width // 2]
        for (gx, gy) in r.gates.values():
            assert walk[gy, gx] and labels[gy, gx] == main, "every gate reachable from the centre"
        for node in r.resources.values():
            assert node.id not in ids
            ids.add(node.id)
            if node.kind not in ("shoal",):
                assert walk[node.y, node.x]
    cids = [c.id for c in w.creatures.values()]
    assert len(cids) == len(set(cids)) and not ids & set(cids)
    assert len({r.name for r in w.regions.values()}) == len(w.regions)


def test_generation_is_deterministic(tmp_path):
    a = World(small_config(tmp_path, seed=3))
    b = World(small_config(tmp_path, seed=3))
    generate_world(a)
    generate_world(b)
    assert [(r.biome_id, r.name, sorted(r.gates)) for r in a.regions.values()] == \
           [(r.biome_id, r.name, sorted(r.gates)) for r in b.regions.values()]
    assert [(c.species_id, round(c.x, 3)) for c in a.creatures.values()] == \
           [(c.species_id, round(c.x, 3)) for c in b.creatures.values()]
