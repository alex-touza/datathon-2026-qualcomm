# MindDragon

An offline maths tutor on an **Arduino UNO Q 4 GB**: a small language model picks
one letter for a scored exam, and a teaching layer gives three cumulative hints
with rule-based diagnosis. No network at run time.

This file is the entry point for a coding agent. The full engineering notes are in
[`AGENTS.md`](AGENTS.md); the task-specific instructions are the skills in
[`.opencode/skills/`](.opencode/skills/).

## Use the skills

```bash
mkdir -p .claude && ln -s ../.opencode/skills .claude/skills   # Claude Code
```

Then ask for one by name, for example: *"use the minddragon skill to run the
private test"*. `minddragon` is the one to reach for when operating the board,
running `predict.py`, reading a results CSV, or starting the demo.

## The two entry points

| Command | Purpose |
| --- | --- |
| `python3 predict.py --input X.jsonl --output Y.csv` | the scored task: one letter A–E per problem |
| `./board/06_install_app.sh --start` | the teaching demo on port 7000 |

`predict.py` defaults to the **Qwen3.5 0.8B** on port 8081. `--solver deepseek`
switches to a local DeepSeek-R1 distill on port 18738, which is an experiment and
is **not** running by default.

## Hard rules

- One letter per problem, at most **60 seconds**, **offline**, temperature 0, seed 42, `FALLBACK_ANSWER=D`.
- The scored numbers come from the board, not the laptop. A `timeout` row is a failure, not an answer.
- Never commit `data/`, `*.db`, `*.gguf` or a key. M3Kang is research use only.
- Board runtime code is Python **standard library only**; there is no pip on the board.

## Where things live

`predict.py`, `eval.py`, `copilot/` (the pipeline and the tutor), `app/math-tutor/`
(the App Lab app and the LED sketch), `board/` (setup and service scripts),
`tools/` (build-time generation and checks), `docs/` (the datathon guide, the
Project Hub draft, the architecture diagram).
