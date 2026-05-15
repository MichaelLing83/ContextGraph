"""Shared HTTP mock helpers for tests that patch `urlopen`.

`agent_rerank` and `query_memory_impl` both interact with HTTP endpoints
via `urllib.request.urlopen`. Their tests use a small fake-response
class as the patched return value. This module is the single source of
truth for that shape so the mocked status/read semantics stay
consistent.
"""
from __future__ import annotations

import json
from typing import Any


class FakeResp:
    """Minimal stand-in for an `http.client.HTTPResponse`.

    Supports the methods the production code actually calls: context
    manager protocol, ``read()``, and ``status`` / ``code``. ``body``
    may be a dict (JSON-encoded for you) or a raw ``str`` / ``bytes``
    if a test wants to feed malformed input.
    """

    def __init__(self, body: Any = None, *, status: int = 200):
        if body is None:
            payload = b""
        elif isinstance(body, (bytes, bytearray)):
            payload = bytes(body)
        elif isinstance(body, str):
            payload = body.encode("utf-8")
        else:
            payload = json.dumps(body).encode("utf-8")
        self._body = payload
        self.status = status
        # urllib's HTTPResponse also exposes the status as `.code`.
        self.code = status

    def __enter__(self):  # noqa: D401 — protocol implementation
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._body
