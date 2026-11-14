#!/usr/bin/env python3
"""Two fake llama.cpp servers, to test the tutor loop without models.

    python3 tools/fake_servers.py          # embeddings on 9092, hints on 9091

They speak just enough of the llama.cpp HTTP API for ``copilot/embed.py`` and
``copilot/tutor.py``, with no model and no download:

* embeddings are a deterministic hashed bag of words, so texts that share words
  really are nearer each other, and the same text always gets the same vector;
* hints are canned sentences, one of which deliberately repeats an option value
  so that the leak check has something to catch.

Use it to verify plumbing and to demo the loop offline. Numbers from it are not
measurements: latency is meaningless and the hint texts are not the 0.8B model.
"""
import argparse
import hashlib
import json
import math
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DIM = 256
OPTION_RE = re.compile(r"^\s*([A-E])\)\s*(.+)$", re.MULTILINE)

NEUTRAL_HINTS = (
    "Start by naming the quantities the problem gives you.",
    "Write the question in your own words before choosing an operation.",
    "Try a smaller example with the same numbers first.",
    "Draw the situation, then look for what stays the same.",
    "Check which unit the question asks for before you calculate.",
)


def embed_one(text):
    vector = [0.0] * DIM
    for word in re.findall(r"[^\W_]+", (text or "").lower()):
        digest = hashlib.md5(word.encode()).digest()
        for i in range(0, 8, 2):
            index = digest[i] % DIM
            sign = 1.0 if digest[i + 1] & 1 else -1.0
            vector[index] += sign
    norm = math.sqrt(sum(x * x for x in vector)) or 1.0
    return [x / norm for x in vector]


def hint_for(prompt):
    """A canned hint; every second one repeats an option value to test the checker."""
    options = OPTION_RE.findall(prompt or "")
    seed = int(hashlib.md5((prompt or "").encode()).hexdigest()[:8], 16)
    if options and seed % 2 == 0:
        letter, value = options[seed % len(options)]
        return f"You could work from {value.strip()} and see what it becomes."
    return NEUTRAL_HINTS[seed % len(NEUTRAL_HINTS)]


def make_handler():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _send(self, body, code=200):
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.endswith("/health"):
                return self._send({"status": "ok", "fake": True})
            return self._send({"error": "not found"}, 404)

        def do_POST(self):
            size = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(size) or b"{}")
            if self.path.endswith("/v1/embeddings"):
                inputs = body.get("input") or []
                return self._send({"data": [{"index": i, "embedding": embed_one(text)}
                                            for i, text in enumerate(inputs)]})
            if self.path.endswith("/v1/chat/completions"):
                prompt = "\n".join(str(message.get("content", ""))
                                   for message in body.get("messages", []))
                text = hint_for(prompt)
                return self._send({
                    "choices": [{"message": {"content": text}}],
                    "timings": {"prompt_n": len(prompt) // 4, "predicted_n": 8}})
            return self._send({"error": "not found"}, 404)

    return Handler


def start(embed_port=9092, llm_port=9091):
    """Start both servers in daemon threads. Returns [(server, thread), ...]."""
    started = []
    for port in (embed_port, llm_port):
        server = ThreadingHTTPServer(("127.0.0.1", port), make_handler())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append((server, thread))
    return started


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--embed-port", type=int, default=9092)
    parser.add_argument("--llm-port", type=int, default=9091)
    args = parser.parse_args()
    start(args.embed_port, args.llm_port)
    print(f"fake embeddings on {args.embed_port}, fake hints on {args.llm_port}. Ctrl+C to stop.")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        print("stopped")


if __name__ == "__main__":
    main()
