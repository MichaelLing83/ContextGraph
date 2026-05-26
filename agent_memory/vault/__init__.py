"""Obsidian / markdown vault ingestion for the context graph."""

from agent_memory.vault.models import RawVaultNote, VaultSection
from agent_memory.vault.parser import parse_vault_note, iter_vault_notes
from agent_memory.vault.segmenter import segment_note
from agent_memory.vault.writer import VaultWriter
from agent_memory.vault.obsidian_graph import ObsidianGraphBuilder
from agent_memory.vault.obsidian_index import ObsidianVaultIndex
from agent_memory.vault.wikilinks import extract_wikilinks, wikilink_for_path

__all__ = [
    "RawVaultNote",
    "VaultSection",
    "parse_vault_note",
    "iter_vault_notes",
    "segment_note",
    "VaultWriter",
    "ObsidianGraphBuilder",
    "ObsidianVaultIndex",
    "extract_wikilinks",
    "wikilink_for_path",
]
