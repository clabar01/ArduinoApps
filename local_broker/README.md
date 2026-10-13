# local_broker

Part of the ME193 ping-pong game
([github.com/clabar01/ME193-pingpong](https://github.com/clabar01/ME193-pingpong)).
Swings from [`../paddle_imu`](../paddle_imu) reach the laptop through this
broker over the local WiFi, which is much faster than a round trip through
a public broker on the internet.

Mosquitto MQTT broker running on the UNO Q in Docker, for the ME193 ping-pong
game's fast topics (`ME193/CeciLaBarge/imu`, `.../imu/rejected`, `.../game`).
Not an App Lab app: it runs next to whatever app is running, as its own
container (`me193-mqtt`), restarted automatically after reboots.

- `mosquitto.conf`: listener on port 1883, anonymous, and a bridge of
  `ME193/CeciLaBarge/#` (both directions) to broker.hivemq.com so clients that
  fall back to hivemq still get the messages. The record topic is not bridged.
- `start_broker.sh`: (re)creates the container.

Clients: the laptop game connects to `<board IP>:1883` (BOARD_IP in
`tools/board_secrets.py`); paddle_imu (inside its container) connects to
`msgpack-rpc-router:1883` (the board itself). Both also keep a hivemq
connection as a fallback.

Copy to the board and start (from this repo's root):

    python3 tools/deploy.py --cmd "mkdir -p /home/arduino/ArduinoApps/local_broker"
    # copy mosquitto.conf + start_broker.sh there, then:
    python3 tools/deploy.py --cmd "sh /home/arduino/ArduinoApps/local_broker/start_broker.sh"
