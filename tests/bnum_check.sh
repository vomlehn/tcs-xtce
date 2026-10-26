#!/bin/sh
# Checks that cpp/include/xtce/bnum.h and bnum.hpp compute as the bnum crate
# does: the C functions (built as C and as C++) and the C++ class each run
# random cases of every operation on every size from tests/bnum_cases.py,
# and must print what rust/examples/bnum_tool.rs, which uses bnum, prints.
#
# usage: tests/bnum_check.sh RUST_TOOL C_TOOL C_AS_CXX_TOOL CXX_TOOL [COUNT]
set -eu

top=$(cd "$(dirname "$0")/.." && pwd)
count=${5:-1500}
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT

cd "$top"
python3 tests/bnum_cases.py 1 "$count" --c >"$out/c.in"
python3 tests/bnum_cases.py 2 "$count" >"$out/cxx.in"

status=0
compare() { # set want got name
    if ! cmp -s "$out/$2" "$out/$3"; then
        echo "MISMATCH: $4 differs from bnum" >&2
        paste -d'|' "$out/$1.in" "$out/$2" "$out/$3" |
            awk -F'|' '$2 != $3' | head -10 >&2
        status=1
    fi
}
for set in c cxx; do
    "$1" <"$out/$set.in" >"$out/$set.rust"
done
"$2" <"$out/c.in" >"$out/c.c"
"$3" <"$out/c.in" >"$out/c.c-as-cxx"
"$4" <"$out/cxx.in" >"$out/cxx.cxx"
compare c c.rust c.c "xtce/bnum.h built as C"
compare c c.rust c.c-as-cxx "xtce/bnum.h built as C++"
compare cxx cxx.rust cxx.cxx "xtce/bnum.hpp"

[ $status -eq 0 ] &&
    echo "bnum_check: $(wc -l <"$out/c.in") cases of the C functions and" \
        "$(wc -l <"$out/cxx.in") of the C++ class identical to bnum"
exit $status
