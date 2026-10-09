"""SWING DETECTION: turns 100 Hz accelerometer samples into one event per swing.

No board-specific imports, so it can be tested on a laptop.

How it works:

  1. Magnitude. One number per sample that is about 0 when the paddle is
     still and grows the harder it moves. Two choices (FEATURE):
       "accel": sqrt(ax^2 + ay^2 + az^2) - 1 g. A still paddle feels only
                gravity (|a| = 1 g), so this is about 0 at rest however the
                paddle is tilted, and grows with acceleration in ANY
                direction (it can dip slightly below 0, e.g. in free fall).
       "gyro":  sqrt(gx^2 + gy^2 + gz^2) in deg/s: how fast the paddle is
                rotating about any axis. A swing is mostly a fast rotation
                of the wrist/forearm, so this can ignore jolts and shakes
                that accelerate the paddle without turning it.
     Which one separates real swings from fake moves better was measured
     with labeled data (tools/record_swings.py + tools/analyze_swings.py in
     ME193-pingpong).

  2. Start of a swing. A swing starts when magnitude goes above the
     threshold (g for "accel", deg/s for "gyro"). The start time is the
     swing's timestamp `t`.

  3. Peak. While the swing lasts, the largest magnitude is tracked. The
     event is sent as soon as magnitude drops back below the threshold, or
     PEAK_WINDOW_S after the start, whichever comes first. That bounds the
     delay (the game needs the swing quickly for its timing window) while
     still catching the peak of a normal, short swing.

  4. Refractory period. After a swing ends, new swings are ignored for
     REFRACTORY_S (300 ms). A real swing shakes the paddle several times
     (wind-up, hit, stopping the follow-through); this makes it count once.

  5. Extra info (doesn't affect detection): every event also reports
     peak_gyro, the fastest rotation (deg/s) from GYRO_LOOKBACK_S before the
     swing started until the event. The look-back is there because the
     wrist's rotation can peak a little before the acceleration crosses the
     threshold. Used to check whether rotation separates real swings from
     fake moves better than acceleration does.
"""
import math
from collections import deque
from typing import Optional

GRAVITY_G = 1.0
UNITS = {"accel": "g", "gyro": "dps"}
GYRO_LOOKBACK_S = 0.1


def accel_magnitude(ax: float, ay: float, az: float) -> float:
    """|a| - 1 g: about 0 at rest, any tilt."""
    return math.sqrt(ax * ax + ay * ay + az * az) - GRAVITY_G


def gyro_magnitude(gx: float, gy: float, gz: float) -> float:
    """Rotation speed in deg/s, about any axis."""
    return math.sqrt(gx * gx + gy * gy + gz * gz)


class SwingDetector:
    def __init__(self, threshold: float, feature: str = "accel",
                 refractory_s: float = 0.3, peak_window_s: float = 0.06):
        if feature not in UNITS:
            raise ValueError(f'feature must be "accel" or "gyro", not {feature!r}')
        self.threshold = threshold
        self.feature = feature
        self.unit = UNITS[feature]
        self.refractory_s = refractory_s
        self.peak_window_s = peak_window_s
        self.magnitude = 0.0            # magnitude of the latest sample (for logging)
        self.in_swing = False
        self._start = 0.0
        self._peak = 0.0
        self._quiet_until = 0.0         # end of the refractory period
        self._peak_gyro = 0.0
        self._recent_gyro = deque()     # (time, gyro magnitude) for the look-back

    def update(self, ax: float, ay: float, az: float, gx: float, gy: float, gz: float,
               now: float) -> Optional[dict]:
        """Feed one sample (accel in g, gyro in deg/s) taken at time `now` (s).

        Returns {"swing": 1, "peak": <magnitude>, "unit": "g"|"dps",
        "peak_gyro": <deg/s>, "t": <start time>} when a swing completes,
        else None.
        """
        gyro = gyro_magnitude(gx, gy, gz)
        if self.feature == "accel":
            self.magnitude = accel_magnitude(ax, ay, az)
        else:
            self.magnitude = gyro
        self._recent_gyro.append((now, gyro))
        while self._recent_gyro and self._recent_gyro[0][0] < now - GYRO_LOOKBACK_S:
            self._recent_gyro.popleft()

        if not self.in_swing:
            if self.magnitude > self.threshold and now >= self._quiet_until:
                self.in_swing = True
                self._start = now
                self._peak = self.magnitude
                self._peak_gyro = max(g for _, g in self._recent_gyro)
            return None

        self._peak = max(self._peak, self.magnitude)
        self._peak_gyro = max(self._peak_gyro, gyro)
        ended = self.magnitude <= self.threshold
        timed_out = now - self._start >= self.peak_window_s
        if not (ended or timed_out):
            return None
        self.in_swing = False
        self._quiet_until = now + self.refractory_s
        return {"swing": 1, "peak": round(self._peak, 2), "unit": self.unit,
                "peak_gyro": round(self._peak_gyro), "t": round(self._start, 3)}
