#!/usr/bin/env bash
# Run both model servers on a laptop, to develop without the Arduino UNO Q.
# The options match the board's systemd units in board/systemd/.
#
#   laptop/dev_servers.sh start     # start language model (8081) and embeddings (8082)
#   laptop/dev_servers.sh stop      # stop both
#   laptop/dev_servers.sh status    # are they up?
#   laptop/dev_servers.sh logs      # follow the logs (Ctrl+C to stop)
set -euo pipefail

source "$(dirname "$0")/../board/common.sh"
RUN_DIR="${XDG_RUNTIME_DIR:-/tmp}/math-copilot"
LLM_PID="$RUN_DIR/llm.pid"
EMBED_PID="$RUN_DIR/embed.pid"
LLM_LOG="$RUN_DIR/llm.log"
EMBED_LOG="$RUN_DIR/embed.log"

running() {
  local pidfile="$1" pid
  [ -f "$pidfile" ] || return 1
  pid="$(cat "$pidfile" 2>/dev/null)" || return 1
  [ -n "$pid" ] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  # A recycled PID would otherwise let stop kill an unrelated process.
  if [ -r "/proc/$pid/cmdline" ] && ! tr '\0' ' ' <"/proc/$pid/cmdline" | grep -q "llama-server"; then
    return 1
  fi
  return 0
}

start_one() {  # name port pidfile logfile args...
  local name="$1" port="$2" pidfile="$3" logfile="$4"
  shift 4
  if running "$pidfile"; then
    ok "$name already running (pid $(cat "$pidfile"))"
    return
  fi
  mkdir -p "$RUN_DIR"
  nohup "$@" >"$logfile" 2>&1 &
  echo $! >"$pidfile"
  if wait_for_health "$port" 180; then
    ok "$name on port $port (pid $(cat "$pidfile"))"
  else
    fail "$name did not answer. See $logfile"
    kill "$(cat "$pidfile")" 2>/dev/null || true
    rm -f "$pidfile"
    return 1
  fi
}

case "${1:-start}" in
  start)
    [ -x "$LLAMA_SERVER" ] || die "llama-server not built. See README step 3, or run board/02_build_llama_cpp.sh."
    [ -f "$LLM_PATH" ] && [ -f "$EMBED_PATH" ] || die "models missing from $MODELS_DIR. Run board/03_download_models.sh."
    start_one "language model" "$LLM_PORT" "$LLM_PID" "$LLM_LOG" \
      "$LLAMA_SERVER" -m "$LLM_PATH" --host 127.0.0.1 --port "$LLM_PORT" \
      --threads "$LLM_THREADS" --ctx-size "$LLM_CTX" --parallel 1 --cache-ram 0 \
      --ctx-checkpoints 2 --jinja --reasoning off --reasoning-budget 0 --alias "$LLM_ALIAS"
    start_one "embeddings" "$EMBED_PORT" "$EMBED_PID" "$EMBED_LOG" \
      "$LLAMA_SERVER" -m "$EMBED_PATH" --embedding --host 127.0.0.1 --port "$EMBED_PORT" \
      --threads "$EMBED_THREADS" --ctx-size "$EMBED_CTX" --parallel 1 --cache-ram 0 \
      --batch-size "$EMBED_CTX" --ubatch-size "$EMBED_CTX"
    ;;
  stop)
    for pair in "language model:$LLM_PID" "embeddings:$EMBED_PID"; do
      name="${pair%%:*}"; pidfile="${pair#*:}"
      if running "$pidfile"; then
        pid="$(cat "$pidfile")"
        kill "$pid" 2>/dev/null || true
        for _ in $(seq 20); do
          kill -0 "$pid" 2>/dev/null || break
          sleep 0.5
        done
        if kill -0 "$pid" 2>/dev/null; then warn "$name is still running (pid $pid)"
        else ok "$name stopped"; fi
      else
        echo "  -     $name was not running"
      fi
      rm -f "$pidfile"
    done
    ;;
  status)
    for pair in "language model:$LLM_PID:$LLM_PORT" "embeddings:$EMBED_PID:$EMBED_PORT"; do
      name="${pair%%:*}"; rest="${pair#*:}"; pidfile="${rest%%:*}"; port="${rest#*:}"
      if running "$pidfile" && curl -sf "http://127.0.0.1:$port/health" >/dev/null; then
        ok "$name up on port $port"
      else echo "  -     $name is down"; fi
    done
    ;;
  logs)
    [ -f "$LLM_LOG" ] && [ -f "$EMBED_LOG" ] || die "no logs yet; start the servers first"
    tail -f "$LLM_LOG" "$EMBED_LOG"
    ;;
  *)
    die "usage: $0 start|stop|status|logs"
    ;;
esac
