#!/bin/bash
# Clone the primary Neo4j graph to a treatment instance for online learning.
#
# The treatment instance runs on different ports (7475 HTTP, 7688 Bolt)
# so both can run simultaneously. Treatment will ingest new trajectories
# without affecting the primary graph.
#
# Usage:
#   bash scripts/clone_neo4j_graph.sh              # copy data from primary (fast)
#   bash scripts/clone_neo4j_graph.sh --rebuild     # rebuild graph from scratch (~12 min)
#   bash scripts/clone_neo4j_graph.sh --remove      # remove treatment container
set -euo pipefail

PRIMARY_CONTAINER=neo4j-contextgraph
TREATMENT_CONTAINER=neo4j-contextgraph-treatment
TREATMENT_HTTP_PORT=7475
TREATMENT_BOLT_PORT=7688
NEO4J_AUTH="neo4j/contextgraph123"

# ── Remove mode ──────────────────────────────────────────────────────
if [ "${1:-}" = "--remove" ]; then
    echo "Removing treatment container..."
    docker stop "$TREATMENT_CONTAINER" 2>/dev/null || true
    docker rm "$TREATMENT_CONTAINER" 2>/dev/null || true
    echo "Done."
    exit 0
fi

# ── Pre-checks ───────────────────────────────────────────────────────
if ! docker ps --format '{{.Names}}' | grep -q "^${PRIMARY_CONTAINER}$"; then
    echo "ERROR: Primary container '${PRIMARY_CONTAINER}' is not running."
    echo "Start it with: docker start ${PRIMARY_CONTAINER}"
    exit 1
fi

# Remove old treatment container if it exists
if docker ps -a --format '{{.Names}}' | grep -q "^${TREATMENT_CONTAINER}$"; then
    echo "Removing existing treatment container..."
    docker stop "$TREATMENT_CONTAINER" 2>/dev/null || true
    docker rm "$TREATMENT_CONTAINER"
fi

# ── Rebuild mode (slow but safe) ─────────────────────────────────────
if [ "${1:-}" = "--rebuild" ]; then
    echo "Creating empty treatment container..."
    docker run -d --name "$TREATMENT_CONTAINER" \
        -p "$TREATMENT_HTTP_PORT":7474 -p "$TREATMENT_BOLT_PORT":7687 \
        -e "NEO4J_AUTH=${NEO4J_AUTH}" \
        neo4j:5
    echo "Waiting for Neo4j to start..."
    sleep 15
    echo ""
    echo "Treatment container ready. Now rebuild the graph:"
    echo "  NEO4J_URI=bolt://localhost:${TREATMENT_BOLT_PORT} python scripts/build_context_graph.py"
    exit 0
fi

# ── Copy mode (default, fast) ────────────────────────────────────────
echo "=== Clone Neo4j Graph for Treatment Group ==="

TMPDIR=$(mktemp -d)
trap 'rm -rf "$TMPDIR"' EXIT

# Stop primary briefly for a consistent snapshot
echo "Stopping primary Neo4j for consistent copy..."
docker stop "$PRIMARY_CONTAINER"

echo "Copying data from primary..."
docker cp "$PRIMARY_CONTAINER":/data "$TMPDIR"/data

echo "Restarting primary Neo4j..."
docker start "$PRIMARY_CONTAINER"

# Create treatment container
echo "Creating treatment container on ports ${TREATMENT_HTTP_PORT}/${TREATMENT_BOLT_PORT}..."
docker create --name "$TREATMENT_CONTAINER" \
    -p "$TREATMENT_HTTP_PORT":7474 -p "$TREATMENT_BOLT_PORT":7687 \
    -e "NEO4J_AUTH=${NEO4J_AUTH}" \
    neo4j:5

echo "Copying data into treatment container..."
docker cp "$TMPDIR"/data/. "$TREATMENT_CONTAINER":/data/

echo "Starting treatment Neo4j..."
docker start "$TREATMENT_CONTAINER"

echo ""
echo "=== Done ==="
echo "Primary:   bolt://localhost:7687  (HTTP: 7474)"
echo "Treatment: bolt://localhost:${TREATMENT_BOLT_PORT}  (HTTP: ${TREATMENT_HTTP_PORT})"
echo ""
echo "Waiting for treatment to become ready..."
for i in $(seq 1 30); do
    if docker logs "$TREATMENT_CONTAINER" 2>&1 | grep -q "Started."; then
        echo "Treatment Neo4j is ready!"
        exit 0
    fi
    sleep 1
done
echo "Treatment may still be starting. Check: docker logs ${TREATMENT_CONTAINER}"
