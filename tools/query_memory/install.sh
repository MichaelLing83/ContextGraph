#!/bin/bash
# Install dependencies for query_memory tool inside the SWE-bench Docker container.
# This script runs as: cd /root/tools/query_memory && source install.sh

set -e

# Install neo4j driver — required for memory queries
pip install -q "neo4j>=5.0.0" 2>&1 || pip3 install -q "neo4j>=5.0.0" 2>&1 || true

# Copy agent_memory from host project if available (for development).
if [ -n "$CONTEXT_GRAPH_ROOT" ] && [ -d "$CONTEXT_GRAPH_ROOT/agent_memory" ]; then
    cp -r "$CONTEXT_GRAPH_ROOT/agent_memory" /root/tools/query_memory/lib/
    echo "Copied agent_memory from CONTEXT_GRAPH_ROOT"
elif [ -d "/root/tools/query_memory/lib/agent_memory" ]; then
    echo "agent_memory already in lib/"
fi

# Ensure lib/ is on PYTHONPATH so agent_memory is importable
export PYTHONPATH="/root/tools/query_memory/lib:${PYTHONPATH:-}"
