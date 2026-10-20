/* Tests of the packet codecs generated from tests/mission/demo.xml with
 * --bit-packing (prefix demo_packed_).
 *
 * tests/packet_check.sh checks that they decode and encode exactly like the
 * unpacked codecs; these tests cover the accessors and the layout.  The
 * Makefile builds this file both as C and as C++.
 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "mission/demo.h"
#include "mission/demo_packed.h"

static int failures = 0;

static void check(bool ok, const char* what, int line) {
  if (!ok) {
    fprintf(stderr, "packed_test.c:%d: check failed: %s\n", line, what);
    ++failures;
  }
}
#define CHECK(x) check((x), #x, __LINE__)

static xtce_pk_error err;

static demo_packed_Housekeeping housekeeping(void) {
  demo_packed_Housekeeping hk;
  demo_packed_Housekeeping_init(&hk);
  CHECK(demo_packed_Housekeeping_set_version(&hk, 5, &err) == 0);
  demo_packed_Housekeeping_set_sec_hdr_flag(&hk, true);
  CHECK(demo_packed_Housekeeping_set_seq_flags(&hk, demo_packed_SeqFlagsType_UNSEGMENTED, &err) == 0);
  CHECK(demo_packed_Housekeeping_set_seq_count(&hk, 42, &err) == 0);
  CHECK(demo_packed_Housekeeping_set_packet_length(&hk, 18, &err) == 0);
  CHECK(demo_packed_Housekeeping_set_mode(&hk, demo_packed_ModeType_NOMINAL, &err) == 0);
  hk.temperature = 25.0;
  hk.battery_voltage = 12.0;
  demo_packed_Housekeeping_set_charging(&hk, true);
  hk.bus_current = 1.5;
  CHECK(demo_packed_Housekeeping_set_uptime(&hk, 0x01020304, &err) == 0);
  hk.attitude = 1.5;
  CHECK(demo_packed_Housekeeping_set_delta(&hk, -2, &err) == 0);
  CHECK(demo_packed_Housekeeping_set_offset(&hk, -5, &err) == 0);
  CHECK(demo_packed_Housekeeping_set_trim(&hk, -3, &err) == 0);
  return hk;
}

static demo_Housekeeping plain_housekeeping(void) {
  demo_Housekeeping hk;
  demo_Housekeeping_init(&hk);
  hk.version = 5;
  hk.sec_hdr_flag = true;
  hk.seq_flags = demo_SeqFlagsType_UNSEGMENTED;
  hk.seq_count = 42;
  hk.packet_length = 18;
  hk.mode = demo_ModeType_NOMINAL;
  hk.temperature = 25.0;
  hk.battery_voltage = 12.0;
  hk.charging = true;
  hk.bus_current = 1.5;
  hk.uptime = 0x01020304;
  hk.attitude = 1.5;
  hk.delta = -2;
  hk.offset = -5;
  hk.trim = -3;
  return hk;
}

int main(void) {
  demo_packed_Housekeeping hk = housekeeping(), before;
  demo_Housekeeping plain = plain_housekeeping();
  demo_packed_CCSDSPacketKind kind;
  uint8_t a[demo_Housekeeping_MAX_BYTES], b[demo_Housekeeping_MAX_BYTES];
  char ta[1024], tb[1024];
  size_t na = 0, nb = 0;

  /* 113 bits of integer, enumerated and boolean fields in 15 bytes, stored
   * as XTCE packs encoded data, most significant bit first from the top of
   * byte 0: Version (3 bits), SecHdrFlag (1), SeqFlags (2), then SeqCount
   * (14) crossing into the second byte. */
  CHECK(sizeof hk.packed == 15);
  CHECK(hk.packed[0] == 0xbc && hk.packed[1] == 0x02);
  /* Uptime is little-endian, so it is stored with its bytes swapped, as it is
   * encoded. */
  CHECK(xtce_pk_packed_get(hk.packed, 45, 32, false) == 0x04030201);

  /* Accessors. */
  CHECK(demo_packed_Housekeeping_version(&hk) == 5);
  CHECK(demo_packed_Housekeeping_sec_hdr_flag(&hk));
  CHECK(demo_packed_Housekeeping_seq_flags(&hk) == demo_packed_SeqFlagsType_UNSEGMENTED);
  CHECK(demo_packed_Housekeeping_seq_count(&hk) == 42);
  CHECK(demo_packed_Housekeeping_mode(&hk) == demo_packed_ModeType_NOMINAL);
  CHECK(demo_packed_Housekeeping_uptime(&hk) == 0x01020304);
  CHECK(demo_packed_Housekeeping_delta(&hk) == -2);
  CHECK(demo_packed_Housekeeping_offset(&hk) == -5);
  CHECK(demo_packed_Housekeeping_trim(&hk) == -3);
  demo_packed_Housekeeping_set_sec_hdr_flag(&hk, false);
  CHECK(!demo_packed_Housekeeping_sec_hdr_flag(&hk));
  CHECK(demo_packed_Housekeeping_version(&hk) == 5);
  demo_packed_Housekeeping_set_sec_hdr_flag(&hk, true);

  /* Setters check the encoding and leave the field as it was on failure. */
  before = hk;
  CHECK(demo_packed_Housekeeping_set_version(&hk, 8, &err) == XTCE_PK_OUT_OF_RANGE);
  CHECK(err.field && strcmp(err.field, "Version") == 0);
  CHECK(demo_packed_Housekeeping_set_seq_count(&hk, 1 << 14, &err) == XTCE_PK_OUT_OF_RANGE);
  CHECK(demo_packed_Housekeeping_set_offset(&hk, 2048, &err) == XTCE_PK_OUT_OF_RANGE);
  CHECK(demo_packed_Housekeeping_set_trim(&hk, -128, &err) == XTCE_PK_OUT_OF_RANGE);
  CHECK(demo_packed_Housekeeping_set_seq_flags(&hk, 4, &err) == XTCE_PK_OUT_OF_RANGE);
  CHECK(memcmp(before.packed, hk.packed, sizeof hk.packed) == 0);

  /* Encodes and describes exactly like the unpacked codec. */
  CHECK(demo_packed_Housekeeping_encode(&hk, a, sizeof a, &na, &err) == 0);
  CHECK(demo_Housekeeping_encode(&plain, b, sizeof b, &nb, &err) == 0);
  CHECK(na == nb && memcmp(a, b, na) == 0);
  demo_packed_Housekeeping_describe(&hk, ta, sizeof ta);
  demo_Housekeeping_describe(&plain, tb, sizeof tb);
  CHECK(strcmp(ta, tb) == 0);

  /* Decoding fills the packed storage, including the unused bits after the
   * last field, whatever the struct held before. */
  memset(&kind, 0xff, sizeof kind);
  CHECK(demo_packed_CCSDSPacketKind_decode(&kind, a, na, &err) == 0);
  CHECK(kind.tag == demo_packed_CCSDSPacketKind_Housekeeping_tag);
  CHECK(memcmp(kind.u.Housekeeping.packed, hk.packed, sizeof hk.packed) == 0);

  if (failures) return EXIT_FAILURE;
  printf("packed_test: all checks passed\n");
  return EXIT_SUCCESS;
}
