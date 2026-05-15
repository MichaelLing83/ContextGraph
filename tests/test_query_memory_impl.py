"""Smoke tests for the SWE-agent tool's query_memory_impl entry point.

We exercise the HTTP path with the rerank toggle on, mocking both the
memory server's /query_memory_items endpoint and the rerank LLM. Tests
mirror what runs inside the SWE-agent Docker container.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_TOOL_BIN = Path(__file__).resolve().parents[1] / "tools" / "query_memory" / "bin"
_TOOL_LIB = Path(__file__).resolve().parents[1] / "tools" / "query_memory" / "lib"
for p in (_TOOL_BIN, _TOOL_LIB):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import query_memory_impl as qmi  # noqa: E402


from _http_helpers import FakeResp as _FakeResp  # noqa: E402


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    for key in (
        "AGENT_RERANK_ENABLED",
        "AGENT_RERANK_K_IN",
        "AGENT_RERANK_K_OUT",
        "OPENAI_API_BASE",
        "LITELLM_MASTER_KEY",
        "OPENAI_API_KEY",
        "RERANK_API_KEY",
        "RERANK_MODEL",
        "REWRITER_MODEL",
        "MEMORY_SERVER_URL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_legacy_path_when_rerank_disabled():
    """AGENT_RERANK_ENABLED unset → falls straight to /query_memory."""
    body = {"playbook": "<memory_playbook>old path</memory_playbook>"}
    with patch("query_memory_impl.urlopen", return_value=_FakeResp(body)) as mock_url:
        out = qmi.query_via_http(
            "http://memory:8013",
            current_error="err",
            task_description="task",
            phase="exploring",
        )
    assert "<memory_playbook>old path</memory_playbook>" in out
    # Exactly one HTTP call to /query_memory
    sent = mock_url.call_args.args[0]
    assert sent.full_url.endswith("/query_memory")


def test_rerank_path_hits_items_then_llm(monkeypatch):
    """With rerank on, fetch items, call LLM, return reformatted playbook."""
    monkeypatch.setenv("AGENT_RERANK_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_BASE", "http://proxy/v1")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")
    monkeypatch.setenv("RERANK_MODEL", "fake-model")
    monkeypatch.setenv("AGENT_RERANK_K_OUT", "2")

    items = [
        {"id": "r0", "text": "[repo/x] alpha", "section": "REPO_SPECIFIC"},
        {"id": "r1", "text": "[repo/x] beta", "section": "REPO_SPECIFIC"},
        {"id": "r2", "text": "[other/y] gamma", "section": "GENERAL_PATTERN"},
        {"id": "r3", "text": "[other/z] delta", "section": "GENERAL_PATTERN"},
    ]
    items_body = {"items": items, "repo": "repo/x", "num_entries": 4}
    llm_body = {
        "choices": [{"message": {"content": json.dumps({"picks": [1, 2]})}}]
    }

    call_log: list[str] = []

    def fake_urlopen(req, timeout=60):
        call_log.append(req.full_url)
        if req.full_url.endswith("/query_memory_items"):
            return _FakeResp(items_body)
        if req.full_url.endswith("/chat/completions"):
            return _FakeResp(llm_body)
        return _FakeResp({"error": f"unexpected {req.full_url}"}, status=500)

    # Patch urlopen in both modules — the impl module calls the server,
    # the rerank module calls the LLM, and they each import their own.
    with patch("query_memory_impl.urlopen", side_effect=fake_urlopen), \
         patch("agent_rerank.urlopen", side_effect=fake_urlopen):
        out = qmi.query_via_http(
            "http://memory:8013",
            current_error="ImportError",
            task_description="fix imports",
            phase="exploring",
        )

    # Both endpoints hit, in the right order, exactly once each
    assert len(call_log) == 2
    assert call_log[0].endswith("/query_memory_items")
    assert call_log[1].endswith("/chat/completions")
    # Output renders the two LLM-picked items, repo-aware formatting
    assert "<memory_playbook>" in out
    assert "## REPO-SPECIFIC RULES (repo/x)" in out
    assert "[r1] [repo/x] beta" in out
    assert "## GENERAL PATTERNS" in out
    assert "[r2] [other/y] gamma" in out
    # r0 and r3 weren't picked — confirm absence
    assert "alpha" not in out
    assert "delta" not in out


def test_rerank_falls_back_to_legacy_when_items_endpoint_404(monkeypatch):
    """If /query_memory_items errors, we silently fall back to /query_memory."""
    from urllib.error import URLError

    monkeypatch.setenv("AGENT_RERANK_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_BASE", "http://proxy/v1")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")
    monkeypatch.setenv("RERANK_MODEL", "fake-model")

    legacy_body = {"playbook": "<memory_playbook>legacy fallback</memory_playbook>"}

    def fake_urlopen(req, timeout=60):
        if req.full_url.endswith("/query_memory_items"):
            raise URLError("404 not found")
        return _FakeResp(legacy_body)

    with patch("query_memory_impl.urlopen", side_effect=fake_urlopen):
        out = qmi.query_via_http(
            "http://memory:8013",
            current_error="e",
            task_description="t",
            phase="exploring",
        )
    assert "legacy fallback" in out


def test_rerank_empty_items_falls_back_to_legacy(monkeypatch):
    """If items endpoint returns []  the legacy path still gets queried."""
    monkeypatch.setenv("AGENT_RERANK_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_BASE", "http://proxy/v1")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")
    monkeypatch.setenv("RERANK_MODEL", "fake-model")

    items_body = {"items": [], "repo": None, "num_entries": 0}
    legacy_body = {"playbook": "<memory_playbook>no rules found</memory_playbook>"}

    def fake_urlopen(req, timeout=60):
        if req.full_url.endswith("/query_memory_items"):
            return _FakeResp(items_body)
        return _FakeResp(legacy_body)

    with patch("query_memory_impl.urlopen", side_effect=fake_urlopen):
        out = qmi.query_via_http(
            "http://memory:8013",
            current_error="e",
            task_description="t",
            phase="exploring",
        )
    assert "no rules found" in out


def test_post_json_treats_500_as_error(monkeypatch):
    """_post_json now refuses non-2xx responses instead of silently parsing
    a server error body as a valid JSON result."""
    monkeypatch.setenv("AGENT_RERANK_ENABLED", "1")
    monkeypatch.setenv("OPENAI_API_BASE", "http://proxy/v1")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")
    monkeypatch.setenv("RERANK_MODEL", "fake-model")
    # /query_memory_items returns 500 with a JSON-shaped body — should
    # be rejected and the rerank path falls back to legacy /query_memory.
    legacy_body = {"playbook": "<memory_playbook>legacy fallback</memory_playbook>"}

    def fake_urlopen(req, timeout=60):
        if req.full_url.endswith("/query_memory_items"):
            return _FakeResp({"items": [{"id": "wrong"}]}, status=500)
        return _FakeResp(legacy_body)

    with patch("query_memory_impl.urlopen", side_effect=fake_urlopen):
        out = qmi.query_via_http(
            "http://memory:8013",
            current_error="e",
            task_description="t",
            phase="exploring",
        )
    # Should have fallen back to legacy, not consumed the 500 body
    assert "legacy fallback" in out
    assert "wrong" not in out


def test_format_items_no_repo_path():
    items = [
        {"id": "r0", "text": "[a/b] one", "section": "OTHERS"},
        {"id": "r1", "text": "[c/d] two", "section": "OTHERS"},
    ]
    out = qmi._format_items_as_playbook(items, repo=None)
    assert "REPO-SPECIFIC" not in out
    assert "[r0] [a/b] one" in out
    assert "[r1] [c/d] two" in out
