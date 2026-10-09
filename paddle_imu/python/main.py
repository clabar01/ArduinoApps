"""paddle_imu: swing events from the MPU6050 on the paddle -> MQTT.

The sketch streams accelerometer/gyro samples at 100 Hz over the Bridge.
This side runs swing detection (swing_detector.py: accel peak AND gyro peak
must both pass) and publishes ONE MQTT message per swing to IMU_TOPIC, never
the raw samples:
    {"swing": 1, "peak": <peak accel g>, "unit": "g", "peak_gyro": <deg/s>,
     "t": <swing start, Unix time s>}
Moves that started like a swing but failed a check go to IMU_REJECTED_TOPIC
instead (same fields, "swing": 0, plus "failed"), so they can be seen while
tuning without the game ever treating them as swings.

MQTT setup is the same as minifig_tracker (broker.hivemq.com:1883, paho-mqtt
2.1.0, connect_async + loop_start: connects and reconnects in the background).

DEBUG = True also (a) writes every sample to /app/imu_debug_<start time>.csv
(on the board: ~/ArduinoApps/paddle_imu/) and (b) streams the raw samples, in
batches of 10, to IMU_RAW_TOPIC, so the laptop can record labeled swings
(tools/record_swings.py). IMU_TOPIC itself only ever carries swing events.
"""
import csv
import json
import time

import paho.mqtt.client as mqtt

from arduino.app_utils import App, Bridge, Logger
from swing_detector import SwingDetector

logger = Logger("paddle_imu")

# --- Swing detection (see swing_detector.py for how these are used) ---------
# From the gyro hand test 2026-10-09 (ME193-pingpong data/gyro_test.txt):
# accel >= 2.2 g AND gyro >= 500 dps kept 10/10 swings, rejected 5/5 fakes.
START_G = 2.0               # accel (|a| - 1 g) that starts a candidate; the test's
                            # peaks were measured with this start, so keep it
MIN_ACCEL_PEAK_G = 2.2      # a swing's peak accel must reach this...
MIN_GYRO_PEAK_DPS = 500.0   # ...AND its peak rotation speed must reach this
GYRO_LOOKBACK_S = 0.1       # gyro peak also counts up to 100 ms before the start
REFRACTORY_S = 0.3          # ignore new swings for this long after a swing
PEAK_WINDOW_S = 0.06        # decide at most this long after the candidate starts
REPORT_REJECTED = True      # publish rejected candidates to IMU_REJECTED_TOPIC

# --- Debug: CSV of every sample + raw stream, for tuning the thresholds ------
DEBUG = False
DEBUG_LOG_EVERY = 10    # in DEBUG, also log every 10th sample (10 lines/s)

# --- MQTT (same broker as the door-to-door minifig app) ----------------------
MQTT_HOST = "broker.hivemq.com"
MQTT_PORT = 1883
IMU_TOPIC = "ME193/CeciLaBarge/imu"
IMU_REJECTED_TOPIC = "ME193/CeciLaBarge/imu/rejected"   # near misses, for tuning
IMU_RAW_TOPIC = "ME193/CeciLaBarge/imu/raw"   # DEBUG only: raw samples for labeling
RAW_BATCH = 10                                 # samples per raw message (10 msgs/s)

STATUS_PERIOD_S = 5.0   # log sample rate + MQTT state this often

detector = SwingDetector(START_G, MIN_ACCEL_PEAK_G, MIN_GYRO_PEAK_DPS,
                         REFRACTORY_S, PEAK_WINDOW_S, GYRO_LOOKBACK_S)
raw_batch = []
mqtt_connected = False
swings = 0
rejected = 0
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
                         "accel_mag", "gyro_mag", "in_candidate", "event"])
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
    global swings, rejected, samples, rate_samples, rate_start
    now = time.time()
    event = detector.update(ax, ay, az, gx, gy, gz, now)
    samples += 1
    rate_samples += 1

    if event is not None and event["swing"] == 1:
        swings += 1
        payload = json.dumps(event)
        if mqtt_connected:
            client.publish(IMU_TOPIC, payload, qos=0)   # fire-and-forget: speed matters
        logger.info(f"SWING #{swings}: accel {event['peak']:.2f} g, gyro {event['peak_gyro']} dps"
                    f"{'' if mqtt_connected else ' (MQTT down, not sent)'}")
    elif event is not None:
        rejected += 1
        if mqtt_connected and REPORT_REJECTED:
            client.publish(IMU_REJECTED_TOPIC, json.dumps(event), qos=0)
        logger.info(f"rejected: accel {event['peak']:.2f} g, gyro {event['peak_gyro']} dps "
                    f"({', '.join(event['failed'])})")

    if DEBUG:
        csv_writer.writerow([f"{now:.3f}", t_ms, f"{ax:.3f}", f"{ay:.3f}", f"{az:.3f}",
                             f"{gx:.1f}", f"{gy:.1f}", f"{gz:.1f}",
                             f"{detector.accel:.3f}", f"{detector.gyro:.1f}",
                             int(detector.in_candidate), "" if event is None else event["swing"]])
        raw_batch.append([round(now, 3), round(ax, 3), round(ay, 3), round(az, 3),
                          round(gx, 1), round(gy, 1), round(gz, 1)])
        if len(raw_batch) >= RAW_BATCH:
            if mqtt_connected:
                client.publish(IMU_RAW_TOPIC, json.dumps({"s": raw_batch}), qos=0)
            raw_batch.clear()
        if samples % 100 == 0:
            csv_file.flush()
        if samples % DEBUG_LOG_EVERY == 0:
            logger.info(f"accel {detector.accel:+5.2f} g   gyro {detector.gyro:6.1f} dps")

    mono = time.monotonic()
    if mono - rate_start >= STATUS_PERIOD_S:
        logger.info(f"status: {rate_samples / (mono - rate_start):5.1f} samples/s, "
                    f"MQTT {'connected' if mqtt_connected else 'DISCONNECTED'}, "
                    f"{swings} swings, {rejected} rejected so far")
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
