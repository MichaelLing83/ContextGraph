"""Tests for passage (virtual fragment) graph query."""

from pathlib import Path

from agent_memory.vault.obsidian_index import ObsidianVaultIndex
from agent_memory.vault.passage_query import PassageQueryConfig, search_passage


def _write_fragment(
    vault: Path,
    rel: str,
    *,
    title: str,
    body: str,
    summary: str = "",
    outlinks: list[str] | None = None,
) -> None:
    path = vault / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    summary_line = f'cg_llm_summary: "{summary}"\n' if summary else ""
    text = (
        f"---\ncg_type: fragment\ntags:\n  - cg/fragment\n"
        f"{summary_line}---\n\n# {title}\n\n{body}\n"
    )
    if outlinks:
        text += "\n## Related\n" + "\n".join(f"- [[{t}]]" for t in outlinks) + "\n"
    path.write_text(text, encoding="utf-8")


def test_passage_query_semantic_and_lexical(tmp_path: Path):
    vault = tmp_path / "graph"
    vault.mkdir()
    _write_fragment(
        vault,
        "Fragments/cache.md",
        title="Cache",
        body="Invalidate cache when data changes.",
        summary="distributed cache invalidation staleness",
    )
    _write_fragment(
        vault,
        "Fragments/migrate.md",
        title="Migrate",
        body="Schema migration steps for databases.",
        summary="database schema migration versioning",
    )
    _write_fragment(
        vault,
        "Fragments/pip.md",
        title="Pip",
        body="Legacy pip install workflow.",
        summary="python package install pip",
    )

    index = ObsidianVaultIndex(vault)
    index.build()

    hits = search_passage(
        index,
        "When the distributed cache becomes stale, invalidate entries.",
        query_summary="distributed cache invalidation staleness",
        config=PassageQueryConfig(seed_topk=3, limit=5, hops=0),
    )
    assert hits
    assert hits[0].rel_path.endswith("cache.md")
    assert any("semantic:" in r for r in hits[0].reasons)


def test_passage_query_lexical_without_summary(tmp_path: Path):
    vault = tmp_path / "graph"
    vault.mkdir()
    _write_fragment(
        vault,
        "Fragments/alpha.md",
        title="Alpha",
        body="Unique keyword xyzzy appears here.",
    )
    _write_fragment(
        vault,
        "Fragments/beta.md",
        title="Beta",
        body="Unrelated content.",
    )

    index = ObsidianVaultIndex(vault)
    index.build()
    hits = search_passage(
        index,
        "Find the xyzzy keyword in documentation.",
        config=PassageQueryConfig(hops=0, limit=5),
    )
    assert hits[0].rel_path.endswith("alpha.md")


def test_passage_query_hop_expansion(tmp_path: Path):
    vault = tmp_path / "graph"
    vault.mkdir()
    _write_fragment(
        vault,
        "Fragments/neighbor.md",
        title="Neighbor",
        body="linked neighbor note",
        summary="neighbor",
    )
    _write_fragment(
        vault,
        "Fragments/seed.md",
        title="Seed",
        body="cache topic seed",
        summary="cache seed",
        outlinks=["Fragments/neighbor.md"],
    )

    index = ObsidianVaultIndex(vault)
    index.build()
    hits = search_passage(
        index,
        "cache invalidation",
        query_summary="cache",
        config=PassageQueryConfig(seed_topk=1, hops=1, limit=5),
    )
    paths = {h.rel_path for h in hits}
    assert any(p.endswith("seed.md") for p in paths)
    assert any(p.endswith("neighbor.md") for p in paths)
