import ipaddress
import os
import time

from arduino.app_utils import App, Bridge, Logger

logger = Logger("show_ip")

# How often to look for the IP and (re)send it to the sketch. Resending also
# covers the sketch starting after Python, so the matrix never stays blank.
RETRY_PERIOD_S = 3

# Written every minute by write_host_ip.sh (cron on the board's Linux side).
# This app folder is mounted into the container as /app.
HOST_IP_FILE = "/app/host_ip.txt"

NO_IP_TEXT = "no IP"

last_text = None


def valid_ip(text: str):
    """Return text if it's a usable IPv4 address for reaching the board, else None."""
    try:
        ip = ipaddress.IPv4Address(text.strip())
    except ValueError:
        return None
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        return None
    return str(ip)


def get_ip():
    """The board's WiFi (wlan0) IP, or None if there isn't one yet.

    The app's Python runs inside a Docker container with its own network, so
    wlan0 isn't visible here and socket tricks return the container's 172.x
    address. Two sources:
      1. host_ip.txt: refreshed every minute from the board's Linux side, so it
         picks up WiFi connecting late at boot or the IP changing. If it
         exists, it wins (empty = no WiFi right now).
      2. HOST_IP: set by App Lab when the app starts and fixed after that, so
         it's only used until host_ip.txt first appears.
    """
    try:
        with open(HOST_IP_FILE) as f:
            return valid_ip(f.read())
    except OSError:
        pass  # file not written yet (cron not set up, or first minute after boot)
    return valid_ip(os.environ.get("HOST_IP", ""))


def loop():
    global last_text
    text = get_ip() or NO_IP_TEXT
    try:
        Bridge.call("set_text", text)
        if text != last_text:
            logger.info(f"Showing: {text}")
            last_text = text
    except Exception as e:
        logger.warning(f"Bridge call failed: {e}")
    time.sleep(RETRY_PERIOD_S)


App.run(user_loop=loop)
