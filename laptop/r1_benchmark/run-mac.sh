#!/usr/bin/env bash
# Usage from the repository root: bash laptop/r1_benchmark/run-mac.sh R1.gguf Qwen.gguf mmproj.gguf [output-dir]
set -euo pipefail
r1=${1:?DeepSeek IQ3_M GGUF}
vision=${2:?Qwen3.5-0.8B Q8_0 GGUF}
projector=${3:?Qwen3.5-0.8B vision projector}
out=${4:-results/r1-mac}
base=${BENCH_PORT:-18752}
pid=
stop_server() {
    if [[ -n "$pid" ]]; then
        kill "$pid" 2>/dev/null || true
        wait "$pid" 2>/dev/null || true
        pid=
    fi
}
trap stop_server EXIT
run() {
    local modality=$1 model=$2 port=$3
    shift 3
    local url="http://127.0.0.1:$port"
    [[ ! -e "$out/$modality.jsonl" ]] || { echo "Output already exists: $out/$modality.jsonl" >&2; return 1; }
    llama-server -m "$model" -lv 4 --device MTL0 -ngl 99 -fa on -c 16384 -np 8 \
        --cache-ram 0 --no-cache-prompt --host 127.0.0.1 --port "$port" \
        --reasoning off --reasoning-budget 0 "$@" > "$out/$modality-server.log" 2>&1 &
    pid=$!
    for _ in {1..80}; do
        kill -0 "$pid"
        if curl -fsS "$url/health" &>/dev/null; then break; fi
        sleep 0.25
    done
    curl -fsS "$url/health" >/dev/null
    grep -aEq 'offloaded [1-9][0-9]*/[0-9]+ layers to GPU' "$out/$modality-server.log"
    python3 laptop/r1_benchmark/run.py "$out/input/cases.jsonl" "$out/$modality.jsonl" \
        --kind "$modality" --url "$url"
    stop_server
}
run text "$r1" "$base"
run image "$vision" "$((base + 1))" --mmproj "$projector"
