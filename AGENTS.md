# AGENTS.md

Offline math copilot for the Arduino UNO Q (4 GB, aarch64) built on M3Kang.
`README.md` is the student guide; `.opencode/skills/` holds task-specific instructions.

## Non-negotiables

- `predict.py` is scored on a hidden test **for every team, Teach or Reason** (README Step 13.3). Keep the contract: one letter A–E per problem, ≤60 s, no network, temperature 0 + fixed seed, `FALLBACK_ANSWER=D`.
- In teaching features, use the dataset `label` to check the student's answer and to reject a hint that leaks it. The model is not the oracle; `predict.py` is still scored separately.
- Answering code runs **on the board**. The laptop only prepares data (and may run a local replica for development).
- Board runtime code uses the Python **standard library only**. There is no pip on the board.
- Never commit data or models: `data/`, `data/images/`, `data/index.db`, `*.gguf`. M3Kang is research-only.
- Never commit secrets. `opencode.json` is local and gitignored; a provider key was committed and pushed earlier — rotate it and clean history before publishing.

## Where things run

| Work | Machine |
| --- | --- |
| OCR, train/val split, image export | laptop (`.venv/bin/python`) |
| `predict.py`, `eval.py`, `build_index.py`, model servers | board (`python3`) |
| Local replica of the two servers | laptop (`laptop/dev_servers.sh`) |

On this workstation adb is at `~/.local/opt/platform-tools/adb`; SSH is the alternative when Wi-Fi is up. Board repo lives at `~/datathon-UPC-maths-qualcomm-arduino-unoq`; run Python commands from there.

## Commands

