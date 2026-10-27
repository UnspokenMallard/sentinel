#!/bin/zsh
# Run all Heimdall tests in Jarvis's venv; the last line is the pass count ("N passed").
exec $HOME/.jarvis/.venv/bin/python "${0:A:h}/test_heimdall.py"
