#!/bin/sh
# Runs on the board's Linux side (not in the app container), every minute from
# the arduino user's crontab:
#   * * * * * sh /home/arduino/ArduinoApps/paddle_imu/write_host_ip.sh
# The app's Python runs in a Docker container that can't see wlan0, but this
# folder is mounted into it as /app, so it reads the IP from /app/host_ip.txt.
# Writes an empty file when wlan0 has no IPv4 address.
DIR="$(dirname "$0")"
/usr/bin/ip -4 -o addr show wlan0 2>/dev/null \
  | awk '{split($4, a, "/"); print a[1]; exit}' > "$DIR/host_ip.tmp" \
  && mv "$DIR/host_ip.tmp" "$DIR/host_ip.txt"
