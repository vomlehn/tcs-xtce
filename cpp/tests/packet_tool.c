/* Decodes and re-encodes the packet vectors in tests/mission/vectors.txt.
 *
 * For each vector prints `<description of the decoded packet> | <hex of the
 * re-encoded packet>`, with `error:<kind>` in place of either on failure.  The
 * Rust and Python versions of this tool print identical output.  The Makefile
 * builds this file both as C and as C++.
 *
 * Usage: packet_tool VECTORS
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DEMO_HOOKS
#include "mission/demo.h"

/* Buffers for the largest packet and description, allocated once. */
static uint8_t input[4096];
/* Room for any packet, a transformed one's work space included. */
static uint8_t output[demo_CCSDSPacketKind_MAX_BYTES >
                                      demo_CCSDSCommandKind_MAX_BYTES
                              ? demo_CCSDSPacketKind_MAX_BYTES
                              : demo_CCSDSCommandKind_MAX_BYTES];
static char text[8192];

/* The demo mission's custom algorithms, the same in every version of this
 * tool. */
static bool is_even(xtce_pk_inputs in) {
        return fmod(in.v[0], 2.0) == 0.0;
}

/** The MinMax algorithm: the smallest and largest of its inputs.
 *
 * @param in   The inputs.
 * @param out  Filled in with the smallest, then the largest; HUGE_VAL and
 *             -HUGE_VAL when there are no inputs.
 * @return     True: it always succeeds. */
static bool min_max(xtce_pk_inputs in, double* out) {
        size_t i;
        out[0] = HUGE_VAL;
        out[1] = -HUGE_VAL;
        for (i = 0; i < in.n; ++i) {
                if (in.v[i] < out[0]) out[0] = in.v[i];
                if (in.v[i] > out[1]) out[1] = in.v[i];
        }
        return true;
}

static int bands(xtce_pk_inputs in) {
        return (int)(in.v[0] / 50.0);
}

/** The Twice checksum algorithm: twice the sum of the bytes checked.
 *
 * @param data   The packet.
 * @param start  The first bit checked; only whole bytes count.
 * @param end    The bit after the last one checked.
 * @return       Twice the sum of the bytes from start / 8 to end / 8. */
static uint64_t twice(const uint8_t* data, size_t start, size_t end) {
        uint64_t sum = 0;
        size_t i;
        for (i = start / 8; i < end / 8; ++i) sum += data[i];
        return 2 * sum;
}

/* The demo's transforms: Expand doubles every byte, Shrink takes every
 * other one (and gives nothing when a pair differs); Mask and Unmask are XOR
 * 0x5A. */

/** The Expand transform: doubles every byte.
 *
 * @param in   The bytes to transform.
 * @param n    How many there are.
 * @param out  Where the result goes.
 * @param cap  How many bytes `out` holds.
 * @return     The result's length, 2 * n; more than `cap`, writing
 *             nothing, if it doesn't fit. */
static size_t expand(const uint8_t* in, size_t n, uint8_t* out, size_t cap) {
        size_t i;
        if (2 * n > cap) return 2 * n; /* the room it needs */
        for (i = 0; i < n; ++i) out[2 * i] = out[2 * i + 1] = in[i];
        return 2 * n;
}

/** The Shrink transform, Expand's inverse: takes every other byte.
 *
 * @param in        The bytes to transform.
 * @param n         How many there are; all are used.
 * @param consumed  Filled in with how many bytes of `in` were used.
 * @param out       Where the result goes.
 * @param cap       How many bytes `out` holds.
 * @return          The result's length, n / 2; more than `cap`, writing
 *                  nothing, if it doesn't fit; XTCE_PK_NO_TRANSFORM if `n`
 *                  is odd or a pair of bytes differs. */
