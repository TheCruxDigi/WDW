"""Procedural audio. Optional: if the mixer is unavailable the game is silent
and the simulation is unaffected.

All sounds are synthesised with NumPy at start-up (original, no licensing
concerns); any of them can be replaced by a file ``assets/sounds/<name>.wav``.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

log = logging.getLogger("digitalworld.audio")
RATE = 22050
ASSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "sounds"


def _env(n: int, attack=0.01, release=0.3) -> np.ndarray:
    t = np.linspace(0, 1, n)
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    r = np.clip((1 - t) / max(release, 1e-4), 0, 1)
    return a * r


def tone(freq, dur, kind="sine", sweep=0.0, vol=0.4):
    n = int(RATE * dur)
    t = np.arange(n) / RATE
    f = freq * (1 + sweep * t / max(dur, 1e-3))
    ph = 2 * np.pi * np.cumsum(f) / RATE
    if kind == "sine":
        w = np.sin(ph)
    elif kind == "square":
        w = np.sign(np.sin(ph)) * 0.5
    elif kind == "tri":
        w = 2 / np.pi * np.arcsin(np.sin(ph))
    else:
        w = np.sin(ph)
    return w * _env(n, 0.02, 0.5) * vol


def noise(dur, vol=0.3, seed=0, lowpass=0.0):
    rng = np.random.default_rng(seed)
    n = int(RATE * dur)
    w = rng.uniform(-1, 1, n)
    if lowpass > 0:
        k = int(lowpass)
        w = np.convolve(w, np.ones(k) / k, mode="same")
    return w * _env(n, 0.01, 0.6) * vol


def bitcrush(w, levels=6, hold=6):
    w = np.round(w * levels) / levels
    w = np.repeat(w[::hold], hold)[: len(w)]
    return w


def ambience(base: float, noise_amt: float, shimmer: float, seconds=6.0, seed=1):
    n = int(RATE * seconds)
    t = np.arange(n) / RATE
    rng = np.random.default_rng(seed)
    drone = (np.sin(2 * np.pi * base * t) * 0.18 + np.sin(2 * np.pi * base * 1.5 * t + 0.3) * 0.08
             + np.sin(2 * np.pi * base * 2.01 * t) * 0.05)
    drone *= 0.7 + 0.3 * np.sin(2 * np.pi * t / seconds)
    wind = np.convolve(rng.uniform(-1, 1, n), np.ones(60) / 60, mode="same") * noise_amt * 0.6
    sh = np.zeros(n)
    for _ in range(int(shimmer * 10)):
        st = rng.integers(0, n - RATE // 2)
        f = rng.uniform(900, 2400)
        seg = tone(f, 0.4, "sine", rng.uniform(-0.2, 0.2), 0.05)
        sh[st:st + len(seg)] += seg
    w = drone + wind + sh
    # seamless loop: crossfade the ends
    fade = RATE // 4
    w[:fade] = w[:fade] * np.linspace(0, 1, fade) + w[-fade:] * np.linspace(1, 0, fade)
    return w[:-fade] * 0.8


def to_mixer_array(wave: np.ndarray, init) -> np.ndarray:
    """Convert a mono float wave (at RATE) to the mixer's real rate, sample
    format and channel count (1, 2, 4, 6 or 8 channels)."""
    freq, fmt, channels = init if init else (RATE, -16, 1)
    data = np.clip(np.asarray(wave, dtype=np.float64), -1, 1) * 0.8
    if freq != RATE and len(data) > 1:
        n = max(1, int(round(len(data) * freq / RATE)))
        data = np.interp(np.linspace(0, len(data) - 1, n), np.arange(len(data)), data)
    bits = abs(fmt)
    if fmt == 32 or fmt == -32:
        # SDL reports float32 and int32 both as 32 bits; pygame-ce treats 32 as float
        arr = data.astype(np.float32) if fmt == 32 else (data * 2147483647).astype(np.int32)
    elif bits == 8:
        arr = ((data * 127) + (128 if fmt > 0 else 0)).astype(np.uint8 if fmt > 0 else np.int8)
    elif fmt > 0:
        arr = ((data * 32767) + 32768).astype(np.uint16)
    else:
        arr = (data * 32767).astype(np.int16)
    if channels > 1:
        arr = np.repeat(arr[:, None], channels, axis=1)
    return np.ascontiguousarray(arr)


class Audio:
    def __init__(self, enabled: bool = True, volume: float = 0.5):
        self.ok = False
        self.volume = volume
        self.sounds = {}
        self.ambient = {}
        self.channel = None
        self.current_ambient = None
        self.glitch_channel = None
        if not enabled:
            return
        try:
            import pygame
            # pygame.init() may already have opened the device with the system's
            # layout (e.g. 5.1 surround, 48 kHz, float samples); ask for a simple
            # stereo 16-bit stream, then adapt to whatever was actually granted.
            if pygame.mixer.get_init():
                pygame.mixer.quit()
            pygame.mixer.init(RATE, -16, 2, 512)
            self.pg = pygame
            self.ok = True
        except Exception as exc:  # no audio device: stay silent
            log.warning("audio disabled: %s", exc)
            return
        try:
            self._build()
        except Exception:
            # sound is optional: never let it stop the game
            log.exception("audio setup failed; continuing without sound")
            self.ok = False
            self.sounds.clear()

    def _snd(self, name, wave):
        f = ASSET_DIR / f"{name}.wav"
        if f.is_file():
            try:
                return self.pg.mixer.Sound(str(f))
            except Exception:
                pass
        return self.pg.sndarray.make_sound(to_mixer_array(wave, self.pg.mixer.get_init()))

    def _build(self):
        mk = self._snd
        self.sounds["blip"] = mk("blip", tone(880, 0.07, "square", 0.3, 0.25))
        self.sounds["feed"] = mk("feed", np.concatenate([tone(f, 0.09, "tri", 0, 0.35) for f in (523, 659, 784)]))
        self.sounds["heal"] = mk("heal", tone(440, 0.5, "sine", 1.0, 0.35) + tone(660, 0.5, "sine", 0.8, 0.2))
        self.sounds["scare"] = mk("scare", noise(0.25, 0.5, 3, 4) + tone(180, 0.25, "square", -0.5, 0.2))
        self.sounds["hit"] = mk("hit", noise(0.18, 0.6, 4, 12) + tone(90, 0.18, "sine", -0.6, 0.5))
        self.sounds["beacon"] = mk("beacon", tone(392, 1.0, "sine", 0, 0.25) + tone(587, 1.0, "sine", 0, 0.18) + tone(784, 1.0, "tri", 0, 0.1))
        self.sounds["stabilize"] = mk("stabilize", tone(220, 0.8, "sine", 2.0, 0.35) + noise(0.8, 0.08, 5, 30))
        self.sounds["glitch"] = mk("glitch", bitcrush(noise(0.35, 0.45, 6) + tone(140, 0.35, "square", 3, 0.2), 4, 18))
        self.sounds["discover"] = mk("discover", np.concatenate([tone(f, 0.12, "sine", 0, 0.3) for f in (660, 880, 1320)]))
        self.sounds["chirp_small"] = mk("chirp_small", tone(1400, 0.12, "sine", 0.6, 0.25))
        self.sounds["chirp_big"] = mk("chirp_big", tone(220, 0.35, "tri", -0.3, 0.35))
        self.sounds["howl"] = mk("howl", tone(330, 0.9, "sine", 0.4, 0.25) * (1 + 0.3 * np.sin(np.arange(int(RATE * 0.9)) / 300)))
        self.sounds["warn"] = mk("warn", tone(120, 0.5, "square", 0.2, 0.25))

    def ensure_ambient(self, biome) -> None:
        if not self.ok:
            return
        key = biome.id
        if self.current_ambient == key:
            return
        snd = self.ambient.get(key)
        if snd is None:
            a = biome.ambience
            wave = ambience(a.get("base", 100), a.get("noise", 0.3), a.get("shimmer", 0.2), seed=len(key))
            snd = self.ambient[key] = self._snd(f"amb_{key}", wave)
        try:
            if self.channel is not None:
                self.channel.fadeout(800)
            self.channel = snd.play(loops=-1, fade_ms=1200)
            if self.channel is not None:
                self.channel.set_volume(self.volume * 0.6)
            self.current_ambient = key
        except Exception:
            log.exception("ambient playback failed")

    def corruption_level(self, level: float) -> None:
        """Glitch layer grows with local corruption."""
        if not self.ok:
            return
        import random
        if level > 0.3 and random.random() < (level - 0.3) * 0.02:
            self.play("glitch", min(1.0, level))

    def play(self, name: str, vol: float = 1.0) -> None:
        if not self.ok:
            return
        s = self.sounds.get(name)
        if s is None:
            return
        try:
            ch = s.play()
            if ch is not None:
                ch.set_volume(max(0.0, min(1.0, vol * self.volume)))
        except Exception:
            pass
