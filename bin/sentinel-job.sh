#!/bin/zsh
# Entry point for Jarvis's scheduler (job kind "script", args = the sentinel subcommand):
# Jarvis keeps the clock, Heimdall does the work. `sentinel-job.sh check disk` etc.
exec $HOME/.sentinel/bin/sentinel "$@"
