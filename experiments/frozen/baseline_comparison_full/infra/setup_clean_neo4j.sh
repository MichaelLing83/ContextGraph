#!/bin/bash
set -e

# 1. Create a new volume by copying from baseline
echo "=== Creating new Neo4j volume ==="
docker volume create neo4j-contextgraph-clean

# 2. Copy data from baseline volume to new volume
echo "=== Copying baseline data ==="
docker run --rm \
  -v neo4j-contextgraph-data:/source:ro \
  -v neo4j-contextgraph-clean:/dest \
  alpine sh -c "cp -a /source/. /dest/"

echo "=== Starting clean Neo4j on port 7691 ==="
docker run -d \
  --name neo4j-contextgraph-clean \
  -p 7691:7687 \
  -p 7475:7474 \
  -v neo4j-contextgraph-clean:/data \
  -e NEO4J_AUTH=neo4j/contextgraph123 \
  -e NEO4J_PLUGINS='["apoc"]' \
  --restart unless-stopped \
  neo4j:5

echo "=== Waiting for Neo4j to start ==="
sleep 15

# Check it's up
echo "=== Checking health ==="
docker exec neo4j-contextgraph-clean neo4j status || true
echo "Done!"
