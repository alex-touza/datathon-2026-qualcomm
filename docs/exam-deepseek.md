# Offline exam predictor on Arduino UNO Q

`predict.py` and `eval.py` default to **DeepSeek-R1-Distill-Qwen-1.5B IQ3_M**,
without LoRA or RAG. Both call the same scorer. The tutor keeps using the
existing 0.8B service. Use `--solver baseline` to reproduce the old benchmark.

Before the offline run, copy the already benchmarked GGUF to
`~/models/deepseek-r1-distill-yq-1.5b-iq3_m.gguf` on the board. The original
benchmark artifact is named `deepseek-iq3_m.gguf` (836 MiB); its SHA-256 is
`65380a89c4ccf3a2941739fe8fde454c9bfbeccd3548a7a0899324cd66e80f4e`.
Keep the GGUF outside Git. Ensure the board's existing llama.cpp build is
present. For runtime OCR, have Tesseract with `eng` and `cat` available locally.
If using a separately unpacked Tesseract install, set `OCR_BIN`,
`OCR_TESSDATA_DIR` and its library path in the predictor environment.
`OCR_LANG=eng` can be used if only the English language pack is installed.

On the board, first start the model in a dedicated terminal:

```bash
bash board/07_start_deepseek.sh
```

Then, in a second terminal on the board:

```bash
curl -fsS http://127.0.0.1:18738/health
python3 predict.py --input data/demo/train.jsonl --output results/demo.csv
python3 eval.py --input data/demo/train.jsonl --limit 0 --name demo-deepseek
```

Prepare the practice data on the laptop, using the Parquet from the Discord ZIP:

```bash
.venv/bin/python laptop/prepare_m3kang.py \
  --parquet data/demo-00000-of-00001.parquet --languages eng,cat \
  --val-fraction 0 --out data/demo --save-images --workers 4
```

Copy the prepared directory, including `images/`, to the board. Exam inputs may
supply options directly, or provide a local `image_path` relative to the JSONL
file. The predictor also finds images named `<id>_<language>.png` next to the
JSONL under `images/` (or in `data/images/`). Tesseract runs on the board only
when an image can help; it can read text and diagram labels, but this model
cannot see the diagram itself. If no answer choices can be read, the predictor
answers `D` with status `missing_options`.

The model receives a short prompt with temperature 0, seed 42, and a grammar
restricting replies to A–E. There is one board-local request per problem with
usable choices. A 57 s wall-clock watchdog covers OCR and inference, leaving
3 s to write and flush the answer. A timeout, missing server or invalid reply
also yields `D`. Check the `status` column when reporting accuracy; a fallback
can score by chance. If using a different port, set `DEEPSEEK_PORT` in both
terminals.
