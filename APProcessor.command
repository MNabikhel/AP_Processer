#!/bin/bash
# AP Coder - DOUBLE-CLICK THIS FILE (macOS; the first time: right-click > Open), or run ./APProcessor.command
# (Linux). It is the only one you need.
#
# The first time it sets up whatever is missing (a few minutes), checks that everything works and opens the
# dashboard in your browser. After that it starts in seconds. Keep this window open while you use AP Coder;
# close it (or press Ctrl+C) to stop. Nothing already on this computer is installed twice: an installed
# Python 3.11 or newer is used as it is (3.12 recommended: the offline bundle has packages for the Python
# versions it was built for), the .venv folder is made once, and only missing packages are installed.
# Offline build: nothing is downloaded (developers only: AP_ALLOW_INTERNET=1). Packages come from the
# wheelhouse/ folder of the offline bundle next to this file.
#
# install.sh and start.sh run this same file. The steps themselves are in scripts/launch.py.
cd "$(dirname "$0")" || exit 1

# The Python versions looked for first, best first: the ones the offline bundle has packages for.
PREFERRED="3.12 3.11 3.13 3.14"
# Apple's stand-in for Python: without Apple's developer tools, running it only opens an install dialog.
APPLE_STUB=/usr/bin/python3

stop() {
  echo
  echo "AP Coder stopped. The message above says what to do."
  # AP_NO_PAUSE=1 skips the pause (automated runs).
  [ -n "${AP_NO_PAUSE:-}" ] || read -r -p "Press Enter to close. " _
  exit 1
}

MAC=""
[ "$(uname 2>/dev/null)" = Darwin ] && MAC=1
mac() {
  [ -n "$MAC" ]
}

# Apple's /usr/bin/python3 is a real Python only when the developer tools (Command Line Tools or Xcode) are
# installed: xcode-select says where, and asking it never opens a dialog. When unsure, it is not run.
apple_python_ok() {
  local dev
  dev="$(xcode-select -p 2>/dev/null)" || return 1
  [ -n "$dev" ] && [ -x "$dev/usr/bin/python3" ]
}

# "3.12" for a Python that starts; nothing for one that does not.
py_version() {
  local v
  v="$("$1" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null </dev/null)"
  case "$v" in *[!0-9.]* | .* | *. | *.*.* | "") return 1 ;; *.*) echo "$v" ;; esac
}

# "3.10" -> 310, to compare versions.
number() {
  echo $((${1%%.*} * 100 + ${1#*.}))
}

# How good a Python version is (smaller is better): the PREFERRED ones in order, then any newer 3.x.
# Nothing for one older than 3.11.
rank() {
  local v i=0 major="${1%%.*}" minor="${1#*.}"
  for v in $PREFERRED; do
    [ "$v" = "$1" ] && echo "$i" && return 0
    i=$((i + 1))
  done
  [ "$major" -eq 3 ] && [ "$minor" -ge 11 ] && echo $((100 + minor))
}

# Every place a Python may be, in order: python3.12, python3.11, python3.13, python3.14, then any newer
# python3.X, then python3 and python, on PATH; on a Mac also Homebrew's and python.org's folders.
candidates() {
  local v minor dir
  for v in $PREFERRED; do echo "python$v"; done
  for minor in {15..40}; do echo "python3.$minor"; done
  echo python3
  echo python
  if mac; then
    for dir in /opt/homebrew/bin /usr/local/bin; do
      for v in $PREFERRED; do echo "$dir/python$v"; done
      for minor in {15..40}; do echo "$dir/python3.$minor"; done
      echo "$dir/python3"
    done
    for v in $PREFERRED; do echo "/Library/Frameworks/Python.framework/Versions/$v/bin/python$v"; done
    for dir in /Library/Frameworks/Python.framework/Versions/3.*/bin; do
      [ -d "$dir" ] && echo "$dir/python3"
    done
  fi
}

# PY = the best Python 3.11 or newer on this computer: each candidate is asked its version, the best rank wins
# (3.12 over 3.14 whatever their names), the first one found when two rank the same. When there is none but
# an older one, OLD_PYTHON says which ("3.10"), so the message says it is too old instead of "not installed".
find_python() {
  local py found version r best_rank="" seen=" "
  PY=""
  OLD_PYTHON=""
  while read -r py; do
    found="$(command -v "$py" 2>/dev/null)" || continue
    case "$seen" in *" $found "*) continue ;; esac
    seen="$seen$found "
    if mac && [ "$found" = "$APPLE_STUB" ] && ! apple_python_ok; then
      continue
    fi
    version="$(py_version "$found")"
    [ -n "$version" ] || continue
    if ! r="$(rank "$version")" || [ -z "$r" ]; then
      if [ -z "$OLD_PYTHON" ] || [ "$(number "$version")" -gt "$(number "$OLD_PYTHON")" ]; then
        OLD_PYTHON="$version"  # the newest of the too-old ones
      fi
      continue
    fi
    if [ -z "$best_rank" ] || [ "$r" -lt "$best_rank" ]; then
      PY="$found"
      best_rank="$r"
    fi
  done < <(candidates)
  [ -n "$PY" ]
}

# Set up before: AP Coder's own Python in .venv starts it straight away (whatever Python made it).
if [ -x .venv/bin/python ] && .venv/bin/python -c 'import encodings, pip' >/dev/null 2>&1; then
  PY=.venv/bin/python
elif ! find_python; then
  if [ -n "$OLD_PYTHON" ]; then
    echo "Python $OLD_PYTHON is installed, but AP Coder needs Python 3.11 or newer (3.12 is best)."
  else
    echo "AP Coder needs Python 3.11 or newer (3.12 is best), and it is not installed."
  fi
  echo "What to do: this is an offline build, so it does not download Python. Install Python 3.12 from"
  echo "https://www.python.org/downloads/ (or: brew install python@3.12), then open APProcessor.command again."
  stop
fi

"$PY" scripts/launch.py "$@" || stop
