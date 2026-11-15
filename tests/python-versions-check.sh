#!/bin/sh
# Installs the Python distributions that `make python-dist` built in a clean
# container for each Python they say they support, 3.10 to 3.14, and runs
# tests/python_dist_check.sh there: the official python:3.N-slim images,
# with nothing but Python in them, so that what is checked is the wheels
# alone. The repository is mounted read-only. Needs podman, and the network
# the first time each image is pulled.
#
# Usage: tests/python-versions-check.sh [DIST_DIR]
set -eu

top=$(cd "$(dirname "$0")/.." && pwd)
dist=$(cd "${1:-$top/dist}" && pwd)
versions="3.10 3.11 3.12 3.13 3.14"

command -v podman > /dev/null || {
    echo "python-versions-check: needs podman" >&2
    exit 1
}
for v in $versions; do
    echo "python-versions-check: Python $v"
    podman run --rm --network=none \
        -v "$top:/src:ro" -v "$dist:/dist:ro" \
        "docker.io/library/python:$v-slim" \
        sh /src/tests/python_dist_check.sh /dist ||
        { echo "python-versions-check: Python $v failed" >&2; exit 1; }
done
echo "python-versions-check: the distributions install and work on" \
    "Python $versions"
