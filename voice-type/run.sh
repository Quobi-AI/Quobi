#!/usr/bin/env bash
# Convenience launcher: source .env, then exec the daemon inside the venv.
set -euo pipefail

cd "$(dirname "$(readlink -f "$0")")"

if [[ -f .env ]]; then
    set -a
    # shellcheck disable=SC1091
    source .env
    set +a
fi

if [[ -x .venv/bin/python ]]; then
    exec .venv/bin/python -m voice_type "$@"
else
    exec python3 -m voice_type "$@"
fi
