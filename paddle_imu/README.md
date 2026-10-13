# paddle_imu

Board side of the ME193 ping-pong game
([github.com/clabar01/ME193-pingpong](https://github.com/clabar01/ME193-pingpong)).
An MPU6050 (GY-521) taped to the paddle is read at 100 Hz; every real swing
becomes one MQTT message to the laptop, and the game's state comes back to be
drawn on the 13x8 LED matrix.

## Key files

| File | What it does |
|---|---|
| [`python/swing_detector.py`](python/swing_detector.py) | **The swing detector.** A move counts only if peak accel >= 2.2 g AND peak gyro >= 500 deg/s, decided within 60 ms of the start, with a 300 ms refractory period. No board imports, so it can be tested on a laptop |
| [`python/main.py`](python/main.py) | Receives samples from the sketch over the Bridge, runs the detector, publishes swings, and subscribes to the game state for the display |
| [`python/matrix_display.py`](python/matrix_display.py) | Decides what the LED matrix shows: the ball as a dot while playing, countdowns, level, final score, or the board's IP when no game is running |
| [`sketch/sketch.ino`](sketch/sketch.ino) | Microcontroller side: reads the MPU6050 over I2C at a steady 100 Hz and drives the LED matrix |
| [`write_host_ip.sh`](write_host_ip.sh) | Cron job on the board that writes its WiFi IP to a file the app container can read |

## How it connects to the game (MQTT)

```
paddle MPU6050 --I2C--> sketch --Bridge--> main.py + swing_detector.py
                                              |  ME193/CeciLaBarge/imu (one message per swing)
                                              v
                    local_broker (Mosquitto on the board, port 1883) <--> laptop game
                                              ^
                                              |  ME193/CeciLaBarge/game (state + ball position)
                              main.py --> matrix_display.py --> sketch --> LED matrix
```

| Topic | Direction | Payload |
|---|---|---|
| `ME193/CeciLaBarge/imu` | board -> laptop | `{"swing": 1, "peak": g, "unit": "g", "peak_gyro": deg/s, "t": unix time}` |
| `ME193/CeciLaBarge/imu/rejected` | board -> laptop | near misses (`"swing": 0` plus `"failed"`), for tuning only |
| `ME193/CeciLaBarge/game` | laptop -> board | game state and ball position for the LED matrix |

Messages go through the board's own broker ([`../local_broker`](../local_broker))
for low latency, with broker.hivemq.com as the fallback. On the laptop,
`laptop/mqtt_client.py` in the game repo receives the swings, and the hit rule
(`is_hit` in `laptop/game_state.py`) matches each one to the ball's arrival
at the paddle.

## Deploy

From the ArduinoApps root: `python3 tools/deploy.py paddle_imu`
