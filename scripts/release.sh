#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/release.sh VERSION
Example: scripts/release.sh 2.0.1

Builds Decky Dialogs, commits the version update, creates and pushes a tag,
and publishes a GitHub Release with a prebuilt archive and SHA-256 checksum.
EOF
}

[[ $# -eq 1 ]] || { usage >&2; exit 2; }
VERSION="${1#v}"
TAG="v$VERSION"
REPO="${DECKY_DIALOGS_REPO:-Lukaswnd/decky-dialogs}"

[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][0-9A-Za-z.-]+)?$ ]] || {
  printf 'Invalid version: %s\n' "$VERSION" >&2
  exit 2
}

for command in git pnpm gh tar sha256sum; do
  command -v "$command" >/dev/null 2>&1 || {
    printf 'Required command not found: %s\n' "$command" >&2
    exit 1
  }
done

git rev-parse --show-toplevel >/dev/null
ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"

[[ -z "$(git status --porcelain)" ]] || {
  printf 'The Git working tree is not clean. Commit or stash changes first.\n' >&2
  exit 1
}

git remote get-url origin >/dev/null 2>&1 || {
  printf 'The origin remote is not configured.\n' >&2
  exit 1
}

if git rev-parse "$TAG" >/dev/null 2>&1 || git ls-remote --exit-code --tags origin "refs/tags/$TAG" >/dev/null 2>&1; then
  printf 'Tag %s already exists.\n' "$TAG" >&2
  exit 1
fi

gh auth status >/dev/null

printf 'Updating package version to %s...\n' "$VERSION"
pnpm version "$VERSION" --no-git-tag-version

if [[ -f pnpm-lock.yaml ]]; then
  pnpm install --frozen-lockfile
else
  pnpm install
  printf 'Created pnpm-lock.yaml; it will be included in the release commit.\n'
fi

rm -rf dist release
pnpm build

test -s dist/index.js
test -f main.py
test -f plugin.json
test -f decky-dialogs
chmod +x decky-dialogs

STAGE="release/DeckyDialogs"
mkdir -p "$STAGE/dist"
cp main.py plugin.json package.json README.md decky-dialogs "$STAGE/"
cp dist/index.js "$STAGE/dist/index.js"
[[ ! -f LICENSE ]] || cp LICENSE "$STAGE/LICENSE"

ARCHIVE="release/decky-dialogs.tar.gz"
tar -C release -czf "$ARCHIVE" DeckyDialogs
(
  cd release
  sha256sum decky-dialogs.tar.gz > decky-dialogs.tar.gz.sha256
)

# Keep generated release archives out of the commit.
git add package.json pnpm-lock.yaml
git commit -m "Release $TAG"
git tag -a "$TAG" -m "Decky Dialogs $TAG"

git push origin HEAD
git push origin "$TAG"

gh release create "$TAG" \
  "$ARCHIVE" \
  "$ARCHIVE.sha256" \
  --repo "$REPO" \
  --title "Decky Dialogs $TAG" \
  --generate-notes \
  --verify-tag

printf '\nPublished %s successfully.\n' "$TAG"
printf 'Installer: curl -fsSL https://raw.githubusercontent.com/%s/main/install.sh | bash\n' "$REPO"
