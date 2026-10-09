#!/bin/bash
# Double-click (macOS) or run ./APProcessor.command (Linux) to start AP Coder: the review dashboard
# opens in your browser. The first run sets everything up (a few minutes). Keep this window open while
# you use it. With a wheelhouse/ folder next to this file (the offline bundle) it installs without internet.
cd "$(dirname "$0")" || exit 1

fail() {
  echo
  echo "AP Coder stopped. $1"
  [ -n "${AP_NO_PAUSE:-}" ] || read -r -p "Press Enter to close. " _
  exit 1
}

PIPFROM=()
if [ -d wheelhouse ]; then
  PIPFROM=(--no-index --find-links wheelhouse)
fi

packages() {
  [ ${#PIPFROM[@]} -eq 0 ] || echo "Installing from the wheelhouse folder (no internet needed)."
  .venv/bin/python -m pip install --quiet --disable-pip-version-check "${PIPFROM[@]}" --upgrade pip >/dev/null 2>&1
  # The OCR add-on reads scanned PDFs; if it can't install here, AP Coder runs without it.
  .venv/bin/python -m pip install --quiet --disable-pip-version-check "${PIPFROM[@]}" -e ".[ocr]" ||
    .venv/bin/python -m pip install --quiet --disable-pip-version-check "${PIPFROM[@]}" -e .
}

find_python() {
  for py in python3.12 python3.11 python3.13 python3.14 python3 python; do
    if command -v "$py" >/dev/null 2>&1 &&
      "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      command -v "$py"
      return 0
    fi
  done
  return 1
}

if [ ! -x .venv/bin/python ]; then
  echo "First run: setting up AP Coder. This takes a few minutes."
  PY="$(find_python)" || fail "Python 3.11 or newer is needed: install it from https://www.python.org/downloads/, then open APProcessor again."
  "$PY" -m venv .venv || fail "Could not create the .venv folder. The message above says why."
  packages || fail "Setup failed. The message above says why."
  # The data folder and the OCR models (an extra: a failure here never stops the setup).
  .venv/bin/python scripts/first_run.py
  echo "Setup finished."
fi

# An update can add a package; install it before starting.
if ! .venv/bin/python scripts/check_deps.py >/dev/null 2>&1; then
  echo "Installing what this update needs..."
  packages || fail "Install failed. The message above says why."
  .venv/bin/python scripts/first_run.py
fi

echo "Starting AP Coder. It opens in your browser; close this window (or press Ctrl+C) to stop."
exec .venv/bin/python -m ap_coder dashboard "$@"
