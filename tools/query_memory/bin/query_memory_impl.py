#!/usr/bin/env python3
"""SWE-agent tool: query the context graph memory for debugging strategies."""

import json
import os
import subprocess
import sys


def main():
    if len(sys.argv) < 4:
        print("Usage: query_memory <current_error> <task_description> <phase>")
        sys.exit(1)

    current_error = sys.argv[1]
    task_description = sys.argv[2]
    phase = sys.argv[3]

    # Determine tool_lib path
    script_dir = os.path.dirname(os.path.abspath(__file__))
    tool_root = os.path.dirname(script_dir)
    tool_lib = os.path.join(tool_root, "lib")

    # Add to sys.path
    if os.path.isdir(tool_lib) and tool_lib not in sys.path:
        sys.path.insert(0, tool_lib)

    project_root = os.environ.get("CONTEXT_GRAPH_ROOT", "")
    if project_root and project_root not in sys.path:
        sys.path.insert(0, project_root)

    # Install neo4j if not available
    try:
        import neo4j  # noqa: F401
    except (ImportError, SyntaxError):
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-q", "neo4j"],
            stderr=subprocess.PIPE,
        )
        if result.returncode != 0:
            print(f"ERROR: Failed to install neo4j driver (exit {result.returncode})", file=sys.stderr)
            print("RESULT: ERROR - neo4j driver installation failed", flush=True)
            sys.exit(1)

    try:
        from agent_memory import AgentMemory
        from agent_memory.evaluation.swe_agent_tool import (
            QueryMemoryTool,
            QueryMemoryInput,
        )

        neo4j_uri = os.environ.get("NEO4J_URI", "bolt://host.docker.internal:7687")
        neo4j_user = os.environ.get("NEO4J_USER", "neo4j")
        neo4j_password = os.environ.get("NEO4J_PASSWORD", "")
        if not neo4j_password:
            print("ERROR: NEO4J_PASSWORD env var is required but not set.", file=sys.stderr)
            print("RESULT: ERROR - NEO4J_PASSWORD not set", flush=True)
            sys.exit(1)

        # Embedding configuration
        # Prefer LITELLM_MASTER_KEY (proxy auth), fall back to OPENAI_API_KEY (direct)
        embedding_api_key = os.environ.get("LITELLM_MASTER_KEY", "") or os.environ.get("OPENAI_API_KEY", "")
        embedding_base_url = os.environ.get("OPENAI_API_BASE", "http://localhost:4000/v1")
        embedding_model = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-large")

        # Query rewriter configuration
        rewriter_enabled = os.environ.get("REWRITER_ENABLED", "").lower() in ("1", "true", "yes")
        rewriter_api_base = os.environ.get("REWRITER_API_BASE", "http://localhost:4000/v1")
        rewriter_api_key = os.environ.get("REWRITER_API_KEY", "") or os.environ.get("LITELLM_MASTER_KEY", "")
        rewriter_model = os.environ.get("REWRITER_MODEL", "claude-sonnet-4-20250514")

        memory = AgentMemory(
            neo4j_uri=neo4j_uri,
            neo4j_auth=(neo4j_user, neo4j_password),
            embedding_api_key=embedding_api_key or None,
            embedding_base_url=embedding_base_url or None,
            embedding_model=embedding_model,
            rewriter_api_base=rewriter_api_base or None,
            rewriter_api_key=rewriter_api_key or None,
            rewriter_model=rewriter_model,
            rewriter_enabled=rewriter_enabled,
        )
        try:
            tool = QueryMemoryTool(memory)
            input_data = QueryMemoryInput(
                current_error=current_error,
                task_description=task_description,
                phase=phase,
            )
            output = tool.invoke(input_data)
            try:
                print(output.to_structured())
            except Exception:
                print(output.to_json())
        finally:
            memory.close()

    except Exception as e:
        error_output = {
            "similar_experiences": [],
            "strategies": [],
            "similar_problems": [],
            "warnings": [f"Memory query failed: {e}"],
            "playbook_text": "",
        }
        print(json.dumps(error_output, indent=2))


if __name__ == "__main__":
    main()
