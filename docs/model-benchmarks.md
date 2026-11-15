# Model benchmark notes

The [main results table](../README.md#results) includes results from different
samples and protocols, all with real contest problems. Sample sizes count
language versions as separate inputs. These are internal tests; the organizers'
hidden set is separate. The project keeps M3Kang source material and per-case
logs locally. The aggregate results and benchmark code below do not contain
problem text, scans or answer keys.

## Historical runs

| README run | Local evidence and setup |
| --- | --- |
| DeepSeek exam predictor, practice ZIP | “Implementa predict.py per al test”; local commit `10eccda`, `eval-20261115-114346-discord-final.csv`. Precomputed OCR, 50 Catalan / 50 English, 20 rows labelled as having a figure. Inference timings exclude OCR. The 57.005 s fallback checked in that thread was a simulated stalled server. |
| DeepSeek base / LoRA and OCR pilot | “Benchmark DeepSeek a la placa”; `mac-short-base.jsonl`, `mac-short-lora.jsonl`, `summary-e2e.json`. Same 178 holdout prompts on the Mac. Different, four-problem board smoke test with OCR (all four arrived by 60 s). |
| d3-mini Q3 and Q4, d3-lite Q8, 80 original scans | “Investiga alternatives locals a JEV”; `d3-mini-uno-q.md` and `d3-benchmark/*-metal-multilingual.jsonl`. Full source scan in every request; the sample contains 40 text-solvable and 40 visual problems. The Q3 got 17/40 and 12/40 respectively; Q4 got 16/40 and 13/40. |
| d3-mini pruning | Same chat; `mini-prune-holdout/report.md`. Same 220 text/visual inputs with the full vision budget; Q3 full p50 3.12 s, minus eight blocks p50 2.85 s. |
| d3-mini Q3 board | “Benchmark d3-mini Q3 on Uno Q”; `uno-q/results.csv`. Native decision endpoint, zero generated tokens, 135–187 prompt tokens. Full answers measured with an extended timeout; every sample exceeded the 60 s deadline. |
| d3-nano, Liquid d1-omni, Laya English and Laya Vision | “Investiga alternatives locals a JEV”; `ampliacio-models/protocol.md`, `summary.json` and per-model JSONL. Selected separated-input run; Laya Vision row uses the prefixed-options arm. The Nano's matched 36-scan pilot (`nano-quick/nano.jsonl`) was 10/36 (27.8%), p50 1.39 s. |
| Liquid d1-2B Q4 and qev-0.8b labqoat | Same chat; `nano-quick/comparativa.md`, `ocr-image-only/resultats.md`. Liquid’s 36-scan pilot is distinct from the 80-scan qev run. qev-labqoat is a separate checkpoint from the older Jev-Vision rows. |
| d3-edge, d3-lite and Liquid d1-2B on the board | Same chat; `presentation-board/comparativa.md` (three cases, all returned within 60 s) and `uno-q-followup/summary.json` (12 CA/EN cases with literal text or OCR plus figure, Lite p50 27.48 s and Liquid p50 54.38 s). d3-edge also scored 12/58 (20.7%) in the separate `presentation-colab/paired-q8/resultats.md` comparison. |
| Jev 1.14 API | Remote reference only: 72/160 (45.0%) real M3Kang, all returned within 60 s, p50 0.282 s. Three languages, eight levels, 10 text + 10 visual problems per level **across** languages. Source: `real-160-local/jev-final/summary.json` and `results.jsonl` in the local M3Kang benchmark archive. |

## Balanced Mac benchmark

We selected 160 distinct problems (80 only text, 80 needing a figure or visual
options) from M3Kang's pinned `high_quality` revision
`e772e611d45679cf48f1c92618b2963a40d1d584`. Each problem appears in Catalan,
English and Spanish. That makes **480 inputs; ten per level × language × input
type**. Text uses five numeric choices with identical OCR options in our
Catalan and English caches; these language-neutral values were reused for
Spanish without an independent Spanish OCR check. The samples cover the available train/validation pool; this is an
exploratory benchmark. The vision requests give Qwen the full source scan and
a localized instruction. Distinct problems and models are used in each modality.

Both models ran offline on an Apple M4 with llama.cpp `b1b770d90`, Flash
Attention, eight concurrent slots, temperature 0 and seed 42. The server log
shows 29/29 DeepSeek layers and 26/26 Qwen layers offloaded to Metal. The
Qwen vision projector also used Metal. The tested
configuration disables reasoning and constrains the answer to A–E (at most two
generated tokens). This measures the short answer mode. In a separate discarded
diagnostic, all 208 observed text responses ran out of the 80-token cap without a
valid letter. It is excluded from the results. Neither run changes the project
predictor or its deadline handling.

The client times the request after building the payload. Mac time includes
server queueing and image encoding where applicable. It excludes model loading,
scan extraction, client-side Base64 encoding and any OCR. Token counts are the
server's `usage.prompt_tokens` and `usage.completion_tokens` (including
terminal tokens counted by the server); the image prompt includes its visual
tokens. **Mac >60 s** counts observed client times. **UNO Q est.** counts a
token-based scenario: for DeepSeek, `prompt / 3.233 + completion / 1.837`
using median rates from the prior four-case board pilot; for Qwen,
`prompt / 11 + completion / 5` using the reference rates in the datathon guide.
These are approximations that omit OCR, image preprocessing and fixed overhead.
The Qwen scenario does not measure UNO Q vision speed. The two model/tokenizer
counts and modality scores cannot isolate the effect of adding an image.

