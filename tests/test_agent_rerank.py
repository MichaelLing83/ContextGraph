"""Tests for the agent-side LLM rerank module.

The module is stdlib-only and used inside the SWE-agent container, so the
tests mock urlopen / env directly rather than mocking via openai client.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# The module ships next to bin/ inside the SWE-agent tool bundle. Add that
# lib dir to sys.path so the tests import the exact code that runs in the
# container.
_TOOL_LIB = Path(__file__).resolve().parents[1] / "tools" / "query_memory" / "lib"
if str(_TOOL_LIB) not in sys.path:
    sys.path.insert(0, str(_TOOL_LIB))

import agent_rerank  # noqa: E402


def _items(n: int) -> list[dict]:
    return [
        {
            "id": f"r{i}",
            "text": f"[repo/x] rule {i}",
            "section": "REPO_SPECIFIC" if i % 2 == 0 else "GENERAL_PATTERN",
            "score": 1.0 - i * 0.05,
        }
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _env_clean(monkeypatch):
    for key in (
        "OPENAI_API_BASE",
        "LITELLM_MASTER_KEY",
        "OPENAI_API_KEY",
        "RERANK_API_KEY",
        "RERANK_MODEL",
        "REWRITER_MODEL",
        "AGENT_RERANK_ENABLED",
        "AGENT_RERANK_K_IN",
        "AGENT_RERANK_K_OUT",
    ):
        monkeypatch.delenv(key, raising=False)


from _http_helpers import FakeResp as _FakeResp  # noqa: E402


def _configured_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_BASE", "http://proxy/v1")
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-test")
    monkeypatch.setenv("RERANK_MODEL", "fake-model")


def test_rerank_no_config_falls_back_to_top_k():
    items = _items(5)
    out = agent_rerank.rerank_items(
        items,
        task_description="t",
        current_error="e",
        k_out=2,
    )
    assert [i["id"] for i in out] == ["r0", "r1"]


def test_rerank_returns_all_when_k_geq_len():
    items = _items(2)
    out = agent_rerank.rerank_items(
        items,
        task_description="t",
        current_error="e",
        k_out=5,
    )
    assert out == items


def test_rerank_empty_input():
    assert agent_rerank.rerank_items([], task_description="t", current_error="e") == []


def test_rerank_k_out_zero():
    assert agent_rerank.rerank_items(_items(3), task_description="t", current_error="e", k_out=0) == []


def test_rerank_picks_from_llm(monkeypatch):
    _configured_env(monkeypatch)
    items = _items(6)
    body = {
        "choices": [
            {"message": {"content": json.dumps({"picks": [2, 0, 4]})}}
        ]
    }
    with patch("agent_rerank.urlopen", return_value=_FakeResp(body)) as mock_url:
        out = agent_rerank.rerank_items(
            items,
            task_description="fix import",
            current_error="ModuleNotFoundError",
            k_out=3,
            working_context="(no git)",
        )
    assert [i["id"] for i in out] == ["r2", "r0", "r4"]
    # Exactly one chat/completions call
    assert mock_url.call_count == 1
    sent_req = mock_url.call_args.args[0]
    assert sent_req.full_url.endswith("/chat/completions")
    assert sent_req.headers["Authorization"] == "Bearer sk-test"


def test_rerank_falls_back_when_llm_returns_garbage(monkeypatch):
    _configured_env(monkeypatch)
    items = _items(5)
    body = {"choices": [{"message": {"content": "I am not JSON"}}]}
    with patch("agent_rerank.urlopen", return_value=_FakeResp(body)):
        out = agent_rerank.rerank_items(
            items,
            task_description="t",
            current_error="e",
            k_out=2,
            working_context="(no git)",
        )
    # Garbage → fall back to top-k by server order
    assert [i["id"] for i in out] == ["r0", "r1"]


def test_rerank_falls_back_when_llm_network_fails(monkeypatch):
    _configured_env(monkeypatch)
    items = _items(4)
    from urllib.error import URLError

    def boom(*_a, **_kw):
        raise URLError("connection refused")

    with patch("agent_rerank.urlopen", side_effect=boom):
        out = agent_rerank.rerank_items(
            items,
            task_description="t",
            current_error="e",
            k_out=2,
            working_context="(no git)",
        )
    assert [i["id"] for i in out] == ["r0", "r1"]


def test_rerank_clamps_out_of_range_picks(monkeypatch):
    _configured_env(monkeypatch)
    items = _items(5)  # >k_out so the early "return all" branch doesn't fire
    # 99 is out of range, -1 is too, "two" is wrong type, 1 and 2 are valid
    body = {"choices": [{"message": {"content": json.dumps({"picks": [99, -1, "two", 1, 2, 1]})}}]}
    with patch("agent_rerank.urlopen", return_value=_FakeResp(body)):
        out = agent_rerank.rerank_items(
            items,
            task_description="t",
            current_error="e",
            k_out=3,
            working_context="(no git)",
        )
    # Should dedupe + drop out-of-range. With only 2 valid picks ([1, 2])
    # and k_out=3, the rerank returns only those 2 (no padding from below
    # — that's the caller's job if they need exactly k_out).
    assert [i["id"] for i in out] == ["r1", "r2"]


def test_rerank_extracts_json_from_prose(monkeypatch):
    _configured_env(monkeypatch)
    items = _items(4)
    content = (
        "Sure! Based on the context, here are my picks:\n\n"
        '{"picks": [3, 1]}\n\n'
        "Hope this helps."
    )
    body = {"choices": [{"message": {"content": content}}]}
    with patch("agent_rerank.urlopen", return_value=_FakeResp(body)):
        out = agent_rerank.rerank_items(
            items,
            task_description="t",
            current_error="e",
            k_out=2,
            working_context="(no git)",
        )
    assert [i["id"] for i in out] == ["r3", "r1"]


def test_is_enabled_truthy(monkeypatch):
    for val in ("1", "true", "TRUE", "yes", "On"):
        monkeypatch.setenv("AGENT_RERANK_ENABLED", val)
        assert agent_rerank.is_enabled() is True


def test_is_enabled_falsy(monkeypatch):
    for val in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("AGENT_RERANK_ENABLED", val)
        assert agent_rerank.is_enabled() is False


def test_default_k_env(monkeypatch):
    monkeypatch.setenv("AGENT_RERANK_K_IN", "20")
    monkeypatch.setenv("AGENT_RERANK_K_OUT", "5")
    assert agent_rerank.get_default_k_in() == 20
    assert agent_rerank.get_default_k_out() == 5


def test_git_diff_stat_cached_per_cwd(monkeypatch, tmp_path):
    """_capture_git_diff_stat caches per cwd — only one subprocess call
    even when invoked twice from the same working dir."""
    agent_rerank.invalidate_git_diff_cache()
    calls = []

    class _FakeResult:
        returncode = 0
        stdout = " src/x.py | 2 +-\n 1 file changed"

    def fake_run(cmd, **kwargs):
        calls.append((tuple(cmd), kwargs.get("cwd")))
        return _FakeResult()

    monkeypatch.setattr(agent_rerank.subprocess, "run", fake_run)
    first = agent_rerank._capture_git_diff_stat(str(tmp_path))
    second = agent_rerank._capture_git_diff_stat(str(tmp_path))
    assert first == second
    assert "src/x.py" in first
    assert len(calls) == 1  # the second call was served from cache


def test_default_k_invalid_ignored(monkeypatch):
    monkeypatch.setenv("AGENT_RERANK_K_IN", "not-a-number")
    monkeypatch.setenv("AGENT_RERANK_K_OUT", "0")
    # Both invalid → defaults from module constants
    assert agent_rerank.get_default_k_in() == agent_rerank.DEFAULT_K_IN
    assert agent_rerank.get_default_k_out() == agent_rerank.DEFAULT_K_OUT


def test_prompt_includes_task_error_and_items():
    items = _items(3)
    prompt = agent_rerank._build_rerank_prompt(
        items=items,
        task_description="cap headers correctly",
        current_error="AttributeError: NoneType",
        k_out=2,
        working_context=" src/x.py | 3 +-",
    )
    assert "cap headers correctly" in prompt
    assert "AttributeError: NoneType" in prompt
    assert "rule 0" in prompt
    assert "rule 1" in prompt
    assert "rule 2" in prompt
    # k_out is named explicitly so the model doesn't have to guess
    assert "at most 2" in prompt or "UP TO 2" in prompt
    # Working context echoed verbatim
    assert "src/x.py" in prompt
