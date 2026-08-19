#!/usr/bin/env bash
set -euo pipefail

: "${GH_TOKEN:?GH_TOKEN is required}"
: "${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is required}"
: "${GITHUB_RUN_ID:?GITHUB_RUN_ID is required}"

report=${ETR_REPORT_PATH:-/tmp/etr-last-deploy.txt}
test -s "$report"
echo "::add-mask::$GH_TOKEN"
proof_dir="${RUNNER_TEMP:-/tmp}/etr-deploy-proof-${GITHUB_RUN_ID}"
rm -rf "$proof_dir"
trap 'rm -rf "$proof_dir"' EXIT

git clone --filter=blob:none "https://github.com/${GITHUB_REPOSITORY}.git" "$proof_dir"
git -C "$proof_dir" config user.name "EtR deployment agent"
git -C "$proof_dir" config user.email "actions@users.noreply.github.com"
git -C "$proof_dir" remote set-url origin \
  "https://x-access-token:${GH_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"

pushed=false
for attempt in $(seq 1 5); do
  git -C "$proof_dir" fetch origin main || { sleep 2; continue; }
  git -C "$proof_dir" checkout -B proof-edge origin/main || { sleep 2; continue; }
  mkdir -p "$proof_dir/.github/deployment"
  cp "$report" "$proof_dir/.github/deployment/etr-last-deploy.txt"
  git -C "$proof_dir" add .github/deployment/etr-last-deploy.txt
  if git -C "$proof_dir" diff --cached --quiet; then
    pushed=true
    break
  fi
  git -C "$proof_dir" commit -m "chore: record EtR deployment result [skip ci]" \
    || { sleep 2; continue; }
  if git -C "$proof_dir" push origin HEAD:main; then
    pushed=true
    break
  fi
  sleep 3
done
[ "$pushed" = true ]
