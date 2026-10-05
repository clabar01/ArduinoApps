# 🟢 Minifig Tracker

Subscribes to `ME193/ceci/<COLOR>` (`COLOR` = `green` or `blue`, set at the top of
`python/main.py`; default `green`) on `broker.hivemq.com` and lights one pixel on the
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

## Centering (PD control)

The car drives forward or backward until the minifig's `x` is within
`TARGET_X ± CENTER_TOLERANCE`, then stops. Each MQTT message computes
`u = KP·error + KD·d(error)/dt` (with `error = x − TARGET_X`) and asks for speed
`|u|` toward the center. If the D term outweighs the P term (approaching fast), the
car coasts. TT motors stall below `MIN_DRIVE_SPEED`, so smaller requests are
made as bursts at `MIN_DRIVE_SPEED` — near the center the car creeps in short
nudges. Every start and burst begins with a short kick at `KICK_SPEED`.

Tuning constants are at the top of `python/main.py`:

| Constant | Default | Meaning |
|----------|---------|---------|
| `COLOR` | `"green"` | which minifig: topic `ME193/ceci/green` or `ME193/ceci/blue` |
| `TARGET_X` | `1 / 3` | `x` position the car stops at |
| `CENTER_TOLERANCE` | `0.015` | stop band around the target |
| `RESTART_TOLERANCE` | `0.03` | once stopped, move again only beyond this |
| `KP` | `120` | speed per unit of error; lower = gentler approach |
| `KD` | `40` | damping; raise if it overshoots |
| `D_SMOOTHING` | `0.8` | low-pass on the derivative (1 = none) |
| `MIN_DRIVE_SPEED` | `55` | slowest speed the motors keep turning at |
| `MAX_DRIVE_SPEED` | `80` | top speed far from the center |
| `PULSE_PERIOD_S` | `0.5` | burst cycle for requests below `MIN_DRIVE_SPEED` |
| `KICK_SPEED` / `KICK_TIME_S` | `80` / `0.15` | start-up kick; `KICK_TIME_S = 0` disables |
| `FORWARD_INCREASES_X` | `True` | driving forward makes `x` increase; flip if the car runs away from the center |
| `MESSAGE_TIMEOUT_S` | `1.0` | stop if no MQTT message for this long |

The car also stops on `detected: false` or a message without a valid `x`/`y`.
