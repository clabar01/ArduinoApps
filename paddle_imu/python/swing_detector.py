"""SWING DETECTION: turns 100 Hz accelerometer + gyro samples into one event per swing.

No board-specific imports, so it can be tested on a laptop.

A move counts as a swing only if it is BOTH hard enough AND fast-turning
enough. Hand tests (see "Swing threshold tuning" in ME193-pingpong/CLAUDE.md)
showed that neither acceleration nor rotation alone separates real swings
from fake moves (sliding, reaching), but together they do: every fake failed
at least one of the two checks.

How it works:

  1. Two magnitudes per sample, both about 0 when the paddle is still:
       accel = sqrt(ax^2 + ay^2 + az^2) - 1 g. A still paddle feels only
               gravity (|a| = 1 g), so this is ~0 at rest however the paddle
               is tilted, and grows with acceleration in ANY direction.
       gyro  = sqrt(gx^2 + gy^2 + gz^2) in deg/s: how fast the paddle is
               rotating, about any axis. A real swing is a fast turn of the
               wrist/forearm; sliding or reaching the paddle around mostly
               isn't.

  2. Candidate. A candidate move starts when accel goes above START_G
     (2.0 g). Its start time is the event's timestamp `t`.

  3. Peaks. While the candidate lasts, the largest accel is tracked, and
     the largest gyro from GYRO_LOOKBACK_S (100 ms) BEFORE the start (the
     wrist's rotation can peak a little before the acceleration crosses
     the line) until the decision. The decision is made as soon as accel
     drops back below START_G, or PEAK_WINDOW_S (60 ms) after the start,
     whichever comes first. That bounds the delay, which matters for the
     game's timing window.

  4. Decision. It is a SWING only if
         peak accel >= MIN_ACCEL_PEAK_G (2.2 g)   AND
         peak gyro  >= MIN_GYRO_PEAK_DPS (500 deg/s).
     Otherwise it is rejected (reported separately, for tuning).

  5. Refractory period. After an accepted swing, new candidates are ignored
     for REFRACTORY_S (300 ms): a real swing shakes the paddle several times
     (wind-up, hit, stopping the follow-through) and must count once. A
     rejected candidate does NOT start a refractory period, so a rejected
     wind-up can't block the real stroke right after it.
"""
import math
from collections import deque
from typing import Optional

GRAVITY_G = 1.0


def accel_magnitude(ax: float, ay: float, az: float) -> float:
    """|a| - 1 g: about 0 at rest, any tilt."""
    return math.sqrt(ax * ax + ay * ay + az * az) - GRAVITY_G


def gyro_magnitude(gx: float, gy: float, gz: float) -> float:
    """Rotation speed in deg/s, about any axis."""
    return math.sqrt(gx * gx + gy * gy + gz * gz)


class SwingDetector:
    def __init__(self, start_g: float = 2.0, min_accel_peak_g: float = 2.2,
                 min_gyro_peak_dps: float = 500.0, refractory_s: float = 0.3,
                 peak_window_s: float = 0.06, gyro_lookback_s: float = 0.1):
        self.start_g = start_g
        self.min_accel_peak_g = min_accel_peak_g
        self.min_gyro_peak_dps = min_gyro_peak_dps
        self.refractory_s = refractory_s
        self.peak_window_s = peak_window_s
        self.gyro_lookback_s = gyro_lookback_s
        self.accel = 0.0                # magnitudes of the latest sample (for logging)
        self.gyro = 0.0
        self.in_candidate = False
        self._start = 0.0
        self._peak_accel = 0.0
        self._peak_gyro = 0.0
        self._quiet_until = 0.0         # end of the refractory period
        self._recent_gyro = deque()     # (time, gyro) for the look-back

    def update(self, ax: float, ay: float, az: float, gx: float, gy: float, gz: float,
               now: float) -> Optional[dict]:
        """Feed one sample (accel in g, gyro in deg/s) taken at time `now` (s).

        When a candidate is decided, returns
            {"swing": 1 or 0, "peak": <accel g>, "unit": "g",
             "peak_gyro": <deg/s>, "t": <start time>}
        plus, for rejections (swing 0), "failed": the check(s) it failed.
        Otherwise returns None.
        """
        self.accel = accel_magnitude(ax, ay, az)
        self.gyro = gyro_magnitude(gx, gy, gz)
        self._recent_gyro.append((now, self.gyro))
        while self._recent_gyro and self._recent_gyro[0][0] < now - self.gyro_lookback_s:
            self._recent_gyro.popleft()

        if not self.in_candidate:
            if self.accel > self.start_g and now >= self._quiet_until:
                self.in_candidate = True
                self._start = now
                self._peak_accel = self.accel
                self._peak_gyro = max(g for _, g in self._recent_gyro)
            return None

        self._peak_accel = max(self._peak_accel, self.accel)
        self._peak_gyro = max(self._peak_gyro, self.gyro)
        ended = self.accel <= self.start_g
        timed_out = now - self._start >= self.peak_window_s
        if not (ended or timed_out):
            return None

        # Decide: both checks must pass
        self.in_candidate = False
        failed = []
        if self._peak_accel < self.min_accel_peak_g:
            failed.append(f"accel < {self.min_accel_peak_g:g} g")
        if self._peak_gyro < self.min_gyro_peak_dps:
            failed.append(f"gyro < {self.min_gyro_peak_dps:g} dps")
        event = {"swing": 0 if failed else 1, "peak": round(self._peak_accel, 2), "unit": "g",
                 "peak_gyro": round(self._peak_gyro), "t": round(self._start, 3)}
        if failed:
            event["failed"] = failed
        else:
            self._quiet_until = now + self.refractory_s   # only after a real swing
        return event
