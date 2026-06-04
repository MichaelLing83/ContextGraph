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
| Neo4j retrieval | `query_obsidian_graph.py` (tags + links + text) |

Recommended layout: **two vaults**.

```
~/Vaults/
├── MyNotes/              # source — your normal Obsidian library (read-only for the tool)
└── MyNotesGraph/         # graph — generated knowledge graph only
    ├── MOC.md
    ├── Fragments/        # graph nodes (default build)
    └── .graph_registry.json
```

By default the graph vault contains **fragment notes only** — no `Sources/` index cards and no source wikilinks in Graph view. Each fragment records the original path in YAML frontmatter (`source_vault`, `source_rel_path`, `source_heading`) and carries a `#cg/source/...` tag for grouping.

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

Build runs in **refresh mode**: if the target graph vault already exists, the script clears previously generated `Fragments/`, `Sources/`, `MOC.md`, `.graph_registry.json`, and `build_report.json` before rebuilding.  
LLM summary caches under `.llm_summary_cache/` are preserved.

Options:

| Flag | Description |
|------|-------------|
| `--link-mode frontmatter` | **(default)** Source path in YAML only — **no `Sources/` notes, no source wikilinks**; Graph view shows fragment nodes only |
| `--link-mode stub` | One `Sources/*.md` card per source note; fragments link with `[[Sources/...]]` (adds source nodes to Graph view) |
| `--link-mode symlink` | `Sources/` → symlink to source vault; links like `[[Sources/Projects/Note]]` |
| `--max-notes N` | Ingest only the first N files (testing) |
| `--chunk-mode adaptive` | **(default)** Greedy merge by `--fragment-chars`; short notes stay whole |
| `--chunk-mode heading` | One fragment per markdown heading |
| `--chunk-mode chapter` | One fragment per source note |
| `--fragment-chars 500` | Target size for `adaptive` mode (default 500) |
| `--fragment-max-chars 3000` | Cap per fragment; longer pieces split on `\\n\\n` paragraphs (default 3000; `0`=off) |
| `--related-mode all` | Sibling links in each fragment’s `## Related` section (see below) |
| `--related-topk 2` | With `topk` mode: max links per fragment (default **2**) |
| `--llm-summary` | Generate `cg_llm_summary` in fragment frontmatter via LLM (optional) |
| `--llm-summary-model` | Model for `--llm-summary` (default: `claude-sonnet-4-20250514`; recommended local default: `llama3:latest` with Ollama) |
| `--llm-proxy` | `auto` (env/system proxy), `none` (direct — use for localhost on Windows), or proxy URL |
| `--llm-http-version` | `1.1` (default) or `2` for LLM API HTTP version |

### LLM fragment summaries (`--llm-summary`)

When enabled, each fragment gets a **`cg_llm_summary`** field in YAML frontmatter. The **original chunk stays in the note body** — query output and `--full-body` are unchanged.
After summaries are generated, build adds a `## Semantic` section in each fragment with top related wikilinks computed from `cg_llm_summary` similarity.

Summaries are **cached by SHA-256 of the fragment body** under `.llm_summary_cache/` in the graph vault.  
Each model writes to a separate file: `.llm_summary_cache/<model>.json` (sanitized filename, e.g. `deepseek-r1-1.5b.json`).  
Each successful LLM summary is **written to disk immediately** (incremental autosave); rebuild skips the LLM when the source chunk text is identical (even if the fragment file was recreated). Cache also keys on prompt version.

```bash
uv run python scripts/build_obsidian_graph.py \
  --source-vault ~/Vaults/MyNotes \
  --graph-vault ~/Vaults/MyNotesGraph \
  --llm-summary
```

Requires `LITELLM_MASTER_KEY` or `OPENAI_API_KEY` (LiteLLM proxy at `http://localhost:4000/v1` by default). `build_report.json` includes `llm_summary_stats` (`cache_hits`, `llm_calls`, …).
When `--llm-summary` is enabled, the build shows a terminal progress bar (`tqdm`) with `processed/total fragments`, cache hits, LLM calls, and failures. Install with `uv pip install tqdm` if missing.

Use a different model (and therefore a different cache file):

```bash
uv run python scripts/build_obsidian_graph.py \
  --source-vault ~/Vaults/MyNotes \
  --graph-vault ~/Vaults/MyNotesGraph \
  --llm-summary \
  --llm-summary-model claude-sonnet-4-20250514
```

Ollama local model example (recommended default for local summarization; `--llm-proxy none` bypasses system proxy on Windows):

```bash
uv run python scripts/build_obsidian_graph.py \
  --source-vault ~/Vaults/MyNotes \
  --graph-vault ~/Vaults/MyNotesGraph \
  --llm-summary \
  --llm-summary-model llama3:latest \
  --llm-api-base http://localhost:11434/v1 \
  --llm-api-key ollama \
  --llm-proxy none \
  --llm-http-version 1.1
```

