#!/usr/bin/env bash
# Build and publish the Obsidian context graph release to GitHub.
#
# Usage:
#   ./scripts/release_obsidian_graph.sh              # bump patch, build, tag, gh release
#   ./scripts/release_obsidian_graph.sh --dry-run    # preview only
#   ./scripts/release_obsidian_graph.sh --no-bump    # rebuild current VERSION
#   ./scripts/release_obsidian_graph.sh --no-publish # build artifacts locally only
#   ./scripts/release_obsidian_graph.sh --draft      # create a draft GitHub release
#   ./scripts/release_obsidian_graph.sh --no-commit  # skip committing VERSION bump
#   ./scripts/release_obsidian_graph.sh --no-push      # skip pushing branch before gh release
#   ./scripts/release_obsidian_graph.sh --no-tag       # skip creating/pushing obsidian-v* git tag

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

DRY_RUN=false
NO_BUMP=false
NO_PUBLISH=false
DRAFT=false
NO_COMMIT=false
NO_PUSH=false
NO_TAG=false
REPO=""

usage() {
    cat <<'EOF'
Build and publish Obsidian context graph releases.

Options:
  --dry-run       Preview version bump and gh release plan (no writes)
  --no-bump       Rebuild the current releases/obsidian/VERSION
  --no-publish    Build artifacts only; do not create a GitHub release
  --draft         Publish as a GitHub draft release
  --no-commit     Do not auto-commit releases/obsidian/VERSION after bump
  --no-push       Do not push the current branch before creating the GitHub release
  --no-tag        Do not create or push obsidian-v* git tag on the release commit
  --repo OWNER/REPO  GitHub repo for gh (default: current git remote origin)
  -h, --help      Show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry-run) DRY_RUN=true; shift ;;
        --no-bump) NO_BUMP=true; shift ;;
        --no-publish) NO_PUBLISH=true; shift ;;
        --draft) DRAFT=true; shift ;;
        --no-commit) NO_COMMIT=true; shift ;;
        --no-push) NO_PUSH=true; shift ;;
        --no-tag) NO_TAG=true; shift ;;
        --repo)
            REPO="$2"
            shift 2
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
done

if [[ -z "$REPO" ]]; then
    REPO="$(gh repo view --json nameWithOwner -q .nameWithOwner)"
fi

VERSION_BEFORE="$(tr -d '[:space:]' < releases/obsidian/VERSION)"

PY_ARGS=()
if $NO_BUMP; then
    PY_ARGS+=(--no-bump)
fi
if $DRY_RUN; then
    PY_ARGS+=(--dry-run)
fi

