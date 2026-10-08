import os
import time

from arduino.app_utils import App, Bridge, Logger

logger = Logger("show_ip")

# How often to (re)send the IP to the sketch. Resending covers the sketch
# starting after Python, so the matrix never stays blank.
RESEND_PERIOD_S = 5

last_ip = None


def get_ip() -> str:
    """The board's own IP address (wlan0 on WiFi).

    The app's Python runs inside a Docker container with its own network, so
    wlan0 isn't visible here and socket tricks return the container's 172.x
    address. App Lab passes the board's real IP into the container as the
    HOST_IP environment variable when the app starts.
    """
    return os.environ.get("HOST_IP", "").strip() or "no IP"


def loop():
    global last_ip
    ip = get_ip()
    try:
        Bridge.call("set_text", ip)
        if ip != last_ip:
            logger.info(f"Showing IP: {ip}")
            last_ip = ip
    except Exception as e:
        logger.warning(f"Bridge call failed: {e}")
    time.sleep(RESEND_PERIOD_S)


App.run(user_loop=loop)
