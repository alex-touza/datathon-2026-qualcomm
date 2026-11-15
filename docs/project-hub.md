# Building MindDragon: an offline maths tutor on the Arduino UNO Q

*Draft for Arduino Project Hub. Replace `REPO_URL` with the public repository
address, and add the photo and video before posting.*

---

## 1. Introduction

Students preparing for the Kangaroo maths competition often practise alone,
with an answer key and no feedback. A tutor would say: *what should I look at
first?*, *where did I go wrong?*, *what is the next step?* — not just *the
answer is C*.

Cloud chat assistants can do this, but they need a network, they cost money per
question, and they cannot be used in an exam room or a school without Wi-Fi.

This project puts the tutor on an Arduino UNO Q 4 GB. It runs with the network
off. A student reads a problem, picks A–E, and presses a button for a hint. The
hints come in three cumulative steps, and the board's LED matrix shows the level
as a digit, then a tick or a cross when the student answers.

Around that core: a rule-based diagnosis of the wrong option, a streak for clean
first-try answers, offline text to speech for the problem and the options, level
and topic pickers, and an end-of-session report with six charts drawn as SVG so
the board needs no plotting library.

The design decision that matters: **the small model is not trusted to explain
mathematics.** The explanations are generated once, on a laptop, verified by
code, and shipped as data. On the board, a 0.8B model picks one letter and, at
most, writes one short hint that is checked before it is shown.

*(Photo and a 20-second video here.)*

## 2. Components and apps

- Arduino UNO Q 4 GB (Qualcomm QRB2210, four Arm cores, 3.6 GB usable RAM, plus
  an STM32 microcontroller)
- Arduino App Lab, with one app and one brick: `arduino:web_ui`
- llama.cpp, built natively on the board
- Qwen3.5 0.8B, Q8_0, 835 MB — one letter, one short hint
- bge-small-en-v1.5, Q8_0, 37 MB — retrieval embeddings
- The M3Kang dataset, used at build time only

## 3. Architecture

*(Insert `docs/architecture.svg` from the repository.)*

Two halves on the same board:

- **Linux** runs the app, both `llama-server` processes, and SQLite.
- **STM32** runs a small sketch that draws on the 8×13 LED matrix.
- The **Bridge** carries tick, cross, level and topic from the app to the sketch.
- A **laptop** is used once, before the demo, to OCR the dataset, build the index,
  and generate the explanations. Nothing at demo time touches the network.

Three databases:

- `index.db` — solved problems, embedded, for retrieval.
- `explanations.db` — verified three-part explanations.
- `tutor.db` — the student's attempts and points.

## 4. Step by step

Each step says what you should see. The full code and every command is in the
repository: `REPO_URL`.

**Step 1 — Board ready.** Follow steps 0–12 of the datathon guide in the
repository (`docs/datathon-guide.md`). You should have both model services
answering on ports 8081 and 8082.

**Step 2 — Prepare the data on a laptop.** Accept the M3Kang licence on
Hugging Face, then run `laptop/prepare_m3kang.py`. You should get
`data/train.jsonl`, `data/val.jsonl` and an image per problem.

**Step 3 — Build the index.** Run `build_index.py`. You should get
`data/index.db` with one row per training problem that has options.

**Step 4 — Copy the data to the board**, over `scp` or `adb push`.

**Step 5 — Check the board.** Run `./board/00_check_board.sh`, then
`./board/05_smoke_test.sh`. You should see a CSV of predictions and no timeouts.

**Step 6 — Generate explanations (optional).** On a laptop, with any cloud API
key, run `tools/generate_solutions.py --all`. Copy `data/explanations.db` to the
board. Skip this and every problem falls back to the rule-based ladder.

**Step 7 — Install the app.** Run `./board/06_install_app.sh --start`. Open
`http://<board-ip>:7000` in a browser on the same Wi-Fi. You should see a
problem, five options, and hint and reveal buttons. Answer; the LED matrix
shows a tick or a cross.

## 5. Results and error analysis

On the **board**, 50 validation problems: **20.0%**, 9.4 s mean, 15.2 s worst.
On a **laptop**, all 562 validation problems: **23.1%** — which is exactly the
score of always answering `D`.

That number is the honest one, and it is why the project leads with teaching.

| Model | Problems | Accuracy | Board-feasible |
| --- | --- | --- | --- |
| Qwen3.5 0.8B (deployed) | 562 | 23.1% | yes, 9 s |
| Qwen3.5 2B | 562 | 22.1% | yes, no gain |
| Qwen2.5-Math 1.5B | 50 | 16.0% | yes, worse |
| Qwen2.5 3B | 562 | 24.6% | **no: 6/10 timeouts, 57 s median** |
| Qwen2.5 3B, options only | 339 | 28.3% (p = 0.08) | no |

Error analysis, full 562-problem run:

- **Level 3 is the worst, at 8.3%** — figure-heavy problems whose options
  survive OCR least often.
- **Language makes no difference**: English 23.5%, Catalan 22.8%.
- **Problems with OCR options score lower** (21.8%) than problems without
  (25.1%), because the OCR failures cluster in the hard, image-based problems.
- **The model has a letter bias**: it predicted `C` and `E` almost never, and
  `D` far more often than the truth. On this task that is guessing, not reasoning.

What the teaching layer costs: precomputed hints were measured at **0.0 s** (a
SQLite read), the first two rungs are a local search well under a second, and the
one model hint is **estimated at 15–20 s on the board** from the measured decode
speed of about 6 tokens per second. It is leak-checked before the student sees it.

## 6. What we learned, and what is next

- **A 0.8B model cannot be a maths tutor.** It does not reliably chain three
  arithmetic steps, and it sounds equally confident when wrong. Confident wrong
  mathematics is worse than no explanation.
- **Precompute what matters.** Generating and verifying explanations offline
  costs about $0.0075 per problem and gives a hint that is correct by
  construction. Runtime generation gave invented numbers.
- **Make the fallback good.** For problems without a precomputed solution, the
  ladder is: an easier solved neighbour, then the topic method, then one
  leak-checked model hint. It degrades visibly, and that is better than pretending.
- **Verify the verifier.** About thirty good explanations were initially rejected
  because the checks misread fractions and units. The whole database is now
  rechecked offline on every change.

Next: a time-boxed fine-tune of a 322M encoder on the labelled pairs, and a
deterministic option-value solver that abstains to `D` when it is not sure.
Both are attempts to leave the always-`D` floor without needing a bigger model.

## Credits

M3Kang: Torres-Camps et al., 2026, arXiv:2601.16218, Qualcomm. Research use
only — the dataset is not redistributed here.
llama.cpp: MIT. Qwen3.5 0.8B and bge-small-en-v1.5: see their model cards.
RapidOCR: Apache 2.0.
