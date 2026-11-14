#!/usr/bin/env bash
# Install the tutor as an App Lab app, and check what it needs. No sudo.
#
#   ./board/06_install_app.sh            # copy the app to ~/ArduinoApps and check data
#   ./board/06_install_app.sh --start    # ...and start it
#
# The app imports copilot/ from this repository, so the repository has to stay
# where it is. MATH_TUTOR_REPO overrides the lookup if you move it.
set -euo pipefail
source "$(dirname "$0")/common.sh"

APP_SRC="$REPO_DIR/app/math-tutor"
APP_DST="$HOME/ArduinoApps/math-tutor"

step "Checking what the tutor needs"
[ -f "$REPO_DIR/data/index.db" ] && ok "retrieval index data/index.db" \
  || die "data/index.db is missing. Run: python3 build_index.py --input data/train.jsonl --db data/index.db --only-with-options"
[ -d "$REPO_DIR/data/images" ] && ok "images data/images" || warn "data/images is missing: problems show without figures"
command -v arduino-app-cli >/dev/null || die "arduino-app-cli not found; open App Lab once on the board"
wait_for_health "${LLM_PORT}" 5 && ok "language model on port ${LLM_PORT}" \
  || warn "the language model is down: rung 3 will fall back to the method"
wait_for_health "${EMBED_PORT}" 5 && ok "embeddings on port ${EMBED_PORT}" \
  || warn "embeddings are down: retrieval will be slow or fail"

step "Copying the app"
[ -d "$APP_SRC" ] || die "$APP_SRC is missing"
if [ -d "$APP_DST" ]; then
  warn "replacing $APP_DST"
  rm -rf "$APP_DST"
fi
mkdir -p "$HOME/ArduinoApps"
cp -r "$APP_SRC" "$APP_DST"
ok "$APP_DST"

step "How to run it"
echo "  Open it in App Lab, or from here:"
echo "    arduino-app-cli app start user:math-tutor"
echo "  Logs:  arduino-app-cli app logs $APP_DST --all"
echo "  Page:  http://\$(hostname -I | awk '{print \$1}'):7000"
echo "  Web page only, no matrix:  python3 explorer/tutor_server.py --host 0.0.0.0"

if [ "${1:-}" = "--start" ]; then
  step "Starting"
  arduino-app-cli app start user:math-tutor
fi
