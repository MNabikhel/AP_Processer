#!/usr/bin/env bash
# Most people don't need this file: open APProcessor.command instead (it sets up and starts AP Coder).
# install.sh is kept for older instructions: the same setup as APProcessor.command, plus the installer's
# questions (data folder, Azure settings). Options: --yes --no-update --skip-tests --no-start --fresh-start
exec "$(dirname "$0")/APProcessor.command" --installer "$@"
