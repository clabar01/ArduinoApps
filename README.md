# UnoQ

Arduino App Lab apps for an Arduino UNO Q board. Each folder with an `app.yaml`
is one app: `python/main.py` runs on the board's Linux side, `sketch/` on its
microcontroller.

## ME193 Midterm: Virtual Ping-Pong

The game itself runs on the laptop: **https://github.com/clabar01/ME193-pingpong**.
These folders are the board side of it:

| Folder | What it does |
|---|---|
| [`paddle_imu/`](https://github.com/clabar01/ArduinoApps/tree/main/paddle_imu) | Reads the MPU6050 on the paddle at 100 Hz, detects swings, publishes one MQTT message per swing, and shows the game on the LED matrix |
| [`local_broker/`](https://github.com/clabar01/ArduinoApps/tree/main/local_broker) | Mosquitto MQTT broker on the board, so swings reach the laptop over local WiFi (bridged to broker.hivemq.com as a fallback) |
| [`tools/`](https://github.com/clabar01/ArduinoApps/tree/main/tools) | `deploy.py` copies and starts apps on the board; `board_secrets.py` (git-ignored) holds the board's IP, which the game also reads |

## Other apps

Not part of the midterm:

- `blue_blink/`: blinks the onboard RGB LED blue, driven from Python
- `color-sorter-hackathon-6-6-26/`: color sorter from the 6/6/26 hackathon
- `ip-address-bar/`: shows the board's IP in a small status bar
- `medford-weather-bar/`: shows the Medford, MA weather in a small status bar
- `minifig_tracker/`: follows a minifig over MQTT, shows it on the LED matrix, and drives the car
- `motor_test/`: wiring check for the car's motors
- `show_ip/`: scrolls the board's IP on the LED matrix (paddle_imu reuses its approach when no game is running)
- `start-here/`: App Lab starter app
