"""Smoke tests of the real pygame front-end using SDL's dummy video driver."""

import pytest

pygame = pytest.importorskip("pygame")

from conftest import small_config  # noqa: E402


@pytest.fixture
def app(tmp_path):
    from digitalworld.render.app import App
    a = App(small_config(tmp_path, seed=4), new_world=True, seed=4)
    yield a
    pygame.quit()


def _frames(app, n=3):
    for _ in range(n):
        app.dt = 1 / 30
        app.update_game()
        app.draw_game()


def _varied(surface) -> bool:
    small = pygame.transform.scale(surface, (32, 18))
    colors = {small.get_at((x, y))[:3] for x in range(32) for y in range(18)}
    return len(colors) > 20


def test_game_view_renders_world(app):
    assert app.state == "game"
    _frames(app, 5)
    assert _varied(app.screen)


def test_overlays_and_inspector_render(app):
    sim = app.sim
    sim.run(30)
    c = next(iter(sim.world.creatures_in(sim.world.player.region_id)), None)
    if c is not None:
        app.view.selected = c.id
    for ov in (None, "map", "chronicle", "journal", "help"):
        app.overlay = ov
        _frames(app, 1)
    app.view.debug = True
    app.show_profiler = True
    _frames(app, 2)
    assert _varied(app.screen)


def test_keyboard_commands_reach_simulation(app):
    sim = app.sim
    p = sim.world.player
    seeds = p.inventory["seeds"]
    x0 = p.x
    for key in (pygame.K_q, pygame.K_g, pygame.K_r, pygame.K_SPACE, pygame.K_SPACE, pygame.K_RIGHTBRACKET):
        app.handle(pygame.event.Event(pygame.KEYDOWN, key=key, mod=0, unicode=""))
    assert app.runner.speed == 2
    assert p.energy < 100 or "energy" in str(list(p.toasts))
    # movement through the real input path
    act_x = p.x
    from digitalworld.player import interactions as act
    act.move_player(sim, 1, 0, 0.1)
    assert p.x != act_x or not sim.world.regions[p.region_id].is_walkable(int(p.x + 1), int(p.y))


def test_save_from_app_and_continue(app, tmp_path):
    assert app.save(quiet=True)
    tick = app.sim.world.clock.tick
    assert app.try_continue()
    assert app.sim.world.clock.tick >= tick
