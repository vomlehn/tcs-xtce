#!/bin/sh
# Checks that every runtime computes numbers the same way: Rust, C, C++ and
# Python each decode and encode every float format and size XTCE has, apply
# every MathOperationCalibrator operator, and compute every ErrorDetectCorrect
# checksum, for the cases in tests/float/known.txt, whose results the formats'
# standards and the CRC catalogue give, and for random cases from
# tests/float_cases.py, on which they must agree with each other.
#
# usage: tests/float_check.sh RUST_TOOL C_TOOL CXX_TOOL [COUNT]
set -eu

top=$(cd "$(dirname "$0")/.." && pwd)
count=${4:-2000}
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

cd "$top"
# Each known line is a tool's input, then the output it must give.
grep -v '^#' tests/float/known.txt | awk '{NF--; print}' >"$out/known.in"
grep -v '^#' tests/float/known.txt | awk '{print $NF}' >"$out/known.want"
python3 tests/float_cases.py 1 "$count" >"$out/random.in"

status=0
for set in known random; do
    "$1" <"$out/$set.in" >"$out/$set.rust"
    "$2" <"$out/$set.in" >"$out/$set.c"
    "$3" <"$out/$set.in" >"$out/$set.cxx"
    python3 python/tests/float_tool.py <"$out/$set.in" >"$out/$set.python"
    for lang in c cxx python; do
        if ! cmp -s "$out/$set.rust" "$out/$set.$lang"; then
            echo "MISMATCH: $set: Rust and $lang differ" >&2
            paste -d'|' "$out/$set.in" "$out/$set.rust" "$out/$set.$lang" |
                awk -F'|' '$2 != $3' | head -10 >&2
            status=1
        fi
    done
done
if ! cmp -s "$out/known.rust" "$out/known.want"; then
    echo "MISMATCH: results differ from tests/float/known.txt" >&2
    paste -d'|' "$out/known.in" "$out/known.want" "$out/known.rust" |
        awk -F'|' '$2 != $3' >&2
    status=1
fi

[ $status -eq 0 ] &&
    echo "float_check: $(wc -l <"$out/known.in") known and" \
        "$(wc -l <"$out/random.in") random cases identical in Rust, C, C++" \
        "and Python"
exit $status
