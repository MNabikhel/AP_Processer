#!/usr/bin/env bash
# AP Coder installer / updater for macOS and Linux. Safe to run again.
set -u
cd "$(dirname "$0")"
for py in python3.12 python3.13 python3.11 python3.10 python3 python; do
  if command -v "$py" >/dev/null 2>&1 && "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    exec "$py" scripts/install.py "$@"
  fi
done
echo "Python 3.10 or newer was not found. Install it from https://www.python.org/downloads/ and run ./install.sh again."
exit 1