static size_t shrink(const uint8_t* in, size_t n, size_t* consumed,
                     uint8_t* out, size_t cap) {
        size_t i;
        if (n % 2) return XTCE_PK_NO_TRANSFORM;
        for (i = 0; i < n / 2; ++i)
                if (in[2 * i] != in[2 * i + 1]) return XTCE_PK_NO_TRANSFORM;
        *consumed = n;
        if (n / 2 > cap) return n / 2; /* the room it needs */
        for (i = 0; i < n / 2; ++i) out[i] = in[2 * i];
        return n / 2;
}

/** The Mask transform: XORs every byte with 0x5A.
 *
 * @param in   The bytes to transform.
 * @param n    How many there are.
 * @param out  Where the result goes.
 * @param cap  How many bytes `out` holds.
 * @return     The result's length, `n`; more than `cap`, writing nothing,
 *             if it doesn't fit. */
static size_t mask(const uint8_t* in, size_t n, uint8_t* out, size_t cap) {
        size_t i;
        if (n > cap) return n;
        for (i = 0; i < n; ++i) out[i] = (uint8_t)(in[i] ^ 0x5A);
        return n;
}

static size_t unmask(const uint8_t* in, size_t n, size_t* consumed,
                     uint8_t* out, size_t cap) {
        *consumed = n;
        return mask(in, n, out, cap);
}

/* Stuff and Unstuff frame bytes with their length; MsFrom and MsTo are
 * milliseconds in four bytes, VarFrom and VarTo milliseconds in a
 * varint. */

/** The Stuff transform: prefixes the bytes with their length in a byte.
 *
 * @param in   The bytes to transform.
 * @param n    How many there are, at most 255.
 * @param out  Where the result goes.
 * @param cap  How many bytes `out` holds.
 * @return     The result's length, n + 1; more than `cap`, writing
 *             nothing, if it doesn't fit; XTCE_PK_NO_TRANSFORM if `n` is
 *             more than 255. */
static size_t stuff(const uint8_t* in, size_t n, uint8_t* out, size_t cap) {
        if (n > 255) return XTCE_PK_NO_TRANSFORM;
        if (n + 1 > cap) return n + 1; /* the room it needs */
        out[0] = (uint8_t)n;
        memcpy(out + 1, in, n);
        return n + 1;
}

/** The Unstuff transform, Stuff's inverse: takes the bytes that a leading
 * length byte counts.
 *
 * @param in        The bytes to transform, starting with the length.
 * @param n         How many there are.
 * @param consumed  Filled in with how many bytes of `in` were used, the
 *                  length byte included.
 * @param out       Where the result goes.
 * @param cap       How many bytes `out` holds.
 * @return          The result's length; more than `cap`, writing nothing,
 *                  if it doesn't fit; XTCE_PK_NO_TRANSFORM if `in` is
 *                  shorter than the length says. */
static size_t unstuff(const uint8_t* in, size_t n, size_t* consumed,
                      uint8_t* out, size_t cap) {
        size_t k;
        if (!n || n <= in[0]) return XTCE_PK_NO_TRANSFORM;
        k = in[0];
        *consumed = k + 1;
        if (k > cap) return k; /* the room it needs */
        memcpy(out, in + 1, k);
        return k;
}

/** Converts seconds to whole milliseconds, rounding halves up.
 *
 * @param s  The seconds.
 * @param v  Filled in with the milliseconds.
 * @return   True, or false if they are outside 0 to 2^32 - 1. */
static bool ms(double s, uint32_t* v) {
        double x = floor(s * 1000.0 + 0.5);
        if (!(x >= 0.0 && x < 4294967296.0)) return false;
        *v = (uint32_t)x;
        return true;
}

/** The MsFrom time transform: milliseconds in four big-endian bytes.
 *
 * @param in        The bytes.
 * @param n         How many there are.
 * @param consumed  Filled in with how many were used, 4.
 * @param s         Filled in with the time in seconds.
 * @return          True, or false if there are fewer than four bytes. */
