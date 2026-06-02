"""LLM summaries for vault fragments with content-hash caching."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
PROMPT_VERSION = "1"
CACHE_DIRNAME = ".llm_summary_cache"
DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_BODY_CHARS = 4000

_SUMMARY_PROMPT = """Summarize this vault note fragment in 2-4 concise sentences.
Use the same language as the source text. Focus on key concepts and facts only.
Do not use markdown headings or bullet lists.

Heading: {heading}

Content:
{body}
"""


def fragment_body_hash(body: str) -> str:
    """Stable SHA-256 of fragment source body (the markdown chunk, not the whole note file)."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _model_slug(model: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", model).strip("-").lower()
    return slug or "default"


def cache_path_for_model(cache_root: Path, model: str) -> Path:
    """Model-scoped cache file path under ``.llm_summary_cache/``."""
    return cache_root / CACHE_DIRNAME / f"{_model_slug(model)}.json"


@dataclass
class FragmentSummaryStats:
    cache_hits: int = 0
    llm_calls: int = 0
    skipped_empty: int = 0
    failures: int = 0

    def to_dict(self) -> dict:
        return {
            "cache_hits": self.cache_hits,
            "llm_calls": self.llm_calls,
            "skipped_empty": self.skipped_empty,
            "failures": self.failures,
        }


@dataclass
class FragmentSummaryCache:
    """Persistent cache: body hash → LLM summary (shared across fragments with identical text)."""

    cache_path: Path
    prompt_version: str = PROMPT_VERSION
    _entries: dict[str, dict] = field(default_factory=dict, init=False, repr=False)

    def load(self) -> None:
        if not self.cache_path.is_file():
            self._entries = {}
            return
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Could not load LLM summary cache %s: %s", self.cache_path, e)
            self._entries = {}
            return
        if data.get("version") != CACHE_VERSION:
            self._entries = {}
            return
        self._entries = dict(data.get("entries") or {})

    def save(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": CACHE_VERSION,
            "prompt_version": self.prompt_version,
            "entries": self._entries,
        }
        self.cache_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get(self, body: str, *, model: str) -> Optional[str]:
        entry = self._entries.get(fragment_body_hash(body))
        if not entry:
            return None
        if entry.get("model") != model:
            return None
        if entry.get("prompt_version") != self.prompt_version:
            return None
        summary = entry.get("summary")
        return summary if isinstance(summary, str) and summary.strip() else None

    def put(self, body: str, summary: str, *, model: str) -> None:
        h = fragment_body_hash(body)
        self._entries[h] = {
            "summary": summary,
            "model": model,
            "prompt_version": self.prompt_version,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


class FragmentSummarizer:
    """Summarize fragment bodies via LLM, reusing cache when body text is unchanged."""

    def __init__(
        self,
        *,
        api_base: str,
        api_key: str,
        model: str = DEFAULT_MODEL,
        cache: FragmentSummaryCache,
    ):
        self.api_base = api_base.rstrip("/")
        if not self.api_base.endswith("/v1"):
            self.api_base = self.api_base + "/v1"
        self.api_key = api_key
        self.model = model
        self.cache = cache
        self.stats = FragmentSummaryStats()
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=self.api_base, api_key=self.api_key)
        return self._client

    def summarize(self, heading: str, body: str) -> Optional[str]:
        text = body.strip()
        if not text:
            self.stats.skipped_empty += 1
            return None

        cached = self.cache.get(text, model=self.model)
        if cached is not None:
            self.stats.cache_hits += 1
            return cached

        prompt_body = text
        if len(prompt_body) > MAX_BODY_CHARS:
            prompt_body = prompt_body[:MAX_BODY_CHARS] + "…"

        prompt = _SUMMARY_PROMPT.format(
            heading=(heading or "Untitled").strip(),
            body=prompt_body,
        )

        try:
            response = self._get_client().chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300,
                temperature=0.2,
            )
            raw = (response.choices[0].message.content or "").strip()
        except Exception as e:
            self.stats.failures += 1
            logger.warning("LLM summary failed for %r: %s", heading, e)
            return None

        if not raw:
            self.stats.failures += 1
            return None

        summary = " ".join(raw.split())
        self.stats.llm_calls += 1
        self.cache.put(text, summary, model=self.model)
        return summary
