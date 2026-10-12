"""What the 13x8 LED matrix shows, from the game's MQTT messages.

No board-specific imports, so it can be tested on a laptop. main.py feeds it
game messages (ME193/CeciLaBarge/game) and asks it every ~50 ms what to draw:

  - no game message for GAME_TIMEOUT_S (3 s), or none yet -> the board's IP
  - PLAYING        -> the ball as one lit dot (like the minifig dot):
                      x 0..1 -> column 0..12 (left -> right),
                      y 0..1 -> row 0..7 (opponent's end at the top, mine at
                      the bottom: a view from above the table)
                      ...except right after each of my hits: the volley count
                      (my streak) for HIT_FLASH_S, while the ball flies away
                      to the opponent; then the dot again. (13 x 8 LEDs are
                      too few for digits and a moving dot at the same time.)
  - "flash" in the message -> that text, before anything else: S = SLOW-MO
                      or L = EXTRA LIFE card just activated, GO = serve
  - READY          -> the serve countdown number ("count": 3, 2, 1)
  - EXTRA_LIFE     -> the countdown number ("count": 5..1, EXTRA_LIFE_COUNTDOWN_S on the laptop) after a miss
  - WAITING        -> the level, e.g. "L1"
  - GAME_OVER      -> the final volley count ("final"; if missing, the last
                      count seen while PLAYING, since the game already reset
                      its streak to 0)

Text up to 13 columns wide is shown still and centered; wider text (the IP)
scrolls right to left.
"""
from typing import Optional

COLS, ROWS = 13, 8
GAME_TIMEOUT_S = 3.0
HIT_FLASH_S = 0.8        # volley count shown this long after each hit
SCROLL_STEP_S = 0.12     # one column per step
SCROLL_GAP = 4           # blank columns between repeats of scrolling text
TEXT_TOP_ROW = 1         # 5-row font drawn on rows 1..5

# 3x5 font: each glyph is 5 rows of 3 bits ("#" = on). Only the characters the
# display needs: digits, '.', and the letters in "L1" and "no IP".
_GLYPHS = {
    "0": ["###", "#.#", "#.#", "#.#", "###"],
    "1": [".#.", "##.", ".#.", ".#.", "###"],
    "2": ["###", "..#", "###", "#..", "###"],
    "3": ["###", "..#", ".##", "..#", "###"],
    "4": ["#.#", "#.#", "###", "..#", "..#"],
    "5": ["###", "#..", "###", "..#", "###"],
    "6": ["###", "#..", "###", "#.#", "###"],
    "7": ["###", "..#", ".#.", ".#.", ".#."],
    "8": ["###", "#.#", "###", "#.#", "###"],
    "9": ["###", "#.#", "###", "..#", "###"],
    "L": ["#..", "#..", "#..", "#..", "###"],
    "I": ["###", ".#.", ".#.", ".#.", "###"],
    "P": ["##.", "#.#", "##.", "#..", "#.."],
    "S": ["###", "#..", "###", "..#", "###"],
    "G": ["###", "#..", "#.#", "#.#", "###"],
    "O": ["###", "#.#", "#.#", "#.#", "###"],
    "n": ["...", "##.", "#.#", "#.#", "#.#"],
    "o": ["...", ".#.", "#.#", "#.#", ".#."],
    ".": [".", ".", ".", ".", "#"],
    " ": ["..", "..", "..", "..", ".."],
    "?": ["###", "..#", ".##", "...", ".#."],
}


def text_columns(text: str) -> list:
    """Render text to a list of columns; each column is 5 bools (top to bottom)."""
    cols = []
    for i, ch in enumerate(text):
        glyph = _GLYPHS.get(ch, _GLYPHS["?"])
        for c in range(len(glyph[0])):
            cols.append([row[c] == "#" for row in glyph])
        if i < len(text) - 1:
            cols.append([False] * 5)   # 1 blank column between characters
    return cols


def frame_rows(columns: list, offset: int = 0) -> tuple:
    """13 columns starting at `offset` (wrapping) -> 8 row bitmasks.
    Bit 12 is the leftmost column. Rows outside the font stay dark."""
    rows = [0] * ROWS
    n = len(columns)
    for c in range(COLS):
        col = columns[(offset + c) % n] if n else [False] * 5
        for r, on in enumerate(col):
            if on:
                rows[TEXT_TOP_ROW + r] |= 1 << (COLS - 1 - c)
    return tuple(rows)


def scale(value: float, cells: int) -> int:
    """Map value in [0, 1] to a cell index in [0, cells - 1] (as in minifig_tracker)."""
    return max(0, min(cells - 1, int(value * cells)))


class MatrixDisplay:
    def __init__(self, timeout_s: float = GAME_TIMEOUT_S):
        self.timeout_s = timeout_s
        self._msg: Optional[dict] = None
        self._msg_time: Optional[float] = None
        self._last_streak = 0      # streak seen while PLAYING
        self._flash_until = 0.0    # show the volley count until then (after a hit)
        self._text = None          # text currently shown (for scrolling)
        self._scroll_start = 0.0

    def on_game(self, msg: dict, now: float):
        """A game message arrived (any thread; replaced as a whole)."""
        self._msg, self._msg_time = msg, now

    def what_to_show(self, now: float, ip_text: str):
        """("dot", col, row) or ("rows", (8 row bitmasks))."""
        msg = self._msg
        if msg is None or self._msg_time is None or now - self._msg_time > self.timeout_s:
            return self._text_frame(ip_text, now)
        state = msg.get("state")
        if msg.get("flash"):                     # a power-up card just activated
            return self._text_frame(str(msg["flash"])[:3], now)
        if state in ("EXTRA_LIFE", "READY"):     # extra-life / serve countdown
            return self._text_frame(str(msg.get("count", "")), now)
        if state == "PLAYING":
            streak = int(msg.get("streak", 0))
            if streak > self._last_streak:      # a hit: flash the new count
                self._flash_until = now + HIT_FLASH_S
            self._last_streak = streak
            if now < self._flash_until:
                return self._text_frame(str(streak), now)
            self._text = None
            return ("dot", scale(float(msg.get("x", 0.5)), COLS), scale(float(msg.get("y", 0.5)), ROWS))
        if state == "WAITING":
            return self._text_frame(f"L{msg.get('level', 0)}", now)
        # GAME_OVER (streak already reset by the game): the rally's final volley count
        if "final" in msg:
            return self._text_frame(str(int(msg["final"])), now)
        return self._text_frame(str(max(self._last_streak, int(msg.get("streak", 0)))), now)

    @property
    def current_text(self):
        """The text being shown (None while showing the ball)."""
        return self._text

    def _text_frame(self, text: str, now: float):
        if text != self._text:
            self._text, self._scroll_start = text, now
        cols = text_columns(text)
        if len(cols) <= COLS:   # fits: show still, centered
            pad = (COLS - len(cols)) // 2
            cols = [[False] * 5] * pad + cols + [[False] * 5] * (COLS - len(cols) - pad)
            return ("rows", frame_rows(cols, 0))
        cols = cols + [[False] * 5] * SCROLL_GAP
        step = int((now - self._scroll_start) / SCROLL_STEP_S)
        return ("rows", frame_rows(cols, step % len(cols)))
