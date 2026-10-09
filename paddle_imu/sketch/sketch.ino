// paddle_imu: reads an MPU6050 (GY-521) at 100 Hz and sends every sample to Python,
// and shows the game on the LED matrix.
//
// The sensor lives on the microcontroller's I2C pins, so the sketch does the
// reading (steady timing, no Linux in the way); Python only receives and logs.
// Same split as Arduino's real-time-accelerometer example: Bridge.notify().
// Bridge messages to Python:
//  - "imu_sample" (t_ms, ax, ay, az, gx, gy, gz)  accel in g, gyro in deg/s
//  - "imu_status" (text)                           which bus/address, or a scan
//
// It also drives the 8x13 LED matrix for the game display (Python decides
// what to show; same Bridge pattern as minifig_tracker):
//  - "show_dot"     (col, row) -> lights exactly one pixel (the ball)
//  - "show_frame"   (rows)     -> 8 rows as 32 hex chars (4 per row, 13 bits,
//                                 bit 12 = leftmost column): text / numbers
//  - "clear_matrix" ()         -> turns every pixel off
// The board runs one app at a time, so the IMU and the display share this app.
// Matrix writes happen in the Bridge thread and never block the 100 Hz IMU loop.
//
// No library: the MPU6050 needs two register writes to set up and one 14-byte
// read per sample, which plain Wire does. (Adafruit_MPU6050 drags in two more
// libraries and isn't known to support the UNO Q's Zephyr core.)
//
// UNO Q I2C buses (variant overlay: i2cs = <&i2c2>, <&i2c4>, <&i2c3>):
//   Wire  = i2c2   header SDA/SCL (expected for this wiring)
//   Wire1 = i2c4   Qwiic connector (Arduino's Modulino examples use Wire1)
//   Wire2 = i2c3   PC0/PC1 = A4/A5
// The sketch tries all three, at 0x68 (AD0 low) and 0x69 (AD0 high). If the
// sensor isn't found it scans every bus, reports what answered, and retries.

#include <Arduino_LED_Matrix.h>
#include <Arduino_RouterBridge.h>
#include <Wire.h>
#include <zephyr/kernel.h>

static const unsigned long SAMPLE_PERIOD_US = 10000;  // 100 Hz
static const unsigned long STATUS_PERIOD_MS = 5000;   // re-send status (Python may start late)
static const unsigned long RETRY_PERIOD_MS = 3000;    // look for the sensor again

// MPU6050 registers
static const uint8_t REG_SMPLRT_DIV = 0x19;
static const uint8_t REG_CONFIG = 0x1A;
static const uint8_t REG_GYRO_CONFIG = 0x1B;
static const uint8_t REG_ACCEL_CONFIG = 0x1C;
static const uint8_t REG_ACCEL_XOUT_H = 0x3B;  // 14 bytes: accel xyz, temp, gyro xyz
static const uint8_t REG_PWR_MGMT_1 = 0x6B;
static const uint8_t REG_WHO_AM_I = 0x75;

// Ranges: a paddle swing easily exceeds the default +-2 g, so use +-8 g and
// +-2000 deg/s. Scale factors from the MPU6050 datasheet.
static const uint8_t ACCEL_FS_8G = 0x10;       // ACCEL_CONFIG bits 4:3 = 2
static const uint8_t GYRO_FS_2000DPS = 0x18;   // GYRO_CONFIG bits 4:3 = 3
static const float ACCEL_LSB_PER_G = 4096.0f;
static const float GYRO_LSB_PER_DPS = 16.4f;
static const uint8_t DLPF_44HZ = 0x03;         // low-pass ~44 Hz: below Nyquist at 100 Hz

struct Bus {
  TwoWire *wire;
  const char *name;
};
static Bus BUSES[] = {{&Wire, "Wire (header SDA/SCL)"},
                      {&Wire1, "Wire1 (Qwiic)"},
                      {&Wire2, "Wire2 (A4/A5)"}};
static const int NUM_BUSES = sizeof(BUSES) / sizeof(BUSES[0]);
static const uint8_t ADDRESSES[] = {0x68, 0x69};

// --- LED matrix (from minifig_tracker) ------------------------------------
// Dimensions from Arduino_LED_Matrix.h (canvasWidth = 13, canvasHeight = 8).
static const int MATRIX_COLS = 13;
static const int MATRIX_ROWS = 8;
static const uint8_t DOT_BRIGHTNESS = 7;  // 3-bit grayscale: 0..7

Arduino_LED_Matrix matrix;
K_MUTEX_DEFINE(matrix_mtx);  // Bridge providers run on a separate thread
static uint8_t frame[MATRIX_ROWS * MATRIX_COLS];  // row-major, one byte per pixel

void show_dot(int col, int row) {
  if (col < 0 || col >= MATRIX_COLS || row < 0 || row >= MATRIX_ROWS) return;
  k_mutex_lock(&matrix_mtx, K_FOREVER);
  memset(frame, 0, sizeof(frame));
  frame[row * MATRIX_COLS + col] = DOT_BRIGHTNESS;
  matrix.draw(frame);
  k_mutex_unlock(&matrix_mtx);
}

void show_frame(String rows) {
  if (rows.length() != MATRIX_ROWS * 4) return;
  k_mutex_lock(&matrix_mtx, K_FOREVER);
  for (int r = 0; r < MATRIX_ROWS; r++) {
    unsigned long bits = strtoul(rows.substring(r * 4, r * 4 + 4).c_str(), nullptr, 16);
    for (int c = 0; c < MATRIX_COLS; c++) {
      frame[r * MATRIX_COLS + c] = (bits >> (MATRIX_COLS - 1 - c)) & 1 ? DOT_BRIGHTNESS : 0;
    }
  }
  matrix.draw(frame);
  k_mutex_unlock(&matrix_mtx);
}

