"""The tutor as an App Lab app: the same page as explorer/tutor_server.py, plus LEDs.

The page asks for a problem, a letter, a hint rung, reveal or next. Python does
the work and mirrors it on the 8x13 LED matrix:

    new problem  -> the level as a bar
    hint         -> the rung number, then the level again
    answer       -> a tick or a cross, then the level again

Slow work (the model hint) runs in a thread; the page polls /api/state, so the
LEDs never block the web page and the web page never blocks the model.
"""
import base64
import os
import sys
import threading
import time
import traceback

from arduino.app_bricks.web_ui import WebUI
from arduino.app_utils import App, Bridge

try:  # the matrix needs numpy, which App Lab provides
    import numpy as np
    from arduino.app_utils import Frame
except ImportError:  # pragma: no cover - only when running outside App Lab
    np = None
    Frame = None

HERE = os.path.dirname(os.path.abspath(__file__))


def find_repo():
    """The repository that holds copilot/tutor.py, on the board or on a laptop."""
    candidates = [
        os.environ.get("MATH_TUTOR_REPO"),
        os.path.abspath(os.path.join(HERE, "..", "..", "..")),  # repo/app/math-tutor/python
        os.path.expanduser("~/datathon-UPC-maths-qualcomm-arduino-unoq"),
    ]
    for candidate in candidates:
        if candidate and os.path.exists(os.path.join(candidate, "copilot", "tutor.py")):
            return candidate
    raise SystemExit("Cannot find the repository with copilot/tutor.py. "
                     "Set MATH_TUTOR_REPO to its path.")


REPO = find_repo()
sys.path.insert(0, REPO)
os.chdir(REPO)

from copilot.client import get  # noqa: E402
from copilot.config import CONFIG  # noqa: E402
from copilot.rag import Index  # noqa: E402
from copilot.tutor import Session, Tutor, image_mime, summary_line  # noqa: E402

INDEX = os.path.join(REPO, "data", "index.db")
IMAGES = os.path.join(REPO, "data", "images")
PROFILE = os.path.join(REPO, "data", "tutor.db")
SOLUTIONS = os.path.join(REPO, CONFIG.get("SOLUTIONS_DB", "data/explanations.db"))

if not os.path.exists(INDEX):
    raise SystemExit(f"{INDEX} is missing. Run build_index.py on the board first.")

TUTOR = Tutor(Index(INDEX), profile_path=PROFILE, images=IMAGES, solutions_path=SOLUTIONS)
SESSION = Session(TUTOR, level=0, language="eng")

ACTION_LOCK = threading.Lock()
BUSY = {"on": False, "label": ""}

# --------------------------------------------------------------------------
# LED matrix: 8 rows by 13 columns, brightness 0 to 7
# --------------------------------------------------------------------------

