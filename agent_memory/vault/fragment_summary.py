"""LLM summaries for vault fragments with content-hash caching."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Literal, Optional

HttpVersion = Literal["1.1", "2"]
LlmProxyMode = Literal["auto", "none"]

logger = logging.getLogger(__name__)

CACHE_VERSION = 1
PROMPT_VERSION = "1"
CACHE_DIRNAME = ".llm_summary_cache"
DEFAULT_MODEL = "claude-sonnet-4-20250514"
MAX_BODY_CHARS = 4000

DEFAULT_SUMMARY_PROMPT = """Summarize this vault note fragment in 2-4 concise sentences.
Use the same language as the source text. Focus on key concepts and facts only.
Do not use markdown headings or bullet lists.

Heading: {heading}

Content:
{body}
"""

_REQUIRED_PROMPT_FIELDS = ("heading", "body")


def validate_summary_prompt_template(template: str) -> None:
    """Ensure the template can be filled with fragment heading and body."""
    missing = [f for f in _REQUIRED_PROMPT_FIELDS if f"{{{f}}}" not in template]
    if missing:
        raise ValueError(
            "LLM summary prompt must include placeholders "
            + ", ".join(f"{{{f}}}" for f in missing)
            + f"; missing: {', '.join(missing)}"
        )
    try:
        template.format(heading="x", body="y")
    except KeyError as e:
        raise ValueError(
            f"LLM summary prompt has unknown placeholder {e}; "
            f"only {_REQUIRED_PROMPT_FIELDS} are supported"
        ) from e


def resolve_summary_prompt(
    *,
    prompt: str = "",
    prompt_file: Path | None = None,
) -> str:
    """Return prompt template text (default, inline, and/or file)."""
    parts: list[str] = []
    if prompt_file is not None:
        parts.append(prompt_file.expanduser().read_text(encoding="utf-8"))
    if prompt.strip():
        parts.append(prompt.strip())
    if not parts:
        return DEFAULT_SUMMARY_PROMPT
    template = "\n\n".join(parts).strip()
    validate_summary_prompt_template(template)
    return template


def prompt_version_for_template(template: str) -> str:
    """Cache key for prompt template; default template keeps legacy version ``1``."""
    if template == DEFAULT_SUMMARY_PROMPT:
        return PROMPT_VERSION
    digest = hashlib.sha256(template.encode("utf-8")).hexdigest()[:12]
    return f"custom-{digest}"


def render_summary_prompt(template: str, *, heading: str, body: str) -> str:
    return template.format(
        heading=(heading or "Untitled").strip(),
        body=body,
    )


def add_llm_summary_prompt_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``--llm-summary-prompt`` and ``--llm-summary-prompt-file``."""
    parser.add_argument(
        "--llm-summary-prompt",
        default="",
        help=(
            "Custom prompt template for --llm-summary; must include {heading} and {body}. "
            "Combined with --llm-summary-prompt-file when both are set."
        ),
    )
    parser.add_argument(
        "--llm-summary-prompt-file",
        type=Path,
        help="Read --llm-summary prompt template from a file ({heading} and {body} required)",
    )


def add_llm_http_arguments(parser: argparse.ArgumentParser) -> None:
    """Register ``--llm-proxy`` and ``--llm-http-version`` on build/query CLIs."""
    parser.add_argument(
        "--llm-proxy",
        default="auto",
        metavar="MODE|URL",
        help=(
            "HTTP proxy for --llm-summary: auto (env/system proxy, default), "
            "none (direct, for localhost), or URL (e.g. http://127.0.0.1:7890)"
        ),
    )
    parser.add_argument(
        "--llm-http-version",
        default="1.1",
        choices=("1.1", "2"),
        help="HTTP version for LLM API requests (default: 1.1; use 2 only if server supports it)",
    )


