# Obsidian Vault Knowledge Graph

Build and query a **context graph stored as Markdown** in an Obsidian vault: nodes are notes, edges are `[[wikilinks]]`, categories use `#tags`. No Neo4j required for this path.

This complements the main SWE-agent pipeline (Neo4j + trajectories). Use it for personal notes, books, research vaults, or any markdown corpus.

## Concepts

| SWE / Neo4j pipeline | Obsidian pipeline |
|---------------------|-------------------|
| Trajectory | Source note (`.md` in your vault) |
| Fragment | `Fragments/*.md` in the **graph vault** |
| Edge | `[[wikilink]]` between fragment notes |
| ErrorPattern / tags | `#cg/fragment`, `#cg/source/...`, etc. |
| Neo4j retrieval | `query_obsidian_vault.py` (tags + links + text) |

Recommended layout: **two vaults**.

```
~/Vaults/
├── MyNotes/              # source — your normal Obsidian library (read-only for the tool)
└── MyNotesGraph/         # graph — generated knowledge graph only
    ├── MOC.md
    ├── Fragments/
    ├── Sources/          # index cards pointing back to source notes
    └── .graph_registry.json
```

Open both libraries in Obsidian: browse the graph in **Graph view**, filter with **tags**, follow **backlinks**.

## Setup

```bash
uv venv .venv --python 3.12
uv pip install -e '.[dev]'
```

No Docker or Neo4j needed for the Obsidian workflow.

## 1. Build the graph

```bash
uv run python scripts/build_obsidian_graph.py \
  --source-vault ~/Vaults/MyNotes \
  --graph-vault ~/Vaults/MyNotesGraph
```

Options:

| Flag | Description |
|------|-------------|
| `--link-mode stub` | (default) One `Sources/*.md` card per source note; fragments link with `[[Sources/...]]` |
| `--link-mode symlink` | `Sources/` → symlink to source vault; links like `[[Sources/Projects/Note]]` |
| `--link-mode frontmatter` | Paths only in YAML, no source wikilinks in the graph |
| `--max-notes N` | Ingest only the first N files (testing) |
| `--vault PATH` | Legacy: single vault, writes under `ContextGraph/` subfolder |

After build, check `MyNotesGraph/MOC.md` and `build_report.json`.

## 2. Search

```bash
uv run python scripts/query_obsidian_vault.py \
  --vault ~/Vaults/MyNotesGraph \
  -q "cache invalidation" \
  --tag cg/fragment \
  --hops 1
```

| Flag | Description |
|------|-------------|
| `-q` / `--query` | Keywords (token match in title + body) |
| `--tag` | Require tag(s), e.g. `cg/fragment` (repeatable) |
| `--hops 1` | Expand along wikilinks one step (graph neighbors) |
| `--limit` | Max hits (default 15) |
| `--graph-only` | Only notes tagged `cg/*` |
| `--list-tags` | List all `cg/*` tags in the vault |
| `--json` | Machine-readable output |

**Score reasons** (in default list mode): `tag_filter`, `title`, `body`, `link_expand`.

## 3. Knowledge summary

Merge top hits into one document (content only — no paths or build stats):

```bash
uv run python scripts/query_obsidian_vault.py \
  --vault ~/Vaults/MyNotesGraph \
  -q "utmärkt" \
  --tag cg/fragment \
  --hops 1 \
  --limit 10 \
  --summary \
  --summary-out ./summary.md
```

| Flag | Description |
|------|-------------|
| `--summary-max-chars` | Total length cap (default 6000) |
| `--limit` | How many fragments to include (search stage) |
| `--summary-llm` | Rewrite into cohesive prose (needs `OPENAI_API_BASE` + `OPENAI_API_KEY` in `.env`) |
| `--summary-meta` | Include title / stats (off by default) |

## Fragment note format

Each generated fragment looks like:

```markdown
---
cg_type: fragment
source_vault: "/path/to/source"
source_rel_path: "Books/Ch1.md"
tags:
  - cg/fragment
  - cg/node
---

## Section heading

Body text…

## Graph
…

## Related
- [[Fragments/sibling-section]]
```

Use Obsidian search: `tag:#cg/fragment utmärkt`.

## Python API

```python
from pathlib import Path
from agent_memory.vault import (
    ObsidianGraphBuilder,
    ObsidianVaultIndex,
    iter_vault_notes,
)
from agent_memory.vault.summarize import build_knowledge_summary

# Build
builder = ObsidianGraphBuilder(
    Path("~/Vaults/MyNotesGraph").expanduser(),
    source_vault=Path("~/Vaults/MyNotes").expanduser(),
)
for note in iter_vault_notes(builder.source_vault):
    builder.ingest_note(note)
builder.write_moc()
builder.save_registry()

# Search + summarize
index = ObsidianVaultIndex(builder.graph_vault)
index.build()
hits = index.search("topic", tags=["cg/fragment"], hops=1, limit=10)
text = build_knowledge_summary("topic", hits, index)
```

## Neo4j path (optional)

For SWE-agent trajectories, the original pipeline remains:

- `scripts/build_context_graph.py` → Neo4j
- `scripts/build_vault_graph.py` → ingest markdown into Neo4j as `task_type=note`

See the main [README](../README.md) and `CLAUDE.md`.

## Tests

```bash
uv run pytest tests/test_vault_parser.py tests/test_obsidian_graph.py tests/test_vault_summarize.py -q
```

## Troubleshooting

**`File name too long` during search** — Fixed by ignoring broken `[[...]]` spans (newlines / huge targets). Rebuild is not required; update and re-run query.

**Too many fragments from one book** — Use `--max-notes` while testing, or narrow `--glob` on build.

**Summary truncated** — Increase `--summary-max-chars` and/or `--limit`.

**Cross-vault links in Obsidian** — With `stub` mode, open the source path from the `Sources/` card or frontmatter `source_rel_path`.
