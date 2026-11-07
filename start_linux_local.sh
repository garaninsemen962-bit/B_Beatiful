#!/usr/bin/env sh
cd "$(dirname "$0")" || exit 1
echo "Demo mode without Docker: procedures run on this computer without isolation."
exec python3 app.py --local-runner "$@"
