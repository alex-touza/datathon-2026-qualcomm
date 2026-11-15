#!/usr/bin/env bash
# Run in a separate terminal on the UNO Q. Ctrl-C stops the exam server.
set -euo pipefail

default_model="$HOME/models/deepseek-r1-distill-yq-1.5b-iq3_m.gguf"
model="${DEEPSEEK_MODEL:-$default_model}"
server="${LLAMA_SERVER_BIN:-$HOME/llama.cpp/build/bin/llama-server}"
port="${DEEPSEEK_PORT:-18738}"

[[ -r "$model" ]] || { echo "Missing local DeepSeek GGUF: $model" >&2; exit 1; }
[[ -x "$server" ]] || { echo "Missing llama-server executable: $server" >&2; exit 1; }

exec "$server" -m "$model" -ngl 0 -t 4 -tb 4 -c 1024 -b 128 -ub 128 \
    -np 1 --cache-ram 0 --no-warmup --host 127.0.0.1 --port "$port"
