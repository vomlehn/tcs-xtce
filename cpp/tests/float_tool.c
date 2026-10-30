/* Converts floats as tests/float_check.sh asks, one case per line of standard
 * input: "d FORMAT BITS RAW" decodes the raw value (hex) and prints the
 * double's bits, and "e FORMAT BITS DOUBLE" encodes the double (its bits in
 * hex) and prints the raw value; an error prints its kind.  "m OP A B" applies
 * a MathOperationCalibrator operator to two doubles (their bits in hex) and
 * prints the result's bits.  "k KIND BITS POLY INIT XOR REFLECT_DATA
 * REFLECT_REMAINDER ODD DATA START END" computes an ErrorDetectCorrect
 * checksum (numbers in hex, flags 0 or 1) of the bits from START to END of
 * DATA.  Built as C and as C++. */
#include <xtce-packet.h>

/** Maps a FloatDataEncoding format name to its runtime code.
 *
 * @param name  The name, such as "IEEE754" or "MILSTD_1750A".
 * @return      Its XTCE_PK_* format code; XTCE_PK_TI for any other name. */
static int format(const char* name) {
        if (!strcmp(name, "IEEE754")) return XTCE_PK_IEEE;
        if (!strcmp(name, "MILSTD_1750A")) return XTCE_PK_MIL1750A;
        if (!strcmp(name, "DEC")) return XTCE_PK_DEC;
        if (!strcmp(name, "IBM")) return XTCE_PK_IBM;
        return XTCE_PK_TI;
}

static const char* const operators[] = {
        "/", "%", "^", "y^x", "ln", "log", "e^x", "1/x", "x!", "tan", "cos",
        "sin", "atan", "atan2", "acos", "asin", "tanh", "cosh", "sinh",
        "atanh", "acosh", "asinh", "<<", ">>", "&", "|", "xor", "~", "&&",
        "||", "!", "abs", "div", "int", ">", ">=", "<", "<=", "==", "!=",
        "min", "max"};

/** Maps a MathOperationCalibrator operator to its runtime code.
 *
 * @param name  The operator as XTCE spells it, such as "y^x" or "atan2".
 * @return      Its XTCE_PK_OP_* code, or -1 if it isn't one. */
static int operator_code(const char* name) {
        static const int codes[] = {
                XTCE_PK_OP_DIV,
                XTCE_PK_OP_MOD,
                XTCE_PK_OP_POW,
                XTCE_PK_OP_REV_POW,
                XTCE_PK_OP_LN,
                XTCE_PK_OP_LOG10,
                XTCE_PK_OP_EXP,
                XTCE_PK_OP_INV,
                XTCE_PK_OP_FACTORIAL,
                XTCE_PK_OP_TAN,
                XTCE_PK_OP_COS,
                XTCE_PK_OP_SIN,
                XTCE_PK_OP_ATAN,
                XTCE_PK_OP_ATAN2,
                XTCE_PK_OP_ACOS,
                XTCE_PK_OP_ASIN,
                XTCE_PK_OP_TANH,
                XTCE_PK_OP_COSH,
                XTCE_PK_OP_SINH,
                XTCE_PK_OP_ATANH,
                XTCE_PK_OP_ACOSH,
                XTCE_PK_OP_ASINH,
                XTCE_PK_OP_SHL,
                XTCE_PK_OP_SHR,
                XTCE_PK_OP_BIT_AND,
                XTCE_PK_OP_BIT_OR,
                XTCE_PK_OP_BIT_XOR,
                XTCE_PK_OP_BIT_NOT,
                XTCE_PK_OP_AND,
                XTCE_PK_OP_OR,
                XTCE_PK_OP_NOT,
                XTCE_PK_OP_ABS,
                XTCE_PK_OP_EUCLID_DIV,
                XTCE_PK_OP_TRUNC,
                XTCE_PK_OP_GT,
                XTCE_PK_OP_GE,
                XTCE_PK_OP_LT,
                XTCE_PK_OP_LE,
                XTCE_PK_OP_EQ,
                XTCE_PK_OP_NE,
                XTCE_PK_OP_MIN,
                XTCE_PK_OP_MAX};
        size_t i;
        for (i = 0; i < sizeof codes / sizeof codes[0]; ++i)
                if (!strcmp(name, operators[i])) return codes[i];
        return -1;
}

/** Parses up to 32 lowercase hexadecimal digits into a 128-bit value.
 *
 * @param s   The digits.
 * @param hi  Filled in with the value's high 64 bits.
 * @param lo  Filled in with its low 64 bits. */
