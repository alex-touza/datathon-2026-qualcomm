---
name: minddragon
description: Operate and demonstrate MindDragon, the offline maths tutor on the Arduino UNO Q, during a private test or a jury demo. Covers which service runs on which port, the exact entry point for a held-out set, how to read the output CSV honestly, how to start the teaching app, how to reach the page over USB with adb when the board is offline, and the failure modes that look like bugs but are not. Use it when someone runs predict.py or eval.py on the board, when the demo has to be started or set up with Wi-Fi off, or when a result has to be explained or quoted.
---

# Operate MindDragon under test and in the demo

Two entry points, and they are not the same program:

| Entry point | Question it answers | Default model |
| --- | --- | --- |
| `predict.py` / `eval.py` | "one letter per problem", the scored task | **Qwen3.5 0.8B** (`--solver baseline`) |
| the App Lab app on port 7000 | "explain and teach", the jury demo | 0.8B for one hint, plus precomputed solutions |

Never quote the tutor's hints as if they were a scored prediction, and never quote the accuracy of the demo.

## What runs where

| Where | What | Port |
| --- | --- | --- |
| Board, Linux | `llama-server`, Qwen3.5 0.8B Q8 (`math-llm`) | **8081** |
| Board, Linux | `llama-server`, bge-small-en-v1.5 (`math-embed`) | **8082** |
| Board, Linux | DeepSeek-R1-Distill-Qwen-1.5B IQ3_M, **optional**, started by hand | **18738** |
| Board, Linux | App Lab app: page + tutor, with the LED matrix | **7000** |
| Board or laptop | `explorer/tutor_server.py`: page + tutor, **no** matrix | **8010** |
| Board, STM32 | `sketch/sketch.ino`: LED matrix | — |

The two model services listen on more than `127.0.0.1` when the App Lab app is installed, because the container reaches the host on `MODEL_HOST=172.17.0.1`. `predict.py` and `eval.py` run outside the container and use `127.0.0.1`.

## The contract: never break it

`predict.py` is what is scored. One letter A–E per problem, at most 60 seconds, offline, temperature 0, seed 42, `FALLBACK_ANSWER=D`. Run it from the repository root on the board:

```bash
python3 predict.py --input <problems.jsonl> --output <predictions.csv>
```

Options that exist and what they do:

- `--solver baseline` (default): the 0.8B on 8081.
- `--solver deepseek`: the local distill on 18738. **If that server is not running, every row comes back `D` with `status=error`.** It is an opt-in experiment, not the shipped default.
- `--rag K` and `--alpha`: retrieval and letter-bias correction; they imply the baseline solver.

## Check the board is alive before blaming the code

```bash
curl -fsS http://127.0.0.1:8081/health    # the language model
curl -fsS http://127.0.0.1:8082/health    # embeddings
systemctl status math-llm math-embed --no-pager
free -m                                   # 3.6 GB total; both services must fit
```

On the board, `./board/00_check_board.sh` is a read-only health check and `./board/05_smoke_test.sh` runs one embedding plus the five sample problems. A fresh clone has no `data/`, so the smoke test needs the data copied first.

## Read the output honestly

`predict.py` writes five columns: `id, language, answer, seconds, status`.

1. **Look at `status` before the accuracy.** `ok` is an answer. `timeout`, `error: ...` and `missing_options` are failures that happen to carry a letter.
2. **A failure row is a `D`.** The fallback is not evidence of skill, and it pushes accuracy towards the always-`D` floor.
3. **Know the floor of the set.** On the team's 562-problem validation set, always-`D` scores 23.1%. On a **balanced** set (20 of each letter) it scores **20.0%**. Compare against the floor of the set in hand, never against 23.1% by habit.
4. **A small set is coarse.** With 100 problems the standard error is about 4 points; do not call a 1–2 answer difference a result. Use `experiments/mcnemar.py` for a paired comparison.
5. **Laptop numbers rank models; they do not prove the board.** Only a board run with zero `timeout` rows and a comfortable median is a deployable result.

`eval.py` writes the same thing plus `label`, `correct`, `has_options` and a row in `results/ablations.csv`:

```bash
python3 eval.py --limit 0 --input <problems.jsonl> --name <short-name>
```

## Start the teaching demo

On the board, from the repository root:

```bash
cp data/explanations-demo.db data/explanations.db    # if the DB has another name
./board/06_install_app.sh --start
```

Then open `http://$(hostname -I | awk '{print $1}'):7000` on the same Wi-Fi. You should see a problem, five options, and hint and reveal buttons. The matrix shows the level as a digit, then a tick or a cross.

What the demo does, in order:

- Step 1 = a short nudge, Step 2 = nudge + working, Step 3 = the full solution. The steps are cumulative.
- Hints are pre-warmed in the background when a problem appears, so pressing a step is usually instant. Step 3 depends on the model and may still take a few seconds.
- Reveal is answer-only and needs an explicit second press.
- With no precomputed solution, the same steps fall back to: an easier solved problem, then the method, then one model hint that is checked before it is shown.

