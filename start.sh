#!/usr/bin/env bash
# Starts the AP Coder dashboard (Ctrl+C to stop).
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "AP Coder is not installed yet: run ./install.sh first."
  exit 1
fi
exec .venv/bin/python -m ap_coder dashboard
