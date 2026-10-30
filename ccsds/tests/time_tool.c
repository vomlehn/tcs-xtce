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
                char* w[8] = {0};
                int c = 0;
                char* t = strtok(line, " \t\n");
                size_t k = 0, n;
                bool ok;
                double s = 0.0;
                while (t && c < 8) {
                        w[c++] = t;
                        t = strtok(NULL, " \t\n");
                }
                if (!c) continue;
                if (!strcmp(w[0], "cuc_decode")) {
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
