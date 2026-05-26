"""Data models for vault (markdown note) ingestion."""

from dataclasses import dataclass
from pathlib import Path


@dataclass
class VaultSection:
    """One logical chunk of a note (usually a heading block)."""

    index: int
    heading: str
    body: str
    level: int = 2


@dataclass
class RawVaultNote:
    """Parsed markdown note before graph ingestion."""

    path: Path
    rel_path: str
    title: str
    body: str  # plain text (for summaries / embeddings)
    folder: str
    structured_body: str = ""  # frontmatter stripped; headings kept for segmentation

    @property
    def char_count(self) -> int:
        return len(self.body)
