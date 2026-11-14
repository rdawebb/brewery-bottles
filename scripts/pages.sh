#!/usr/bin/env bash
# Run "$@" against a fresh gh-pages checkout in ./pages, then commit and push what it changed.
#
# A push is rejected when another run pushed first, so this fetches, re-runs and retries: the
# push is the lock; "$@" must therefore start from the files in pages, and any output it keeps
# must be overwritten, not appended to, on each attempt; needs TOKEN (contents: write).
set -euo pipefail

for _ in {1..10}; do
  rm -rf pages
  git worktree prune
  if git fetch -q --depth=1 origin gh-pages; then
    git worktree add -q -B gh-pages pages FETCH_HEAD
  else
    git worktree add -q --orphan -b gh-pages pages
  fi

  "$@"

  touch pages/.nojekyll
  git -C pages add -A
  git -C pages diff --cached --quiet && exit 0
  git -C pages -c user.name="github-actions[bot]" \
    -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
    commit -qm "Run $GITHUB_RUN_ID"
  git -C pages push -q "https://x-access-token:$TOKEN@github.com/$GITHUB_REPOSITORY.git" gh-pages \
    && exit 0
  sleep $((RANDOM % 20 + 5))
done

echo "::error::Gave up pushing to gh-pages after 10 attempts"
exit 1