<!-- BEGIN MAC RESULTS -->

#### **DeepSeek R1 1.5B IQ3_M — text**

| Group | Correct | Always D | Valid answers | Median in / out tokens | Mac mean / p50 / p95 / max (s) | Mac >60 s | UNO Q est. p50 / count >60 s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| All | 40/240 (16.7%) | 48 | 240 | 123.5 / 2 | 2.24 / 2.15 / 3.26 / 4.48 | 0 | 39.3 s / 1 |
| cat | 14/80 (17.5%) | 16 | 80 | 137.5 / 2 | 2.30 / 2.19 / 3.20 / 4.48 | 0 | 43.6 s / 1 |
| eng | 13/80 (16.2%) | 16 | 80 | 112 / 2 | 2.20 / 2.15 / 3.23 / 3.87 | 0 | 35.7 s / 0 |
| esp | 13/80 (16.2%) | 16 | 80 | 128.5 / 2 | 2.22 / 2.13 / 3.30 / 3.97 | 0 | 40.8 s / 0 |
| Level 0 | 0/30 (0.0%) | 9 | 30 | 119.5 / 2 | 2.22 / 2.14 / 3.23 / 3.72 | 0 | 38.1 s / 0 |
| Level 1 | 6/30 (20.0%) | 9 | 30 | 122 / 2 | 2.39 / 2.24 / 3.38 / 3.52 | 0 | 38.8 s / 0 |
| Level 2 | 6/30 (20.0%) | 3 | 30 | 126.5 / 2 | 2.13 / 2.10 / 2.82 / 2.91 | 0 | 40.2 s / 0 |
| Level 3 | 6/30 (20.0%) | 0 | 30 | 129.5 / 2 | 2.23 / 2.13 / 2.84 / 3.30 | 0 | 41.1 s / 0 |
| Level 4 | 9/30 (30.0%) | 6 | 30 | 116 / 2 | 2.32 / 2.20 / 3.73 / 3.97 | 0 | 37.0 s / 0 |
| Level 5 | 5/30 (16.7%) | 6 | 30 | 121 / 2 | 2.11 / 2.00 / 3.44 / 4.48 | 0 | 38.5 s / 0 |
| Level 6 | 1/30 (3.3%) | 6 | 30 | 118.5 / 2 | 2.24 / 2.23 / 2.86 / 3.54 | 0 | 37.7 s / 1 |
| Level 7 | 7/30 (23.3%) | 9 | 30 | 135.5 / 2 | 2.28 / 2.18 / 3.56 / 3.87 | 0 | 43.0 s / 0 |

Correct by level and language (10 questions per cell):

| Level | Catalan | English | Spanish |
| --- | ---: | ---: | ---: |
| 0 | 0/10 | 0/10 | 0/10 |
| 1 | 2/10 | 2/10 | 2/10 |
| 2 | 2/10 | 1/10 | 3/10 |
| 3 | 3/10 | 2/10 | 1/10 |
| 4 | 3/10 | 2/10 | 4/10 |
| 5 | 2/10 | 2/10 | 1/10 |
| 6 | 0/10 | 1/10 | 0/10 |
| 7 | 2/10 | 3/10 | 2/10 |

