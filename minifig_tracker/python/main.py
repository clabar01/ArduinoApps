import json
import threading
import time

import paho.mqtt.client as mqtt

from arduino.app_utils import App, Bridge, Logger

logger = Logger("minifig_tracker")

# --- Centering tuning --------------------------------------------------------
# The car drives forward/backward until the minifig's x is within
# CENTER_X +/- CENTER_TOLERANCE, then stops.
CENTER_X = 0.5            # target x (same units as the incoming x, 0..X_MAX)
CENTER_TOLERANCE = 0.05   # stop when |x - CENTER_X| <= this
DRIVE_SPEED = 80          # set_speed magnitude while moving, 0..100
MIN_DRIVE_SPEED = 70      # floor for DRIVE_SPEED; TT motors stall/start slowly at 60
# True if driving the car FORWARD makes the minifig's x in the image INCREASE.
# If the car runs away from the center instead of toward it, flip this.
FORWARD_INCREASES_X = True
# Stop the motors if no MQTT message has arrived for this many seconds.
MESSAGE_TIMEOUT_S = 1.0

MQTT_HOST = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_TOPIC = "ME193/ceci/green"

# Range of the incoming x/y values (normalized by the detector). x in [0, X_MAX],
# y in [0, Y_MAX], with y = 0 at the top. Adjust to match the publisher.
X_MAX = 1.0
Y_MAX = 1.0

# LED matrix size, from Arduino_LED_Matrix.h (canvasWidth x canvasHeight).
MATRIX_COLS = 13
MATRIX_ROWS = 8

last_cell = None  # (col, row) currently shown, or None when cleared

# Speed actually used while moving, never below the floor.
drive_speed = max(MIN_DRIVE_SPEED, min(100, DRIVE_SPEED))

# drive() is called from the MQTT thread and the App loop thread.
motor_lock = threading.Lock()
last_speed = None         # last speed sent with set_speed, None = unknown
last_message_time = None  # time.monotonic() of the last MQTT message


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


def centering_speed(x: float) -> int:
    """Speed that moves the minifig toward CENTER_X, or 0 when it's close enough."""
    error = x - CENTER_X
    if abs(error) <= CENTER_TOLERANCE:
        return 0
    # x too big -> we want x to decrease. Forward decreases x if FORWARD_INCREASES_X is False.
    want_x_increase = error < 0
    forward = want_x_increase == FORWARD_INCREASES_X
    return drive_speed if forward else -drive_speed


def on_connect(client, userdata, flags, reason_code, properties):
    logger.info(f"Connected to {MQTT_HOST} ({reason_code}); subscribing to {MQTT_TOPIC}")
    client.subscribe(MQTT_TOPIC)


def on_message(client, userdata, msg):
    global last_message_time
    last_message_time = time.monotonic()
    try:
        data = json.loads(msg.payload)
    except ValueError:
        logger.warning(f"Ignoring non-JSON payload: {msg.payload[:100]!r}")
        return

    if not data.get("detected", False):
        sent = show(None)
        moved = drive(0)
        logger.info(f"detected=false -> sent {sent}, {moved}")
        return

    try:
        col = scale(float(data["x"]), X_MAX, MATRIX_COLS)
        row = scale(float(data["y"]), Y_MAX, MATRIX_ROWS)
    except (KeyError, TypeError, ValueError):
        logger.warning(f"Missing/invalid x or y: {data}")
        drive(0)
        return
    sent = show((col, row))
    moved = drive(centering_speed(float(data["x"])))
    logger.info(f"x={data['x']!r} y={data['y']!r} -> col={col} row={row} -> sent {sent}, {moved}")


def watchdog():
    """App loop: stop the car if MQTT messages stop arriving."""
    if last_speed != 0:
        t = last_message_time
        if t is None or time.monotonic() - t > MESSAGE_TIMEOUT_S:
            sent = drive(0)
            if sent.startswith("set_speed"):
                logger.info(f"No message for {MESSAGE_TIMEOUT_S}s -> {sent}")
    time.sleep(0.1)


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_message = on_message
client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
client.loop_start()  # background thread; reconnects automatically

if drive_speed != DRIVE_SPEED:
    logger.warning(f"DRIVE_SPEED={DRIVE_SPEED} out of range; using {drive_speed}")

try:
    App.run(user_loop=watchdog)
finally:
    try:
        Bridge.call("set_speed", 0)  # don't leave the car driving after a stop
    except Exception as e:
        logger.warning(f"Could not stop motors: {e}")
    client.loop_stop()
    client.disconnect()
