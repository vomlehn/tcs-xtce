/* Converts floats as tests/float_check.sh asks, one case per line of standard
 * input: "d FORMAT BITS RAW" decodes the raw value (hex) and prints the
 * double's bits, and "e FORMAT BITS DOUBLE" encodes the double (its bits in
 * hex) and prints the raw value; an error prints its kind.  Built as C and as
 * C++. */
#include <xtce/packet.h>

static int format(const char* name) {
        if (!strcmp(name, "IEEE754")) return XTCE_PK_IEEE;
        if (!strcmp(name, "MILSTD_1750A")) return XTCE_PK_MIL1750A;
        if (!strcmp(name, "DEC")) return XTCE_PK_DEC;
        if (!strcmp(name, "IBM")) return XTCE_PK_IBM;
        return XTCE_PK_TI;
}

/* Parses up to 32 hex digits into (hi, lo). */
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

static void print_hex(uint64_t hi, uint64_t lo) {
        if (hi)
                printf("%llx%016llx\n", (unsigned long long)hi,
                       (unsigned long long)lo);
        else
                printf("%llx\n", (unsigned long long)lo);
}

int main(void) {
        char op[4], fmt[16], raw[40];
        unsigned bits;
        while (scanf("%3s %15s %u %39s", op, fmt, &bits, raw) == 4) {
                xtce_pk_error err = XTCE_PK_ZERO;
                uint64_t hi, lo;
                int rc;
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
