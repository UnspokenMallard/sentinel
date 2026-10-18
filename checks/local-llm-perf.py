"""Runs Gateway's local-llm-perf job from Sentinel's job entry point.

The script and its artifact (state/local-llm-perf.json) stay in ~/.gateway, which owns them, and Jarvis's
local-LLM triage reads that artifact. This wrapper only gives the job the same entry point as the others;
it writes no envelope, so `sentinel checks` shows no ran_at for it.
"""
import subprocess
import sys
from pathlib import Path

SCRIPT = Path.home() / ".gateway" / "gateway" / "jobs" / "local-llm-perf.py"

sys.exit(subprocess.run([sys.executable, str(SCRIPT), *sys.argv[1:]]).returncode)
