import json
import threading
import time

import paho.mqtt.client as mqtt

from arduino.app_utils import App, Bridge, Logger

logger = Logger("minifig_tracker")

# --- Which minifig and where to stop -----------------------------------------
COLOR = "green"           # "green" or "blue": subscribes to ME193/ceci/<COLOR>
TARGET_X = 1 / 5          # x position the car stops at (same units as x, 0..X_MAX)

# --- Centering tuning --------------------------------------------------------
# PD control on error = x - TARGET_X. Each message computes
#   u = KP * error + KD * d(error)/dt
# and asks for speed |u| (capped at MAX_DRIVE_SPEED) in the direction that
# shrinks the error. When the D term outweighs the P term (the minifig is
# approaching the center fast) the car coasts instead.
# The motors can't run slower than MIN_DRIVE_SPEED, so a request below it is
# made in bursts: drive at MIN_DRIVE_SPEED for (request / MIN_DRIVE_SPEED) of
# every PULSE_PERIOD_S. Near the center the car creeps in short nudges.
CENTER_TOLERANCE = 0.015  # stop when |error| <= this (about 10 px at 640 wide)
RESTART_TOLERANCE = 0.03  # once stopped, only move again when |error| > this
KP = 120.0                # speed per unit of error (error is about -0.5..0.5)
KD = 40.0                 # speed per unit of error/second; damps the approach
D_SMOOTHING = 0.8         # 0..1 low-pass on d(error)/dt; 1 = raw, smaller = smoother
MIN_DRIVE_SPEED = 55      # slowest speed the motors keep turning at (was 70)
MAX_DRIVE_SPEED = 80      # fastest speed, far from the center
PULSE_PERIOD_S = 0.5      # burst cycle length for requests below MIN_DRIVE_SPEED
# TT motors are slow to start: when the car starts or reverses, drive at
# KICK_SPEED for KICK_TIME_S, then drop to the PD speed. KICK_TIME_S = 0 disables.
KICK_SPEED = 80
KICK_TIME_S = 0.15
# True if driving the car FORWARD makes the minifig's x in the image INCREASE.
# If the car runs away from the center instead of toward it, flip this.
FORWARD_INCREASES_X = True
# Stop the motors if no MQTT message has arrived for this many seconds.
MESSAGE_TIMEOUT_S = 1.0
LOOP_PERIOD_S = 0.02      # how often the App loop applies the speed / checks timeout

MQTT_HOST = "broker.hivemq.com"
MQTT_PORT = 1883
if COLOR not in ("green", "blue"):
    raise ValueError(f'COLOR must be "green" or "blue", not {COLOR!r}')
MQTT_TOPIC = f"ME193/ceci/{COLOR}"

# Range of the incoming x/y values (normalized by the detector). x in [0, X_MAX],
# y in [0, Y_MAX], with y = 0 at the top. Adjust to match the publisher.
X_MAX = 1.0
Y_MAX = 1.0

# LED matrix size, from Arduino_LED_Matrix.h (canvasWidth x canvasHeight).
MATRIX_COLS = 13
MATRIX_ROWS = 8

last_cell = None  # (col, row) currently shown, or None when cleared

# The MQTT thread computes target_speed; the App loop thread sends it.
state_lock = threading.Lock()
target_speed = 0          # PD output, -100..100
last_message_time = None  # time.monotonic() of the last MQTT message
prev_error = None         # error and arrival time of the previous message
prev_time = None
d_error = 0.0             # smoothed d(error)/dt
moving = False            # outside the stop band (hysteresis state)

# App loop thread only (plus the shutdown stop).
motor_lock = threading.Lock()
last_speed = None         # last speed sent with set_speed, None = unknown
kick_dir = 0              # direction of the current run, 0 = stopped
kick_until = 0.0
pulse_start = None        # start of the current burst cycle, None = not pulsing


def scale(value: float, max_value: float, cells: int) -> int:
    """Map value in [0, max_value] to a cell index in [0, cells - 1]."""
    idx = int(value / max_value * cells)
    return max(0, min(cells - 1, idx))


def show(cell) -> str:
    """Send the dot (or a clear) to the MCU, only when it changes.

    Returns a description of what was sent, for logging.
    """
    global last_cell
    if cell == last_cell:
        return "nothing (unchanged)"
    try:
        if cell is None:
            Bridge.call("clear_matrix")
            sent = "clear_matrix()"
        else:
            Bridge.call("show_dot", cell[0], cell[1])
            sent = f"show_dot({cell[0]}, {cell[1]})"
        last_cell = cell
        return sent
    except Exception as e:
        logger.warning(f"Bridge call failed: {e}")
        return "nothing (Bridge call failed)"


