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
mkdir -p "$HOME/ArduinoApps" "$APP_DST"
# App Lab mounts only the app folder in the container, so everything the tutor
# reads at run time has to live inside it: the app files, copilot/, config.env
# and the data the page shows. The practice profile in data/tutor.db is kept.
cp -r "$APP_SRC"/. "$APP_DST"/
rm -rf "$APP_DST/python/__pycache__" "$APP_DST/copilot" "$APP_DST/python/copilot"
cp -r "$REPO_DIR/copilot" "$APP_DST/copilot"
rm -rf "$APP_DST/copilot/__pycache__"
cp "$REPO_DIR/config.env" "$APP_DST/config.env"
# Inside the App Lab container 127.0.0.1 is the container itself; the model
# servers run on the host and listen on the docker bridge address.
grep -q '^MODEL_HOST=' "$APP_DST/config.env" || printf '%s\n' '' \
  '# The App Lab container sees the host here, not on 127.0.0.1.' \
  'MODEL_HOST=172.17.0.1' >> "$APP_DST/config.env"
ok "app files and copilot/ in $APP_DST"

step "Copying the data the page reads (about 150 MB the first time)"
mkdir -p "$APP_DST/data"
cp -f "$REPO_DIR/data/index.db" "$APP_DST/data/index.db"
ok "data/index.db"
if [ -d "$REPO_DIR/data/images" ]; then
  cp -r "$REPO_DIR/data/images" "$APP_DST/data/"
  ok "data/images"
else
  warn "data/images is missing: problems show without figures"
fi
if [ -f "$REPO_DIR/data/explanations.db" ]; then
  cp -f "$REPO_DIR/data/explanations.db" "$APP_DST/data/explanations.db"
  ok "data/explanations.db"
fi
[ -f "$APP_DST/data/tutor.db" ] && ok "keeping the practice profile data/tutor.db"

step "How to run it"
echo "  Open it in App Lab, or from here:"
echo "    arduino-app-cli app start $APP_DST"
echo "  Logs:  arduino-app-cli app logs $APP_DST --all"
echo "  Page:  http://\$(hostname -I | awk '{print \$1}'):7000"
echo "  Web page only, no matrix:  python3 explorer/tutor_server.py --host 0.0.0.0"

if [ "${1:-}" = "--start" ]; then
  step "Starting"
  arduino-app-cli app restart "$APP_DST" 2>/dev/null || arduino-app-cli app start "$APP_DST"
fi
