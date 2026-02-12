"""Tests for SWE-agent query_memory tool."""

from agent_memory.evaluation.swe_agent_tool import (
    QueryMemoryTool,
    QueryMemoryInput,
    QueryMemoryOutput,
)
from agent_memory import AgentMemory


class TestQueryMemoryTool:
    def test_tool_creation(self):
        """Test tool can be created with AgentMemory."""
        memory = AgentMemory(neo4j_uri=None, embedding_api_key=None)
        tool = QueryMemoryTool(memory)

        assert tool.name == "query_memory"
        assert "memory" in tool.description.lower()
        memory.close()

    def test_tool_schema(self):
        """Test tool returns proper JSON schema."""
        memory = AgentMemory(neo4j_uri=None, embedding_api_key=None)
        tool = QueryMemoryTool(memory)

        schema = tool.get_schema()

        assert "name" in schema
        assert "parameters" in schema
        assert "current_error" in schema["parameters"]["properties"]
        assert "locating" in schema["parameters"]["properties"]["phase"]["enum"]
        memory.close()

    def test_tool_invoke(self):
        """Test invoking the tool returns structured output."""
        memory = AgentMemory(neo4j_uri=None, embedding_api_key=None)
        tool = QueryMemoryTool(memory)

        input_data = QueryMemoryInput(
            current_error="ImportError: No module named 'foo'",
            task_description="Fix import error",
            phase="locating",
        )

        output = tool.invoke(input_data)

        assert isinstance(output, QueryMemoryOutput)
        assert isinstance(output.methodologies, list)
        assert isinstance(output.similar_fragments, list)
        assert isinstance(output.warnings, list)
        memory.close()

    def test_tool_to_json(self):
        """Test output can be serialized to JSON."""
        memory = AgentMemory(neo4j_uri=None, embedding_api_key=None)
        tool = QueryMemoryTool(memory)

        input_data = QueryMemoryInput(
            current_error="TypeError",
            task_description="Fix type error",
            phase="fixing",
        )

        output = tool.invoke(input_data)
        json_output = output.to_json()

        assert isinstance(json_output, str)
        import json
        parsed = json.loads(json_output)
        assert "methodologies" in parsed
        memory.close()
