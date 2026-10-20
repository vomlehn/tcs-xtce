/* Tests of the packet codecs generated from tests/mission/demo.xml.
 *
 * Decoding of hand-built packets is checked across languages by
 * tests/packet_check.sh; these tests cover encoding from structs.  The
 * Makefile builds this file both as C and as C++.
 */
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "mission/demo.h"

static int failures = 0;

static void check(bool ok, const char* what, int line) {
  if (!ok) {
    fprintf(stderr, "packet_test.c:%d: check failed: %s\n", line, what);
    ++failures;
  }
}
#define CHECK(x) check((x), #x, __LINE__)

/* Packets are encoded into one buffer that is never reallocated. */
static uint8_t buf[demo_CCSDSPacketKind_MAX_BYTES];
static size_t len;
static xtce_pk_error err;

/* Encodes `p` of generated type `T` into `buf`; yields the error kind. */
#define ENCODE(T, p) xtce_pk_error_kind(T##_encode((p), buf, sizeof buf, &len, &err))

static void set_str(char* data, size_t* n, const char* s, size_t sn) {
  memcpy(data, s, sn);
  data[sn] = 0;
  *n = sn;
}
#define SET_STR(field, s) set_str((field).data, &(field).len, (s), sizeof(s) - 1)

static demo_Housekeeping housekeeping(void) {
  demo_Housekeeping hk;
  demo_Housekeeping_init(&hk);
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

static bool same_event(const demo_Event* a, const demo_Event* b) {
  char ta[512], tb[512];
  demo_Event_describe(a, ta, sizeof ta);
  demo_Event_describe(b, tb, sizeof tb);
  return strcmp(ta, tb) == 0;
}

int main(void) {
  demo_Housekeeping hk, hk2;
  demo_ScienceHousekeeping sci;
  demo_CCSDSPacketKind kind;
  demo_Thermal thermal;
  demo_Event ev, bad, back, timed, untimed;
  demo_Reboot cmd;
  demo_CCSDSCommandKind cmd_kind;
  demo_SetMode set, set_back;
  size_t full_len;
  char hex[128];
  xtce_pk_text t;
  int i;

  /* Decoding fills these; zero them so the compiler sees them set. */
  demo_CCSDSPacketKind_init(&kind);
  demo_CCSDSCommandKind_init(&cmd_kind);
  demo_SetMode_init(&set_back);
  demo_Housekeeping_init(&hk2);
  demo_Event_init(&back);

  /* Constants inherited from restriction criteria are written. */
  hk = housekeeping();
  CHECK(strcmp(ENCODE(demo_Housekeeping, &hk), "Ok") == 0);
  CHECK(len == demo_Housekeeping_MAX_BYTES);
  CHECK(buf[0] == 0x08 && buf[1] == 0x64);
  CHECK(demo_CCSDSPacketKind_decode(&kind, buf, len, &err) == 0);
  CHECK(kind.tag == demo_CCSDSPacketKind_Housekeeping_tag);
  CHECK(fabs(kind.u.Housekeeping.temperature - 25.0) < 0.5);
  CHECK(kind.u.Housekeeping.trim == -3);

  /* Dispatch to a derived container. */
  demo_ScienceHousekeeping_init(&sci);
  sci.temperature = 20.0;
  sci.science_count = UINT64_MAX;
  CHECK(strcmp(ENCODE(demo_ScienceHousekeeping, &sci), "Ok") == 0);
  CHECK(demo_CCSDSPacketKind_decode(&kind, buf, len, &err) == 0);
  CHECK(kind.tag == demo_CCSDSPacketKind_ScienceHousekeeping_tag);
  CHECK(demo_Housekeeping_decode(&hk2, buf, len, &err) == 0 && hk2.mode == demo_ModeType_SCIENCE);

  /* Encoding errors. */
  sci.temperature = 45.0;
  CHECK(strcmp(ENCODE(demo_ScienceHousekeeping, &sci), "Mismatch") == 0);
  hk = housekeeping();
  hk.seq_count = 1 << 14;
  CHECK(strcmp(ENCODE(demo_Housekeeping, &hk), "OutOfRange") == 0);
  hk = housekeeping();
  hk.temperature = 150.0;
  CHECK(strcmp(ENCODE(demo_Housekeeping, &hk), "Calibration") == 0);
  hk = housekeeping();
  hk.offset = -2048;
  CHECK(strcmp(ENCODE(demo_Housekeeping, &hk), "OutOfRange") == 0);
  demo_Thermal_init(&thermal);
  CHECK(strcmp(ENCODE(demo_Thermal, &thermal), "NotInvertible") == 0);
  hk = housekeeping();
  CHECK(demo_Housekeeping_encode(&hk, buf, 3, &len, &err) == XTCE_PK_NO_SPACE);

  /* Strings and binary. */
  demo_Event_init(&ev);
  SET_STR(ev.origin, "ORBIT");
  SET_STR(ev.message, "xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx");
  SET_STR(ev.source, "abc");
  ev.data_.data[0] = 1;
  ev.data_.data[1] = 2;
  ev.data_.data[2] = 3;
  ev.data_.data[3] = 4;
  CHECK(strcmp(ENCODE(demo_Event, &ev), "Ok") == 0);
  CHECK(demo_Event_decode(&back, buf, len, &err) == 0 && same_event(&back, &ev));
  bad = ev;
  bad.message.len = 33; /* more than the field holds */
  CHECK(strcmp(ENCODE(demo_Event, &bad), "TooLong") == 0);
  bad = ev;
  SET_STR(bad.origin, "NINE BYT");
  bad.origin.data[8] = 'E';
  bad.origin.len = 9;
  CHECK(strcmp(ENCODE(demo_Event, &bad), "TooLong") == 0);
  bad = ev;
  SET_STR(bad.origin, "a\0b");
  CHECK(strcmp(ENCODE(demo_Event, &bad), "InvalidString") == 0);
  bad = ev;
  SET_STR(bad.origin, "\xff");
  CHECK(strcmp(ENCODE(demo_Event, &bad), "InvalidString") == 0);

  /* Commands. */
  demo_Reboot_init(&cmd);
  cmd.seq_count = 6;
  cmd.packet_length = 10;
  cmd.delay = 30.0;
  cmd.force = true;
  SET_STR(cmd.reason, "maint");
  CHECK(strcmp(ENCODE(demo_Reboot, &cmd), "Ok") == 0);
  t = xtce_pk_text_init(hex, sizeof hex);
  xtce_pk_text_bytes(&t, buf, len);
  CHECK(strcmp(hex, "0x112cc006000a02012c0100056d61696e74") == 0);
  CHECK(demo_CCSDSCommandKind_decode(&cmd_kind, buf, len, &err) == 0);
  CHECK(cmd_kind.tag == demo_CCSDSCommandKind_Reboot_tag);
  CHECK(cmd_kind.u.Reboot.force && cmd_kind.u.Reboot.reason.len == 5);
  demo_SetMode_init(&set);
  set.mode = 9;
  CHECK(strcmp(ENCODE(demo_SetMode, &set), "Ok") == 0);
  CHECK(demo_SetMode_decode(&set_back, buf, len, &err) == 0);
  CHECK(set_back.mode == 9 && demo_ModeArgType_label(set_back.mode) == NULL);

  /* Include conditions. */
  demo_Event_init(&timed);
  timed.sec_hdr_flag = true;
  timed.has_coarse_time = true;
  timed.coarse_time = 1.6e9;
  timed.has_fine_time = true;
  timed.fine_time = 0.5;
  CHECK(strcmp(ENCODE(demo_Event, &timed), "Ok") == 0);
  full_len = len;
  CHECK(demo_Event_decode(&back, buf, len, &err) == 0 && back.has_fine_time && back.fine_time == 0.5);
  untimed = timed;
  untimed.sec_hdr_flag = false;
  CHECK(strcmp(ENCODE(demo_Event, &untimed), "Ok") == 0);
  CHECK(len + 6 == full_len);
  CHECK(demo_Event_decode(&back, buf, len, &err) == 0 && !back.has_coarse_time);
  timed.has_fine_time = false;
  CHECK(strcmp(ENCODE(demo_Event, &timed), "Missing") == 0);
  CHECK(err.field && strcmp(err.field, "FineTime") == 0);

  /* Decode errors carry a message. */
  {
    char msg[128];
    CHECK(demo_Housekeeping_decode(&hk2, buf, 2, &err) == XTCE_PK_TRUNCATED);
    xtce_pk_error_message(&err, msg, sizeof msg);
    CHECK(strncmp(msg, "packet too short for ", 21) == 0);
  }

  /* Byte and zero writes, aligned and unaligned, overwrite bits written
   * before a seek back, as in the Rust runtime's test. */
  {
    static const uint8_t ff[6] = {0xff, 0xff, 0xff, 0xff, 0xff, 0xff};
    static const uint8_t ab[2] = {0x12, 0x34}, c = 0xab;
    static const uint8_t expect[6] = {0x12, 0x34, 0xb5, 0x60, 0x00, 0x0f};
    uint8_t out[8];
    xtce_pk_writer w = xtce_pk_writer_init(out, sizeof out, &err);
    CHECK(xtce_pk_write_bytes(&w, ff, 6) == 0);
    CHECK(xtce_pk_write_seek(&w, 0, 0, "x") == 0);
    CHECK(xtce_pk_write_bytes(&w, ab, 2) == 0);
    CHECK(xtce_pk_write_bits(&w, 3, 5) == 0);
    CHECK(xtce_pk_write_bytes(&w, &c, 1) == 0);
    CHECK(xtce_pk_write_zeros(&w, 13) == 0);
    CHECK(xtce_pk_write_bits(&w, 4, 0) == 0);
    CHECK(w.len == 6 && memcmp(out, expect, 6) == 0);
    w = xtce_pk_writer_init(out, sizeof out, &err);
    CHECK(xtce_pk_write_bits(&w, 4, 0xf) == 0);
    CHECK(xtce_pk_write_zeros(&w, 2) == 0);
    CHECK(xtce_pk_write_skip(&w, 18, "x") == 0);
    CHECK(xtce_pk_write_zeros(&w, 0) == 0);
    CHECK(xtce_pk_write_bytes(&w, ab, 0) == 0);
    CHECK(w.len == 1 && out[0] == 0xf0);
    /* The aligned paths check the capacity. */
    w = xtce_pk_writer_init(out, sizeof out, &err);
    CHECK(xtce_pk_write_bytes(&w, ff, 6) == 0);
    CHECK(xtce_pk_write_bytes(&w, ff, 6) == XTCE_PK_NO_SPACE);
    CHECK(xtce_pk_write_zeros(&w, 24) == XTCE_PK_NO_SPACE);
    CHECK(xtce_pk_write_zeros(&w, 16) == 0 && w.len == 8);
  }

  /* binary32 rounds like Rust's `as f32` and Python's struct: values just
   * above FLT_MAX round down to it, and half an ulp above rounds to infinity. */
  CHECK(xtce_pk_float_to_raw(3.4028235e38, 32) == 0x7f7fffff);
  CHECK(xtce_pk_float_to_raw(-3.4028235e38, 32) == 0xff7fffff);
  CHECK(xtce_pk_float_to_raw(3.4028235677973362e38, 32) == 0x7f7fffff);
  CHECK(xtce_pk_float_to_raw(3.4028235677973366e38, 32) == 0x7f800000);
  CHECK(xtce_pk_float_to_raw(1e39, 32) == 0x7f800000);
  CHECK(xtce_pk_float_to_raw(-1e39, 32) == 0xff800000);
  CHECK(xtce_pk_float_to_raw(1.5, 32) == 0x3fc00000);

  /* Decoding and re-encoding is a fixed point; the buffers are reused. */
  for (i = 0; i < 100; ++i) {
    uint8_t copy[sizeof buf];
    size_t n;
    hk = housekeeping();
    CHECK(strcmp(ENCODE(demo_Housekeeping, &hk), "Ok") == 0);
    memcpy(copy, buf, len);
    n = len;
    CHECK(demo_CCSDSPacketKind_decode(&kind, copy, n, &err) == 0);
    CHECK(strcmp(ENCODE(demo_CCSDSPacketKind, &kind), "Ok") == 0);
    CHECK(len == n && memcmp(copy, buf, n) == 0);
  }

  if (failures) return EXIT_FAILURE;
  printf("packet_test: all checks passed\n");
  return EXIT_SUCCESS;
}
