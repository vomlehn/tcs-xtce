#!/bin/sh
# Cross-language check of the frame synchronizers of the demo mission's
# streams: each runtime finds the frames of tests/mission/streams.txt, which
# must be streams.expected, and of random streams from random_streams.py, on
# which they must agree.  The C tool is the generated header compiled as C,
# the C++ tool the same header compiled as C++.
#
# Usage: tests/stream_check.sh RUST_TOOL C_TOOL CXX_TOOL [COUNT]
set -eu

top=$(cd "$(dirname "$0")/.." && pwd)
count=${4:-300}
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT INT TERM
mission=$top/tests/mission

status=0
run_all() { # input name
    "$1" "$2" >"$out/$3.rust"
    "$C_TOOL" "$2" >"$out/$3.c"
    "$CXX_TOOL" "$2" >"$out/$3.cxx"
    PYTHONPATH="$top/python" python3 "$top/python/tests/stream_tool.py" \
        "$2" >"$out/$3.py"
    for lang in c cxx py; do
        if ! cmp -s "$out/$3.rust" "$out/$3.$lang"; then
            echo "MISMATCH: $3: rust vs $lang" >&2
            diff "$out/$3.rust" "$out/$3.$lang" | head -10 >&2
            status=1
        fi
    done
}

C_TOOL=$2
CXX_TOOL=$3
run_all "$1" "$mission/streams.txt" streams
if ! cmp -s "$out/streams.rust" "$mission/streams.expected"; then
    echo "MISMATCH: streams.txt output differs from streams.expected" >&2
    diff "$mission/streams.expected" "$out/streams.rust" >&2 || true
    status=1
fi
python3 "$mission/random_streams.py" 1 "$count" >"$out/random.txt"
run_all "$1" "$out/random.txt" random

if [ $status -eq 0 ]; then
    lines=$(wc -l <"$mission/streams.txt")
    frames=$(tr ' ' '\n' <"$out/random.rust" | grep -c '^[0-9a-f]*$' || true)
    echo "stream_check: $lines lines match streams.expected; $count random" \
        "streams ($frames frames) identical across Rust, C, C++ and Python"
fi
exit $status
