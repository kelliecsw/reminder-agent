#!/usr/bin/env bash
# Build the Reminder Agent image and tag it with :latest and the Git commit.
# The script can be invoked from any directory.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

IMAGE="${IMAGE:-ghcr.io/kelliecsw/reminder-agent}"
COMMIT_HASH="$(git rev-parse --short HEAD)"

docker build \
  --progress=plain \
  --tag "${IMAGE}:latest" \
  .

docker tag "${IMAGE}:latest" "${IMAGE}:${COMMIT_HASH}"

echo
echo "Built:"
echo "  ${IMAGE}:latest"
echo "  ${IMAGE}:${COMMIT_HASH}"