#### Qwen3.5 0.8B Q8_0 — original scan

| Group | Correct | Always D | Valid answers | Median in / out tokens | Mac mean / p50 / p95 / max (s) | Mac >60 s | UNO Q est. p50 / count >60 s |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| All | 54/240 (22.5%) | 57 | 240 | 454 / 2 | 6.75 / 6.48 / 9.15 / 12.47 | 0 | 41.7 s / 24 |
| cat | 21/80 (26.2%) | 19 | 80 | 458 / 2 | 6.95 / 6.85 / 9.29 / 11.27 | 0 | 42.0 s / 8 |
| eng | 14/80 (17.5%) | 19 | 80 | 454 / 2 | 6.61 / 6.24 / 9.02 / 12.47 | 0 | 41.7 s / 8 |
| esp | 19/80 (23.8%) | 19 | 80 | 454 / 2 | 6.69 / 6.41 / 8.99 / 11.81 | 0 | 41.7 s / 8 |
| Level 0 | 9/30 (30.0%) | 12 | 30 | 560 / 2 | 6.81 / 6.52 / 9.53 / 11.81 | 0 | 51.3 s / 15 |
| Level 1 | 10/30 (33.3%) | 9 | 30 | 430 / 2 | 6.99 / 6.83 / 8.99 / 12.47 | 0 | 39.5 s / 6 |
| Level 2 | 10/30 (33.3%) | 12 | 30 | 406 / 2 | 6.75 / 6.52 / 8.34 / 11.27 | 0 | 37.3 s / 0 |
| Level 3 | 3/30 (10.0%) | 6 | 30 | 406 / 2 | 6.60 / 6.31 / 8.76 / 8.90 | 0 | 37.3 s / 0 |
| Level 4 | 4/30 (13.3%) | 3 | 30 | 458 / 2 | 6.43 / 6.24 / 8.87 / 9.15 | 0 | 42.0 s / 3 |
| Level 5 | 5/30 (16.7%) | 6 | 30 | 430 / 2 | 6.93 / 6.83 / 10.35 / 10.72 | 0 | 39.5 s / 0 |
| Level 6 | 10/30 (33.3%) | 3 | 30 | 486 / 2 | 6.78 / 6.46 / 10.10 / 11.11 | 0 | 44.6 s / 0 |
| Level 7 | 3/30 (10.0%) | 6 | 30 | 506 / 2 | 6.74 / 6.52 / 9.12 / 9.54 | 0 | 46.4 s / 0 |

Correct by level and language (10 questions per cell):

| Level | Catalan | English | Spanish |
| --- | ---: | ---: | ---: |
| 0 | 3/10 | 4/10 | 2/10 |
| 1 | 5/10 | 1/10 | 4/10 |
| 2 | 2/10 | 3/10 | 5/10 |
| 3 | 2/10 | 0/10 | 1/10 |
| 4 | 2/10 | 0/10 | 2/10 |
| 5 | 2/10 | 2/10 | 1/10 |
| 6 | 3/10 | 3/10 | 4/10 |
| 7 | 2/10 | 1/10 | 0/10 |

<!-- END MAC RESULTS -->

On this particular sample, always answering `D` scored 48/240 for text and
57/240 for images. Neither model beat that baseline on its own subset. The
practice ZIP and earlier d3 runs used different problems and prompts; their
scores do not conflict with this result.

## Reproduce

From the repository root, with the project's laptop environment and the local
M3Kang parquet shards and OCR option caches already present:

```bash
.venv/bin/python laptop/r1_benchmark/cases.py
bash laptop/r1_benchmark/run-mac.sh /path/to/deepseek-iq3_m.gguf \
    /path/to/Qwen3.5-0.8B-Q8_0.gguf /path/to/Qwen3.5-0.8B-mmproj-f16.gguf
python3 laptop/r1_benchmark/summarize.py --csv results/r1-mac/per-case.csv
```

Use llama.cpp with the Metal backend. See [cases.py](../laptop/r1_benchmark/cases.py)
for `--source`, `--data-dir` and `--out` overrides. The optional per-case CSV includes both threshold flags. The scripts save inputs,
per-question prompt/completion token counts, predicted choices, and server logs to
`results/r1-mac/` (gitignored). For a fresh run, select a new output directory
and generate its manifest at `<output-dir>/input` before running.
