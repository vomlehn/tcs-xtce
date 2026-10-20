#!/bin/sh
# Cross-language conformance check.
#
# For every sample document:
#   1. the sample itself must validate against the XTCE schema;
#   2. the Rust, C++ and Python libraries must each deserialize and
#      reserialize it;
#   3. all three outputs must be byte-identical; and
#   4. the output must also validate against the XTCE schema.
#
# Usage: tests/cross_check.sh RUST_TOOL CPP_TOOL [SAMPLE...]
set -eu

top=$(cd "$(dirname "$0")/.." && pwd)
rust_tool=$1
cpp_tool=$2
shift 2
[ $# -gt 0 ] || set -- "$top"/tests/samples/*.xml

out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT INT TERM

validate() {
    XML_CATALOG_FILES="$top/references/catalog.xml" \
        xmllint --noout --nonet --schema "$top/references/SpaceSystem.xsd" "$@" 2>&1 |
        grep -v ' validates$' || true
}

errors=$(validate "$@")
if [ -n "$errors" ]; then
    printf 'samples do not validate:\n%s\n' "$errors" >&2
    exit 1
fi

status=0
for f in "$@"; do
    b=$(basename "$f" .xml)
    "$rust_tool" "$f" "$out/$b.rust.xml"
    "$cpp_tool" "$f" "$out/$b.cpp.xml"
    PYTHONPATH="$top/python" python3 -c \
        'import sys, xtce; xtce.Document.from_file(sys.argv[1]).write_to(open(sys.argv[2], "wb"))' \
        "$f" "$out/$b.py.xml"
    for lang in cpp py; do
        if ! cmp -s "$out/$b.rust.xml" "$out/$b.$lang.xml"; then
            echo "MISMATCH: $b: rust vs $lang" >&2
            diff "$out/$b.rust.xml" "$out/$b.$lang.xml" | head -20 >&2
            status=1
        fi
    done
done

errors=$(validate "$out"/*.rust.xml)
if [ -n "$errors" ]; then
    printf 'serialized output does not validate:\n%s\n' "$errors" >&2
    status=1
fi

[ $status -eq 0 ] && echo "cross_check: $# samples identical across Rust, C++ and Python, all schema-valid"
exit $status
