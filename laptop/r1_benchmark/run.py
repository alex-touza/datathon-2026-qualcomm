"""Run a local llama.cpp model on one modality of an M3Kang manifest."""
import argparse
import base64
import concurrent.futures
import json
import mimetypes
from pathlib import Path
import random
import re
import time
from urllib.request import Request, urlopen

INSTRUCTIONS = {
    "cat": "Resol el problema. Dona només la lletra de l’opció correcta (A, B, C, D o E).",
    "eng": "Solve the problem. Give only the letter of the correct choice (A, B, C, D or E).",
    "esp": "Resuelve el problema. Da solo la letra de la opción correcta (A, B, C, D o E).",
}


def solve(row, args):
    out = {k: row[k] for k in ("id", "language", "level", "kind", "label")}
    prompt = INSTRUCTIONS[row["language"]]
    if row["kind"] == "text":
        options = "\n".join(f"{k}) {row['options'][k]}" for k in "ABCDE")
        content = prompt + "\n\n" + row["text"] + "\n" + options
    else:
        image_path = args.manifest.parent / row["image"]
        image = image_path.read_bytes()
        mime = mimetypes.guess_type(image_path.name)[0]
        content = [{"type": "image_url", "image_url": {
            "url": f"data:{mime};base64," + base64.b64encode(image).decode()
        }}, {"type": "text", "text": prompt}]

    payload = {"messages": [{"role": "user", "content": content}], "temperature": 0,
               "seed": 42, "max_tokens": args.max_tokens, "cache_prompt": False,
               "timings_per_token": True, "grammar": "root ::= [A-E]"}
    request = Request(args.url + "/v1/chat/completions", json.dumps(payload).encode(),
                      {"Content-Type": "application/json"})
    start = time.monotonic()
    try:
        with urlopen(request, timeout=75) as response:
            reply = json.load(response)
        choice = reply["choices"][0]
        message = choice["message"]
        raw = message.get("content") or ""
        match = re.fullmatch(r"\s*(?:\\boxed\{([A-E])\}|\(?([A-E])\)?[.)]?)\s*", raw)
        prediction = next((g for g in match.groups() if g), None) if match else None
        usage = reply["usage"]
        inputs, outputs = usage["prompt_tokens"], usage["completion_tokens"]
        out.update(status="ok", prompt_tokens=inputs, completion_tokens=outputs,
                   total_tokens=inputs + outputs, answer=prediction, correct=prediction == row["label"],
                   finish_reason=choice.get("finish_reason"), timings=reply.get("timings", {}),
                   # Reference rates only; native image encoding adds time outside this formula.
                   token_time_uno_ref_s=round(inputs / 11 + outputs / 5, 4))
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        out.update(status="error", error=str(exc), correct=False)
    return {**out, "wall_s": round(time.monotonic() - start, 6)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("manifest", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--kind", required=True, choices=("text", "image"))
    p.add_argument("--url", required=True)
    p.add_argument("--jobs", type=int, default=8)
    p.add_argument("--max-tokens", type=int, default=2)
    args = p.parse_args()
    rows = [json.loads(line) for line in args.manifest.read_text().splitlines()]
    rows = [row for row in rows if row["kind"] == args.kind]
    random.Random(42).shuffle(rows)
    start = time.monotonic()
    with args.output.open("x") as fh, concurrent.futures.ThreadPoolExecutor(args.jobs) as pool:
        for result in pool.map(lambda row: solve(row, args), rows):
            fh.write(json.dumps(result) + "\n")
            fh.flush()
    print(f"{args.kind}: {len(rows)} rows in {time.monotonic()-start:.2f} s")


if __name__ == "__main__":
    main()
