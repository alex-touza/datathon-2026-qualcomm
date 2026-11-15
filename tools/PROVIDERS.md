# Using a different model provider

`tools/generate_solutions.py` speaks the **OpenAI chat-completions API**. Any
endpoint that implements it works; you change four flags and nothing else.

```bash
python tools/generate_solutions.py \
  --base-url https://generativelanguage.googleapis.com/v1beta/openai \
  --model gemini-3.8-flash \
  --api-key-env GEMINI_API_KEY \
  --reasoning-effort "" \
  --no-seed \
  --max-tokens 12000 \
  --out data/explanations-demo.db \
  --name gemini-v1 \
  --limit-ids 10 --max-new 20
```

## The four flags that matter

| Flag | Meaning | DeepSeek | Gemini |
| --- | --- | --- | --- |
| `--base-url` | endpoint root; the tool appends `/chat/completions` | `https://api.deepseek.com` | `https://generativelanguage.googleapis.com/v1beta/openai` |
| `--model` | model id | `deepseek-v4-pro` | e.g. `gemini-3.8-flash` |
| `--api-key-env` | environment variable holding the key | `DEEPSEEK_API_KEY` | `GEMINI_API_KEY` |
| `--reasoning-effort` | DeepSeek's budget knob; **use `""`** where unsupported | `low` | `""` |

Optional flags, for endpoints that reject OpenAI-only fields:

- `--no-seed` — do not send `seed` (Gemini ignores it or rejects it).
- `--no-json-mode` — do not send `response_format`; the prompt alone already
  asks for a bare JSON object, and the parser strips code fences.
- `--api-key-file PATH` — read the key from a file instead of the environment.

## Smoke test before spending anything

Run **two rows** and look at the log:

```bash
python tools/generate_solutions.py --limit-ids 1 --max-new 2 --name smoke
```

You want `2/2 OK` and a plausible `(prompt+completion tok)` per row. If you get
`HTTP 400`, the provider's own message is printed verbatim — it is almost always
an unsupported field, so add `--no-seed` / `--no-json-mode` / `--reasoning-effort ""`.

A raw curl, when you want to see the shape of the response:

```bash
curl -sS "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $GEMINI_API_KEY" \
  -d '{"model":"gemini-3.8-flash","messages":[{"role":"user","content":"Say OK"}],"max_tokens":16}'
```

List the models your key can actually use:

```bash
curl -sS -H "Authorization: Bearer $GEMINI_API_KEY" \
  "https://generativelanguage.googleapis.com/v1beta/openai/models"
```

## What the tool already handles for you

- **Truncation.** Providers that spend hidden reasoning inside `max_tokens` come
  back empty or cut off. The tool retries the row **once with double the budget**
  (6000 → 12000). `finish_reason` values `length` / `MAX_TOKENS` are both
  recognised. This is what cut truncations from 18% of rows to 2%.
- **Response shapes.** `message.content` as a string *or* as a list of parts.
  Token usage from `usage.prompt_tokens` *or* `usage_metadata.prompt_token_count`.
- **JSON extraction.** A fenced block or surrounding prose is stripped before
  parsing.
- **Verification.** Provider-independent: every row goes through
  `copilot.solutions.check_parts`, so a different model's mistakes are caught by
  the same rules.

## Resuming, and not spending twice

- Generated rows are keyed by `id|language` in the SQLite DB; a second run skips
  them. `--retry-failed` regenerates only the unverified ones.
- `--max-new N` stops after N rows in this invocation.
- `--price-in` / `--price-out` (USD per million tokens) and `--max-spend` give a
  hard estimated-spend cap. Default prices are the measured DeepSeek figure
  (~$1.9/M); set your provider's real prices before relying on the number.

Measured on DeepSeek v4 Pro: **$0.0075/row**, 94% accepted, mean 3,400 output
tokens per row. Expect a different figure for Gemini; measure it with a small
`--max-new` run and the balance, not by guessing.

## Keys

- Never commit a key. `tools/api_key_*` is gitignored; `opencode.json` is local.
- Prefer the environment variable; `--api-key-file` exists for convenience.
- There is no key for the board: generation is **build-time only**, and the
  board reads the finished DB offline.
