// show_ip: scrolls a line of text (the board's IP) across the 8x13 LED matrix.
// Python owns finding the IP; this sketch only exposes a Bridge provider:
//  - "set_text" (text) -> text to scroll, repeated until it changes

// ArduinoGraphics must be included first: it turns on the matrix's text support.
#include <ArduinoGraphics.h>
#include <Arduino_LED_Matrix.h>
#include <Arduino_RouterBridge.h>
#include <zephyr/kernel.h>

static const unsigned long SCROLL_SPEED_MS = 80;  // ms per 1-pixel step (smaller = faster)
static const unsigned long PAUSE_MS = 500;        // blank gap between repeats

Arduino_LED_Matrix matrix;

// Bridge providers run on a separate thread; serialize access to `text`.
K_MUTEX_DEFINE(text_mtx);

static String text = "";  // empty until Python sends the IP

void set_text(String new_text) {
  k_mutex_lock(&text_mtx, K_FOREVER);
  text = new_text;
  k_mutex_unlock(&text_mtx);
}

void setup() {
  matrix.begin();
  matrix.textFont(Font_5x7);
  matrix.textScrollSpeed(SCROLL_SPEED_MS);
  matrix.clear();

  Bridge.begin();
  Bridge.provide("set_text", set_text);
}

void loop() {
  k_mutex_lock(&text_mtx, K_FOREVER);
  String to_show = text;
  k_mutex_unlock(&text_mtx);

  if (to_show.length() == 0) {
    delay(100);
    return;
  }

  // Leading/trailing spaces make the text enter from the right edge and fully
  // leave on the left before the next repeat. endText(SCROLL_LEFT) blocks
  // until the scroll finishes; set_text still works meanwhile (other thread).
  matrix.beginText(0, 0, 127, 0, 0);  // x, y, then r, g, b (any nonzero = on)
  matrix.print("   " + to_show + "   ");
  matrix.endText(SCROLL_LEFT);
  matrix.clear();
  delay(PAUSE_MS);
}
