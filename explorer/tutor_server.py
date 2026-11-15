#!/usr/bin/env python3
"""The tutor on a web page, without App Lab. Standard library only.

    python3 explorer/tutor_server.py
    python3 explorer/tutor_server.py --host 0.0.0.0 --level 0 --language cat

Then open http://<board>:8010 on the laptop (see the README for the ssh tunnel).
This serves the same page and the same API as the App Lab app in
app/math-tutor/, and adds nothing but the LED matrix.

There is no free-form chat: the page can ask for a letter, the next rung, reveal,
next, and restart. That is the whole surface.
"""
import argparse
import base64
import json
import os
import sys
import time
import traceback
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from copilot.client import get  # noqa: E402
from copilot.config import CONFIG  # noqa: E402
from copilot.rag import Index  # noqa: E402
from copilot.tutor import Session, Tutor, image_mime, summary_line  # noqa: E402

PAGE = os.path.join(ROOT, "app", "math-tutor", "assets", "index.html")
LETTERS = set("ABCDE")


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--index", default="data/index.db", help="retrieval index from build_index.py")
    parser.add_argument("--images", default="data/images", help="problem images")
    parser.add_argument("--profile", default="data/tutor.db", help="where the student profile lives")
    parser.add_argument("--solutions", default="data/explanations.db",
                        help="verified three-part solutions DB")
    parser.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to open it to the network")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--level", type=int, default=0)
    parser.add_argument("--language", default="eng", choices=["eng", "cat"])
    return parser.parse_args()


def server_is_up(port):
    try:
        host = CONFIG.get("MODEL_HOST", "127.0.0.1")
        return get(f"http://{host}:{port}/health", timeout=2).get("status") == "ok"
    except (OSError, ValueError):
        return False


class App:
    def __init__(self, args):
        os.chdir(ROOT)
        if not os.path.exists(args.index):
            sys.exit(f"{args.index} is missing: run build_index.py on the board first.")
        self.tutor = Tutor(Index(args.index), profile_path=args.profile, images=args.images,
                           solutions_path=args.solutions)
        self.session = Session(self.tutor, level=args.level, language=args.language)
        self.images = args.images
        self.image_keys = {key for key in self.tutor.by_key}
        self.index_ids = {p["id"] for p in self.tutor.by_key.values()}
        self._health = {"at": 0.0, "llm": False, "embed": False}

    def image_path(self, key):
        """Allow an image key whose problem id is indexed, in any language.

        The page asks for the image in its own language, which may be the twin
        of the language of the problem text.
        """
        key = key.rsplit(".", 1)[0]
        if key.rsplit("_", 1)[0] not in self.index_ids:
            return None
        path = os.path.join(self.images, key + ".png")
        return path if os.path.exists(path) else None

    def servers(self):
        """Cached for ten seconds: the page polls, and /health twice a poll is waste."""
        if time.time() - self._health["at"] > 10:
            for name in ("llm", "embed"):
                self._health[name] = server_is_up(CONFIG[f"{name.upper()}_PORT"])
            self._health["at"] = time.time()
        return {"llm": self._health["llm"], "embed": self._health["embed"]}

    def state(self):
        state = self.session.state()
        state["servers"] = self.servers()
        state["busy"] = False
        state["time_limit"] = float(CONFIG["TIME_LIMIT_SECONDS"])
        return state


