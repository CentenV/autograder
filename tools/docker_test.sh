#!/usr/bin/env bash
# Run an autograder zip against a submission inside Gradescope's base image,
# mimicking Gradescope: unzip to /autograder/source, run setup.sh, then
# run_autograder with the submission at /autograder/submission.
#
#   tools/docker_test.sh p4_routing path/to/submission_dir_or_tarball [variant]
#
# Prints results.json. Uses $DOCKER (default: docker, falls back to podman).
set -euo pipefail

PROJECT="${1:?usage: docker_test.sh PROJECT SUBMISSION [VARIANT]}"
SUBMISSION="$(cd "$(dirname "$2")" && pwd)/$(basename "$2")"
VARIANT="${3:-}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE_IMAGE="${BASE_IMAGE:-gradescope/autograder-base:ubuntu-22.04}"
DOCKER="${DOCKER:-$(command -v docker || command -v podman)}"
NAME="$PROJECT${VARIANT:+-$VARIANT}"
TAG="autograder-$(echo "$NAME" | tr '_' '-'):local"

"$ROOT/tools/make_zip.sh" "$PROJECT" $VARIANT >&2
CTX="$(mktemp -d)"
trap 'rm -rf "$CTX"' EXIT
cp "$ROOT/dist/$NAME.zip" "$CTX/autograder.zip"
cat > "$CTX/Dockerfile" <<EOF
FROM $BASE_IMAGE
RUN apt-get update && apt-get install -y --no-install-recommends unzip && rm -rf /var/lib/apt/lists/*
COPY autograder.zip /tmp/autograder.zip
RUN mkdir -p /autograder/source && unzip -q /tmp/autograder.zip -d /autograder/source \
 && cp /autograder/source/run_autograder /autograder/run_autograder \
 && chmod +x /autograder/run_autograder && bash /autograder/source/setup.sh
EOF
"$DOCKER" build -q -t "$TAG" "$CTX" >/dev/null

SUBDIR="$(mktemp -d)"
trap 'rm -rf "$CTX" "$SUBDIR"' EXIT
if [ -d "$SUBMISSION" ]; then cp -R "$SUBMISSION"/. "$SUBDIR/"; else cp "$SUBMISSION" "$SUBDIR/"; fi

"$DOCKER" run --rm -v "$SUBDIR:/autograder/submission:ro" "$TAG" \
  bash -c '/autograder/run_autograder > /tmp/stdout 2>&1; tail -40 /tmp/stdout >&2; cat /autograder/results/results.json'
