"""paddle_imu: swing events from the MPU6050 on the paddle -> MQTT.

The sketch streams accelerometer/gyro samples at 100 Hz over the Bridge.
This side runs swing detection (swing_detector.py) and publishes ONE MQTT
message per swing to IMU_TOPIC, never the raw samples:
    {"swing": 1, "peak": <peak magnitude in g>, "t": <swing start, Unix time s>}

MQTT setup is the same as minifig_tracker (broker.hivemq.com:1883, paho-mqtt
2.1.0, connect_async + loop_start: connects and reconnects in the background).

DEBUG = True also writes every sample to /app/imu_debug_<start time>.csv
(on the board: ~/ArduinoApps/paddle_imu/) to help choose SWING_THRESHOLD.
"""
import csv
import json
import math
import time

import paho.mqtt.client as mqtt

from arduino.app_utils import App, Bridge, Logger
from swing_detector import SwingDetector

logger = Logger("paddle_imu")

# --- Swing detection (see swing_detector.py for how these are used) ---------
SWING_THRESHOLD = 1.5   # g above gravity: |a| - 1 g must exceed this to start a swing
REFRACTORY_S = 0.3      # ignore new swings for this long after one ends
PEAK_WINDOW_S = 0.06    # send the event at most this long after the swing starts

# --- Debug: CSV of every sample, to choose SWING_THRESHOLD -------------------
DEBUG = True
DEBUG_LOG_EVERY = 10    # in DEBUG, also log every 10th sample (10 lines/s)

# --- MQTT (same broker as the door-to-door minifig app) ----------------------
MQTT_HOST = "broker.hivemq.com"
MQTT_PORT = 1883
IMU_TOPIC = "ME193/CeciLaBarge/imu"

STATUS_PERIOD_S = 5.0   # log sample rate + MQTT state this often

detector = SwingDetector(SWING_THRESHOLD, REFRACTORY_S, PEAK_WINDOW_S)
mqtt_connected = False
swings = 0
samples = 0
rate_samples = 0
rate_start = time.monotonic()
last_status = None

csv_file = csv_writer = None
if DEBUG:
    csv_path = f"/app/imu_debug_{time.strftime('%Y%m%d-%H%M%S')}.csv"
    csv_file = open(csv_path, "w", newline="")
    csv_writer = csv.writer(csv_file)
    csv_writer.writerow(["time_s", "mcu_ms", "ax", "ay", "az", "gx", "gy", "gz",
                         "magnitude", "in_swing", "event"])
    logger.info(f"DEBUG: writing every sample to {csv_path}")


def on_connect(client, userdata, flags, reason_code, properties):
    global mqtt_connected
    mqtt_connected = not reason_code.is_failure
    logger.info(f"MQTT: connected to {MQTT_HOST} ({reason_code}); publishing swings to {IMU_TOPIC}")


def on_disconnect(client, userdata, flags, reason_code, properties):
    global mqtt_connected
    mqtt_connected = False
    logger.warning(f"MQTT: disconnected ({reason_code}); reconnecting automatically")


def imu_sample(t_ms: int, ax: float, ay: float, az: float, gx: float, gy: float, gz: float):
    """Called by the sketch (Bridge.notify) for every sample. Accel in g, gyro in deg/s."""
    global swings, samples, rate_samples, rate_start
    now = time.time()
    event = detector.update(ax, ay, az, now)
    samples += 1
    rate_samples += 1

    if event is not None:
        swings += 1
        payload = json.dumps(event)
        if mqtt_connected:
            client.publish(IMU_TOPIC, payload, qos=0)   # fire-and-forget: speed matters
            logger.info(f"SWING #{swings}: peak {event['peak']:.2f} g -> {IMU_TOPIC} {payload}")
        else:
            logger.info(f"SWING #{swings}: peak {event['peak']:.2f} g (MQTT down, not sent)")

    if DEBUG:
        csv_writer.writerow([f"{now:.3f}", t_ms, f"{ax:.3f}", f"{ay:.3f}", f"{az:.3f}",
                             f"{gx:.1f}", f"{gy:.1f}", f"{gz:.1f}", f"{detector.magnitude:.3f}",
                             int(detector.in_swing), int(event is not None)])
        if samples % 100 == 0:
            csv_file.flush()
        if samples % DEBUG_LOG_EVERY == 0:
            logger.info(f"magnitude {detector.magnitude:+5.2f} g   "
                        f"(|a| {detector.magnitude + 1:4.2f} g, threshold {SWING_THRESHOLD} g)")

    mono = time.monotonic()
    if mono - rate_start >= STATUS_PERIOD_S:
        logger.info(f"status: {rate_samples / (mono - rate_start):5.1f} samples/s, "
                    f"MQTT {'connected' if mqtt_connected else 'DISCONNECTED'}, {swings} swings so far")
        rate_samples, rate_start = 0, mono


def imu_status(text: str):
    """Sensor found / not found + I2C scan. The sketch repeats it every 5 s."""
    global last_status
    if text != last_status:
        logger.info(f"IMU: {text}")
        last_status = text


client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
client.on_connect = on_connect
client.on_disconnect = on_disconnect
client.connect_async(MQTT_HOST, MQTT_PORT, keepalive=30)
client.loop_start()   # background thread; reconnects automatically

Bridge.provide("imu_sample", imu_sample)
Bridge.provide("imu_status", imu_status)

try:
    App.run()
finally:
    client.loop_stop()
    client.disconnect()
    if csv_file is not None:
        csv_file.close()
