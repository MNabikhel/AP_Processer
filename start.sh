#!/usr/bin/env bash
# Starts the AP Coder dashboard (sets it up first if needed). Same as ./APProcessor.command.
exec "$(dirname "$0")/APProcessor.command" "$@"
