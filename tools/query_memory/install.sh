#!/bin/bash
# Install dependencies for query_memory tool inside the SWE-bench Docker container.
# This script runs as: cd /root/tools/query_memory && source install.sh
#
# IMPORTANT: We ONLY install into the standalone Python 3.11 (installed by SWE-agent).
# The testbed Python may be as old as 3.6 and cannot run the neo4j driver which
# requires `from __future__ import annotations` (Python 3.7+).

# Find a Python 3.11+ interpreter (base conda or standalone)
PYBIN=""
for candidate in /opt/miniconda3/bin/python3 /root/python3.11/bin/python3.11; do
    if [ -x "$candidate" ]; then
        PYBIN="$candidate"
        break
    fi
done

if [ -n "$PYBIN" ]; then
    # Install neo4j driver into the modern Python
    "$PYBIN" -m pip install -q "neo4j>=5.0.0" 2>&1 || true
    echo "Installed neo4j into $PYBIN"
else
    echo "WARNING: no Python 3.11+ found, query_memory may not work"
fi

# Copy agent_memory from host project if available (for development).
if [ -n "$CONTEXT_GRAPH_ROOT" ] && [ -d "$CONTEXT_GRAPH_ROOT/agent_memory" ]; then
    cp -r "$CONTEXT_GRAPH_ROOT/agent_memory" /root/tools/query_memory/lib/
    echo "Copied agent_memory from CONTEXT_GRAPH_ROOT"
elif [ -d "/root/tools/query_memory/lib/agent_memory" ]; then
    echo "agent_memory already in lib/"
fi

# NOTE: Do NOT export PYTHONPATH here! The testbed env may use Python 3.6
# and having agent_memory/neo4j on PYTHONPATH would cause SyntaxError if
# any tool accidentally imports them. The bash wrapper sets PYTHONPATH locally.
