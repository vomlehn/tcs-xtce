/* Computes integer operations with xtce-bnum.h, one case per line of
 * standard input, as tests/bnum_check.sh asks; rust/examples/bnum_tool.rs
 * describes the cases and what it prints, and does the same with bnum.
 * Built as C and as C++. */
#include <stdio.h>
#include <stdlib.h>
#include <xtce-bnum.h>

#define MAXN 32

typedef struct num {
        uint8_t b[MAXN];
} num;

static size_t n;
static bool s;

static void type_of(const char* t, size_t* nn, bool* ss) {
        *ss = t[0] == 'i';
        *nn = (size_t)strtoul(t + 1, NULL, 10);
}

/** Parses a decimal integer of the current case's type, exiting with
 * status 2 if it isn't one.
 *
 * @param text  The decimal digits, with a sign if any.
 * @return      The integer. */
static num parse(const char* text) {
        num v;
        if (xtce_bn_from_str_radix(v.b, n, s, text, strlen(text), 10)) {
                fprintf(stderr, "bad number %s\n", text);
                exit(2);
        }
        return v;
}

/** Prints an integer in decimal, without a newline.
 *
 * @param v   The integer's bytes, little endian.
 * @param nn  Its size in bytes, at most MAXN.
 * @param ss  Whether it is signed. */
static void put(const uint8_t* v, size_t nn, bool ss) {
        uint8_t scratch[MAXN];
        char buf[8 * MAXN + 2];
        xtce_bn_to_str_radix(v, nn, ss, 10, scratch, buf, sizeof buf);
        fputs(buf, stdout);
}

static void value(const num* v) {
        put(v->b, n, s);
        putchar('\n');
}

static void overflowing(const num* v, bool o) {
        put(v->b, n, s);
        printf(" %s\n", o ? "true" : "false");
}

/** Prints the result of a checked operation: the value, or "none" if it
 * failed.
 *
 * @param v   The value, of the current case's type.
 * @param ok  Whether the operation succeeded. */
static void checked(const num* v, bool ok) {
        if (ok)
                value(v);
        else
                puts("none");
}

static void hex(const uint8_t* b, size_t len) {
        size_t i;
        for (i = 0; i < len; ++i) printf("%02x", b[i]);
        putchar('\n');
}

/** Converts a string of hexadecimal digit pairs to bytes.
 *
 * @param t    The digits, two per byte.
 * @param out  Filled in with the bytes; must hold strlen(t) / 2.
 * @return     How many bytes were written. */
static size_t unhex(const char* t, uint8_t* out) {
        size_t len = strlen(t) / 2, i;
        for (i = 0; i < len; ++i) {
                unsigned x;
                sscanf(t + 2 * i, "%2x", &x);
                out[i] = (uint8_t)x;
        }
        return len;
}

static const char* const kinds[] = {"", "Empty", "InvalidDigit",
                                    "PosOverflow", "NegOverflow"};

/** Computes one case with the C functions and prints its result, or
 * "skip" for an operation only the C++ class has.
 *
 * @param op   The operation's name, as bnum names it.
 * @param arg  Its operands, null-terminated: decimal integers of the
 *             current type, or a shift, power or radix. */
