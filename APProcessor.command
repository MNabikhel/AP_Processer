#!/bin/bash
# AP Coder - DOUBLE-CLICK THIS FILE (macOS; the first time: right-click > Open), or run ./APProcessor.command
# (Linux). It is the only one you need.
#
# The first time it sets up whatever is missing (a few minutes), checks that everything works and opens the
# dashboard in your browser. After that it starts in seconds. Keep this window open while you use AP Coder;
# close it (or press Ctrl+C) to stop. Nothing already on this computer is installed twice: an installed
# Python (3.12 or 3.11 first, then 3.13) is used as it is, the .venv folder is made once, and only missing
# packages are installed. Offline build: nothing is downloaded (developers only: AP_ALLOW_INTERNET=1).
# Packages come from the wheelhouse/ folder of the offline bundle next to this file.
#
# install.sh and start.sh run this same file. The steps themselves are in scripts/launch.py.
cd "$(dirname "$0")" || exit 1

stop() {
  echo
  echo "AP Coder stopped. The message above says what to do."
  # AP_NO_PAUSE=1 skips the pause (automated runs).
  [ -n "${AP_NO_PAUSE:-}" ] || read -r -p "Press Enter to close. " _
  exit 1
}

supported() {
  "$1" -c 'import sys; sys.exit(0 if (3, 11) <= sys.version_info[:2] <= (3, 13) else 1)' >/dev/null 2>&1
}

find_python() {
  # Never /usr/bin/python3 on a Mac: without Apple's developer tools it only opens an install dialog.
  local py found
  for py in python3.12 python3.11 python3.13 python3 python \
    /opt/homebrew/bin/python3.12 /usr/local/bin/python3.12 \
    /Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12 \
    /Library/Frameworks/Python.framework/Versions/3.11/bin/python3.11 \
    /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13; do
    found="$(command -v "$py" 2>/dev/null)" || continue
    if [ "$(uname)" = Darwin ] && [ "$found" = /usr/bin/python3 ]; then
      continue
    fi
    if supported "$found"; then
      echo "$found"
      return 0
    fi
  done
  return 1
}

# Set up before: AP Coder's own Python in .venv starts it straight away.
if [ -x .venv/bin/python ] && .venv/bin/python -c 'import encodings, pip' >/dev/null 2>&1; then
  PY=.venv/bin/python
elif ! PY="$(find_python)"; then
  echo "AP Coder needs Python 3.11, 3.12 or 3.13, and it is not installed."
  echo "What to do: install Python 3.12 from https://www.python.org/downloads/ (or: brew install python@3.12),"
  echo "then open APProcessor.command again."
  stop
fi

"$PY" scripts/launch.py "$@" || stop
