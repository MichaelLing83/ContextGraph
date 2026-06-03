"""Package Obsidian vault graph sources into a versioned release archive.

Increments the patch/revision in ``releases/obsidian/VERSION`` (e.g. 0.1.0 → 0.1.1),
copies only Obsidian-related library code, CLI scripts, docs, and tests, then writes
``dist/obsidian-context-graph-<version>.tar.gz``, a Python wheel, and a manifest.

Usage:
    uv run python scripts/release_obsidian_graph.py
    uv run python scripts/release_obsidian_graph.py --dry-run
    uv run python scripts/release_obsidian_graph.py --no-bump --version 0.1.5
    uv run python scripts/release_obsidian_graph.py --tag
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VERSION_FILE = PROJECT_ROOT / "releases" / "obsidian" / "VERSION"
DIST_DIR = PROJECT_ROOT / "dist"

OBSIDIAN_CLI_SCRIPTS = [
    "scripts/build_obsidian_graph.py",
    "scripts/query_obsidian_graph.py",
    "scripts/repair_obsidian_graph_links.py",
]

# Source files copied verbatim from the repo (Obsidian path only; no Neo4j vault writer).
OBSIDIAN_SOURCE_FILES = [
    "agent_memory/vault/models.py",
    "agent_memory/vault/parser.py",
    "agent_memory/vault/segmenter.py",
    "agent_memory/vault/wikilinks.py",
    "agent_memory/vault/obsidian_graph.py",
    "agent_memory/vault/fragment_summary.py",
    "agent_memory/vault/similarity.py",
    "agent_memory/vault/passage_query.py",
    "agent_memory/vault/obsidian_index.py",
    "agent_memory/vault/summarize.py",
    "agent_memory/vault/graph_stats.py",
] + OBSIDIAN_CLI_SCRIPTS + [
    "docs/obsidian-vault.md",
    "tests/test_obsidian_graph.py",
    "tests/test_passage_query.py",
    "tests/test_fragment_summary.py",
    "tests/test_vault_parser.py",
    "tests/test_vault_summarize.py",
    "tests/test_graph_stats.py",
]

PACKAGE_INIT = '''\
"""Obsidian context graph (standalone release slice of agent-memory)."""

__version__ = "{version}"
'''

VAULT_INIT = '''\
"""Obsidian / markdown vault ingestion for the context graph."""

from agent_memory.vault.models import RawVaultNote, VaultSection
from agent_memory.vault.parser import parse_vault_note, iter_vault_notes
from agent_memory.vault.segmenter import segment_note
from agent_memory.vault.obsidian_graph import ObsidianGraphBuilder
from agent_memory.vault.obsidian_index import ObsidianVaultIndex
from agent_memory.vault.wikilinks import extract_wikilinks, wikilink_for_path

__all__ = [
    "RawVaultNote",
    "VaultSection",
    "parse_vault_note",
    "iter_vault_notes",
    "segment_note",
    "ObsidianGraphBuilder",
    "ObsidianVaultIndex",
    "extract_wikilinks",
    "wikilink_for_path",
]
'''

PYPROJECT = '''\
[project]
name = "obsidian-context-graph"
version = "{version}"
description = "Obsidian-native context graph build and search (no Neo4j)"
requires-python = ">=3.10"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=7.0.0"]

[project.scripts]
build-obsidian-graph = "agent_memory.vault.cli:build_graph"
query-obsidian-graph = "agent_memory.vault.cli:query_graph"
repair-obsidian-graph-links = "agent_memory.vault.cli:repair_links"

[build-system]
requires = ["setuptools>=61.0", "wheel"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["agent_memory*"]
'''

CLI_MODULE = '''\
"""Console entry points for the Obsidian context graph release wheel."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _run_bundled_script(filename: str) -> None:
    path = Path(__file__).with_name("_bundled_scripts") / filename
    if not path.is_file():
        raise SystemExit(f"Bundled script not found: {path}")
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Cannot load bundled script: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    main = getattr(module, "main", None)
    if main is None:
        raise SystemExit(f"Bundled script has no main(): {path}")
    main()