def drive(speed: int) -> str:
    """Send set_speed to the MCU, only when it changes. Returns what was sent."""
    global last_speed
    with motor_lock:
        if speed == last_speed:
            return "nothing (unchanged)"
        try:
            Bridge.call("set_speed", speed)
        except Exception as e:
            last_speed = None  # unknown state; resend next time
            logger.warning(f"set_speed({speed}) failed: {e}")
            return "nothing (Bridge call failed)"
        last_speed = speed
        return f"set_speed({speed})"


def reset_pd():
    """Forget the derivative history and stop (call with state_lock held)."""
    global target_speed, prev_error, prev_time, d_error, moving
    target_speed = 0
    prev_error = prev_time = None
    d_error = 0.0
    moving = False


def pd_speed(x: float, now: float) -> str:
    """Update target_speed from a new x (call with state_lock held). Returns a log string."""
    global target_speed, prev_error, prev_time, d_error, moving
    error = x - TARGET_X

    if prev_time is not None and 0 < now - prev_time < MESSAGE_TIMEOUT_S:
        raw = (error - prev_error) / (now - prev_time)
        d_error += D_SMOOTHING * (raw - d_error)
    else:
        d_error = 0.0
    prev_error, prev_time = error, now

    band = CENTER_TOLERANCE if moving else RESTART_TOLERANCE
    u = KP * error + KD * d_error
    if abs(error) <= band:
        moving = False
        target_speed = 0
    else:
        moving = True
        if u * error <= 0:
            target_speed = 0  # approaching fast: coast instead of pushing on
        else:
            speed = round(min(MAX_DRIVE_SPEED, abs(u)))
            # u > 0 means x must decrease; forward decreases x if not FORWARD_INCREASES_X.
            forward = (u < 0) == FORWARD_INCREASES_X
            target_speed = speed if forward else -speed
    return f"err={error:+.3f} d={d_error:+.2f}/s u={u:+.1f} -> target {target_speed}"


def with_pulses(speed: int, now: float) -> int:
    """Turn a request below MIN_DRIVE_SPEED into bursts at MIN_DRIVE_SPEED."""
    global pulse_start
    if speed == 0 or abs(speed) >= MIN_DRIVE_SPEED:
        pulse_start = None
        return speed
    if pulse_start is None:
        pulse_start = now
    phase = (now - pulse_start) % PULSE_PERIOD_S
    on = phase < PULSE_PERIOD_S * abs(speed) / MIN_DRIVE_SPEED
    return (MIN_DRIVE_SPEED if speed > 0 else -MIN_DRIVE_SPEED) if on else 0


def with_kick(speed: int, now: float) -> int:
    """Use KICK_SPEED briefly when the car starts (or each burst starts) or reverses."""
    global kick_dir, kick_until
    direction = (speed > 0) - (speed < 0)
    if direction == 0:
        kick_dir = 0
        return 0
    if direction != kick_dir:
        kick_dir = direction
        kick_until = now + KICK_TIME_S
    if now < kick_until and abs(speed) < KICK_SPEED:
        return direction * KICK_SPEED
    return speed


def on_connect(client, userdata, flags, reason_code, properties):
    logger.info(f"Connected to {MQTT_HOST} ({reason_code}); subscribing to {MQTT_TOPIC}")
    client.subscribe(MQTT_TOPIC)


def on_message(client, userdata, msg):
    global last_message_time
    now = time.monotonic()
    with state_lock:
        last_message_time = now
    try:
        data = json.loads(msg.payload)
    except ValueError:
        logger.warning(f"Ignoring non-JSON payload: {msg.payload[:100]!r}")
        return

    if not data.get("detected", False):
        with state_lock:
            reset_pd()
        sent = show(None)
        logger.info(f"detected=false -> sent {sent}, target 0")
        return

    try:
        col = scale(float(data["x"]), X_MAX, MATRIX_COLS)
        row = scale(float(data["y"]), Y_MAX, MATRIX_ROWS)
    except (KeyError, TypeError, ValueError):
        logger.warning(f"Missing/invalid x or y: {data}")
        with state_lock:
            reset_pd()
        return
    sent = show((col, row))
    with state_lock:
        control = pd_speed(float(data["x"]), now)
    logger.info(f"x={data['x']!r} y={data['y']!r} -> col={col} row={row} -> sent {sent}, {control}")


def control_loop():
    """App loop: send the PD target (bursts + kick-start); stop if messages stop."""
    now = time.monotonic()
    with state_lock:
        t = last_message_time
        if target_speed != 0 and (t is None or now - t > MESSAGE_TIMEOUT_S):
            reset_pd()
            logger.info(f"No message for {MESSAGE_TIMEOUT_S}s -> stopping")
        speed = target_speed
    drive(with_kick(with_pulses(speed, now), now))
    time.sleep(LOOP_PERIOD_S)


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message
client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
client.loop_start()  # background thread; reconnects automatically

try:
    App.run(user_loop=control_loop)
finally:
    try:
        Bridge.call("set_speed", 0)  # don't leave the car driving after a stop
    except Exception as e:
        logger.warning(f"Could not stop motors: {e}")
    client.loop_stop()
    client.disconnect()
