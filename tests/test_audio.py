"""Audio must adapt to any mixer layout and never crash the game."""
import numpy as np
import pytest

pygame = pytest.importorskip("pygame")

from digitalworld.audio.synth import to_mixer_array, tone, Audio


@pytest.mark.parametrize("init", [(22050, -16, 1), (22050, -16, 2), (48000, -16, 6),
                                  (44100, 32, 2), (48000, -16, 8), (22050, 8, 2), (44100, 16, 4)])
def test_wave_matches_any_mixer_layout(init):
    a = to_mixer_array(tone(440, 0.2), init)
    freq, fmt, ch = init
    assert a.shape[0] == pytest.approx(0.2 * freq, rel=0.02)
    assert (a.ndim == 1) if ch == 1 else (a.shape[1] == ch)
    assert a.flags["C_CONTIGUOUS"]


@pytest.mark.parametrize("channels", [1, 2, 6])
def test_sounds_build_on_real_mixer(channels):
    pygame.mixer.quit()
    try:
        pygame.mixer.init(48000, -16, channels, 512)
    except pygame.error:
        pytest.skip("mixer layout not available here")
    for name, w in (("t", tone(440, 0.1)),):
        snd = pygame.sndarray.make_sound(to_mixer_array(w, pygame.mixer.get_init()))
        assert snd.get_length() > 0.05
    pygame.mixer.quit()


def test_audio_object_survives_and_plays():
    a = Audio(True, 0.5)
    a.play("feed")
    a.play("missing-sound")
    assert a.ok in (True, False)  # silent fallback is acceptable, crashing is not