def build_graph() -> None:
    _run_bundled_script("build_obsidian_graph.py")


def query_graph() -> None:
    _run_bundled_script("query_obsidian_graph.py")


def repair_links() -> None:
    _run_bundled_script("repair_obsidian_graph_links.py")
'''

VAULT_MAIN = '''\
"""Cross-platform CLI: python -m agent_memory.vault <command> [args...]"""

from __future__ import annotations

import sys

from agent_memory.vault.cli import build_graph, query_graph, repair_links

_COMMANDS = {
    "build-graph": ("build-obsidian-graph", build_graph),
    "query-graph": ("query-obsidian-graph", query_graph),
    "repair-links": ("repair-obsidian-graph-links", repair_links),
}


def main(argv: list[str] | None = None) -> None:
    argv = list(argv if argv is not None else sys.argv[1:])
    if not argv or argv[0] in ("-h", "--help"):
        print("Usage: python -m agent_memory.vault <command> [args...]")
        print()
        print("Commands:")
        for name in _COMMANDS:
            print(f"  {name}")
        raise SystemExit(0)
    command = argv[0]
    if command not in _COMMANDS:
        raise SystemExit(f"Unknown command: {command!r} (try --help)")
    prog, handler = _COMMANDS[command]
    sys.argv = [prog, *argv[1:]]
    handler()


if __name__ == "__main__":
    main()
'''

README = '''\
# Obsidian Context Graph {version}

Standalone release of the Obsidian vault pipeline from ContextGraph.

## Install

From wheel (inside a uv virtual environment):

```bash
uv pip install obsidian_context_graph-{version}-py3-none-any.whl
```

Run CLI (pick one):

```bash
# Recommended with uv (macOS/Linux/Windows)
uv run build-obsidian-graph --help
uv run query-obsidian-graph --help

# Cross-platform module fallback
python -m agent_memory.vault build-graph --help
python -m agent_memory.vault query-graph --help

# Windows: after activating .venv\\Scripts\\activate
build-obsidian-graph --help
```

Console scripts are installed to the environment's ``Scripts/`` folder (Windows) or ``bin/`` (Unix). They are not on PATH until the venv is activated or you use ``uv run``.

From source directory:

```bash
uv venv .venv --python 3.12
uv pip install -e ".[dev]"
```

## Build a graph

```bash
build-obsidian-graph \\
  --source-vault ~/Vaults/MyNotes \\
  --graph-vault ~/Vaults/MyNotesGraph
```

## Search

```bash
query-obsidian-graph \\
  --vault ~/Vaults/MyNotesGraph \\
  -q "your query" \\
  --tag cg/fragment \\
  --hops 1
```

