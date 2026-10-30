#!/bin/sh
# Checks the Python package that `make python-dist` builds.
#
#   1. The wheel and the sdist hold python/tcs_xtce/generated.py, which is
#      not in git, and the wheel the package's py.typed.
#   2. Installed in a fresh virtual environment, with nothing from the
#      repository on the path, tcs-xtce generates the demo mission's Python,
#      C and Rust, and the Python it generated decodes a packet with the
#      installed runtime.
#   3. The installed xtce-digest gives the demo mission the digest the
#      repository's own copy does.
#
# Nothing is downloaded: the wheel is installed from dist/ alone.
#
# Usage: tests/python_dist_check.sh DIST_DIR
set -eu

top=$(cd "$(dirname "$0")/.." && pwd)
dist=$(cd "$1" && pwd)
wheel=$(ls "$dist"/tcs_xtce-*.whl)
sdist=$(ls "$dist"/tcs_xtce-*.tar.gz)
demo=$top/tests/mission/demo.xml
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Says what is wrong with the package, and stops.
fail() {
    echo "python_dist_check: $*" >&2
    exit 1
}

python3 -m zipfile -l "$wheel" > "$work/wheel.txt"
grep -q 'tcs_xtce/generated.py' "$work/wheel.txt" ||
    fail "the wheel has no tcs_xtce/generated.py: run make generate first"
grep -q 'tcs_xtce/py.typed' "$work/wheel.txt" ||
    fail "the wheel has no tcs_xtce/py.typed"
tar tzf "$sdist" | grep -q 'python/tcs_xtce/generated.py$' ||
    fail "the sdist has no python/tcs_xtce/generated.py"

python3 -m venv "$work/venv"
"$work/venv/bin/pip" install -q --no-index --no-deps "$wheel"

# From outside the repository, so that only what was installed is found.
cd "$work"
bin=$work/venv/bin
"$bin/tcs-xtce" "$demo" demo.py --lang python
"$bin/tcs-xtce" "$demo" demo.h --lang c
"$bin/tcs-xtce" "$demo" demo.rs --lang rust
"$bin/python" - <<'EOF'
import demo

packet = demo.CCSDSPacketKind.decode(
    bytes.fromhex("0864c02a001201a04e2805dc040302013fc00000fffe8050fc"))
assert type(packet).__name__ == "Housekeeping", packet
assert packet.seq_count == 42, packet
EOF

installed=$("$bin/xtce-digest" "$demo")
ours=$(cd "$top/python" && python3 -m tcs_xtce.digest_cli "$demo")
[ "$installed" = "$ours" ] ||
    fail "xtce-digest gives $installed installed and $ours in the repository"

echo "python_dist_check: $(basename "$wheel") and $(basename "$sdist")" \
    "install, generate and decode"