Laptop setup and data prep:

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -r laptop/requirements.txt
.venv/bin/python laptop/prepare_m3kang.py --languages eng,cat --save-images --workers 8
./laptop/dev_servers.sh start    # also stop | status | logs
```

Use `.venv/bin/python` for laptop scripts: system Python is 3.14 and lacks the OCR deps. OCR is resumable via `data/.ocr_cache.jsonl`; `--workers` is process-parallel because one image takes ~2.6 s regardless of threads.

Board bring-up, in order:

```bash
cd ~/datathon-UPC-maths-qualcomm-arduino-unoq
./board/00_check_board.sh          # read-only health check
./board/01_install_packages.sh     # sudo
./board/02_build_llama_cpp.sh      # ~28 min native aarch64 build
./board/03_download_models.sh
./board/04_install_services.sh     # sudo
./board/05_smoke_test.sh
```

Evaluation and index:

```bash
python3 eval.py --limit 50 --name <name>
python3 eval.py --limit 0 --name <name>                 # full 562, ~90 min at 9 s/problem
python3 eval.py --languages eng --limit 0 --name <name> # README's 281-problem reference
python3 build_index.py --input data/train.jsonl --db data/index.db --only-with-options
```

- `eval.py` shuffles with `random.Random(42)`; the 50-problem sample is fixed. A 1–2 answer difference is noise.
- Each run appends `results/ablations.csv` with the git commit. Commit first if you plan to cite the number; uncommitted runs record a misleading commit.
- There is no unit-test suite. Verification is `board/05_smoke_test.sh`, `predict.py` on `data/sample_problems.jsonl`, and `eval.py`.

## Board gotchas

- 3.6 GB RAM total. Every `llama-server` needs `--parallel 1 --cache-ram 0`; the LLM also needs `--ctx-checkpoints 2 --jinja --reasoning off --reasoning-budget 0`. Without them the OOM killer takes the server.
- The 60 s limit is prefill-bound: a 1.8 GB 3B Q4 model times out at ~57 s on the four A53 cores. The 0.8B Q8 is the practical ceiling for this pipeline.
- `/tmp` is tmpfs and is wiped on reboot. Put long build/eval logs, PID and exit markers, and detached scripts in `/home/arduino`. Use `nohup … </dev/null &`.
- Clock must be NTP-synced; a wrong clock breaks apt/HTTPS. App Lab updates fix it; check `timedatectl`. Prefer `curl -4`: DNS may return IPv6 with no route.
- Unexpected reboots: `last -x` marks unclean boots as "crash". Repeated reboots usually mean power; run the board from the **5 V / 3 A supply**, with USB for data only. Both services are enabled at boot and return in ~15 s.
- Do **not** cross-compile. Build natively. An interrupted incremental build can fail at link with undefined `libllama-common` symbols; delete `~/llama.cpp/build` and rebuild clean. `02_build_llama_cpp.sh` skips `git fetch` when the source is already at the pinned commit.
- adb shells set `TMPDIR=/data/local/tmp`, which does not exist; `export TMPDIR=/tmp` before a build to avoid make warnings.
- `board/04_install_services.sh` expands `~`/`$USER`. If invoked through `sudo bash -c`, pass `HOME=/home/arduino USER=arduino`, or it looks under `/root` and fails.
- sudo needs a password. Never guess it; ask the user or let them run the sudo steps.
- Check `free -h` and `df -h ~` after adding a server or model. Board Wi-Fi/IP changes; use `hostname -I` or adb instead of hardcoding an IP.
- The board copy may have no `.git` (pushed as a tar). `git_commit()` in `eval.py` can be empty there; record the commit from the laptop.
- Extra GGUF files in `~/models` are experiments. `config.env` pins the served model; update repo/revision/file/SHA and rerun `03`/`04` to switch. Check `free -h` first.

## Architecture

- Cost model: prefill ~11 tok/s and decode ~6 tok/s on the board; embeddings, retrieval and Python are nearly free. Prefer rules/retrieval over generation.
- `predict.py` → `copilot.pipeline.solve()` → `copilot.llm.answer_letter()`, optional `copilot.rag.Index` via `copilot.embed`.
- `copilot/llm.py`: prompt formatting, stable `SYSTEM` (prompt caching), grammar `root ::= [A-E]`, one token. `letter_logprobs()` scores all five letters with a total time budget.
- `copilot/rag.py`: SQLite index; `search(..., exclude_id=...)`; index training problems only.
- `board/`: setup scripts + systemd templates rendered from `config.env`; `laptop/`: data prep + local servers; `experiments/`: offline experiments.
- Keep teaching features in `copilot/tutor.py`, never in `predict.py`.

## Data and retrieval rules

- 2,932 train / 562 val (eng+cat), 63% with OCR options, 3,494 images. The same problem `id` exists across languages; the split is by `id`.
- `options={}` means OCR failed. Coverage is ~62–63%; image/figure-heavy problems are the weak spot (level 3 is notably bad).
- Retrieval must index training problems only and pass `exclude_id`, including translations, or validation is leaked.
- bge-small is English: queries get `EMBED_QUERY_PREFIX`, stored documents do not. For cat/eng, search with the English text of the same `id` when available.

## Measured results (2026-11-14; don't redo blindly)

- Board 0.8B, 50 problems: **20.0%**, mean 9.4 s, worst 15.2 s. Laptop full 562: **23.1%**, exactly the always-D floor.
- Board and laptop disagreed on 3/50 answers with the same model/seed (ARM vs x86 numerics). Only board measurements count for accuracy and latency.
- The 0.8B almost never predicts C or E (C 5, E 9 on full 562) and is below the always-D floor at level 0; level 3 is image-heavy and worst.
- Permutation averaging over option orders: 23.1% → 22.4% full. Dead end.
- Qwen2.5-Math-1.5B Q4: 16% on 50 (severe A bias). Qwen3.5-2B Q4: 22% on 50 but **22.1% full 562**, below the 23.1% baseline; rejected.
- Qwen2.5-3B Q4: **24.6% full 562** (p=0.64), **28.3% with options** (p=0.08), 18.8% without. A 3B-with-options / `D`-otherwise rule gives 27.4% (p=0.14) — not established. **On the board it timed out on 6/10 problems (median 57.1 s), so it is not deployable.**
- Laya multilingual zero-shot: **19.2%** on the 339 val-with-options (0.8B: 21.8%); near chance, so fine-tuning is required. It is tiny and fast (~0.02 s/problem on GPU, 322M), making it a viable fine-tune/deploy base.
- Jev-Vision 0.8B, GGUF + llama.cpp, CPU here: **21.8%** on the 339 options (22.5% text-only, 21.1% image); mean 2.05 s, worst 7.6 s. No gain.
- OneJev 1.6 0.8B, GGUF + llama.cpp, CPU: **19.5%** on 339; mean 3.57 s. No gain.
- Jev-Vision 2B, GGUF + llama.cpp, CPU: **24.8%** on 339 (22.5% text, 27.3% image); vs 0.8B 76 fixed / 66 broken, **p=0.45**; predicts C 159 times vs 66 true C. Mean 6.51 s, worst 25.5 s. Not significant and a board-latency risk.
- Off-the-shelf model swaps are exhausted: none both beats 23.1% significantly and fits the board. Do not start another model download. Prioritise teaching; the only optional reasoning bets are OCR crop recovery and a time-boxed Laya fine-tune.
- The GGUF numbers above are laptop **CPU** (local llama.cpp has no CUDA); they are not board timings.
- Treat `status=timeout` as a failure, not an answer: the `D` fallback inflates apparent accuracy. A model is only admissible if a board run has zero timeouts and comfortable median latency.
- Simple letter/position debiasing does not move accuracy; the content signal is the bottleneck. Partial running accuracy drifts; report the final full-set number and McNemar, not a 50-problem delta or an early subset.

## Repo quirks

- Laya experiments use `.venv-laya` (Python 3.12 + CUDA torch). Keep the main `.venv` for data prep; do not install Laya there.
- `.venv-*/` (e.g. `.venv-laya`, `.venv-flide`) and `/jaume/` (local M3Kang copies) are gitignored. Never commit M3Kang data.
- Laya's newer runtime advertises image input (`laya[vision]` / `vega`); untested on our data. OCR remains the source of option text for teaching and hint checks.
- `config.env` is parsed by both bash (`board/common.sh`) and Python (`copilot/config.py`); Python environment variables override. Bash expands `~`.
- `.opencode/skills/<id>/SKILL.md` is the discovery path (not `.opencode/.skills`). Load skills by ID with the skill tool.
- `.idea/` is tracked from the initial commit; avoid adding IDE noise. `results/` and `data/` are gitignored.
