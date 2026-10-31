#!/bin/sh
# Checks that every runtime computes the same XTCE digest: Rust, C, C++ and
# Python each digest the demo mission, the examples' XTCE files, the
# schema-covering samples and the documents in tests/digest, and must agree
# with each other and with tests/digest/expected.txt; and each digests the
# sets of documents in tests/digest/sets.txt.
#
# usage: tests/digest_check.sh RUST_TOOL C_TOOL CXX_TOOL
set -eu

top=$(cd "$(dirname "$0")/.." && pwd)
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

cd "$top"
files="tests/mission/demo.xtce $(ls examples/*/*.xtce tests/samples/*.xtce \
    tests/digest/*.xtce)"
# shellcheck disable=SC2086
"$1" $files >"$out/rust"
# shellcheck disable=SC2086
"$2" $files >"$out/c"
# shellcheck disable=SC2086
"$3" $files >"$out/cxx"
# shellcheck disable=SC2086
python3 python/tests/digest_tool.py $files >"$out/python"

status=0
for lang in c cxx python; do
    if ! cmp -s "$out/rust" "$out/$lang"; then
        echo "MISMATCH: Rust and $lang digests differ" >&2
        echo "$files" | tr ' ' '\n' | paste -d' ' - "$out/rust" "$out/$lang" |
            awk '$2 != $3' >&2
        status=1
    fi
done

# The documents in tests/digest have known digests, so that a change to the
# digest that every runtime makes at once is caught too.
echo "$files" | tr ' ' '\n' | paste -d' ' - "$out/rust" |
    grep '^tests/digest/' >"$out/known"
if ! cmp -s "$out/known" tests/digest/expected.txt; then
    echo "MISMATCH: digests differ from tests/digest/expected.txt" >&2
    diff tests/digest/expected.txt "$out/known" >&2 || true
    status=1
fi

# Sets of documents, which have one digest: tests/digest/sets.txt has each
# set and its digest.
sets=0
while read -r want set; do
    case $want in '' | '#'*) continue ;; esac
    sets=$((sets + 1))
    for tool in "$1" "$2" "$3" "python3 python/tests/digest_tool.py"; do
        # shellcheck disable=SC2086
        got=$($tool --set $set)
        if [ "$got" != "$want" ]; then
            echo "MISMATCH: $tool --set $set: $got, not $want" >&2
            status=1
        fi
    done
done <tests/digest/sets.txt

[ $status -eq 0 ] &&
    echo "digest_check: $(wc -l <"$out/rust") documents and $sets sets give" \
        "the same digest in Rust, C, C++ and Python"
exit $status
