"""Shared utility functions for agent_memory."""


def escape_lucene(text: str) -> str:
    """Escape Lucene special characters for BM25/full-text queries."""
    special = r'+-&|!(){}[]^"~*?:\/'
    escaped = []
    for ch in text:
        if ch in special:
            escaped.append("\\")
        escaped.append(ch)
    return "".join(escaped)