static void parse_hex(const char* s, uint64_t* hi, uint64_t* lo) {
        size_t n = strlen(s), i;
        *hi = *lo = 0;
        for (i = 0; i < n; ++i) {
                unsigned d = s[i] <= '9' ? (unsigned)(s[i] - '0')
                                         : (unsigned)(s[i] - 'a' + 10);
                *hi = (*hi << 4) | (*lo >> 60);
                *lo = (*lo << 4) | d;
        }
}

/** Prints a 128-bit value in hexadecimal, without leading zeros, on a
 * line.
 *
 * @param hi  The value's high 64 bits.
 * @param lo  Its low 64 bits. */
static void print_hex(uint64_t hi, uint64_t lo) {
        if (hi)
                printf("%llx%016llx\n", (unsigned long long)hi,
                       (unsigned long long)lo);
        else
                printf("%llx\n", (unsigned long long)lo);
}

/** Runs the cases on standard input, printing each result on standard
 * output.
 *
 * @return  0, or 1 if a case's operands can't be read. */
int main(void) {
        char op[4], fmt[16], raw[40];
        unsigned bits;
        while (scanf("%3s %15s", op, fmt) == 2) {
                xtce_pk_error err = XTCE_PK_ZERO;
                uint64_t hi, lo;
                int rc;
                if (op[0] == 'k') {
                        static const char* const kinds[] = {
                                "crc",  "sum",      "unix_sum", "fletcher",
                                "adler32", "luhn",  "verhoeff", "damm",
                                "xor",  "parity"};
                        char sb[16], sp[24], si[24], sx[24], data[256];
                        char ss[24], se[24];
                        int ri, ro, od, k;
                        xtce_pk_checksum_spec spec = XTCE_PK_ZERO;
                        uint64_t v;
                        if (scanf("%15s %23s %23s %23s %d %d %d %255s "
                                  "%23s %23s",
                                  sb, sp, si, sx, &ri, &ro, &od, data, ss,
                                  se) != 10)
                                return 1;
                        for (k = 0; k < 10; ++k)
                                if (!strcmp(fmt, kinds[k])) spec.kind = k;
                        parse_hex(sb, &hi, &lo);
                        spec.bits = (unsigned)lo;
                        parse_hex(sp, &hi, &spec.poly);
                        parse_hex(si, &hi, &spec.init);
                        parse_hex(sx, &hi, &spec.final_xor);
                        spec.reflect_data = ri;
                        spec.reflect_remainder = ro;
                        spec.odd = od;
                        {
                                uint8_t bytes[128];
                                size_t nb = strlen(data) / 2, i;
                                uint64_t start, end;
                                for (i = 0; i < nb; ++i) {
                                        char two[3] = {data[2 * i],
                                                       data[2 * i + 1], 0};
                                        parse_hex(two, &hi, &lo);
                                        bytes[i] = (uint8_t)lo;
                                }
                                parse_hex(ss, &hi, &start);
                                parse_hex(se, &hi, &end);
                                rc = xtce_pk_checksum(&spec, bytes, start, end,
                                                      "x", &err, &v);
                        }
                        if (rc)
                                printf("error:%s\n", xtce_pk_error_kind(rc));
                        else
                                print_hex(0, v);
                        continue;
                }
                if (op[0] == 'm') {
                        char sa[40], sb[40];
                        double a, b, x;
                        uint64_t u;
                        if (scanf("%39s %39s", sa, sb) != 2) return 1;
                        parse_hex(sa, &hi, &lo);
                        memcpy(&a, &lo, 8);
                        parse_hex(sb, &hi, &lo);
                        memcpy(&b, &lo, 8);
                        x = xtce_pk_math(operator_code(fmt), a, b);
                        memcpy(&u, &x, 8);
                        if (isnan(x)) u = 0x7ff8000000000000ull;
                        print_hex(0, u);
                        continue;
                }
                if (scanf("%u %39s", &bits, raw) != 2) return 1;
                parse_hex(raw, &hi, &lo);
                if (op[0] == 'd') {
                        double x;
                        rc = xtce_pk_float_decode(format(fmt), bits, hi, lo,
                                                  "x", &err, &x);
                        if (!rc) {
                                uint64_t b;
                                if (isnan(x)) x = NAN;
                                memcpy(&b, &x, 8);
                                if (isnan(x)) b = 0x7ff8000000000000ull;
                                print_hex(0, b);
                        }
                } else {
                        double x;
                        memcpy(&x, &lo, 8);
                        rc = xtce_pk_float_encode(format(fmt), bits, x, "x",
                                                  &err, &hi, &lo);
                        if (!rc) print_hex(hi, lo);
                }
                if (rc) printf("error:%s\n", xtce_pk_error_kind(rc));
        }
        return 0;
}
