#!/usr/bin/env python3
"""Run live-SWE-agent treatment (with ContextGraph memory injection).

Monkey-patches mini-swe-agent's ProgressTrackingAgent to prepend memory
context to each task, then delegates to mini-extra swebench.

Usage:
  /tmp/mini-swe-venv/bin/python scripts/run_liveswe_treatment.py [mini-extra swebench args...]

Example:
  /tmp/mini-swe-venv/bin/python scripts/run_liveswe_treatment.py \
    --subset princeton-nlp/SWE-bench_Verified --split test \
    -c agent.mode=yolo -m google/gemini-3.1-pro-preview \
    --model-class openrouter_textbased --environment-class docker \
    -w 5 -o /tmp/liveswe-500-treatment
"""

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("MSWEA_CONFIGURED", "1")

# Apply treatment patch before importing swebench
from scripts.patch_treatment import enable
enable()

# Delegate to mini-extra swebench CLI
from minisweagent.run.benchmarks.swebench import app
app(standalone_mode=True)