def make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt, *args):
            sys.stderr.write("  " + fmt % args + "\n")

        def send(self, code, body, kind="application/json"):
            data = body if isinstance(body, bytes) else json.dumps(
                body, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def act(self, result):
            """A session action returns the whole state, or {"error": ...}."""
            if "error" in result:
                return self.send(409, result)
            return self.send(200, app.state())

        def do_GET(self):
            path = self.path.partition("?")[0]
            parts = [part for part in path.strip("/").split("/") if part]
            try:
                if path in ("/", "/index.html"):
                    with open(PAGE, "rb") as handle:
                        return self.send(200, handle.read(), "text/html; charset=utf-8")
                if path == "/api/state":
                    return self.send(200, app.state())
                if parts[:2] == ["api", "image"] and len(parts) == 3:
                    file = app.image_path(parts[2])
                    if file:
                        with open(file, "rb") as handle:
                            raw = handle.read()
                            return self.send(200, {"data": base64.b64encode(raw).decode(),
                                                   "mime": image_mime(raw)})
                    return self.send(404, {"error": "no image"})
                if parts[:2] == ["api", "first"]:
                    level = int(parts[2]) if len(parts) > 2 else 0
                    language = parts[3] if len(parts) > 3 else "eng"
                    return self.act(app.session.start(level=level, language=language))
                if parts[:2] == ["api", "topic"] and len(parts) >= 5:
                    # Practise one topic: /api/topic/<topic>/<level>/<language>
                    topic, level, language = parts[2], int(parts[3]), parts[4]
                    return self.act(app.session.start(level=level, language=language,
                                                      topic=topic))
                if parts[:2] == ["api", "reset"]:
                    # A restart starts counting from zero: the practice profile
                    # the page shows goes with the session.
                    return self.act(app.session.start(reset_profile=True))
                if parts[:2] == ["api", "finish"]:
                    return self.act(app.session.finish())
                if parts[:2] == ["api", "report"]:
                    if not app.session.finished or app.session.report is None:
                        return self.send(409, {"error": "finish the session first"})
                    return self.send(200, app.session.report)
                if parts[:1] == ["api"] and len(parts) >= 3:
                    action, key = parts[1], parts[2]
                    if key != app.session.current_key():
                        return self.send(409, {"error": "that problem is not on screen"})
                    if action == "answer" and len(parts) == 4 and parts[3].upper() in LETTERS:
                        return self.act(app.session.answer(parts[3]))
                    if action == "hint" and len(parts) == 4 and parts[3].isdigit():
                        result = app.session.hint(int(parts[3]))
                        if app.session.hints:
                            print(summary_line(app.session.hints[-1]), flush=True)
                        return self.act(result)
                    if action == "hint" and len(parts) == 3:
                        result = app.session.hint()
                        if app.session.hints:
                            print(summary_line(app.session.hints[-1]), flush=True)
                        return self.act(result)
                    if action == "reveal" and len(parts) == 3:
                        return self.act(app.session.reveal())
                    if action == "next" and len(parts) == 3:
                        return self.act(app.session.next())
                    return self.send(400, {"error": "bad request"})
                if parts[:1] == ["image"] and len(parts) == 2:
                    key = parts[1].rsplit(".", 1)[0]
                    file = os.path.join(app.images, key + ".png")
                    if key in app.image_keys and os.path.exists(file):
                        with open(file, "rb") as handle:
                            raw = handle.read()
                            return self.send(200, raw, image_mime(raw))
                return self.send(404, {"error": "not found"})
            except urllib.error.URLError as error:
                return self.send(502, {"error": f"model server: {error}"})
            except Exception:
                traceback.print_exc()
                return self.send(500, {"error": "internal error, see the log"})

        def do_POST(self):
            # The page is read-only over GET; POST exists only to say no plainly.
            length = int(self.headers.get("Content-Length", 0))
            if length:
                self.rfile.read(length)   # drain it, so keep-alive stays in sync
            return self.send(405, {"error": "use the buttons; the tutor has no free-form chat"})

    return Handler


def main():
    args = parse_args()
    app = App(args)
    servers = app.state()["servers"]
    print(f"Tutor: {len(app.tutor.by_key)} solved problems, level {args.level}, {args.language}; "
          f"{len(app.tutor.solutions)} cached solutions")
    print(f"Servers: language model {'up' if servers['llm'] else 'DOWN'}, "
          f"embeddings {'up' if servers['embed'] else 'DOWN'}")
    print(f"Open http://{args.host}:{args.port} (Ctrl+C to stop)", flush=True)
    ThreadingHTTPServer((args.host, args.port), make_handler(app)).serve_forever()


if __name__ == "__main__":
    main()
