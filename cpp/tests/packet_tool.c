/* Decodes and re-encodes the packet vectors in tests/mission/vectors.txt.
 *
 * For each vector prints `<description of the decoded packet> | <hex of the
 * re-encoded packet>`, with `error:<kind>` in place of either on failure.  The
 * Rust and Python versions of this tool print identical output.  With
 * --packed it uses the codecs generated with --bit-packing (prefix
 * demo_packed_), which must print the same.  The Makefile builds this file
 * both as C and as C++.
 *
 * Usage: packet_tool [--packed] VECTORS
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "mission/demo.h"
#include "mission/demo_packed.h"

/* Buffers for the largest packet and description, allocated once. */
static uint8_t input[4096];
static uint8_t output[4096];
static char text[8192];

static void print_hex(const uint8_t* b, size_t n) {
  size_t i;
  for (i = 0; i < n; ++i) printf("%02x", b[i]);
}

/* Prints one result line; `decode`, `encode` and `describe` are the
 * generated functions for one packet type, `packet` a value of that type. */
#define RUN(type, len)                                                        \
  do {                                                                        \
    type packet;                                                              \
    xtce_pk_error err;                                                        \
    size_t out_len;                                                           \
    type##_init(&packet);                                                     \
    if (type##_decode(&packet, input, (len), &err)) {                         \
      printf("error:%s | -\n", xtce_pk_error_kind(err.code));                 \
      break;                                                                  \
    }                                                                         \
    if (type##_describe(&packet, text, sizeof text) >= sizeof text) {         \
      fprintf(stderr, "description too long\n");                              \
      exit(1);                                                                \
    }                                                                         \
    printf("%s | ", text);                                                    \
    if (type##_encode(&packet, output, sizeof output, &out_len, &err)) {      \
      printf("error:%s\n", xtce_pk_error_kind(err.code));                     \
    } else {                                                                  \
      print_hex(output, out_len);                                             \
      printf("\n");                                                           \
    }                                                                         \
  } while (0)

int main(int argc, char** argv) {
  FILE* in;
  static char line[16384];
  int packed = argc == 3 && strcmp(argv[1], "--packed") == 0;
  const char* path = argv[argc - 1];
  if (argc != 2 && !packed) {
    fprintf(stderr, "usage: packet_tool [--packed] VECTORS\n");
    return 2;
  }
  in = fopen(path, "r");
  if (!in) {
    perror(path);
    return 1;
  }
  while (fgets(line, sizeof line, in)) {
    char kind[16], hex[sizeof line];
    size_t n = 0, i;
    char* hash = strchr(line, '#');
    if (hash) *hash = 0;
    if (sscanf(line, "%15s %s", kind, hex) != 2) continue;
    for (i = 0; hex[i] && hex[i + 1] && n < sizeof input; i += 2) {
      char byte[3] = {hex[i], hex[i + 1], 0};
      input[n++] = (uint8_t)strtoul(byte, NULL, 16);
    }
    if (strcmp(kind, "tm") == 0 && !packed) {
      RUN(demo_CCSDSPacketKind, n);
    } else if (strcmp(kind, "tc") == 0 && !packed) {
      RUN(demo_CCSDSCommandKind, n);
    } else if (strcmp(kind, "hk") == 0 && !packed) {
      RUN(demo_Housekeeping, n);
    } else if (strcmp(kind, "tm") == 0) {
      RUN(demo_packed_CCSDSPacketKind, n);
    } else if (strcmp(kind, "tc") == 0) {
      RUN(demo_packed_CCSDSCommandKind, n);
    } else if (strcmp(kind, "hk") == 0) {
      RUN(demo_packed_Housekeeping, n);
    } else {
      fprintf(stderr, "unknown vector kind %s\n", kind);
      return 1;
    }
  }
  fclose(in);
  return 0;
}
