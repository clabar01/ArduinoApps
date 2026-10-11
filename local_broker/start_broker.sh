#!/bin/sh
# (Re)start the local Mosquitto broker container on the UNO Q. Docker restarts
# it after a reboot (--restart unless-stopped). Port 1883 on the board.
CONF="$(cd "$(dirname "$0")" && pwd)/mosquitto.conf"
docker rm -f me193-mqtt >/dev/null 2>&1
docker run -d --name me193-mqtt --restart unless-stopped \
  -p 1883:1883 \
  -v "$CONF":/mosquitto/config/mosquitto.conf:ro \
  eclipse-mosquitto:2
