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

#include "mission/demo.h"

static demo_DownlinkStream downlink;
static demo_UplinkStream uplink;
static uint8_t input[65536];
static int frames;

static void take(void* ctx, const uint8_t* frame, size_t len) {
        size_t i;
        (void)ctx;
        printf(" ");
        for (i = 0; i < len; ++i) printf("%02x", frame[i]);
        ++frames;
}

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