static bool ms_from(const uint8_t* in, size_t n, size_t* consumed,
                    double* s) {
        if (n < 4) return false;
        *consumed = 4;
        *s = (double)((uint32_t)in[0] << 24 | (uint32_t)in[1] << 16
                      | (uint32_t)in[2] << 8 | in[3]) / 1000.0;
        return true;
}

/** The MsTo time transform, MsFrom's inverse.
 *
 * @param s    The time in seconds.
 * @param out  Where the four bytes go.
 * @param cap  How many bytes `out` holds.
 * @return     4; more than `cap`, writing nothing, if they don't fit;
 *             XTCE_PK_NO_TRANSFORM if the milliseconds don't fit 32
 *             bits. */
static size_t ms_to(double s, uint8_t* out, size_t cap) {
        uint32_t v;
        if (!ms(s, &v)) return XTCE_PK_NO_TRANSFORM;
        if (cap < 4) return 4; /* the room it needs */
        out[0] = (uint8_t)(v >> 24);
        out[1] = (uint8_t)(v >> 16);
        out[2] = (uint8_t)(v >> 8);
        out[3] = (uint8_t)v;
        return 4;
}

/** The VarFrom time transform: milliseconds in a little-endian base-128
 * varint of up to five bytes.
 *
 * @param in        The bytes.
 * @param n         How many there are.
 * @param consumed  Filled in with how many the varint used.
 * @param s         Filled in with the time in seconds.
 * @return          True, or false if no varint ends within the first five
 *                  bytes or `n`. */
static bool var_from(const uint8_t* in, size_t n, size_t* consumed,
                     double* s) {
        uint64_t v = 0;
        size_t i;
        for (i = 0; i < n && i < 5; ++i) {
                v |= (uint64_t)(in[i] & 0x7F) << (7 * i);
                if (!(in[i] & 0x80)) {
                        *consumed = i + 1;
                        *s = (double)v / 1000.0;
                        return true;
                }
        }
        return false;
}

/** The VarTo time transform, VarFrom's inverse.
 *
 * @param s    The time in seconds.
 * @param out  Where the varint goes.
 * @param cap  How many bytes `out` holds.
 * @return     The varint's length; more than `cap`, writing nothing, if it
 *             doesn't fit; XTCE_PK_NO_TRANSFORM if the milliseconds don't
 *             fit 32 bits. */
static size_t var_to(double s, uint8_t* out, size_t cap) {
        uint8_t buf[5];
        uint32_t v;
        size_t n = 0;
        if (!ms(s, &v)) return XTCE_PK_NO_TRANSFORM;
        while (v >= 0x80) {
                buf[n++] = (uint8_t)((v & 0x7F) | 0x80);
                v >>= 7;
        }
        buf[n++] = (uint8_t)v;
        if (n > cap) return n; /* the room it needs */
        memcpy(out, buf, n);
        return n;
}

static xtce_pk_alarm alarms[64];
static xtce_pk_alarm monitored[64];
static demo_AlarmMonitor mon;
static double now; /* the index of the vector */
static char other[8192];

/** Prints ` | alarms: field=level, ...` for a packet with alarms, and
 * ` | monitor: ...` when the monitor's levels are others.
 *
 * @param a            The packet's alarms.
 * @param n            How many there are; nothing is printed if none.
 * @param monitored_n  How many alarms the monitor gave, in `monitored`. */
static void print_alarms(const xtce_pk_alarm* a, size_t n,
                         size_t monitored_n) {
        if (!n) return;
        xtce_pk_describe_alarms(a, n, text, sizeof text);
        printf(" | alarms: %s", text);
        xtce_pk_describe_alarms(monitored, monitored_n, other, sizeof other);
        if (strcmp(text, other) != 0) printf(" | monitor: %s", other);
}

static xtce_pk_output outputs[64];

/* Prints ` | derived: output=value, ...` for a packet that triggers
 * MathAlgorithms. */