Full guide: ``docs/obsidian-vault.md``.
'''


def read_version() -> str:
    if not VERSION_FILE.is_file():
        raise SystemExit(f"Missing version file: {VERSION_FILE}")
    version = VERSION_FILE.read_text(encoding="utf-8").strip()
    if not version:
        raise SystemExit(f"Empty version file: {VERSION_FILE}")
    return version


def write_version(version: str) -> None:
    VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    VERSION_FILE.write_text(version + "\n", encoding="utf-8")


def bump_patch(version: str) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(.*)", version.strip())
    if not match:
        raise SystemExit(
            f"Invalid semver in {VERSION_FILE}: {version!r} "
            "(expected MAJOR.MINOR.PATCH)"
        )
    major, minor, patch, suffix = match.groups()
    if suffix:
        raise SystemExit(
            f"Cannot auto-bump version with suffix: {version!r} "
            "(use --no-bump --version explicitly)"
        )
    return f"{major}.{minor}.{int(patch) + 1}"


def git_head() -> str:
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=PROJECT_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def validate_sources() -> None:
    missing = [rel for rel in OBSIDIAN_SOURCE_FILES if not (PROJECT_ROOT / rel).is_file()]
    if missing:
        raise SystemExit("Missing source files:\n  " + "\n  ".join(missing))
    validate_release_import_closure()


def _vault_modules_in_release() -> set[str]:
    return {
        Path(rel).stem
        for rel in OBSIDIAN_SOURCE_FILES
        if rel.startswith("agent_memory/vault/") and rel.endswith(".py")
    }


def validate_release_import_closure() -> None:
    """Fail if a packaged vault module imports another vault module not in the release."""
    packaged = _vault_modules_in_release()
    py_files = [
        *(PROJECT_ROOT / rel for rel in OBSIDIAN_SOURCE_FILES if rel.endswith(".py")),
        *(PROJECT_ROOT / rel for rel in OBSIDIAN_CLI_SCRIPTS),
    ]
    import_re = re.compile(
        r"^\s*(?:from agent_memory\.vault\.(\w+)|import agent_memory\.vault\.(\w+))",
        re.MULTILINE,
    )
    missing: set[str] = set()
    for path in py_files:
        if not path.is_file():
            continue
        for m in import_re.finditer(path.read_text(encoding="utf-8")):
            mod = m.group(1) or m.group(2)
            if mod not in packaged:
                missing.add(mod)
    if missing:
        raise SystemExit(
            "Release package missing vault modules required by imports:\n  "
            + "\n  ".join(f"agent_memory/vault/{name}.py" for name in sorted(missing))
        )


def stage_release(version: str, staging: Path) -> list[str]:
    """Copy sources into staging dir; return relative paths written."""
    written: list[str] = []

    def write_text(rel: str, content: str) -> None:
        out = staging / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(content, encoding="utf-8")
        written.append(rel)

    write_text("VERSION", version + "\n")
    write_text("README.md", README.format(version=version))
    write_text("pyproject.toml", PYPROJECT.format(version=version))
    write_text("agent_memory/__init__.py", PACKAGE_INIT.format(version=version))
    write_text("agent_memory/vault/__init__.py", VAULT_INIT)
    write_text("agent_memory/vault/cli.py", CLI_MODULE)
    write_text("agent_memory/vault/__main__.py", VAULT_MAIN)

    bundled_dir = staging / "agent_memory/vault/_bundled_scripts"
    bundled_dir.mkdir(parents=True, exist_ok=True)
    for rel in OBSIDIAN_CLI_SCRIPTS:
        src = PROJECT_ROOT / rel
        dst = bundled_dir / Path(rel).name
        shutil.copy2(src, dst)
        written.append(str(dst.relative_to(staging)))

    for rel in OBSIDIAN_SOURCE_FILES:
        src = PROJECT_ROOT / rel
        dst = staging / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        written.append(rel)

    return sorted(set(written))


def build_wheel(staging: Path, output_dir: Path) -> Path:
    """Build a wheel from the staged release tree."""
    output_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(output_dir)],
        cwd=staging,
        check=True,
    )
    wheels = sorted(output_dir.glob("obsidian_context_graph-*.whl"))
    if not wheels:
        wheels = sorted(output_dir.glob("obsidian-context-graph-*.whl"))
    if not wheels:
        raise SystemExit(f"No wheel produced in {output_dir}")
    return wheels[-1]


def create_archive(staging: Path, archive_path: Path) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if archive_path.exists():
        archive_path.unlink()
    with tarfile.open(archive_path, "w:gz") as tar:
        tar.add(staging, arcname=staging.name)


def write_manifest(
    *,
    version: str,
    staging: Path,
    archive_path: Path,
    wheel_path: Path | None,
    files: list[str],
) -> Path:
    manifest = {
        "name": "obsidian-context-graph",
        "version": version,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "git_commit": git_head(),
        "archive": str(archive_path.relative_to(PROJECT_ROOT)),
        "wheel": str(wheel_path.relative_to(PROJECT_ROOT)) if wheel_path else None,
        "files": [
            {"path": rel, "sha256": sha256_file(staging / rel)}
            for rel in files
            if (staging / rel).is_file()
        ],
    }
    if wheel_path and wheel_path.is_file():
        manifest["wheel_sha256"] = sha256_file(wheel_path)
    manifest_path = archive_path.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return manifest_path


def maybe_git_tag(version: str, *, dry_run: bool) -> None:
    tag = f"obsidian-v{version}"
    cmd = ["git", "tag", "-a", tag, "-m", f"Obsidian context graph release {version}"]
    if dry_run:
        print(f"[dry-run] would run: {' '.join(cmd)}")
        return
    try:
        subprocess.run(cmd, cwd=PROJECT_ROOT, check=True)
        print(f"Created git tag: {tag}")
    except subprocess.CalledProcessError as exc:
        raise SystemExit(f"Failed to create git tag {tag}: {exc}") from exc


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build versioned Obsidian context graph release archive"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show planned version bump and files without writing release artifacts",
    )
    parser.add_argument(
        "--no-bump",
        action="store_true",
        help="Do not increment releases/obsidian/VERSION",
    )
    parser.add_argument(
        "--version",
        help="Use this version instead of bumping (implies --no-bump unless omitted with bump)",
    )
    parser.add_argument(
        "--tag",
        action="store_true",
        help="Create annotated git tag obsidian-v<version> after a successful release",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DIST_DIR,
        help=f"Directory for staging and archives (default: {DIST_DIR})",
    )
    args = parser.parse_args()

    validate_sources()
    current = read_version()
    if args.version:
        release_version = args.version.strip()
        bump = False
    elif args.no_bump:
        release_version = current
        bump = False
    else:
        release_version = bump_patch(current)
        bump = True

    staging = args.output_dir.resolve() / f"obsidian-context-graph-{release_version}"
    archive = args.output_dir.resolve() / f"obsidian-context-graph-{release_version}.tar.gz"

    print(f"Current version: {current}")
    if bump:
        print(f"Release version: {release_version} (patch +1)")
    else:
        print(f"Release version: {release_version} (no bump)")
    print(f"Staging dir:     {staging}")
    print(f"Archive:         {archive}")
    print(f"Wheel:           {args.output_dir.resolve() / f'obsidian_context_graph-{release_version}-py3-none-any.whl'}")
    print(f"Source files:    {len(OBSIDIAN_SOURCE_FILES)} + generated package stubs")

    if args.dry_run:
        print("\n[dry-run] Files to include:")
        for rel in OBSIDIAN_SOURCE_FILES:
            print(f"  {rel}")
        print("  agent_memory/__init__.py (generated)")
        print("  agent_memory/vault/__init__.py (generated, no VaultWriter)")
        print("  agent_memory/vault/cli.py (generated console entry points)")
        print("  agent_memory/vault/_bundled_scripts/*.py")
        print("  pyproject.toml, README.md, VERSION")
        print("\n[dry-run] Would run: uv build --wheel")
        return

    if staging.exists():
        shutil.rmtree(staging)
    files = stage_release(release_version, staging)
    create_archive(staging, archive)
    wheel_path = build_wheel(staging, args.output_dir.resolve())
    manifest_path = write_manifest(
        version=release_version,
        staging=staging,
        archive_path=archive,
        wheel_path=wheel_path,
        files=files,
    )

    if bump:
        write_version(release_version)

    print(f"\nRelease ready: {archive}")
    print(f"Wheel:           {wheel_path}")
    print(f"Manifest:      {manifest_path}")
    print(f"Archive SHA256: {sha256_file(archive)}")
    print(f"Wheel SHA256:   {sha256_file(wheel_path)}")
    if bump:
        print(f"Updated:       {VERSION_FILE}")

    if args.tag:
        maybe_git_tag(release_version, dry_run=False)


if __name__ == "__main__":
    main()
