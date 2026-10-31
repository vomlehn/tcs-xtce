#!/bin/sh
# Cross-language conformance check for the generated packet codecs.
#
#   1. The demo mission validates against the XTCE schema.
#   2. Each language decodes and re-encodes the hand-built packets in
#      tests/mission/vectors.txt; the output must equal vectors.expected.
#   3. Each language decodes and re-encodes random packets; the outputs must
#      be byte-identical.  The C tool is the generated header compiled as C,
#      the C++ tool the same header compiled as C++.  The second Rust tool
#      uses codecs generated with --dynamic-allocations false and also
#      fails if decoding, encoding or describing a packet allocates.
#   4. Re-encoding is a fixed point: decoding a re-encoded packet gives the
#      same values and re-encodes to the same bytes.
#
# Usage: tests/packet_check.sh RUST_TOOL RUST_FIXED_TOOL C_TOOL CXX_TOOL [COUNT]
set -eu

top=$(cd "$(dirname "$0")/.." && pwd)
rust_tool=$1
rust_fixed_tool=$2
c_tool=$3
cxx_tool=$4
count=${5:-3000}
mission=$top/tests/mission

out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT INT TERM

py_tool() {
    PYTHONPATH="$top/python" python3 "$top/python/tests/packet_tool.py" "$@"
}

XML_CATALOG_FILES="$top/references/catalog.xml" \
    xmllint --noout --nonet --schema "$top/references/SpaceSystem.xsd" \
        "$mission/demo.xtce" 2>/dev/null || {
    echo "tests/mission/demo.xtce does not validate against the XTCE schema" >&2
    exit 1
}

status=0
run_all() { # vectors-file name
    "$rust_tool" "$1" >"$out/$2.rust"
    "$rust_fixed_tool" "$1" >"$out/$2.rust-fixed"
    "$c_tool" "$1" >"$out/$2.c"
    "$cxx_tool" "$1" >"$out/$2.cxx"
    py_tool "$1" >"$out/$2.py"
    for lang in rust-fixed c cxx py; do
        if ! cmp -s "$out/$2.rust" "$out/$2.$lang"; then
            echo "MISMATCH: $2: rust vs $lang" >&2
            diff "$out/$2.rust" "$out/$2.$lang" | head -10 >&2
            status=1
        fi
    done
}

run_all "$mission/vectors.txt" vectors
if ! cmp -s "$out/vectors.rust" "$mission/vectors.expected"; then
    echo "MISMATCH: vectors.txt output differs from vectors.expected" >&2
    diff "$mission/vectors.expected" "$out/vectors.rust" >&2 || true
    status=1
fi

python3 "$mission/random_vectors.py" 1 "$count" >"$out/random.txt"
run_all "$out/random.txt" random

# Fixed point: feed every successful re-encoding back in.
paste -d'|' "$out/random.txt" "$out/random.rust" |
    awk -F'|' '$3 ~ /^ [0-9a-f]+$/ {
        split($1, v, " "); print v[1], substr($3, 2)
    }' >"$out/again.txt"
paste -d'|' "$out/random.txt" "$out/random.rust" |
    awk -F'|' '$3 ~ /^ [0-9a-f]+$/ { print $2 "|" $3 }' >"$out/again.expected"
run_all "$out/again.txt" again
if ! cmp -s "$out/again.rust" "$out/again.expected"; then
    echo "MISMATCH: re-encoded packets do not decode to the same values" >&2
    diff "$out/again.expected" "$out/again.rust" | head -10 >&2
    status=1
fi

if [ $status -eq 0 ]; then
    echo "packet_check: $(wc -l <"$out/vectors.rust") vectors match" \
        "vectors.expected; $count random and $(wc -l <"$out/again.txt")" \
        "re-encoded packets identical across Rust (with and without" \
        "dynamic allocation), C, C++ and Python"
fi
exit $status