static void print_outputs(size_t n) {
        if (!n) return;
        xtce_pk_describe_outputs(outputs, n, text, sizeof text);
        printf(" | derived: %s", text);
}

static void print_hex(const uint8_t* b, size_t n) {
        size_t i;
        for (i = 0; i < n; ++i) printf("%02x", b[i]);
}

/* Prints one result line; `decode`, `encode` and `describe` are the
 * generated functions for one packet type, `packet` a value of that type. */
#define RUN(type, len)                                                      \
        do {                                                                \
                type packet;                                                \
                xtce_pk_error err;                                          \
                size_t out_len;                                             \
                type##_init(&packet);                                       \
                if (type##_decode(&packet, input, (len), &err)) {           \
                        printf("error:%s | -\n",                            \
                               xtce_pk_error_kind(err.code));               \
                        break;                                              \
                }                                                           \
                if (type##_describe(&packet, text, sizeof text) >=          \
                    sizeof text) {                                          \
                        fprintf(stderr, "description too long\n");          \
                        exit(1);                                            \
                }                                                           \
                printf("%s | ", text);                                      \
                if (type##_encode(&packet, output, sizeof output, &out_len, \
                                  &err)) {                                  \
                        printf("error:%s", xtce_pk_error_kind(err.code));   \
                } else {                                                    \
                        print_hex(output, out_len);                         \
                }                                                           \
                print_alarms(alarms, type##_alarms(&packet, alarms),        \
                             type##_monitor(&packet, &mon, now, monitored)); \
                print_outputs(type##_algorithms(&packet, outputs));         \
                printf("\n");                                               \
        } while (0)

/** Decodes, describes and re-encodes each vector in a file, printing a
 * line for each.
 *
 * @param argc  The number of arguments, 2.
 * @param argv  The arguments: the vectors file's path.
 * @return      0; 1 if the file can't be opened or a vector's kind is
 *              unknown; 2 for a usage error.  A description too long for
 *              its buffer exits with status 1. */
int main(int argc, char** argv) {
        FILE* in;
        static char line[16384];
        if (argc != 2) {
                fprintf(stderr, "usage: packet_tool VECTORS\n");
                return 2;
        }
        in = fopen(argv[1], "r");
        if (!in) {
                perror(argv[1]);
                return 1;
        }
        demo_AlarmMonitor_init(&mon);
        demo_hook_is_even = is_even;
        demo_hook_min_max = min_max;
        demo_hook_bands = bands;
        demo_hook_twice = twice;
        demo_hook_expand = expand;
        demo_hook_shrink = shrink;
        demo_hook_mask = mask;
        demo_hook_unmask = unmask;
        demo_hook_stuff = stuff;
        demo_hook_unstuff = unstuff;
        demo_hook_ms_from = ms_from;
        demo_hook_ms_to = ms_to;
        demo_hook_var_from = var_from;
        demo_hook_var_to = var_to;
        while (fgets(line, sizeof line, in)) {
                char kind[16], hex[sizeof line];
                size_t n = 0, i;
                static unsigned index;
                char* hash = strchr(line, '#');
                if (hash) *hash = 0;
                if (sscanf(line, "%15s %s", kind, hex) != 2) continue;
                now = index++;
                for (i = 0; hex[i] && hex[i + 1] && n < sizeof input; i += 2) {
                        char byte[3] = {hex[i], hex[i + 1], 0};
                        input[n++] = (uint8_t)strtoul(byte, NULL, 16);
                }
                if (strcmp(kind, "tm") == 0) {
                        RUN(demo_CCSDSPacketKind, n);
                } else if (strcmp(kind, "tc") == 0) {
                        RUN(demo_CCSDSCommandKind, n);
                } else if (strcmp(kind, "hk") == 0) {
                        RUN(demo_Housekeeping, n);
                } else {
                        fprintf(stderr, "unknown vector kind %s\n", kind);
                        return 1;
                }
        }
        fclose(in);
        return 0;
}