static void run(const char* op, char** arg) {
        num a, b, r, t;
        unsigned k = 0;
        bool ok;
#define IS(name) (!strcmp(op, name))
        if (IS("from_str_radix")) {
                const char* text = arg[1] ? arg[1] : "";
                int rc = xtce_bn_from_str_radix(r.b, n, s, text, strlen(text),
                                                (unsigned)atoi(arg[0]));
                if (rc)
                        puts(kinds[rc]);
                else
                        value(&r);
                return;
        }
        if (IS("from_f64")) {
                uint64_t bits = strtoull(arg[0], NULL, 16);
                double d;
                memcpy(&d, &bits, sizeof d);
                xtce_bn_from_f64(r.b, n, s, d);
                value(&r);
                return;
        }
        if (IS("from_le_slice") || IS("from_be_slice")) {
                uint8_t bytes[4 * MAXN];
                size_t len = unhex(arg[0], bytes);
                checked(&r, IS("from_le_slice")
                                    ? xtce_bn_from_le_slice(r.b, n, s, bytes,
                                                            len)
                                    : xtce_bn_from_be_slice(r.b, n, s, bytes,
                                                            len));
                return;
        }
        a = parse(arg[0]);
        if (arg[1] && arg[1][0] != 'u' && arg[1][0] != 'i') {
                /* A shift, power or radix, or another integer. */
                k = (unsigned)strtoul(arg[1], NULL, 10);
                if (xtce_bn_from_str_radix(b.b, n, s, arg[1], strlen(arg[1]),
                                           10))
                        xtce_bn_zero(b.b, n);
        }
        if (IS("wrapping_add")) {
                xtce_bn_wrapping_add(r.b, a.b, b.b, n);
                value(&r);
        } else if (IS("wrapping_sub")) {
                xtce_bn_wrapping_sub(r.b, a.b, b.b, n);
                value(&r);
        } else if (IS("wrapping_mul")) {
                xtce_bn_wrapping_mul(r.b, a.b, b.b, n, s);
                value(&r);
        } else if (IS("wrapping_div") || IS("overflowing_div")) {
                bool o = xtce_bn_overflowing_div(r.b, a.b, b.b, n, s, t.b,
                                                 &ok);
                if (IS("wrapping_div"))
                        value(&r);
                else
                        overflowing(&r, o);
        } else if (IS("wrapping_rem") || IS("overflowing_rem")) {
                bool o = xtce_bn_overflowing_rem(r.b, a.b, b.b, n, s, t.b,
                                                 &ok);
                if (IS("wrapping_rem"))
                        value(&r);
                else
                        overflowing(&r, o);
        } else if (IS("wrapping_div_euclid")) {
                bool o;
                xtce_bn_div_rem_euclid_(r.b, t.b, a.b, b.b, n, s, &o);
                value(&r);
        } else if (IS("wrapping_rem_euclid")) {
                bool o;
                xtce_bn_div_rem_euclid_(t.b, r.b, a.b, b.b, n, s, &o);
                value(&r);
        } else if (IS("wrapping_neg")) {
                xtce_bn_wrapping_neg(r.b, a.b, n);
                value(&r);
        } else if (IS("wrapping_shl")) {
                xtce_bn_wrapping_shl(r.b, a.b, n, k);
                value(&r);
        } else if (IS("wrapping_shr")) {
                xtce_bn_wrapping_shr(r.b, a.b, n, s, k);
                value(&r);
        } else if (IS("overflowing_add")) {
                overflowing(&r, xtce_bn_overflowing_add(r.b, a.b, b.b, n, s));
        } else if (IS("overflowing_sub")) {
                overflowing(&r, xtce_bn_overflowing_sub(r.b, a.b, b.b, n, s));
        } else if (IS("overflowing_mul")) {
                overflowing(&r, xtce_bn_overflowing_mul(r.b, a.b, b.b, n, s));
        } else if (IS("overflowing_neg")) {
                overflowing(&r, xtce_bn_overflowing_neg(r.b, a.b, n, s));
        } else if (IS("overflowing_abs")) {
                overflowing(&r, xtce_bn_overflowing_abs(r.b, a.b, n, s));
        } else if (IS("overflowing_shl")) {
                overflowing(&r, xtce_bn_overflowing_shl(r.b, a.b, n, k));
        } else if (IS("overflowing_shr")) {
                overflowing(&r, xtce_bn_overflowing_shr(r.b, a.b, n, s, k));
        } else if (IS("checked_add")) {
                checked(&r, xtce_bn_checked_add(r.b, a.b, b.b, n, s));
        } else if (IS("checked_sub")) {
                checked(&r, xtce_bn_checked_sub(r.b, a.b, b.b, n, s));
        } else if (IS("checked_mul")) {
                checked(&r, xtce_bn_checked_mul(r.b, a.b, b.b, n, s));
        } else if (IS("checked_div")) {
                checked(&r, xtce_bn_checked_div(r.b, a.b, b.b, n, s, t.b));
        } else if (IS("checked_rem")) {
                checked(&r, xtce_bn_checked_rem(r.b, a.b, b.b, n, s, t.b));
        } else if (IS("checked_div_euclid")) {
                checked(&r,
                        xtce_bn_checked_div_euclid(r.b, a.b, b.b, n, s, t.b));
        } else if (IS("checked_rem_euclid")) {
                checked(&r,
                        xtce_bn_checked_rem_euclid(r.b, a.b, b.b, n, s, t.b));
        } else if (IS("checked_neg")) {
                checked(&r, xtce_bn_checked_neg(r.b, a.b, n, s));
        } else if (IS("checked_abs")) {
                checked(&r, xtce_bn_checked_abs(r.b, a.b, n, s));
        } else if (IS("checked_shl")) {
                checked(&r, xtce_bn_checked_shl(r.b, a.b, n, k));
        } else if (IS("checked_shr")) {
                checked(&r, xtce_bn_checked_shr(r.b, a.b, n, s, k));
        } else if (IS("saturating_add")) {
                xtce_bn_saturating_add(r.b, a.b, b.b, n, s);
                value(&r);
        } else if (IS("saturating_sub")) {
                xtce_bn_saturating_sub(r.b, a.b, b.b, n, s);
                value(&r);
        } else if (IS("saturating_mul")) {
                xtce_bn_saturating_mul(r.b, a.b, b.b, n, s);
                value(&r);
        } else if (IS("saturating_neg")) {
                xtce_bn_saturating_neg(r.b, a.b, n, s);
                value(&r);
        } else if (IS("saturating_abs")) {
                xtce_bn_saturating_abs(r.b, a.b, n, s);
                value(&r);
        } else if (IS("wrapping_abs")) {
                xtce_bn_wrapping_abs(r.b, a.b, n, s);
                value(&r);
        } else if (IS("unsigned_abs")) {
                xtce_bn_unsigned_abs(r.b, a.b, n, s);
                put(r.b, n, false);
                putchar('\n');
        } else if (IS("abs_diff")) {
                xtce_bn_abs_diff(r.b, a.b, b.b, n, s);
                put(r.b, n, false);
                putchar('\n');
        } else if (IS("unbounded_shl")) {
                xtce_bn_unbounded_shl(r.b, a.b, n, k);
                value(&r);
        } else if (IS("unbounded_shr")) {
                xtce_bn_unbounded_shr(r.b, a.b, n, s, k);
                value(&r);
        } else if (IS("rotate_left")) {
                xtce_bn_rotate_left(r.b, a.b, n, k);
                value(&r);
        } else if (IS("rotate_right")) {
                xtce_bn_rotate_right(r.b, a.b, n, k);
                value(&r);
        } else if (IS("bitand")) {
                xtce_bn_bitand(r.b, a.b, b.b, n);
                value(&r);
        } else if (IS("bitor")) {
                xtce_bn_bitor(r.b, a.b, b.b, n);
                value(&r);
        } else if (IS("bitxor")) {
                xtce_bn_bitxor(r.b, a.b, b.b, n);
                value(&r);
        } else if (IS("not")) {
                xtce_bn_not(r.b, a.b, n);
                value(&r);
        } else if (IS("cmp")) {
                printf("%d\n", xtce_bn_cmp(a.b, b.b, n, s));
        } else if (IS("min")) {
                xtce_bn_min_of(r.b, a.b, b.b, n, s);
                value(&r);
        } else if (IS("max")) {
                xtce_bn_max_of(r.b, a.b, b.b, n, s);
                value(&r);
        } else if (IS("count_ones")) {
                printf("%u\n", xtce_bn_count_ones(a.b, n));
        } else if (IS("count_zeros")) {
                printf("%u\n", xtce_bn_count_zeros(a.b, n));
        } else if (IS("leading_zeros")) {
                printf("%u\n", xtce_bn_leading_zeros(a.b, n));
        } else if (IS("leading_ones")) {
                printf("%u\n", xtce_bn_leading_ones(a.b, n));
        } else if (IS("trailing_zeros")) {
                printf("%u\n", xtce_bn_trailing_zeros(a.b, n));
        } else if (IS("trailing_ones")) {
                printf("%u\n", xtce_bn_trailing_ones(a.b, n));
        } else if (IS("bit_width")) {
                printf("%u\n", xtce_bn_bit_width(a.b, n));
        } else if (IS("is_power_of_two")) {
                puts(xtce_bn_is_power_of_two(a.b, n) ? "true" : "false");
        } else if (IS("signum")) {
                printf("%d\n", xtce_bn_signum(a.b, n, s));
        } else if (IS("swap_bytes")) {
                xtce_bn_swap_bytes(r.b, a.b, n);
                value(&r);
        } else if (IS("reverse_bits")) {
                xtce_bn_reverse_bits(r.b, a.b, n);
                value(&r);
        } else if (IS("to_be_bytes")) {
                xtce_bn_to_be_bytes(r.b, a.b, n);
                hex(r.b, n);
        } else if (IS("to_str_radix")) {
                char buf[8 * MAXN + 2];
                xtce_bn_to_str_radix(a.b, n, s, k, t.b, buf, sizeof buf);
                puts(buf);
        } else if (IS("to_f64")) {
                double d = xtce_bn_to_f64(a.b, n, s);
                uint64_t bits;
                memcpy(&bits, &d, sizeof bits);
                printf("%016llx\n", (unsigned long long)bits);
        } else if (IS("cast")) {
                size_t n2;
                bool s2;
                type_of(arg[1], &n2, &s2);
                xtce_bn_cast(r.b, n2, a.b, n, s);
                put(r.b, n2, s2);
                putchar('\n');
        } else {
                /* The rest are in the C++ class, not the C functions. */
                puts("skip");
        }
#undef IS
}

/** Runs the cases on standard input, one per line ("op type operands"),
 * printing each result on standard output.
 *
 * @return  0. */
int main(void) {
        char line[1024];
        while (fgets(line, sizeof line, stdin)) {
                char* w[8] = {0};
                int c = 0;
                char* tok = strtok(line, " \t\n");
                while (tok && c < 7) {
                        w[c++] = tok;
                        tok = strtok(NULL, " \t\n");
                }
                if (c < 2) continue;
                type_of(w[1], &n, &s);
                run(w[0], w + 2);
        }
        return 0;
}