Note: build and passage query always send `reasoning_effort: none` — only the final summary is needed, not chain-of-thought. This avoids empty `message.content` on thinking models (e.g. `deepseek-r1:1.5b`, `qwen3.5:4b` on Ollama). If summaries still fail, try `llama3:latest` or `gemma3:4b`.

Use `--llm-proxy auto` (default) when calling LiteLLM or a remote API through `HTTP_PROXY`. Use `--llm-http-version 2` only if your server requires HTTP/2 (`uv pip install 'httpx[http2]'`).

### Sibling fragment links (`--related-mode`)

Fragments from the same source note can wikilink to each other under `## Related`:

| Mode | Behavior |
|------|----------|
| `all` | (default) Link to every other fragment from that note |
| `none` | No sibling links (only link to source via `## Graph`) |
| `adjacent` | Link only to the previous and next fragment in reading order |
| `topk` | Link to at most **K** siblings; pick those with smallest section index distance first (ties: lower index). Default **K=2** (`--related-topk`) |

Example with five sections `A B C D E` and `--related-mode topk --related-topk 2`:

- `A` → `B`, `C`
- `C` → `B`, `D`
- `E` → `D`, `C`

Use `adjacent` or `topk` to keep Obsidian’s graph view sparse on long docs.

### Adaptive chunking (default)

`--chunk-mode adaptive` is the default. Uses **plain text length** after stripping markup:

1. If the whole note ≤ `fragment-chars` → **one** fragment (entire chapter).
2. Otherwise split into small units (per `##` block, or per paragraph if no headings).
3. **Greedily merge** adjacent units while the combined size ≤ `fragment-chars`.
4. A single unit larger than the limit is kept **whole** (no truncation).

All source text is represented across fragments; nothing is dropped from the graph build.

After adaptive/heading merge, any fragment still above `--fragment-max-chars` is split again by blank-line paragraphs (e.g. `Title (1/3)`, `Title (2/3)`). A single paragraph above the cap is kept whole.

### Markup in fragments

Fragment bodies keep **markdown as in the source** (fenced code blocks, `[links](url)`, etc.). This is required for technical docs (TOML/YAML examples, API links).

```bash
uv run python scripts/build_obsidian_graph.py \
  --source-vault ~/Vaults/MyNotes \
  --graph-vault ~/Vaults/BooksGraph \
  --glob "Books/**/*.md" \
  --fragment-chars 500
```

After build, check `MyNotesGraph/MOC.md` and `build_report.json` (includes `graph_stats`: fragment count, body length min/mean/median/max, wikilink edge counts). The CLI also prints a short summary to the terminal.

### Release (tarball + wheel + GitHub)

Package only the Obsidian pipeline sources into a versioned release (auto-bumps patch in `releases/obsidian/VERSION`, e.g. `0.1.0` → `0.1.1`) and publish to GitHub Releases:

```bash
./scripts/release_obsidian_graph.sh
./scripts/release_obsidian_graph.sh --dry-run
./scripts/release_obsidian_graph.sh --no-bump --no-publish
./scripts/release_obsidian_graph.sh --draft
```

Low-level build only (no `gh`):

```bash
uv run python scripts/release_obsidian_graph.py
uv run python scripts/release_obsidian_graph.py --dry-run
uv run python scripts/release_obsidian_graph.py --no-bump
```

Output:

- `dist/obsidian-context-graph-<version>.tar.gz` — full source bundle (scripts, tests, docs)
- `dist/obsidian_context_graph-<version>-py3-none-any.whl` — installable Python wheel
- `dist/obsidian-context-graph-<version>.tar.manifest.json` — checksums
- GitHub Release tag: `obsidian-v<version>` (e.g. `obsidian-v0.1.1`) — annotated git tag on the VERSION commit, pushed to origin

Install from wheel:

```bash
uv pip install obsidian_context_graph-<version>-py3-none-any.whl

# Recommended (works on Windows/macOS/Linux when using uv venv)
uv run build-obsidian-graph --help
uv run query-obsidian-graph --help

# Cross-platform fallback (no PATH entry required)
python -m agent_memory.vault build-graph --help
python -m agent_memory.vault query-graph --help
```

On Windows, console scripts install as `.venv\\Scripts\\build-obsidian-graph.exe`. Activate the venv first, or prefer `uv run` / `python -m` above.

## 2. Search

```bash
uv run python scripts/query_obsidian_graph.py \
  --vault ~/Vaults/MyNotesGraph \
  -q "cache invalidation" \
  --tag cg/fragment \
  --hops 1
```

| Flag | Description |
|------|-------------|
| `-q` / `--query` | Keywords (token match in title + body) |
| `--exact-phrase` | Require a contiguous case-insensitive phrase in title or body (e.g. `"uv run"`) |
| `--tag` | Require tag(s), e.g. `cg/fragment` (repeatable) |
| `--hops 1` | Expand along wikilinks one step (graph neighbors) |
| `--limit` | Max hits (default 15) |
| `--graph-only` | Only notes tagged `cg/*` |
| `--list-tags` | List all `cg/*` tags in the vault |
| `--json` | Machine-readable output |
| `--full-body` | With `--summary` or list/json mode: output full fragment body (no excerpt truncation) |