If the page loads but hints fail, check `MODEL_HOST` and that both services listen beyond `127.0.0.1`.

## Demo with Wi-Fi off: the page over USB

The jury demo is run "the way the jury will see it": **Wi-Fi off**, services started at boot, nothing on the laptop doing the work. With Wi-Fi off the board is not on any network, so the laptop cannot reach `http://<board-ip>:7000` and SSH is gone. The page is then reached **over the USB-C cable with `adb forward`**, and `adb shell` is the only shell. This is the one part of the demo that adb is required for; over Wi-Fi it is optional.

`adb` on this workstation: `~/.local/opt/platform-tools/adb`. Install it elsewhere with Android SDK Platform-Tools.

### Before the demo (Wi-Fi still on)

On the laptop:

```bash
export PATH="$HOME/.local/opt/platform-tools:$PATH"
adb devices          # must say "device", not "unauthorized" or "no permissions"
```

`unauthorized` → unplug and replug the USB-C cable and accept the prompt. `no permissions` (Linux) → add a udev rule for the board's vendor ID, then `adb kill-server && adb start-server`.

On the board over SSH, because it is easier now than on stage:

```bash
systemctl status math-llm math-embed --no-pager
curl -fsS http://127.0.0.1:8081/health; curl -fsS http://127.0.0.1:8082/health
arduino-app-cli app list
free -m; df -h ~
```

- **Start the app now, not on stage**: the sketch compiles when the app starts (~1 min).
- Do one full warm-up round through the page. The first request loads the model and is slow; the jury must see a warm board.
- Check what the app needs is there: `data/index.db`, `data/images/`, `data/tutor.db`, and `config.env` with `MODEL_HOST=172.17.0.1`.
- Power the board from the **5 V / 3 A supply**, with USB for data only. USB-only power is the usual cause of the reboots that kill a demo.

### At the demo (Wi-Fi off)

Turn the board's Wi-Fi off. SSH drops; that is expected. From here on:

```bash
adb devices                        # still "device": USB does not need Wi-Fi
adb forward tcp:7000 tcp:7000      # the web_ui brick / teaching page
xdg-open http://127.0.0.1:7000
adb shell                          # user arduino, for troubleshooting
```

- Forward **7000** only. The model servers stay on the board's `127.0.0.1:8081`/`8082` and the app reaches them through `172.17.0.1`; nothing else is forwarded.
- If the demo is `explorer/tutor_server.py` instead of the App Lab app, forward **8010** too.
- `adb forward` does not survive a replug or a reboot; re-run it after either.

### On-stage recovery

| Symptom | Fix |
| --- | --- |
| `http://127.0.0.1:7000` stops loading | `adb forward --list`, then re-run `adb forward tcp:7000 tcp:7000` |
| `adb devices` is empty | replug USB, then `adb kill-server && adb start-server` |
| Page loads but "thinking" hangs | `adb shell`, then `systemctl status math-llm math-embed`, `journalctl -u math-llm -n 30` |
| Nothing works | play the recorded video of the demo |

**Rehearse it once, fully offline**, after a board reboot with the app auto-started. That is the only way to catch a missing `tutor.db`, a forward that needs redoing, or a service that did not come back.

## Failure modes that are not bugs

| Symptom | What it is |
| --- | --- |
| Every row is `D` with `status=error: ... Connection refused` | the model server for the selected solver is not running |
| Hints always say "this looks like a ... problem" | no `data/explanations.db`; the rule-based ladder is working as designed |
| A hint reads like a method, not a solution | the model hint was refused for leaking the answer or for inventing a number; the method hint replaces it |
| Figure-heavy problems look broken | OCR failed for that problem; the options may be missing or garbled |
| `couldn't bind HTTP server socket` | another `llama-server` already owns the port; `pgrep -a llama-server` |
| The board reboots by itself | power supply, not software: use 5 V / 3 A |
| SSH stops answering the moment Wi-Fi is turned off | expected; use `adb shell` over USB |
| `adb devices` lists the board as `no permissions` | Linux udev rule missing, not a board fault |

## Never

- Enable the network at run time. The board is offline by design.
- Change the seed, the temperature or the 60-second limit for a scored run.
- Commit `data/`, `*.db` or `*.gguf`. M3Kang is research use only.
- Kill a service you did not start, or run `predict.py` while another run is using the same ports.

## Numbers safe to quote

- **23.1%** on the 562-problem validation set with the 0.8B — the same as always-`D`; this is a floor, not a win.
- **20.0%** over 50 problems **on the board**, 9.4 s mean, 15.2 s worst.
- **437 verified explanations over 231 problems**, 206 in both English and Catalan (local database; it is not published).
- **$0.0075** per generated explanation, once, at build time.
- A precomputed hint is a database read (**~0 s**); hints are also warmed in the background, so even an uncached step is usually ready before the student presses it. Step 3 is the only path that waits on the model.