echo "==> Building Obsidian release (repo: ${REPO})"
if ((${#PY_ARGS[@]})); then
    uv run python scripts/release_obsidian_graph.py "${PY_ARGS[@]}"
else
    uv run python scripts/release_obsidian_graph.py
fi

if $DRY_RUN; then
    if $NO_BUMP; then
        VERSION="${VERSION_BEFORE}"
    else
        IFS=. read -r major minor patch <<< "$VERSION_BEFORE"
        VERSION="${major}.${minor}.$((patch + 1))"
    fi
    TAG="obsidian-v${VERSION}"
    echo
    echo "[dry-run] Would tag:        ${TAG}"
    if ! $NO_COMMIT && ! $NO_BUMP; then
        echo "[dry-run] Would commit:     releases/obsidian/VERSION -> ${VERSION}"
    fi
    if ! $NO_PUSH && ! $NO_PUBLISH; then
        echo "[dry-run] Would push:       origin HEAD"
    fi
    if ! $NO_TAG; then
        echo "[dry-run] Would git tag:    ${TAG} (on release commit)"
        if ! $NO_PUSH && ! $NO_PUBLISH; then
            echo "[dry-run] Would push tag: origin ${TAG}"
        fi
    fi
    if ! $NO_PUBLISH; then
        echo "[dry-run] Would gh release: ${TAG} on ${REPO}"
        echo "  dist/obsidian-context-graph-${VERSION}.tar.gz"
        echo "  dist/obsidian_context_graph-${VERSION}-py3-none-any.whl"
        echo "  dist/obsidian-context-graph-${VERSION}.tar.manifest.json"
    fi
    exit 0
fi

VERSION="$(tr -d '[:space:]' < releases/obsidian/VERSION)"
TAG="obsidian-v${VERSION}"
DIST="$ROOT/dist"
ARCHIVE="${DIST}/obsidian-context-graph-${VERSION}.tar.gz"
WHEEL="${DIST}/obsidian_context_graph-${VERSION}-py3-none-any.whl"
MANIFEST="${DIST}/obsidian-context-graph-${VERSION}.tar.manifest.json"

for f in "$ARCHIVE" "$WHEEL" "$MANIFEST"; do
    if [[ ! -f "$f" ]]; then
        echo "Missing release artifact: $f" >&2
        exit 1
    fi
done

COMMITTED=false
if ! $NO_COMMIT && [[ "$VERSION" != "$VERSION_BEFORE" ]]; then
    if git diff --quiet -- releases/obsidian/VERSION; then
        echo "==> VERSION unchanged on disk; skip commit"
    else
        echo "==> Committing version bump: ${VERSION_BEFORE} -> ${VERSION}"
        git add releases/obsidian/VERSION
        git commit -m "$(cat <<EOF
chore(release): obsidian context graph v${VERSION}

EOF
)"
        COMMITTED=true
    fi
fi

if $COMMITTED && ! $NO_PUSH && ! $NO_PUBLISH; then
    echo "==> Pushing current branch to origin"
    git push origin HEAD
fi

create_release_tag() {
    if $NO_TAG; then
        return 0
    fi
    if git rev-parse "$TAG" >/dev/null 2>&1; then
        local tagged_commit
        tagged_commit="$(git rev-list -n 1 "$TAG")"
        if [[ "$tagged_commit" != "$(git rev-parse HEAD)" ]]; then
            echo "Tag ${TAG} already exists on a different commit (${tagged_commit:0:7})" >&2
            exit 1
        fi
        echo "==> Tag already on HEAD: ${TAG}"
        return 0
    fi
    echo "==> Creating annotated tag ${TAG} on $(git rev-parse --short HEAD)"
    git tag -a "$TAG" -m "Obsidian context graph release ${VERSION}"
}

push_release_tag() {
    if $NO_TAG || $NO_PUSH; then
        return 0
    fi
    echo "==> Pushing tag to origin: ${TAG}"
    git push origin "$TAG"
}

create_release_tag

NOTES_FILE="$(mktemp)"
trap 'rm -f "$NOTES_FILE"' EXIT
ARCHIVE_SHA="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
WHEEL_SHA="$(shasum -a 256 "$WHEEL" | awk '{print $1}')"
cat > "$NOTES_FILE" <<EOF
Obsidian context graph release **${VERSION}**.

## Install (wheel)

\`\`\`bash
uv pip install obsidian_context_graph-${VERSION}-py3-none-any.whl
build-obsidian-graph --help
query-obsidian-vault --help
\`\`\`

## Artifacts

| File | SHA256 |
|------|--------|
| \`obsidian-context-graph-${VERSION}.tar.gz\` | \`${ARCHIVE_SHA}\` |
| \`obsidian_context_graph-${VERSION}-py3-none-any.whl\` | \`${WHEEL_SHA}\` |

See \`docs/obsidian-vault.md\` in the source tarball for the full guide.
EOF

if $NO_PUBLISH; then
    echo "==> Build complete (skipping GitHub release)"
    echo "    Version:  ${VERSION}"
    if ! $NO_TAG; then
        if $NO_PUSH; then
            echo "    Tag:      ${TAG} (local only)"
        else
            echo "    Tag:      ${TAG} (local, not pushed)"
        fi
    fi
    echo "    Archive:  ${ARCHIVE}"
    echo "    Wheel:    ${WHEEL}"
    echo "    Manifest: ${MANIFEST}"
    exit 0
fi

push_release_tag

echo "==> Checking gh authentication"
gh auth status >/dev/null

GH_ARGS=(
    release create "$TAG"
    --repo "$REPO"
    --title "Obsidian Context Graph ${VERSION}"
    --notes-file "$NOTES_FILE"
    --target "$(git rev-parse HEAD)"
)
if $DRAFT; then
    GH_ARGS+=(--draft)
fi

if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
    echo "==> GitHub release ${TAG} exists; uploading assets"
    gh release upload "$TAG" --repo "$REPO" --clobber "$ARCHIVE" "$WHEEL" "$MANIFEST"
else
    echo "==> Creating GitHub release ${TAG} on ${REPO}"
    gh "${GH_ARGS[@]}" "$ARCHIVE" "$WHEEL" "$MANIFEST"
fi

RELEASE_URL="$(gh release view "$TAG" --repo "$REPO" --json url -q .url)"
echo "==> Published: ${RELEASE_URL}"
