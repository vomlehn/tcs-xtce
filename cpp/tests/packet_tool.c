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

#include "mission/demo.h"

/* Buffers for the largest packet and description, allocated once. */
static uint8_t input[4096];
static uint8_t output[4096];
static char text[8192];

static xtce_pk_alarm alarms[64];
static xtce_pk_alarm monitored[64];
static demo_AlarmMonitor mon;
static double now; /* the index of the vector */
static char other[8192];

/* Prints ` | alarms: field=level, ...` for a packet with alarms, and
 * ` | monitor: ...` when the monitor's levels are others. */
static void print_alarms(const xtce_pk_alarm* a, size_t n,
                         size_t monitored_n) {
        if (!n) return;
        xtce_pk_describe_alarms(a, n, text, sizeof text);
        printf(" | alarms: %s", text);
        xtce_pk_describe_alarms(monitored, monitored_n, other, sizeof other);
        if (strcmp(text, other) != 0) printf(" | monitor: %s", other);
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
                printf("\n");                                               \
        } while (0)

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
