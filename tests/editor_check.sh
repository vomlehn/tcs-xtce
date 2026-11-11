#!/bin/sh
# Holds tcs-xtcee to xmllint: makes random documents of every XTCE version
# the editor knows, checks each with both, converts each to every other
# version and has xmllint judge the result.  A need is answered with 8, which
# a few needs refuse; a refusal is reported, not counted as a failure.
#
# usage: tests/editor_check.sh [SEEDS]   (from the top of the repository,
#                                         after make editor)
# Exits with status 1 if xmllint and the editor ever disagree.

seeds=${1:-40}
out=$(mktemp -d)
trap 'rm -rf "$out"' EXIT
export XML_CATALOG_FILES=references/catalog.xml
E=editor/target/release/tcs-xtcee
fail=0
ok=0
for v in 1.1 1.2 1.3; do
    for s in $(seq 0 $((seeds - 1))); do
        f=$out/s$v-$s.xtce
        $E sample "$v" "$s" > "$f"
        if ! xmllint --nonet --noout --schema "references/xtce-$v.xsd" \
                "$f" 2>/dev/null; then
            echo "xmllint rejects the $v sample of seed $s"
            fail=1
        fi
        for t in 1.1 1.2 1.3; do
            [ "$t" = "$v" ] && continue
            o=$out/s$v-$s-to$t.xtce
            sets=""
            done=0
            for try in 1 2 3; do
                if eval "$E convert '$f' --to $t --allow-loss $sets -o '$o'" \
                        > "$o.log" 2>&1; then
                    done=1
                    break
                fi
                keys=$(sed -n "s/^    answer with --set '\(.*\)=VALUE'/\1/p" \
                    "$o.log")
                [ -z "$keys" ] && break
                for k in $keys; do
                    sets="$sets --set '$k=8'"
                done
            done
            if [ $done = 0 ]; then
                echo "refused $v->$t, seed $s: $(grep -m1 refused "$o.log")"
                continue
            fi
            if xmllint --nonet --noout --schema "references/xtce-$t.xsd" \
                    "$o" 2>/dev/null; then
                ok=$((ok + 1))
            else
                echo "xmllint rejects $v->$t of seed $s"
                fail=1
            fi
        done
    done
done
echo "editor_check: $ok conversions xmllint accepts"
exit $fail