void clear_matrix() {
  k_mutex_lock(&matrix_mtx, K_FOREVER);
  memset(frame, 0, sizeof(frame));
  matrix.draw(frame);
  k_mutex_unlock(&matrix_mtx);
}

// --- IMU ---------------------------------------------------------------------
static TwoWire *imu_wire = nullptr;  // bus the sensor was found on
static uint8_t imu_addr = 0;
static String status_text = "starting";
static unsigned long last_status_ms = 0;
static unsigned long last_retry_ms = 0;
static unsigned long next_sample_us = 0;

static bool write_reg(TwoWire *w, uint8_t addr, uint8_t reg, uint8_t value) {
  w->beginTransmission(addr);
  w->write(reg);
  w->write(value);
  return w->endTransmission() == 0;
}

static bool read_regs(TwoWire *w, uint8_t addr, uint8_t reg, uint8_t *buf, size_t len) {
  w->beginTransmission(addr);
  w->write(reg);
  if (w->endTransmission(false) != 0) return false;  // repeated start
  if (w->requestFrom(addr, (uint8_t)len) != len) return false;
  for (size_t i = 0; i < len; i++) buf[i] = w->read();
  return true;
}

static void send_status(const String &text) {
  status_text = text;
  Bridge.notify("imu_status", status_text);
  last_status_ms = millis();
}

// Try every bus/address; on success configure the sensor and remember where it is.
static bool find_imu() {
  for (int b = 0; b < NUM_BUSES; b++) {
    TwoWire *w = BUSES[b].wire;
    for (uint8_t addr : ADDRESSES) {
      uint8_t who = 0;
      if (!read_regs(w, addr, REG_WHO_AM_I, &who, 1)) continue;
      // Wake up (clear sleep), clock from gyro X PLL, then set ranges and filter.
      bool ok = write_reg(w, addr, REG_PWR_MGMT_1, 0x01);
      delay(50);
      ok = ok && write_reg(w, addr, REG_SMPLRT_DIV, 0x00) &&
           write_reg(w, addr, REG_CONFIG, DLPF_44HZ) &&
           write_reg(w, addr, REG_GYRO_CONFIG, GYRO_FS_2000DPS) &&
           write_reg(w, addr, REG_ACCEL_CONFIG, ACCEL_FS_8G);
      if (!ok) continue;
      imu_wire = w;
      imu_addr = addr;
      // WHO_AM_I: 0x68 = genuine MPU6050; clones often report 0x70/0x72/0x98.
      send_status(String("MPU6050 found on ") + BUSES[b].name + " at 0x" + String(addr, HEX) +
                  ", WHO_AM_I=0x" + String(who, HEX) + "; streaming at 100 Hz");
      return true;
    }
  }
  return false;
}

// I2C scan of every bus, reported as one status line.
static void scan_buses() {
  String report = "MPU6050 not found. I2C scan:";
  for (int b = 0; b < NUM_BUSES; b++) {
    report += String(" | ") + BUSES[b].name + ":";
    int found = 0;
    for (uint8_t addr = 1; addr < 127; addr++) {
      BUSES[b].wire->beginTransmission(addr);
      if (BUSES[b].wire->endTransmission() == 0) {
        report += " 0x" + String(addr, HEX);
        found++;
      }
    }
    if (found == 0) report += " (nothing)";
  }
  send_status(report);
}

void setup() {
  matrix.begin();
  matrix.setGrayscaleBits(3);
  matrix.clear();

  Bridge.begin();
  Bridge.provide("show_dot", show_dot);
  Bridge.provide("show_frame", show_frame);
  Bridge.provide("clear_matrix", clear_matrix);
  for (int b = 0; b < NUM_BUSES; b++) {
    BUSES[b].wire->begin();
    BUSES[b].wire->setClock(400000);
  }
  if (!find_imu()) scan_buses();
  last_retry_ms = millis();
  next_sample_us = micros();
}

void loop() {
  unsigned long now_ms = millis();
  if (now_ms - last_status_ms >= STATUS_PERIOD_MS) send_status(status_text);

  if (imu_wire == nullptr) {
    if (now_ms - last_retry_ms >= RETRY_PERIOD_MS) {
      last_retry_ms = now_ms;
      if (!find_imu()) scan_buses();
    }
    delay(10);
    return;
  }

  // Fixed 100 Hz schedule (doesn't drift with how long each read takes)
  if ((long)(micros() - next_sample_us) < 0) return;
  next_sample_us += SAMPLE_PERIOD_US;

  uint8_t raw[14];
  if (!read_regs(imu_wire, imu_addr, REG_ACCEL_XOUT_H, raw, sizeof(raw))) {
    imu_wire = nullptr;  // lost it (loose wire?): go back to searching
    send_status("MPU6050 stopped answering; searching again");
    return;
  }
  // Big-endian signed 16-bit values; raw[6..7] is temperature (unused)
  auto val = [&](int i) { return (int16_t)((raw[i] << 8) | raw[i + 1]); };
  float ax = val(0) / ACCEL_LSB_PER_G, ay = val(2) / ACCEL_LSB_PER_G, az = val(4) / ACCEL_LSB_PER_G;
  float gx = val(8) / GYRO_LSB_PER_DPS, gy = val(10) / GYRO_LSB_PER_DPS, gz = val(12) / GYRO_LSB_PER_DPS;
  Bridge.notify("imu_sample", (uint32_t)now_ms, ax, ay, az, gx, gy, gz);
}
