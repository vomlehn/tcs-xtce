#!/bin/sh
# Checks the Python distributions that `make python-dist` builds.
#
#   1. tcs-xtce's wheel and sdist hold python/tcs_xtce/generated.py, which
#      is not in git, and its wheel py.typed; tcsxtce's wheel holds the
#      packet runtime and its py.typed, and tcs-xtce's does not; each
#      wheel holds both licence files, as files.
#   2. Installed in a fresh virtual environment, with nothing from the
#      repository on the path, tcs-xtce generates the demo mission's Python,
#      C and Rust, and the Python it generated decodes a packet with the
#      installed runtime; tcsccsds encodes and decodes a time. Without
#      rustfmt, generating Rust must fail, saying it needs it.
#   3. The installed xtce-digest gives the demo mission the digest the
#      repository's own copy does.
#
# Nothing is downloaded: the wheels are installed from dist/ alone.
#
# Usage: tests/python_dist_check.sh DIST_DIR
set -eu

top=$(cd "$(dirname "$0")/.." && pwd)
dist=$(cd "$1" && pwd)
wheel=$(ls "$dist"/tcs_xtce-*.whl)
sdist=$(ls "$dist"/tcs_xtce-*.tar.gz)
rt_wheel=$(ls "$dist"/tcsxtce-*.whl)
ccsds_wheel=$(ls "$dist"/tcsccsds-*.whl)
demo=$top/tests/mission/demo.xtce
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

# Says what is wrong with the package, and stops.
fail() {
    echo "python_dist_check: $*" >&2
    exit 1
}

# Prints the size of the licence file a wheel holds, 0 if none.
#   $1  the wheel
#   $2  the licence file's name
licence_size() {
    python3 - "$1" "$2" <<'PY'
import sys
import zipfile

held = [i for i in zipfile.ZipFile(sys.argv[1]).infolist()
        if i.filename.endswith("/licenses/" + sys.argv[2])]
print(held[0].file_size if held else 0)
PY
}

python3 -m zipfile -l "$wheel" > "$work/wheel.txt"
grep -q 'tcs_xtce/generated.py' "$work/wheel.txt" ||
    fail "the wheel has no tcs_xtce/generated.py: run make generate first"
grep -q 'tcs_xtce/py.typed' "$work/wheel.txt" ||
    fail "the wheel has no tcs_xtce/py.typed"
if grep -q '^tcsxtce/' "$work/wheel.txt"; then
    fail "tcs-xtce's wheel holds tcsxtce, which is a distribution of its own"
fi
tar tzf "$sdist" | grep -q 'python/tcs_xtce/generated.py$' ||
    fail "the sdist has no python/tcs_xtce/generated.py"
python3 -m zipfile -l "$rt_wheel" > "$work/rt-wheel.txt"
grep -q 'tcsxtce/packet.py' "$work/rt-wheel.txt" ||
    fail "tcsxtce's wheel has no tcsxtce/packet.py, the packet runtime"
grep -q 'tcsxtce/py.typed' "$work/rt-wheel.txt" ||
    fail "tcsxtce's wheel has no tcsxtce/py.typed"
# A link that setuptools copied as a link would be a few bytes, naming a
# file the wheel does not have.
for w in "$wheel" "$rt_wheel" "$ccsds_wheel"; do
    for licence in LICENSE-APACHE LICENSE-MIT; do
        [ "$(licence_size "$w" "$licence")" -gt 100 ] ||
            fail "$(basename "$w") has no $licence, or only a link to it"
    done
done

python3 -m venv "$work/venv"
# tcs-xtce brings tcsxtce, which it depends on, from dist/.
"$work/venv/bin/pip" install -q --no-index --find-links "$dist" \
    "$wheel" "$ccsds_wheel"

# From outside the repository, so that only what was installed is found.
cd "$work"
bin=$work/venv/bin
"$bin/tcs-xtce" "$demo" demo.py --lang python
"$bin/tcs-xtce" "$demo" demo.h --lang c
# Rust is laid out by rustfmt, which pip cannot install: without it, the
# compiler must say so and fail, as its PyPI page says.
if command -v rustfmt > /dev/null; then
    "$bin/tcs-xtce" "$demo" demo.rs --lang rust
elif "$bin/tcs-xtce" "$demo" demo.rs --lang rust 2> rustfmt.txt; then
    fail "tcs-xtce generated Rust with no rustfmt to lay it out"
else
    grep -q 'rustfmt is required' rustfmt.txt ||
        fail "tcs-xtce failed without rustfmt, without saying why"
fi
"$bin/python" - <<'EOF'
import demo

packet = demo.CCSDSPacketKind.decode(
    bytes.fromhex("0864c02a001201a04e2805dc040302013fc00000fffe8050fc"))
assert type(packet).__name__ == "Housekeeping", packet
assert packet.seq_count == 42, packet
EOF
"$bin/python" - <<'EOF'
from tcsccsds.time import Cds, CdsTime, Epoch, SubMs, decode_cds_exact

layout = Cds(True, SubMs.PICO, Epoch.AGENCY)
t = CdsTime(0x123456, 86399999, 999999999)
assert decode_cds_exact(layout.encode_exact(t)) == (12, t)
EOF

installed=$("$bin/xtce-digest" "$demo")
ours=$(cd "$top/python" && python3 -m tcs_xtce.digest_cli "$demo")
[ "$installed" = "$ours" ] ||
    fail "xtce-digest gives $installed installed and $ours in the repository"

echo "python_dist_check: $(basename "$wheel"), $(basename "$rt_wheel")" \
    "and $(basename "$ccsds_wheel") install, generate, decode and keep" \
    "time"
