import random

from digitalworld.core.clock import WorldClock, FixedStepRunner, TICKS_PER_DAY
from digitalworld.core.events import EventBus, History
from digitalworld.core.rng import derive_seed, make_rng
from digitalworld.core.profiler import Profiler


def test_clock_daylight_and_labels():
    c = WorldClock(12 * 60)
    assert c.daylight() > 0.99
    c.tick = 0
    assert c.daylight() < 0.01
    c.tick = TICKS_PER_DAY + 90
    assert c.day == 1 and c.label() == "Day 2  01:30"


def test_runner_is_fps_independent_and_bounded():
    r = FixedStepRunner(tick_rate=10, max_ticks_per_frame=5, budget_ms=1000)
    n = [0]
    total = 0
    for _ in range(60):          # 1 second at 60 fps
        total += r.run(1 / 60, lambda: n.__setitem__(0, n[0] + 1))
    assert 9 <= total <= 11
    r2 = FixedStepRunner(tick_rate=10, max_ticks_per_frame=5, budget_ms=1000)
    total2 = sum(r2.run(1 / 10, lambda: None) for _ in range(10))  # same second at 10 fps
    assert abs(total - total2) <= 1
    # a huge stall cannot queue unbounded work
    r3 = FixedStepRunner(tick_rate=10, max_ticks_per_frame=5, budget_ms=1000)
    r3.speed = 16
    assert r3.run(5.0, lambda: None) == 5
    assert r3.dropped_ticks > 0
    r3.paused = True
    assert r3.run(1.0, lambda: None) == 0


def test_runner_time_budget_drops_backlog():
    import time
    r = FixedStepRunner(tick_rate=1000, max_ticks_per_frame=1000, budget_ms=5)
    done = r.run(0.2, lambda: time.sleep(0.002))
    assert done < 50 and r.dropped_ticks > 0


def test_rng_is_reproducible():
    assert derive_seed(1, "a", 2) == derive_seed(1, "a", 2)
    assert derive_seed(1, "a", 2) != derive_seed(1, "a", 3)
    assert make_rng(5, "x").random() == make_rng(5, "x").random()


def test_event_bus_isolates_failing_subscribers():
    bus = EventBus()
    got = []
    bus.subscribe("x", lambda e: 1 / 0)
    bus.subscribe("x", lambda e: got.append(e.data["kind"]))
    bus.publish("x", 3, kind="payload-named-kind")
    assert got == ["payload-named-kind"]
    assert bus.counts["x"] == 1


def test_history_causes_and_lookup():
    h = History()
    a = h.record(10, "resource_collapse", "food collapsed", 1, importance=2)
    b = h.record(20, "migration_wave", "they left", 1, importance=2, causes=[a.id])
    c = h.record(30, "species_vanished", "none remain", 1, importance=2, causes=[b.id], kind="ignored-extra")
    chain = h.cause_chain(c.id)
    assert [e.id for e in chain] == [c.id, b.id, a.id]
    assert h.latest("migration_wave", 1, within=100, now=40).id == b.id
    assert h.latest("migration_wave", 1, within=5, now=40) is None
    assert h.get(b.id) is b


def test_profiler_records():
    p = Profiler()
    with p.section("a"):
        sum(range(1000))
    assert p.report()[0][0] == "a"
    assert "a" in p.format()
