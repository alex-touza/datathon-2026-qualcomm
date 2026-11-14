"""The baseline copilot: one problem in, one letter out.

This is the file to change first. predict.py and eval.py both call solve(),
so whatever you build here is exactly what the official run measures.

It now also asks the model how sure it was, and can lean against the model's
letter habits. CALIBRATION_ALPHA=0 in config.env keeps the old behaviour.
"""
import socket
import time
import urllib.error

from .config import CONFIG
from .decide import choose, compact, confidence
from .llm import URL, letter_logprobs

TIME_LIMIT = float(CONFIG["TIME_LIMIT_SECONDS"])
FALLBACK = CONFIG["FALLBACK_ANSWER"]
MARGIN = 3.0  # seconds kept free for Python and for writing the answer
ALPHA = float(CONFIG.get("CALIBRATION_ALPHA") or 0)


def solve(problem, index=None, k=0, exclude_id=None, image_b64=None, llm_url=URL,
          time_limit=TIME_LIMIT):
    """Return {"answer", "seconds", "status", ...} for one problem.

    index and k turn on retrieval: the k most similar solved problems are
    added to the prompt as examples. Each example costs reading time.
    image_b64 and llm_url send the problem image to a vision server.
    """
    start = time.perf_counter()
    result = {"answer": FALLBACK, "status": "ok", "prompt_tokens": 0, "retrieved": "",
              "examples": [], "confidence": 0.0, "scores": ""}
    try:
        examples = []
        if index is not None and k > 0:
            hits = index.search(problem, k=k, exclude_id=exclude_id)
            examples = [p for _score, p in hits]
            result["examples"] = hits
            result["retrieved"] = ";".join(f"{p['id']}:{s:.2f}" for s, p in hits)
        remaining = time_limit - (time.perf_counter() - start) - MARGIN
        scores, timings = letter_logprobs(problem, examples, timeout=max(remaining, 1.0),
                                         image_b64=image_b64, url=llm_url)
        letter = max(scores, key=scores.get)
        # If the server sent no scores, keep exactly what the model wrote.
        result["answer"] = choose(scores, fallback=letter, alpha=ALPHA) if scores else letter
        result["confidence"] = round(confidence(scores, result["answer"]), 4)
        result["scores"] = compact(scores)
        result["prompt_tokens"] = timings.get("prompt_n", 0)
    except (socket.timeout, TimeoutError):
        result["status"] = "timeout"
    except urllib.error.URLError as error:
        if isinstance(error.reason, (socket.timeout, TimeoutError)):
            result["status"] = "timeout"
        else:
            result["status"] = f"error: {error.reason}"
    result["seconds"] = round(time.perf_counter() - start, 2)
    return result
