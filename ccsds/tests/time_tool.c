/* Decodes and encodes CCSDS time codes as tests/ccsds_check.sh asks, one case
 * per line of standard input; ccsds/rust/examples/time_tool.rs describes the
 * cases.  Built as C and as C++. */
#include <ccsds-time.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>

/** Converts a string of hexadecimal digit pairs to octets.
 *
 * @param s    The digits, two per octet.
 * @param out  Filled in with the octets; must hold strlen(s) / 2.
 * @return     How many octets were written. */
static size_t unhex(const char* s, uint8_t* out) {
        size_t n = strlen(s) / 2, i;
        for (i = 0; i < n; ++i) {
                unsigned x;
                sscanf(s + 2 * i, "%2x", &x);
                out[i] = (uint8_t)x;
        }
        return n;
}

/** Prints octets in hexadecimal on a line, or "x" if there are none.
 *
 * @param b  The octets.
 * @param n  How many there are. */
static void hex(const uint8_t* b, size_t n) {
        size_t i;
        if (!n) fputs("x", stdout);
        for (i = 0; i < n; ++i) printf("%02x", b[i]);
        putchar('\n');
}

/** Prints the result of a decoding: the octets read and the seconds as
 * the hexadecimal of their IEEE bits, or "none" if it failed.  Called
 * after the decoding: C doesn't say in what order a call's arguments are
 * worked out.
 *
 * @param ok  Whether the decoding succeeded.
 * @param k   The octets it read.
 * @param s   The seconds it gave. */
static void decoded(bool ok, const size_t* k, const double* s) {
        uint64_t bits;
        if (!ok) {
                puts("none");
                return;
        }
        memcpy(&bits, s, sizeof bits);
        printf("%zu %016" PRIx64 "\n", *k, bits);
}

/** Prints the result of an encoding: "none" if the time doesn't fit the
 * layout, "needs N" if the output was too small, else the octets.
 *
 * @param r    What the encoding function returned.
 * @param out  The octets it wrote.
 * @param cap  The size of the output it was given. */
static void encoded(size_t r, const uint8_t* out, size_t cap) {
        if (r == CCSDS_NONE)
                puts("none");
        else if (r > cap)
                printf("needs %zu\n", r);
        else
                hex(out, r);
}

/** Reads seconds given as the hexadecimal of a double's IEEE bits.
 *
 * @param s  The hexadecimal digits.
 * @return   The double. */
static double secs(const char* s) {
        uint64_t bits = strtoull(s, NULL, 16);
        double d;
        memcpy(&d, &bits, sizeof d);
        return d;
}

/** Prints the bits of a double in hexadecimal.
 *
 * @param d  The double. */
static void bits(double d) {
        uint64_t b;
        memcpy(&b, &d, sizeof b);
        printf("%016llx\n", (unsigned long long)b);
}

/** Reads a CUC time: its seconds in hexadecimal, then its fraction in 20
 * hexadecimal digits.
 *
 * @param w  The two words.
 * @return   The time. */
static ccsds_cuc_time cuc_time(char** w) {
        ccsds_cuc_time t;
        t.seconds = strtoull(w[0], NULL, 16);
        memset(t.fraction, 0, sizeof t.fraction);
        unhex(w[1], t.fraction);
        return t;
}

/** Reads a CDS time: its day, milliseconds and picoseconds in decimal.
 *
 * @param w  The three words.
 * @return   The time. */
static ccsds_cds_time cds_time(char** w) {
        ccsds_cds_time t;
        t.day = (uint32_t)strtoul(w[0], NULL, 10);
        t.ms = (uint32_t)strtoul(w[1], NULL, 10);
        t.picos = (uint32_t)strtoul(w[2], NULL, 10);
        return t;
}

/** Fills in a CDS layout from its DAY24, SUB and EPOCH words.
 *
 * @param w  The three words.
 * @return   The layout. */
static ccsds_cds cds_layout(char** w) {
        ccsds_cds cd;
        cd.day24 = w[0][0] == '1';
        cd.sub = (ccsds_sub_ms)atoi(w[1]);
        cd.epoch = w[2][0] == '1' ? CCSDS_EPOCH_AGENCY : CCSDS_EPOCH_CCSDS;
        return cd;
}

/** Reads a calendar time's seven fields from words.
 *
 * @param w  The words: year, month, day, hour, minute, second, picoseconds.
 * @return   The time. */