def fragment_body_hash(body: str) -> str:
    """Stable SHA-256 of fragment source body (the markdown chunk, not the whole note file)."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _model_slug(model: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", model).strip("-").lower()
    return slug or "default"


def cache_path_for_model(cache_root: Path, model: str) -> Path:
    """Model-scoped cache file path under ``.llm_summary_cache/``."""
    return cache_root / CACHE_DIRNAME / f"{_model_slug(model)}.json"


def normalize_llm_proxy(value: str) -> str:
    """Return ``auto``, ``none``, or an explicit proxy URL."""
    raw = value.strip()
    if not raw:
        return "auto"
    lower = raw.lower()
    if lower in ("auto", "env", "default"):
        return "auto"
    if lower in ("none", "off", "false", "direct", "no"):
        return "none"
    if raw.startswith(("http://", "https://", "socks5://", "socks4://")):
        return raw
    raise ValueError(
        f"Invalid --llm-proxy {value!r}: use auto, none, or a proxy URL "
        "(e.g. http://127.0.0.1:7890)"
    )


def normalize_llm_http_version(value: str) -> HttpVersion:
    v = value.strip().lower()
    if v in ("1.1", "1", "http1", "http/1.1", "http1.1"):
        return "1.1"
    if v in ("2", "http2", "http/2", "h2"):
        return "2"
    raise ValueError(f"Invalid --llm-http-version {value!r}: use 1.1 or 2")


def openai_http_client(
    *,
    use_proxy: str = "auto",
    http_version: HttpVersion = "1.1",
    timeout: float = 120.0,
):
    """
    httpx client for OpenAI SDK LLM calls.

    ``use_proxy``:
      - ``auto``: honor HTTP_PROXY / HTTPS_PROXY (Windows system proxy)
      - ``none``: bypass env proxies (recommended for localhost Ollama/vLLM)
      - ``http://...``: explicit proxy URL
    """
    import httpx

    proxy_mode = normalize_llm_proxy(use_proxy)
    http2 = http_version == "2"
    if http2:
        try:
            import h2  # noqa: F401
        except ImportError:
            logger.warning(
                "httpx HTTP/2 requested but h2 is not installed; using HTTP/1.1 "
                "(uv pip install 'httpx[http2]')"
            )
            http2 = False

    if proxy_mode == "none":
        return httpx.Client(trust_env=False, http2=http2, timeout=timeout)
    if proxy_mode == "auto":
        return httpx.Client(trust_env=True, http2=http2, timeout=timeout)
    return httpx.Client(
        proxy=proxy_mode,
        trust_env=False,
        http2=http2,
        timeout=timeout,
    )


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
    autosave: bool = True
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
        text = json.dumps(payload, indent=2, ensure_ascii=False)
        tmp = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.cache_path)

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
        if self.autosave:
            self.save()


class FragmentSummarizer:
    """Summarize fragment bodies via LLM, reusing cache when body text is unchanged."""

    def __init__(
        self,
        *,
        api_base: str,
        api_key: str,
        model: str = DEFAULT_MODEL,
        cache: FragmentSummaryCache,
        on_progress: Optional[Callable[[FragmentSummaryStats], None]] = None,
        disable_reasoning: bool = True,
        llm_proxy: str = "auto",
        llm_http_version: HttpVersion = "1.1",
        summary_prompt_template: str = DEFAULT_SUMMARY_PROMPT,
    ):
        self.api_base = api_base.rstrip("/")
        if not self.api_base.endswith("/v1"):
            self.api_base = self.api_base + "/v1"
        self.api_key = api_key
        self.model = model
        self.cache = cache
        self.stats = FragmentSummaryStats()
        self.on_progress = on_progress
        self._client = None
        self._http_client = None
        self._warned_empty_content = False
        self.disable_reasoning = disable_reasoning
        self.llm_proxy = normalize_llm_proxy(llm_proxy)
        self.llm_http_version = normalize_llm_http_version(llm_http_version)
        self.summary_prompt_template = summary_prompt_template

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._http_client = openai_http_client(
                use_proxy=self.llm_proxy,
                http_version=self.llm_http_version,
            )
            self._client = OpenAI(
                base_url=self.api_base,
                api_key=self.api_key,
                http_client=self._http_client,
            )
        return self._client

    def summarize(self, heading: str, body: str) -> Optional[str]:
        try:
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

            prompt = render_summary_prompt(
                self.summary_prompt_template,
                heading=heading,
                body=prompt_body,
            )

            try:
                create_kwargs: dict = {
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 300,
                    "temperature": 0.2,
                }
                if self.disable_reasoning:
                    # Build/query only need the final summary, not chain-of-thought.
                    create_kwargs["extra_body"] = {"reasoning_effort": "none"}
                response = self._get_client().chat.completions.create(**create_kwargs)
                msg = response.choices[0].message
                raw = (msg.content or "").strip()
            except Exception as e:
                self.stats.failures += 1
                logger.warning("LLM summary failed for %r: %s", heading, e)
                return None

            if not raw:
                if not self._warned_empty_content:
                    reasoning = (
                        getattr(msg, "reasoning", None)
                        or getattr(msg, "reasoning_content", None)
                        or ""
                    )
                    if reasoning:
                        logger.warning(
                            "Model %r returned empty message.content but non-empty reasoning "
                            "despite reasoning_effort=none. Try another model (e.g. llama3:latest).",
                            self.model,
                        )
                    else:
                        logger.warning(
                            "Model %r returned empty message.content for summaries. "
                            "Try another local model (e.g. llama3:latest).",
                            self.model,
                        )
                    self._warned_empty_content = True
                self.stats.failures += 1
                return None

            summary = " ".join(raw.split())
            self.stats.llm_calls += 1
            self.cache.put(text, summary, model=self.model)
            return summary
        finally:
            if self.on_progress is not None:
                self.on_progress(self.stats)
