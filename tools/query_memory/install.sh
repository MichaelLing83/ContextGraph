#!/bin/bash
# Install dependencies for query_memory tool inside the SWE-bench Docker container.
# This script runs as: cd /root/tools/query_memory && source install.sh
#
# IMPORTANT: Use ONLY the standalone Python 3.11 (installed by SWE-agent).
# The testbed Python / conda base may be as old as 3.6 and CANNOT install
# neo4j>=5.0 or run agent_memory code (uses `from __future__ import annotations`).
#
# IMPORTANT: Do NOT run pip install here! The pip download/index resolution
# consumes memory/disk and produces large stderr output that destabilizes the
# pexpect pty session in resource-constrained containers. Instead, neo4j is
# pre-bundled in lib/ via requirements.txt wheel files.

# Find the standalone Python 3.11 (SWE-agent always installs it at /root/python3.11)
PYBIN=""
if [ -x "/root/python3.11/bin/python3.11" ]; then
    PYBIN="/root/python3.11/bin/python3.11"
elif [ -x "/root/python3.11/bin/python3" ]; then
    PYBIN="/root/python3.11/bin/python3"
fi

if [ -n "$PYBIN" ]; then
    # Install neo4j + numpy from bundled wheels (silent, no network)
    if [ -d "/root/tools/query_memory/lib/wheels" ]; then
        "$PYBIN" -m pip install -q --no-index --find-links /root/tools/query_memory/lib/wheels "neo4j>=5.0.0" "numpy" 2>/dev/null || true
    fi
    # Fallback: install from PyPI if wheels are missing (suppress ALL output to avoid pexpect issues)
    "$PYBIN" -m pip install -q "neo4j>=5.0.0" "numpy" >/dev/null 2>/dev/null || true
fi

# Copy agent_memory from host project if available (for development).
if [ -n "$CONTEXT_GRAPH_ROOT" ] && [ -d "$CONTEXT_GRAPH_ROOT/agent_memory" ]; then
    cp -r "$CONTEXT_GRAPH_ROOT/agent_memory" /root/tools/query_memory/lib/
fi
