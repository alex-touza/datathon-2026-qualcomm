"""Ask the small language model for one letter, A to E."""
import time

from .config import CONFIG
from .client import post

URL = f"http://{CONFIG.get('MODEL_HOST', '127.0.0.1')}:{CONFIG['LLM_PORT']}/v1/chat/completions"

# Keep the system prompt stable: llama-server caches the start of the prompt,
# so text that never changes is only read once.
SYSTEM = ("You solve multiple-choice math competition problems for school students. "
          "Read the problem and the options, then reply with the letter of the correct option.")


def format_problem(problem, sees_image=False):
    lines = [problem["text"].strip()]
    options = problem.get("options") or {}
    for letter in "ABCDE":
        if letter in options:
            lines.append(f"{letter}) {options[letter]}")
    if not options:
        lines.append("(The options are in the picture.)" if sees_image else
                     "(The options are in a picture that you can't see. Pick the most likely letter.)")
    return "\n".join(lines)


def build_messages(problem, examples=(), image_b64=None):
    """image_b64 is a PNG as base64 text. Only a server started with --mmproj can see it."""
    messages = [{"role": "system", "content": SYSTEM}]
    for ex in examples:  # solved problems retrieved with RAG, if any
        messages.append({"role": "user", "content": format_problem(ex) + "\nAnswer:"})
        messages.append({"role": "assistant", "content": ex["label"]})
    text = format_problem(problem, sees_image=bool(image_b64)) + "\nAnswer:"
    if image_b64:
        content = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image_b64}},
                   {"type": "text", "text": text}]
    else:
        content = text
    messages.append({"role": "user", "content": content})
    return messages


def answer_letter(problem, examples=(), timeout=60, image_b64=None, url=URL):
    """Return (letter, timings). The grammar makes any other output impossible."""
    response = post(url, {
        "messages": build_messages(problem, examples, image_b64),
        "grammar": "root ::= [A-E]",
        "max_tokens": 1,
        "temperature": 0,
        "seed": int(CONFIG["SEED"]),
        "cache_prompt": True,
    }, timeout=timeout)
    letter = response["choices"][0]["message"]["content"].strip()
    return letter, response.get("timings", {})


def letter_logprobs(problem, examples=(), timeout=60, image_b64=None, url=URL):
    """Return ({letter: logprob}, timings) for all five letters.

    Uses one request per letter: the grammar forces the token, and the server
    reports the token's score before the grammar, so the five numbers are the
    model's real preference over A..E. After the first request the prompt is
    cached, so the other four read only the last few tokens.

    timeout is a budget for all five requests, not for each one, so a slow
    server cannot make one problem take five times the limit.
    """
    messages = build_messages(problem, examples, image_b64)
    scores = {}
    timings = {"prompt_n": 0, "predicted_n": 0}
    deadline = time.monotonic() + timeout
    for letter in "ABCDE":
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("no time left for the five letter scores")
        response = post(url, {
            "messages": messages,
            "grammar": f'root ::= "{letter}"',
            "max_tokens": 1,
            "temperature": 0,
            "seed": int(CONFIG["SEED"]),
            "cache_prompt": True,
            "logprobs": True,
        }, timeout=remaining)
        choice = response["choices"][0]
        content = (choice.get("logprobs") or {}).get("content") or []
        if not content:
            raise RuntimeError("the server returned no logprobs; use llama-server with logprobs support")
        scores[letter] = content[0]["logprob"]
        measured = response.get("timings", {})
        timings["prompt_n"] += measured.get("prompt_n", 0)
        timings["predicted_n"] += measured.get("predicted_n", 0)
    return scores, timings
