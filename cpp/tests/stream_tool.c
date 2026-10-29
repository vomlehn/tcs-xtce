/* Finds the frames of the demo mission's streams, as tests/stream_check.sh
 * asks: each line of the file it is given is a stream's name and the next
 * bytes of that stream in hex, and it prints `<stream>:` then the frames
 * those bytes complete, in hex, or ` -`.  Each stream keeps its state from
 * line to line.  Compiled as C and as C++; the Rust and Python versions of
 * this tool print identical output.
 *
 * Usage: stream_tool STREAMS */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define DEMO_HOOKS /* this file defines the hooks of the demo mission */
#include "mission/demo.h"

static demo_DownlinkStream downlink;
static demo_UplinkStream uplink;
static demo_DeepStream deep;
static demo_BeaconStream beacon;
static demo_RelayStream relay;
static demo_ScrambledStream scrambled;
static demo_ClearStream clear;
static demo_NestedStream nested;

/** The demo's Descramble: XORs each byte with 0x55.
 *
 * @param ctx      Unused.
 * @param data     The bytes to descramble.
 * @param len      How many there are.
 * @param out      Called with each run of descrambled bytes, in order.
 * @param out_ctx  What `out` is given as its first argument. */
static void descramble(void* ctx, const uint8_t* data, size_t len,
                       void (*out)(void*, const uint8_t*, size_t),
                       void* out_ctx) {
        uint8_t buf[64];
        (void)ctx;
        while (len) {
                size_t n = len < sizeof buf ? len : sizeof buf, i;
                for (i = 0; i < n; ++i) buf[i] = (uint8_t)(data[i] ^ 0x55);
                out(out_ctx, buf, n);
                data += n;
                len -= n;
        }
}

/* The demo's Invert: the bit's complement, as AutoInvert does without it. */
static uint8_t invert(uint8_t bit) { return (uint8_t)(bit ^ 1); }
static uint8_t input[65536];
static int frames;

/** Prints a frame a stream found, in hex after a space, and counts it.
 *
 * @param ctx    Unused.
 * @param frame  The frame's bytes.
 * @param len    How many there are. */
static void take(void* ctx, const uint8_t* frame, size_t len) {
        size_t i;
        (void)ctx;
        printf(" ");
        for (i = 0; i < len; ++i) printf("%02x", frame[i]);
        ++frames;
}

/** Pushes the bytes of each line of a file into the stream it names,
 * printing the frames they complete.
 *
 * @param argc  The number of arguments, 2.
 * @param argv  The arguments: the streams file's path.
 * @return      0; 1 if the file can't be opened or names an unknown
 *              stream; 2 for a usage error. */
int main(int argc, char** argv) {
        static char line[1 << 18];
        FILE* in;
        if (argc != 2) {
                fprintf(stderr, "usage: stream_tool STREAMS\n");
                return 2;
        }
        in = fopen(argv[1], "r");
        if (!in) {
                perror(argv[1]);
                return 1;
        }
        demo_DownlinkStream_init(&downlink);
        demo_UplinkStream_init(&uplink);
        demo_DeepStream_init(&deep);
        demo_BeaconStream_init(&beacon);
        demo_RelayStream_init(&relay);
        demo_ScrambledStream_init(&scrambled, descramble, NULL);
        demo_ClearStream_init(&clear, descramble, NULL, invert);
        demo_NestedStream_init(&nested, descramble, NULL, invert);
        while (fgets(line, sizeof line, in)) {
                static char name[16], hex[sizeof line];
                size_t n = 0, i;
                char* hash = strchr(line, '#');
                if (hash) *hash = 0;
                if (sscanf(line, "%15s %s", name, hex) != 2) continue;
                for (i = 0; hex[i] && hex[i + 1] && n < sizeof input; i += 2) {
                        char byte[3] = {hex[i], hex[i + 1], 0};
                        input[n++] = (uint8_t)strtoul(byte, NULL, 16);
                }
                printf("%s:", name);
                frames = 0;
                if (strcmp(name, "downlink") == 0) {
                        demo_DownlinkStream_push(&downlink, input, n, take,
                                                 NULL);
                } else if (strcmp(name, "uplink") == 0) {
                        demo_UplinkStream_push(&uplink, input, n, take, NULL);
                } else if (strcmp(name, "deep") == 0) {
                        demo_DeepStream_push(&deep, input, n, take, NULL);
                } else if (strcmp(name, "beacon") == 0) {
                        demo_BeaconStream_push(&beacon, input, n, take, NULL);
                } else if (strcmp(name, "relay") == 0) {
                        demo_RelayStream_push(&relay, input, n, take, NULL);
                } else if (strcmp(name, "scrambled") == 0) {
                        demo_ScrambledStream_push(&scrambled, input, n, take,
                                                  NULL);
                } else if (strcmp(name, "clear") == 0) {
                        demo_ClearStream_push(&clear, input, n, take, NULL);
                } else if (strcmp(name, "nested") == 0) {
                        demo_NestedStream_push(&nested, input, n, take, NULL);
                } else {
                        fprintf(stderr, "unknown stream %s\n", name);
                        return 1;
                }
                if (!frames) printf(" -");
                printf("\n");
        }
        fclose(in);
        return 0;
}