static ccsds_calendar_time cal(char** w) {
        ccsds_calendar_time t;
        t.year = (uint16_t)strtoul(w[0], NULL, 10);
        t.month = (uint8_t)strtoul(w[1], NULL, 10);
        t.day = (uint8_t)strtoul(w[2], NULL, 10);
        t.hour = (uint8_t)strtoul(w[3], NULL, 10);
        t.minute = (uint8_t)strtoul(w[4], NULL, 10);
        t.second = (uint8_t)strtoul(w[5], NULL, 10);
        t.picos = strtoull(w[6], NULL, 10);
        return t;
}

/** Prints a calendar time's seven fields, after a prefix.
 *
 * @param t  The time. */
static void show(const ccsds_calendar_time* t) {
        printf("%u %u %u %u %u %u %llu\n", t->year, t->month, t->day,
               t->hour, t->minute, t->second, (unsigned long long)t->picos);
}

static ccsds_epoch epoch(const char* s) {
        return s[0] == '1' ? CCSDS_EPOCH_AGENCY : CCSDS_EPOCH_CCSDS;
}

/** Runs the cases on standard input, one per line, printing each result
 * on standard output.
 *
 * @return  0. */
int main(void) {
        char line[1024];
        static uint8_t data[512], out[512];
        while (fgets(line, sizeof line, stdin)) {
                char* w[14] = {0};
                int c = 0;
                char* t = strtok(line, " \t\n");
                size_t k = 0, n;
                bool ok;
                double s = 0.0;
                while (t && c < 14) {
                        w[c++] = t;
                        t = strtok(NULL, " \t\n");
                }
                if (!c) continue;
                if (!strcmp(w[0], "ccs_decode")) {
                        ccsds_calendar_time ct;
                        n = unhex(w[1], data);
                        if (!ccsds_decode_ccs(data, n, &k, &ct)) {
                                puts("none");
                                continue;
                        }
                        printf("%zu ", k);
                        show(&ct);
                } else if (!strcmp(w[0], "ccs_encode")) {
                        ccsds_ccs cc;
                        ccsds_calendar_time ct = cal(w + 3);
                        size_t cap = (size_t)atoi(w[11]), r;
                        cc.day_of_year = w[1][0] == '1';
                        cc.sub = (uint8_t)atoi(w[2]);
                        r = w[10][0] == '1'
                                    ? ccsds_ccs_encode(&cc, &ct, out, cap)
                                    : ccsds_ccs_encode_t(&cc, &ct, out, cap);
                        encoded(r, out, cap);
                } else if (!strcmp(w[0], "ascii_parse")) {
                        ccsds_calendar_time ct;
                        ccsds_ascii_form f;
                        if (!ccsds_parse_ascii(w[1], strlen(w[1]), &k, &f,
                                               &ct)) {
                                puts("none");
                                continue;
                        }
                        printf("%zu %c ", k, f == CCSDS_ASCII_A ? 'A' : 'B');
                        show(&ct);
                } else if (!strcmp(w[0], "ascii_format")) {
                        ccsds_calendar_time ct = cal(w + 4);
                        size_t cap = (size_t)atoi(w[11]), r;
                        char text[64];
                        r = ccsds_format_ascii(
                                &ct,
                                w[1][0] == 'A' ? CCSDS_ASCII_A : CCSDS_ASCII_B,
                                (unsigned)atoi(w[2]), w[3][0] == '1', text,
                                cap < sizeof text ? cap : sizeof text);
                        if (r == CCSDS_NONE)
                                puts("none");
                        else if (r > cap)
                                printf("needs %zu\n", r);
                        else
                                printf("%.*s\n", (int)r, text);
                } else if (!strcmp(w[0], "utc_to_tai")) {
                        ccsds_calendar_time ct = cal(w + 1);
                        ccsds_tai_time tt;
                        if (!ccsds_utc_to_tai(&ct, &ccsds_leap_seconds_table,
                                              &tt))
                                puts("none");
                        else
                                printf("%llu %llu\n",
                                       (unsigned long long)tt.seconds,
                                       (unsigned long long)tt.picos);
                } else if (!strcmp(w[0], "tai_to_utc")) {
                        ccsds_calendar_time ct;
                        ccsds_tai_time tt;
                        tt.seconds = strtoull(w[1], NULL, 10);
                        tt.picos = strtoull(w[2], NULL, 10);
                        if (!ccsds_tai_to_utc(&tt, &ccsds_leap_seconds_table,
                                              &ct))
                                puts("none");
                        else
                                show(&ct);
                } else if (!strcmp(w[0], "cuc_decode_x")) {
                        ccsds_cuc_time ct;
                        n = unhex(w[1], data);
                        if (!ccsds_decode_cuc_exact(data, n, &k, &ct)) {
                                puts("none");
                                continue;
                        }
                        printf("%zu %016llx ", k,
                               (unsigned long long)ct.seconds);
                        hex(ct.fraction, sizeof ct.fraction);
                } else if (!strcmp(w[0], "cds_decode_x")) {
                        ccsds_cds_time dt;
                        n = unhex(w[1], data);
                        if (!ccsds_decode_cds_exact(data, n, &k, &dt)) {
                                puts("none");
                                continue;
                        }
                        printf("%zu %lu %lu %lu\n", k, (unsigned long)dt.day,
                               (unsigned long)dt.ms, (unsigned long)dt.picos);
                } else if (!strcmp(w[0], "cuc_seconds")) {
                        ccsds_cuc_time ct = cuc_time(w + 1);
                        bits(ccsds_cuc_time_seconds(&ct));
                } else if (!strcmp(w[0], "cds_seconds")) {
                        ccsds_cds_time dt = cds_time(w + 1);
                        bits(ccsds_cds_time_seconds(&dt));
                } else if (!strcmp(w[0], "cuc_encode_x")) {
                        ccsds_cuc cu;
                        ccsds_cuc_time ct = cuc_time(w + 4);
                        size_t cap = (size_t)atoi(w[7]), r;
                        if (!ccsds_cuc_init(&cu, (unsigned)atoi(w[1]),
                                            (unsigned)atoi(w[2]),
                                            epoch(w[3]))) {
                                puts("bad layout");
                                continue;
                        }
                        r = w[6][0] == '1'
                                    ? ccsds_cuc_encode_exact(&cu, &ct, out, cap)
                                    : ccsds_cuc_encode_t_exact(&cu, &ct, out,
                                                               cap);
                        encoded(r, out, cap);
                } else if (!strcmp(w[0], "cds_encode_x")) {
                        ccsds_cds cd = cds_layout(w + 1);
                        ccsds_cds_time dt = cds_time(w + 4);
                        size_t cap = (size_t)atoi(w[8]), r;
                        r = w[7][0] == '1'
                                    ? ccsds_cds_encode_exact(&cd, &dt, out, cap)
                                    : ccsds_cds_encode_t_exact(&cd, &dt, out,
                                                               cap);
                        encoded(r, out, cap);
                } else if (!strcmp(w[0], "cuc_decode")) {
                        n = unhex(w[1], data);
                        ok = ccsds_decode_cuc(data, n, &k, &s);
                        decoded(ok, &k, &s);
                } else if (!strcmp(w[0], "cds_decode")) {
                        n = unhex(w[1], data);
                        ok = ccsds_decode_cds(data, n, &k, &s);
                        decoded(ok, &k, &s);
                } else if (!strcmp(w[0], "cuc_decode_t")) {
                        ccsds_cuc cu;
                        if (!ccsds_cuc_init(&cu, (unsigned)atoi(w[1]),
                                            (unsigned)atoi(w[2]),
                                            epoch(w[3]))) {
                                puts("bad layout");
                                continue;
                        }
                        n = unhex(w[4], data);
                        ok = ccsds_cuc_decode_t(&cu, data, n, &k, &s);
                        decoded(ok, &k, &s);
                } else if (!strcmp(w[0], "cds_decode_t")) {
                        ccsds_cds cd;
                        cd.day24 = w[1][0] == '1';
                        cd.sub = (ccsds_sub_ms)atoi(w[2]);
                        cd.epoch = epoch(w[3]);
                        n = unhex(w[4], data);
                        ok = ccsds_cds_decode_t(&cd, data, n, &k, &s);
                        decoded(ok, &k, &s);
                } else {
                        size_t cap = (size_t)atoi(w[6]), r;
                        bool p = w[5][0] == '1';
                        s = secs(w[4]);
                        if (!strcmp(w[0], "cuc_encode")) {
                                ccsds_cuc cu;
                                if (!ccsds_cuc_init(&cu, (unsigned)atoi(w[1]),
                                                    (unsigned)atoi(w[2]),
                                                    epoch(w[3]))) {
                                        puts("bad layout");
                                        continue;
                                }
                                r = p ? ccsds_cuc_encode(&cu, s, out, cap)
                                      : ccsds_cuc_encode_t(&cu, s, out, cap);
                        } else {
                                ccsds_cds cd;
                                cd.day24 = w[1][0] == '1';
                                cd.sub = (ccsds_sub_ms)atoi(w[2]);
                                cd.epoch = epoch(w[3]);
                                r = p ? ccsds_cds_encode(&cd, s, out, cap)
                                      : ccsds_cds_encode_t(&cd, s, out, cap);
                        }
                        encoded(r, out, cap);
                }
        }
        return 0;
}