**Score reasons** (in default list mode): `tag_filter`, `title`, `body`, `exact_phrase:title`, `exact_phrase:body`, `link_expand`.

### Passage query (virtual fragment)

Treat a long passage as a **virtual fragment** (nothing is written to the vault):

1. Optionally summarize the passage with the same LLM stack as build (`--llm-summary`).
2. Score each `cg/fragment` by **semantic** similarity on `cg_llm_summary` (Jaccard) and **lexical** overlap on title/body.
3. Take top seeds, expand one hop along `## Related` / `## Semantic` wikilinks (default `--hops 1` in passage mode).

```bash
uv run python scripts/query_obsidian_graph.py \
  --vault ~/Vaults/MyNotesGraph \
  --query-passage "How do I migrate from pip to uv in a monorepo?" \
  --tag cg/fragment \
  --hops 1 \
  --llm-summary \
  --llm-api-base http://localhost:11434/v1 \
  --llm-api-key ollama \
  --llm-summary-model llama3:latest \
  --llm-proxy none \
  --llm-http-version 1.1
```

Or read from a file:

```bash
uv run python scripts/query_obsidian_graph.py \
  --vault ~/Vaults/MyNotesGraph \
  --query-passage-file ./question.md \
  --tag cg/fragment --hops 1
```

| Flag | Description |
|------|-------------|
| `--query-passage` | Passage text (virtual fragment body) |
| `--query-passage-file` | Read passage from file |
| `--seed-topk` | Top semantic/lexical seeds before expansion (default 8) |
| `--semantic-min` | Min summary Jaccard to record `semantic:*` reason (default 0.15) |
| `--semantic-weight` / `--lexical-weight` | Fusion weights when both query and fragment have summaries (0.6 / 0.4) |
| `--llm-summary` | Summarize passage before semantic match (cache: `.llm_summary_cache/<model>.json`) |
| `--llm-proxy` | Same as build: `auto`, `none`, or proxy URL |
| `--llm-http-version` | `1.1` (default) or `2` |

**Passage score reasons**: `semantic:0.XX`, `lexical:0.XX`, `passage_seed`, `link_expand`, `link_neighbor`.

Semantic matching works best when fragments were built with `--llm-summary`; without summaries, passage mode falls back to lexical overlap only.

Exact phrase example:

```bash
uv run python scripts/query_obsidian_graph.py \
  --vault ~/Vaults/MyNotesGraph \
  --exact-phrase "uv run" \
  --hops 1 \
  --limit 10
```

## 3. Knowledge summary

Merge top hits into one document (content only — no paths or build stats):

```bash
uv run python scripts/query_obsidian_graph.py \
  --vault ~/Vaults/MyNotesGraph \
  -q "utmärkt" \
  --tag cg/fragment \
  --hops 1 \
  --limit 10 \
  --summary \
  --summary-out ./summary.md
```

Full fragment body (no excerpt truncation):

```bash
uv run python scripts/query_obsidian_graph.py \
  --vault ~/Vaults/MyNotesGraph \
  --exact-phrase "uv run" \
  --tag cg/fragment \
  --limit 1 \
  --summary \
  --full-body
```

| Flag | Description |
|------|-------------|
| `--summary-max-chars` | Total length cap (default 6000; disabled with `--full-body`) |
| `--full-body` | Emit full cleaned fragment bodies instead of 500-char excerpts |
| `--limit` | How many fragments to include (search stage) |
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

# Passage query (virtual fragment)
from agent_memory.vault.passage_query import PassageQueryConfig, search_passage

hits = search_passage(
    index,
    "long question text…",
    query_summary="optional LLM summary",
    config=PassageQueryConfig(hops=1, limit=10),
)
text = build_knowledge_summary("topic", hits, index)
```

## Neo4j path (optional)

For SWE-agent trajectories, the original pipeline remains:

- `scripts/build_context_graph.py` → Neo4j
- `scripts/build_vault_graph.py` → ingest markdown into Neo4j as `task_type=note`

See the main [README](../README.md) and `CLAUDE.md`.

## Tests

```bash
uv run pytest tests/test_vault_parser.py tests/test_obsidian_graph.py tests/test_build_obsidian_graph.py tests/test_vault_summarize.py tests/test_fragment_summary.py tests/test_passage_query.py -q
```

## Troubleshooting

**`File name too long` during search** — Fixed by ignoring broken `[[...]]` spans (newlines / huge targets). Rebuild is not required; update and re-run query.

**Too many fragments from one book** — Use `--max-notes` while testing, or narrow `--glob` on build.

**Summary truncated** — Increase `--summary-max-chars` and/or `--limit`.

**Cross-vault links in Obsidian** — Default (`frontmatter`): open the source note from frontmatter `source_vault` + `source_rel_path` (or the path line under `## Graph`). With `--link-mode stub`, you can also follow `[[Sources/...]]` index cards.