GLYPHS = {
    "A": [".###.", "#...#", "#...#", "#####", "#...#", "#...#", "#...#"],
    "B": ["####.", "#...#", "#...#", "####.", "#...#", "#...#", "####."],
    "C": [".###.", "#...#", "#....", "#....", "#....", "#...#", ".###."],
    "D": ["####.", "#...#", "#...#", "#...#", "#...#", "#...#", "####."],
    "E": ["#####", "#....", "#....", "####.", "#....", "#....", "#####"],
    "0": [".###.", "#...#", "#..##", "#.#.#", "##..#", "#...#", ".###."],
    "1": ["..#..", ".##..", "..#..", "..#..", "..#..", "..#..", ".###."],
    "2": [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
    "3": ["####.", "....#", "....#", ".###.", "....#", "....#", "####."],
    "4": ["...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."],
    "5": ["#####", "#....", "####.", "....#", "....#", "#...#", ".###."],
    "6": [".###.", "#....", "#....", "####.", "#...#", "#...#", ".###."],
    "7": ["#####", "....#", "...#.", "..#..", ".#...", ".#...", ".#..."],
    "8": [".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."],
    "9": [".###.", "#...#", "#...#", ".####", "....#", "....#", ".###."],
    "?": [".###.", "#...#", "....#", "...#.", "..#..", ".....", "..#.."],
    "!": ["..#..", "..#..", "..#..", "..#..", "..#..", ".....", "..#.."],
    "tick": [".....", ".....", "....#", "...#.", "#.#..", ".#...", "....."],
    "cross": [".....", ".....", "#...#", ".#.#.", "..#..", ".#.#.", "#...#"],
}

DRAW_LOCK = threading.Lock()
GENERATION = {"n": 0}


def _send(grid):
    if np is None or Frame is None:
        return
    with DRAW_LOCK:
        try:
            Bridge.call("draw", Frame(grid).to_board_bytes())
        except Exception:  # the matrix is a demo surface; never let it break the tutor
            traceback.print_exc()


def glyph_grid(text):
    grid = np.zeros((8, 13), dtype=np.uint8)
    text = text[:2]
    width = 5 * len(text) + (len(text) - 1)
    left = max(0, (13 - width) // 2)
    for position, char in enumerate(text):
        pattern = GLYPHS.get(char, GLYPHS["?"])
        for row, line in enumerate(pattern):
            for column, pixel in enumerate(line):
                if pixel == "#":
                    grid[row, left + position * 6 + column] = 7
    return grid


def level_grid(level, top=6):
    """A bar whose length is the level, so progress is visible from across the room."""
    grid = np.zeros((8, 13), dtype=np.uint8)
    level = max(0, min(top, int(level)))
    filled = max(1, round(13 * (level + 1) / (top + 1)))
    for row in (2, 3, 4, 5):
        grid[row, :filled] = 7
    for column in range(filled, 13):
        grid[3, column] = 1
        grid[4, column] = 1
    return grid


def show(grid, hold=None, level=None):
    """Draw now, and after `hold` seconds go back to the level bar."""
    GENERATION["n"] += 1
    generation = GENERATION["n"]
    _send(grid)
    if hold:
        def later():
            if GENERATION["n"] == generation and level is not None:
                _send(level_grid(level))
        threading.Timer(hold, later).start()


def show_state_matrix(reason=None):
    level = SESSION.level
    if reason == "answer" and SESSION.result is not None:
        show(glyph_grid("tick" if SESSION.result["correct"] else "cross"), hold=2.5, level=level)
    elif reason == "hint" and SESSION.hints:
        show(glyph_grid(str(SESSION.hints[-1]["rung"])), hold=2.0, level=level)
    else:
        show(level_grid(level))


# --------------------------------------------------------------------------
# Servers, cached so polling /api/state stays cheap
# --------------------------------------------------------------------------

HEALTH = {"at": 0.0, "llm": False, "embed": False}


def servers():
    if time.time() - HEALTH["at"] > 10:
        for name, port in (("llm", CONFIG["LLM_PORT"]), ("embed", CONFIG["EMBED_PORT"])):
            try:
                HEALTH[name] = get(f"http://127.0.0.1:{port}/health", timeout=2).get("status") == "ok"
            except (OSError, ValueError):
                HEALTH[name] = False
        HEALTH["at"] = time.time()
    return {"llm": HEALTH["llm"], "embed": HEALTH["embed"]}


def state():
    snapshot = SESSION.state()
    snapshot["servers"] = servers()
    snapshot["busy"] = BUSY["on"]
    snapshot["time_limit"] = float(CONFIG["TIME_LIMIT_SECONDS"])
    return snapshot


# --------------------------------------------------------------------------
# The API, one function per button
# --------------------------------------------------------------------------

def api_state():
    return state()


def api_first(level="0", language="eng"):
    with ACTION_LOCK:
        SESSION.start(level=int(level), language=language)
    show_state_matrix()
    return state()


def api_answer(key, letter):
    with ACTION_LOCK:
        if key != SESSION.current_key():
            return {"error": "that problem is not on screen"}
        result = SESSION.answer(letter)
    if "error" in result:
        return result
    show_state_matrix("answer")
    return state()


def api_hint(key, rung):
    with ACTION_LOCK:
        if key != SESSION.current_key():
            return {"error": "that problem is not on screen"}
        if BUSY["on"]:
            return {"error": "still working"}
        BUSY["on"] = True
    BUSY["label"] = "hint"
    show(glyph_grid("?"))

    def work():
        try:
            SESSION.hint(int(rung))
            if SESSION.hints:
                print(summary_line(SESSION.hints[-1]), flush=True)
            show_state_matrix("hint")
        except Exception:
            traceback.print_exc()
        finally:
            BUSY["on"] = False

    threading.Thread(target=work, daemon=True).start()
    return {"pending": True}


def api_reveal(key):
    with ACTION_LOCK:
        if key != SESSION.current_key():
            return {"error": "that problem is not on screen"}
        result = SESSION.reveal()
    if "error" in result:
        return result
    show_state_matrix()
    return state()


def api_next(key):
    with ACTION_LOCK:
        if key != SESSION.current_key():
            return {"error": "that problem is not on screen"}
        result = SESSION.next()
    if "error" in result:
        return result
    show_state_matrix()
    return state()


def api_image(key):
    key = key.rsplit(".", 1)[0]
    path = os.path.join(IMAGES, key + ".png")
    if key not in TUTOR.by_key or not os.path.exists(path):
        return {"error": "no image"}
    with open(path, "rb") as handle:
        raw = handle.read()
        return {"data": base64.b64encode(raw).decode(), "mime": image_mime(raw)}


UI = WebUI()
UI.expose_api("GET", "/api/state", api_state)
UI.expose_api("GET", "/api/first/{level}/{language}", api_first)
UI.expose_api("GET", "/api/answer/{key}/{letter}", api_answer)
UI.expose_api("GET", "/api/hint/{key}/{rung}", api_hint)
UI.expose_api("GET", "/api/reveal/{key}", api_reveal)
UI.expose_api("GET", "/api/next/{key}", api_next)
UI.expose_api("GET", "/api/image/{key}", api_image)

show_state_matrix()
print(f"Tutor ready: {len(TUTOR.by_key)} solved problems, "
      f"{len(TUTOR.solutions)} cached solutions, profile {PROFILE}", flush=True)
App.run()
