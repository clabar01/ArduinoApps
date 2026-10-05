# 🟢 Minifig Tracker

Subscribes to `ME193/ceci/green` on `broker.hivemq.com` and lights one pixel on the
8×13 LED matrix at the scaled `x`/`y` position. Clears the matrix when `detected` is false.

Expected payload (JSON):

```json
{"x": 0.5, "y": 0.5, "detected": true, "width": 640, "height": 480, "conf": 0.9}
```

`x`/`y` are normalized 0..1; `x` is scaled to columns `0..12`, `y` to rows `0..7`
(`y = 0` is the top row). `width`, `height` and `conf` are ignored.

## Motors (Phase 2)

Cytron Maker Drive (MX1508) on 5V/GND, TT gearmotors on M1 and M2.

| Input | UNO Q pin | Role in sketch |
|-------|-----------|----------------|
| M1A   | D9 ~      | PWM            |
| M1B   | D10 ~     | digital        |
| M2A   | D7        | digital        |
| M2B   | D8        | PWM            |

Bridge provider `set_speed(speed)`: `-100` full reverse … `0` stop (coast) … `100`
full forward, both motors together. Each channel uses one PWM pin and one digital
pin ("drive/brake" mode) — see the comment block in `sketch/sketch.ino`. If a motor
spins the wrong way, flip its `invert` flag there (and in `motor_test`). The Python
app sends `set_speed(0)` when it stops. Run the separate `motor_test` app to check
wiring and direction.

## Centering (Phase 3)

The car drives forward or backward until the minifig's `x` is within
`CENTER_X ± CENTER_TOLERANCE`, then stops. Tuning constants are at the top of
`python/main.py`:

| Constant | Default | Meaning |
|----------|---------|---------|
| `CENTER_X` | `0.5` | target `x` |
| `CENTER_TOLERANCE` | `0.05` | stop band around the target |
| `DRIVE_SPEED` | `80` | speed while moving (0..100) |
| `MIN_DRIVE_SPEED` | `70` | `DRIVE_SPEED` is never used below this |
| `FORWARD_INCREASES_X` | `True` | driving forward makes `x` increase; flip if the car runs away from the center |
| `MESSAGE_TIMEOUT_S` | `1.0` | stop if no MQTT message for this long |

The car also stops on `detected: false` or a message without a valid `x`/`y`.
