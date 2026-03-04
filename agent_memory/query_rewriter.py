"""LLM-based query rewriting for better retrieval matching.

Rewrites raw domain-specific queries into abstract methodology keywords
that better match the rules stored in the knowledge graph. Both BM25 and
embedding channels benefit from the enriched query text.
"""

import hashlib
import logging
from collections import OrderedDict

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a query rewriter for a coding knowledge base.
The knowledge base contains abstract debugging rules and strategies
(e.g., "shared reference mutation", "copy/clone deep vs shallow",
"import path resolution").

Given a concrete coding problem description, extract 5-10 abstract
methodology keywords/phrases that would match relevant rules.

Rules:
- Output ONLY the keywords/phrases, one per line
- Be abstract: "SplitArrayField" → "field validation", "array field type coercion"
- Include error pattern categories: "import resolution", "type mismatch", "mutation vs copy"
- Include action patterns: "check test configuration", "verify dependency version"
- Do NOT repeat the original error verbatim
- Keep total output under 100 words"""


class QueryRewriter:
    """Rewrite queries using a fast LLM call to extract abstract methodology keywords.

    Follows the StrategyExtractor pattern: lazy OpenAI client, graceful fallback.
    Results are cached by MD5 hash of the input query.
    """

    def __init__(
        self,
        api_base: str,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        enabled: bool = True,
        cache_size: int = 256,
    ):
        self._api_base = api_base
        self._api_key = api_key
        self.model = model
        self.enabled = enabled
        self._cache_size = cache_size
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._client = None

    def _get_client(self):
        """Lazy-initialize the OpenAI client."""
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(base_url=self._api_base, api_key=self._api_key)
        return self._client

    def _cache_key(self, text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()

    def rewrite(self, query_text: str) -> str:
        """Rewrite a query by prepending abstract methodology keywords.

        Returns the original query unchanged if:
        - Rewriter is disabled
        - Query is empty
        - LLM call fails
        - LLM returns empty response
        """
        if not self.enabled or not query_text or not query_text.strip():
            return query_text

        # Truncate input to 1000 chars (used for both cache key and LLM input)
        truncated = query_text[:1000]
        key = self._cache_key(truncated)

        # Check cache
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]

        # Call LLM
        try:
            client = self._get_client()
            response = client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": truncated},
                ],
                max_tokens=200,
                temperature=0.2,
            )
            keywords = (response.choices[0].message.content or "").strip()
        except Exception as e:
            logger.debug("Query rewrite LLM call failed: %s", e)
            return query_text

        if not keywords:
            return query_text

        # Prepend keywords to original query
        rewritten = keywords + " " + query_text

        # Store in cache with LRU eviction
        self._cache[key] = rewritten
        if len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)

        return rewritten
