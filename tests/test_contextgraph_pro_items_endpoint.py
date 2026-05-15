"""Tests for the /query_memory_items endpoint added on the hybrid pro server.

These cover the pure helpers (no Neo4j needed) and a TestClient round-trip
where the AgentMemory dependency is patched out, so the suite runs in CI
without a live Neo4j instance.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import List, Optional, Tuple
from unittest.mock import MagicMock

import pytest

# Make scripts/baselines importable.
_BASELINES = Path(__file__).resolve().parent.parent / "scripts" / "baselines"
if str(_BASELINES) not in sys.path:
    sys.path.insert(0, str(_BASELINES))

from agent_memory.models import PlaybookEntry  # noqa: E402

import contextgraph_pro_server as srv  # noqa: E402


# --- Pure helpers ----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("[flipt-io/flipt] some rule", "flipt-io/flipt"),
        ("  [ansible/ansible] leading whitespace", "ansible/ansible"),
        ("[OTHERS] non-repo bracket", None),     # no slash → not a repo
        ("[a/b/c] nested slashes", "a/b/c"),     # still considered repo-like
        ("no leading bracket", None),
        ("", None),
    ],
)
def test_extract_repo_from_text(text: str, expected: Optional[str]) -> None:
    assert srv._extract_repo_from_text(text) == expected


def test_build_query_text_concatenates_in_documented_order() -> None:
    req = srv.MemoryQueryRequest(
        problem_statement="ps",
        error_message="em",
        current_error="ce",
        task_description="td",
        query="q",
    )
    # Order is error_message > current_error > problem_statement > task_description > query.
    assert srv._build_query_text(req) == "em ce ps td q"


def test_build_query_text_skips_whitespace_only_fields() -> None:
    """Whitespace-only fields are dropped, not joined as empty padding."""
    req = srv.MemoryQueryRequest(
        problem_statement=None,
        error_message="   ",     # whitespace-only — must be dropped
        task_description="real query",
    )
    assert srv._build_query_text(req) == "real query"


def test_build_query_text_strips_each_field_to_avoid_interior_padding() -> None:
    """Each surviving field is stripped, so no extra spaces leak through."""
    req = srv.MemoryQueryRequest(
        error_message="  hello  ",
        task_description="  world  ",
    )
    assert srv._build_query_text(req) == "hello world"


def test_build_query_text_empty_when_all_fields_missing() -> None:
    assert srv._build_query_text(srv.MemoryQueryRequest()) == ""


def test_memory_item_rejects_invalid_section() -> None:
    """Literal-typed section catches typos at validation time."""
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        srv.MemoryItem(
            id="x", text="[a/b] t", section="BOGUS_SECTION",  # type: ignore[arg-type]
            prefix="misc",
        )


def test_entries_to_items_tags_section_and_extracts_repo() -> None:
    e1 = PlaybookEntry(id="p1", prefix="misc", section="OTHERS",
                       text="[flipt-io/flipt] use reorderRuleRanks")
    e2 = PlaybookEntry(id="p2", prefix="testing", section="OTHERS",
                       text="[qutebrowser/qutebrowser] add regression test")
    items = srv._entries_to_items([(e1, 0.9), (e2, 0.8)], "REPO_SPECIFIC")
    assert [i.id for i in items] == ["p1", "p2"]
    assert all(i.section == "REPO_SPECIFIC" for i in items)
    assert [i.repo for i in items] == ["flipt-io/flipt", "qutebrowser/qutebrowser"]
    assert [i.score for i in items] == [0.9, 0.8]


# --- Endpoint via TestClient ----------------------------------------------


@pytest.fixture
def app_with_mocked_memory(monkeypatch):
    """Build the FastAPI app but swap AgentMemory + cypher helpers for mocks.

    Returns ``(app, mock_state)`` so tests can assert on what was called.
    """
    state = {}

    fake_memory = MagicMock(name="AgentMemory")
    fake_memory.playbook_retriever.store = MagicMock(name="store")
    fake_memory.playbook_retriever.embedder.embed.return_value = [0.1] * 3072
    fake_memory.playbook_retriever.retrieve.return_value = []
    state["memory"] = fake_memory

    monkeypatch.setattr(srv, "AgentMemory", lambda **kw: fake_memory)

    # Stub the score-aware cypher helper used by /query_memory_items.
    def fake_scored(store, embedding, top_k, repo, match_repo):
        state["last_scored_call"] = {
            "top_k": top_k, "repo": repo, "match_repo": match_repo,
        }
        if match_repo:
            return [
                (PlaybookEntry(id="pb_in_1", prefix="storage",
                               section="OTHERS",
                               text=f"[{repo}] in-repo rule one"), 0.91),
                (PlaybookEntry(id="pb_in_2", prefix="testing",
                               section="OTHERS",
                               text=f"[{repo}] in-repo rule two"), 0.84),
            ][:top_k]
        return [
            (PlaybookEntry(id="pb_out_1", prefix="testing", section="OTHERS",
                           text="[qutebrowser/qutebrowser] cross-repo rule"),
             0.72),
        ][:top_k]

    monkeypatch.setattr(srv, "cypher_filtered_search_with_scores", fake_scored)

    # Also stub the playbook-formatting helper used by /query_memory so the
    # /health probe path doesn't blow up if a test happens to call it.
    monkeypatch.setattr(srv, "cypher_filtered_search",
                        lambda *a, **kw: [])

    app = srv.create_app(k_in=3, k_out=2)
    return app, state


def test_query_memory_items_repo_inferred_from_request(app_with_mocked_memory):
    from fastapi.testclient import TestClient

    app, _ = app_with_mocked_memory
    client = TestClient(app)
    r = client.post("/query_memory_items", json={
        "task_description": "fix evaluation ordering bug",
        "repo": "flipt-io/flipt",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["repo"] == "flipt-io/flipt"
    assert body["retrieval_method"] == "contextgraph_hybrid"
    assert body["num_entries"] == len(body["items"]) == 3  # 2 in + 1 out
    sections = [it["section"] for it in body["items"]]
    assert sections.count("REPO_SPECIFIC") == 2
    assert sections.count("GENERAL_PATTERN") == 1
    # Every entry must carry its repo field, parsed from text prefix.
    assert all(it["repo"] for it in body["items"])
    # Scores from the Cypher path must round-trip.
    repo_scores = [it["score"] for it in body["items"]
                   if it["section"] == "REPO_SPECIFIC"]
    assert repo_scores == [0.91, 0.84]


def test_query_memory_items_empty_query_returns_empty(app_with_mocked_memory):
    from fastapi.testclient import TestClient

    app, _ = app_with_mocked_memory
    client = TestClient(app)
    r = client.post("/query_memory_items", json={})
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "items": [], "num_entries": 0,
        "retrieval_method": "contextgraph_hybrid", "repo": None,
    }


def test_query_memory_items_repo_inferred_from_instance_id(
    app_with_mocked_memory,
):
    """`instance_id` is the documented fallback when `repo` is omitted —
    /query_memory_items must honour it the same way /query_memory does."""
    from fastapi.testclient import TestClient

    app, _ = app_with_mocked_memory
    client = TestClient(app)
    r = client.post("/query_memory_items", json={
        "task_description": "fix evaluation ordering bug",
        # No explicit `repo`; instance_id regex should parse "flipt-io/flipt".
        "instance_id": "instance_flipt-io__flipt-abc123",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["repo"] == "flipt-io/flipt"
    assert body["num_entries"] == 3


def test_query_memory_items_repo_inferred_from_text(app_with_mocked_memory):
    """When neither `repo` nor `instance_id` is supplied, the score-based
    text match in `infer_repo` should still locate the Pro repo name in
    free text."""
    from fastapi.testclient import TestClient

    app, _ = app_with_mocked_memory
    client = TestClient(app)
    r = client.post("/query_memory_items", json={
        "task_description": (
            "investigate failing tests in qutebrowser/qutebrowser browser "
            "session restoration"
        ),
    })
    assert r.status_code == 200
    body = r.json()
    assert body["repo"] == "qutebrowser/qutebrowser"


def test_filtered_vector_query_empty_repo_returns_empty(monkeypatch):
    """Defensive guard: empty / whitespace-only `repo` must not run a
    silent `STARTS WITH '[]'` query — return [] immediately."""
    sentinel = {"called": False}

    fake_store = MagicMock()

    def fake_execute(*_a, **_kw):
        sentinel["called"] = True
        return []

    fake_store.execute_query = fake_execute

    assert srv._filtered_vector_query(fake_store, [0.0] * 3, top_k=3,
                                      repo=None, match_repo=True) == []
    assert srv._filtered_vector_query(fake_store, [0.0] * 3, top_k=3,
                                      repo="", match_repo=True) == []
    assert srv._filtered_vector_query(fake_store, [0.0] * 3, top_k=3,
                                      repo="   ", match_repo=False) == []
    assert sentinel["called"] is False, (
        "execute_query must not be called for empty repo"
    )


def test_query_memory_items_no_repo_uses_fallback_retrieve(
    app_with_mocked_memory,
):
    from fastapi.testclient import TestClient

    app, state = app_with_mocked_memory
    state["memory"].playbook_retriever.retrieve.return_value = [
        PlaybookEntry(id="pb_fb_1", prefix="misc", section="OTHERS",
                      text="[someorg/somerepo] fallback rule"),
    ]
    client = TestClient(app)
    r = client.post("/query_memory_items", json={
        # No repo, no instance_id, no Pro repo name in text → infer_repo None.
        "task_description": "very generic query about software design",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["repo"] is None
    assert body["num_entries"] == 1
    assert body["items"][0]["section"] == "OTHERS"
    # Fallback path does not expose scores.
    assert body["items"][0]["score"] is None
