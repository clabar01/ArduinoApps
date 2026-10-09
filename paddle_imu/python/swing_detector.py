"""SWING DETECTION: turns 100 Hz accelerometer samples into one event per swing.

No board-specific imports, so it can be tested on a laptop.

How it works:

  1. Magnitude. For each sample, magnitude = sqrt(ax^2 + ay^2 + az^2) - 1 g.
     A still paddle feels only gravity (|a| = 1 g), so magnitude is about 0
     at rest no matter how the paddle is tilted, and grows with how hard the
     paddle is being accelerated in ANY direction. (It can dip slightly below
     0, e.g. in free fall.)

  2. Start of a swing. A swing starts when magnitude goes above
     SWING_THRESHOLD (in g). The start time is the swing's timestamp `t`.

  3. Peak. While the swing lasts, the largest magnitude is tracked. The
     event is sent as soon as magnitude drops back below the threshold, or
     PEAK_WINDOW_S after the start, whichever comes first. That bounds the
     delay (the game needs the swing quickly for its timing window) while
     still catching the peak of a normal, short swing.

  4. Refractory period. After a swing ends, new swings are ignored for
     REFRACTORY_S (300 ms). A real swing shakes the paddle several times
     (wind-up, hit, stopping the follow-through); this makes it count once.
"""
import math
from typing import Optional

GRAVITY_G = 1.0


class SwingDetector:
    def __init__(self, threshold_g: float, refractory_s: float = 0.3, peak_window_s: float = 0.06):
        self.threshold_g = threshold_g
        self.refractory_s = refractory_s
        self.peak_window_s = peak_window_s
        self.magnitude = 0.0            # magnitude of the latest sample (for logging)
        self.in_swing = False
        self._start = 0.0
        self._peak = 0.0
        self._quiet_until = 0.0         # end of the refractory period

    def update(self, ax: float, ay: float, az: float, now: float) -> Optional[dict]:
        """Feed one sample (g units) taken at time `now` (seconds).

        Returns {"swing": 1, "peak": <g>, "t": <start time>} when a swing
        completes, else None.
        """
        self.magnitude = math.sqrt(ax * ax + ay * ay + az * az) - GRAVITY_G

        if not self.in_swing:
            if self.magnitude > self.threshold_g and now >= self._quiet_until:
                self.in_swing = True
                self._start = now
                self._peak = self.magnitude
            return None

        self._peak = max(self._peak, self.magnitude)
        ended = self.magnitude <= self.threshold_g
        timed_out = now - self._start >= self.peak_window_s
        if not (ended or timed_out):
            return None
        self.in_swing = False
        self._quiet_until = now + self.refractory_s
        return {"swing": 1, "peak": round(self._peak, 2), "t": round(self._start, 3)}
