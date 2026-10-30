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

#define DEMO_HOOKS /* this file defines the hooks of the demo mission */
#include "mission/demo.h"

static int failures = 0;

/** Records a check, printing it to standard error if it failed.
 *
 * @param ok    Whether the check passed.
 * @param what  The checked expression's text.
 * @param line  The line of this file the check is on. */
static void check(bool ok, const char* what, int line) {
        if (!ok) {
                fprintf(stderr, "packet_test.c:%d: check failed: %s\n", line,
                        what);
                ++failures;
        }
}
#define CHECK(x) check((x), #x, __LINE__)

/* Packets are encoded into one buffer that is never reallocated. */
static uint8_t buf[demo_CCSDSPacketKind_MAX_BYTES];
static size_t len;
static xtce_pk_error err;

/* Encodes `p` of generated type `T` into `buf`; yields the error kind. */
#define ENCODE(T, p) \
        xtce_pk_error_kind(T##_encode((p), buf, sizeof buf, &len, &err))

/* A copy of the first `n` bytes of `buf` in `out`, with the CCSDS packet
 * length set to `length`. */
static void with_length(uint8_t* out, size_t n, unsigned length) {
        memcpy(out, buf, n);
        out[4] = (uint8_t)(length >> 8);
        out[5] = (uint8_t)length;
}

static void set_str(char* data, size_t* n, const char* s, size_t sn) {
        memcpy(data, s, sn);
        data[sn] = 0;
        *n = sn;
}
#define SET_STR(field, s) \
        set_str((field).data, &(field).len, (s), sizeof(s) - 1)

/* Bytes a batch is read from, as a stream would give them. */
typedef struct source {
        const uint8_t* at;
        size_t left;
} source;

/** Reads from a source, as an xtce_pk_read_fn.
 *
 * @param ctx  The source.
 * @param out  Where the bytes go.
 * @param n    How many bytes to read.
 * @return     0, or 1 if the source has fewer than `n` bytes left. */
static int source_read(void* ctx, uint8_t* out, size_t n) {
        source* s = (source*)ctx;
        if (n > s->left) return 1;
        memcpy(out, s->at, n);
        s->at += n;
        s->left -= n;
        return 0;
}

/** Makes the Housekeeping packet the tests encode.
 *
 * @return  The packet, with every field set to a known value. */
static demo_Housekeeping housekeeping(void) {
        demo_Housekeeping hk;
        demo_Housekeeping_init(&hk);
        hk.sec_hdr_flag = true;
        hk.seq_flags = demo_SeqFlagsType_UNSEGMENTED;
        hk.seq_count = 42;
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

/** Compares two Event packets by their descriptions.
 *
 * @param a  One packet.
 * @param b  The other.
 * @return   Whether their descriptions are the same. */
static bool same_event(const demo_Event* a, const demo_Event* b) {
        char ta[512], tb[512];
        demo_Event_describe(a, ta, sizeof ta);
        demo_Event_describe(b, tb, sizeof tb);
        return strcmp(ta, tb) == 0;
}

/** Runs the tests.
 *
 * @return  EXIT_SUCCESS if every check passes, else EXIT_FAILURE. */
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
        /* Its base container's entries end before the packet length does. */
        CHECK(demo_Housekeeping_decode(&hk2, buf, len, &err) ==
              XTCE_PK_BAD_LENGTH);

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
        CHECK(demo_Housekeeping_encode(&hk, buf, 3, &len, &err) ==
              XTCE_PK_NO_SPACE);

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
        CHECK(demo_Event_decode(&back, buf, len, &err) == 0 &&
              same_event(&back, &ev));
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

        /* The packet length is the number of bytes after the 6-byte primary
         * header, minus 1, so it follows the length of the reason string. */
        {
                uint8_t pk[sizeof buf + 2];
                demo_Reboot r2;
                demo_Reboot_init(&r2);
                CHECK(strcmp(ENCODE(demo_Reboot, &r2), "Ok") == 0);
                CHECK(len == 12 && buf[4] == 0 && buf[5] == 5);
                SET_STR(r2.reason, "maint");
                CHECK(strcmp(ENCODE(demo_Reboot, &r2), "Ok") == 0);
                CHECK(len == 17 && buf[4] == 0 && buf[5] == 10);
                /* Bytes after the packet are ignored. */
                with_length(pk, len, 10);
                pk[len] = pk[len + 1] = 0xee;
                CHECK(demo_Reboot_decode(&r2, pk, len + 2, &err) == 0 &&
                      r2.reason.len == 5);
                /* A length beyond the data received. */
                with_length(pk, len, 11);
                CHECK(demo_Reboot_decode(&r2, pk, len, &err) ==
                              XTCE_PK_TRUNCATED &&
                      strcmp(err.field, "PacketLength") == 0);
                /* A length that ends the packet inside the reason string. */
                with_length(pk, len, 9);
                CHECK(demo_Reboot_decode(&r2, pk, len, &err) ==
                      XTCE_PK_TRUNCATED);
                /* A length that leaves bytes the entries don't use. */
                with_length(pk, len, 11);
                pk[len] = 0;
                CHECK(demo_Reboot_decode(&r2, pk, len + 1, &err) ==
                      XTCE_PK_BAD_LENGTH);
                CHECK(strcmp(xtce_pk_error_kind(err.code), "BadLength") == 0);
        }

        demo_SetMode_init(&set);
        set.mode = 9;
        CHECK(strcmp(ENCODE(demo_SetMode, &set), "Ok") == 0);
        CHECK(demo_SetMode_decode(&set_back, buf, len, &err) == 0);
        CHECK(set_back.mode == 9 &&
              demo_ModeArgType_label(set_back.mode) == NULL);

        /* Include conditions. */
        demo_Event_init(&timed);
        timed.sec_hdr_flag = true;
        timed.has_coarse_time = true;
        timed.coarse_time = 1.6e9;
        timed.has_fine_time = true;
        timed.fine_time = 0.5;
        CHECK(strcmp(ENCODE(demo_Event, &timed), "Ok") == 0);
        full_len = len;
        CHECK(demo_Event_decode(&back, buf, len, &err) == 0 &&
              back.has_fine_time && back.fine_time == 0.5);
        untimed = timed;
        untimed.sec_hdr_flag = false;
        CHECK(strcmp(ENCODE(demo_Event, &untimed), "Ok") == 0);
        CHECK(len + 6 == full_len);
        CHECK(demo_Event_decode(&back, buf, len, &err) == 0 &&
              !back.has_coarse_time);
        timed.has_fine_time = false;
        CHECK(strcmp(ENCODE(demo_Event, &timed), "Missing") == 0);
        CHECK(err.field && strcmp(err.field, "FineTime") == 0);

        /* Arrays, repeated entries and sizes from other fields. */
        {
                demo_Samples s, s2;
                size_t i;
                char msg[128];
                demo_Samples_init(&s);
                s.sec_hdr_flag = true;
                s.readings.len = 2;
                s.readings.data[0] = -1;
                s.readings.data[1] = 7;
                s.has_grid = true;
                s.grid.len = 6;
                for (i = 0; i < 6; ++i) s.grid.data[i] = (uint8_t)(i + 1);
                s.valid.len = 4;
                s.valid.data[0] = s.valid.data[3] = true;
                s.label_size = 4;
                SET_STR(s.label_, "abc");
                s.payload.len = 1;
                s.payload.data[0] = 0x5a;
                CHECK(strcmp(ENCODE(demo_Samples, &s), "Ok") == 0);
                CHECK(demo_Samples_decode(&s2, buf, len, &err) == 0);
                CHECK(s2.readings.len == 2 && s2.readings.data[0] == -1 &&
                      s2.readings.data[1] == 7);
                CHECK(s2.has_grid && s2.grid.len == 6 && s2.grid.data[5] == 6);
                CHECK(s2.valid.len == 4 && s2.valid.data[0] &&
                      !s2.valid.data[1] && s2.valid.data[3]);
                CHECK(strcmp(s2.label_.data, "abc") == 0 &&
                      s2.payload.len == 1 && s2.payload.data[0] == 0x5a);
                /* SampleCount and PayloadSize come from the data, which must
                 * fit them. */
                CHECK(buf[6] == 2);
                s.readings.len = 3;
                CHECK(strcmp(ENCODE(demo_Samples, &s), "Ok") == 0);
                CHECK(demo_Samples_decode(&s2, buf, len, &err) == 0 &&
                      s2.readings.len == 3);
                s.readings.len = 7; /* SampleCount's ValidRange allows 6 */
                CHECK(strcmp(ENCODE(demo_Samples, &s), "BadSize") == 0);
                xtce_pk_error_message(&err, msg, sizeof msg);
                CHECK(strcmp(msg, "invalid size or count for Readings") == 0);
                s.readings.len = 2;
                s.payload.len = 0;
                CHECK(strcmp(ENCODE(demo_Samples, &s), "Ok") == 0);
                s.payload.len = 9; /* PayloadSize's ValidRange allows 8 */
                CHECK(strcmp(ENCODE(demo_Samples, &s), "BadSize") == 0);
                s.payload.len = 1;
                /* A count that the application sets must still match its data.
                 */
                s.valid.len = 1;
                CHECK(strcmp(ENCODE(demo_Samples, &s), "BadSize") == 0);
                s.valid.len = 4;
                /* beyond its ValidRange, which bounds the label */
                s.label_size = 11;
                CHECK(strcmp(ENCODE(demo_Samples, &s), "OutOfRange") == 0);
                s.label_size = 4;
                SET_STR(s.label_, "abcde");
                CHECK(strcmp(ENCODE(demo_Samples, &s), "TooLong") == 0);
                SET_STR(s.label_, "abcd");
                CHECK(strcmp(ENCODE(demo_Samples, &s), "Ok") == 0);
                CHECK(demo_Samples_decode(&s2, buf, len, &err) == 0 &&
                      strcmp(s2.label_.data, "abcd") == 0);
                s.has_grid = false;
                CHECK(strcmp(ENCODE(demo_Samples, &s), "Missing") == 0);
        }
        {
                demo_Upload up, up2;
                demo_Upload_init(&up);
                up.words.len = 3;
                up.words.data[0] = 1;
                up.words.data[2] = 3;
                up.gains.len = 2;
                up.gains.data[0] = 1.0;
                up.gains.data[1] = 2.5;
                up.tag_size = 4;
                SET_STR(up.tag, "ab");
                CHECK(strcmp(ENCODE(demo_Upload, &up), "Ok") == 0);
                CHECK(demo_Upload_decode(&up2, buf, len, &err) == 0);
                CHECK(up2.words.len == 3 && up2.words.data[2] == 3 &&
                      up2.gains.data[1] == 2.5);
                CHECK(strcmp(up2.tag.data, "ab") == 0);
                up.gains.len = 1;
                CHECK(strcmp(ENCODE(demo_Upload, &up), "BadSize") == 0);
        }

        /* Aggregates. */
        {
                demo_Targets t, t2;
                demo_Goto g, g2;
                demo_Targets_init(&t);
                t.sec_hdr_flag = true;
                t.start.x = 1;
                t.start.y = -1;
                t.targets.len = 1;
                t.targets.data[0].id_ = 3;
                t.targets.data[0].mode = demo_ModeType_SAFE;
                SET_STR(t.targets.data[0].name_, "a");
                t.targets.data[0].where_.x = 2;
                t.targets.data[0].where_.y = 3;
                t.targets.data[0].weights.len = 2;
                t.targets.data[0].weights.data[0] = 1;
                t.targets.data[0].weights.data[1] = 2;
                t.has_home = true;
                t.home.x = 4;
                t.home.y = 5;
                CHECK(strcmp(ENCODE(demo_Targets, &t), "Ok") == 0);
                CHECK(demo_Targets_decode(&t2, buf, len, &err) == 0);
                CHECK(t2.start.y == -1 && t2.targets.len == 1 &&
                      t2.targets.data[0].where_.y == 3);
                CHECK(strcmp(t2.targets.data[0].name_.data, "a") == 0 &&
                      t2.targets.data[0].weights.data[1] == 2);
                CHECK(t2.has_home && t2.home.x == 4);
                /* Members are checked like fields. */
                t.targets.data[0].id_ = 16;
                CHECK(strcmp(ENCODE(demo_Targets, &t), "OutOfRange") == 0);
                t.targets.data[0].id_ = 3;
                t.targets.data[0].weights.len = 1;
                CHECK(strcmp(ENCODE(demo_Targets, &t), "BadSize") == 0);
                t.targets.data[0].weights.len = 2;
                t.has_home = false;
                CHECK(strcmp(ENCODE(demo_Targets, &t), "Missing") == 0);

                demo_Goto_init(&g);
                g.waypoint.north = 1.5;
                g.waypoint.east = 2.0;
                g.waypoint.hold = true;
                CHECK(strcmp(ENCODE(demo_Goto, &g), "Ok") == 0);
                CHECK(demo_Goto_decode(&g2, buf, len, &err) == 0 &&
                      g2.waypoint.north == 1.5 && g2.waypoint.hold);
        }

        /* Calibrated integers and the rest of the packet. */
        {
                static demo_Dump d, d2;
                static demo_Poke p, p2;
                static const uint8_t body[] = {0x7d, 0x0c, 7, 8, 0xde, 0xad};
                demo_Dump_init(&d);
                d.sec_hdr_flag = true;
                d.board_temp = 25;
                d.record_count = 2;
                d.records.len = 2;
                d.records.data[0] = 7;
                d.records.data[1] = 8;
                d.rest.len = 2;
                d.rest.data[0] = 0xde;
                d.rest.data[1] = 0xad;
                CHECK(strcmp(ENCODE(demo_Dump, &d), "Ok") == 0);
                /* Raw values: 25 = -100 + 125 and 2 = -4 + 12 / 2. */
                CHECK(len == 12 && memcmp(buf + 6, body, sizeof body) == 0);
                CHECK(demo_Dump_decode(&d2, buf, len, &err) == 0);
                CHECK(d2.board_temp == 25 && d2.record_count == 2 &&
                      d2.records.data[1] == 8 && d2.rest.len == 2);
                /* The rest of the packet is whatever follows the records, up to
                 * the end that the packet length gives. */
                {
                        uint8_t pk[11];
                        with_length(pk, sizeof pk, 4);
                        CHECK(demo_Dump_decode(&d2, pk, sizeof pk, &err) == 0 &&
                              d2.rest.len == 1 && d2.rest.data[0] == 0xde);
                }
                d.record_count = 200; /* raw 408 does not fit 8 bits */
                CHECK(strcmp(ENCODE(demo_Dump, &d), "OutOfRange") == 0);
                d.record_count = 3;
                CHECK(strcmp(ENCODE(demo_Dump, &d), "BadSize") == 0);
                d.record_count = 2;
                d.rest.len = sizeof d.rest.data + 1;
                CHECK(strcmp(ENCODE(demo_Dump, &d), "TooLong") == 0);

                /* 21 needs raw 5.5, which rounds to 6 and decodes as 22. */
                demo_Poke_init(&p);
                p.level = 21;
                p.data_.len = 2;
                p.data_.data[0] = 0xbe;
                p.data_.data[1] = 0xef;
                CHECK(strcmp(ENCODE(demo_Poke, &p), "Ok") == 0);
                CHECK(demo_Poke_decode(&p2, buf, len, &err) == 0 &&
                      p2.level == 22 && p2.data_.len == 2 &&
                      p2.data_.data[1] == 0xef);
        }

        /* Repeated containers and container sizes. */
        {
                static demo_Log l, l2;
                static const uint8_t body[] = {0x02, 0x30, 0x48, 0xd0,
                                               0x00, 0xf3, 0xff, 0xfc,
                                               0x00, 0x80, 0x00};
                demo_Log_init(&l);
                l.sec_hdr_flag = true;
                l.log_record.len = 2;
                l.log_record.data[0].channel = 3;
                l.log_record.data[0].reading = 0x1234;
                l.log_record.data[1].channel = 15;
                l.log_record.data[1].reading = -1;
                l.closed = true;
                CHECK(strcmp(ENCODE(demo_Log, &l), "Ok") == 0);
                /* Each record is 4 + 2 + 16 bits padded to 32; the trailer 1
                 * padded to 16. */
                CHECK(len == 17 && memcmp(buf + 6, body, sizeof body) == 0);
                CHECK(demo_Log_decode(&l2, buf, len, &err) == 0);
                CHECK(l2.log_record.len == 2 &&
                      l2.log_record.data[1].reading == -1 && l2.closed);
                /* Padding must be there, though its value is ignored. */
                {
                        uint8_t pk[sizeof buf];
                        with_length(pk, len - 1, (unsigned)(len - 1 - 7));
                        CHECK(demo_Log_decode(&l2, pk, len - 1, &err) ==
                              XTCE_PK_TRUNCATED);
                }
                l.log_record.len = 7; /* LogCount's ValidRange allows 6 */
                CHECK(strcmp(ENCODE(demo_Log, &l), "BadSize") == 0);
        }

        /* Sizes from aggregate members. */
        {
                static demo_Batches b, b2;
                static const uint8_t body[] = {0x02, 0x2a, 0xab, 0xb0};
                demo_Batches_init(&b);
                b.sec_hdr_flag = true;
                b.batches.len = 2;
                b.batches.data[0].size = 2;
                b.batches.data[0].values.len = 2;
                b.batches.data[0].values.data[0] = 0xaa;
                b.batches.data[0].values.data[1] = 0xbb;
                CHECK(strcmp(ENCODE(demo_Batches, &b), "Ok") == 0);
                CHECK(len == 10 && memcmp(buf + 6, body, sizeof body) == 0);
                CHECK(demo_Batches_decode(&b2, buf, len, &err) == 0);
                CHECK(b2.batches.len == 2 &&
                      b2.batches.data[0].values.data[1] == 0xbb &&
                      b2.batches.data[1].values.len == 0);
                /* Each copy's size must match its own values. */
                b.batches.data[1].size = 1;
                CHECK(strcmp(ENCODE(demo_Batches, &b), "BadSize") == 0);
        }

        /* Booleans and strings with other encodings. */
        {
                static demo_Reply rp, rp2;
                static const uint8_t body[] = {'y', 'e', 's', 0,    'A',
                                               'B', 'C', 2,   0xc3, 0xa9};
                demo_Reply_init(&rp);
                rp.sec_hdr_flag = true;
                rp.answer = true;
                SET_STR(rp.code, "ABC");
                rp.note_size = 2;
                SET_STR(rp.note, "\xc3\xa9");
                CHECK(strcmp(ENCODE(demo_Reply, &rp), "Ok") == 0);
                CHECK(len == 16 && memcmp(buf + 6, body, sizeof body) == 0);
                CHECK(demo_Reply_decode(&rp2, buf, len, &err) == 0);
                CHECK(rp2.answer && strcmp(rp2.code.data, "ABC") == 0 &&
                      strcmp(rp2.note.data, "\xc3\xa9") == 0);
                rp.answer = false;
                CHECK(strcmp(ENCODE(demo_Reply, &rp), "Ok") == 0 &&
                      memcmp(buf + 6, "no\0\0", 4) == 0);
                /* Strings encoded as binary fill their field exactly. */
                SET_STR(rp.code, "AB");
                CHECK(strcmp(ENCODE(demo_Reply, &rp), "OutOfRange") == 0);
                SET_STR(rp.code, "ABC");
                rp.code.data[1] = (char)0xff;
                CHECK(strcmp(ENCODE(demo_Reply, &rp), "InvalidString") == 0);
                SET_STR(rp.code, "ABC");
                SET_STR(rp.note, "e");
                CHECK(strcmp(ENCODE(demo_Reply, &rp), "BadSize") == 0);
        }

        /* Lookups and locations from other values. */
        {
                static demo_Frame fr, fr2;
                static demo_Tune tu, tu2;
                static const uint8_t body[] = {0x01, 0x3a, 0xbc, 0xd1};
                demo_Frame_init(&fr);
                fr.sec_hdr_flag = true;
                fr.frame_kind = 1;
                fr.frame_gap = 3;
                fr.frame_body.len = 2;
                fr.frame_body.data[0] = 0xab;
                fr.frame_body.data[1] = 0xcd;
                fr.frame_end = true;
                CHECK(strcmp(ENCODE(demo_Frame, &fr), "Ok") == 0);
                /* Kind 1 means a 2-byte body; FrameEnd comes 3 bits after it.
                 */
                CHECK(len == 10 && memcmp(buf + 6, body, sizeof body) == 0);
                CHECK(demo_Frame_decode(&fr2, buf, len, &err) == 0 &&
                      fr2.frame_body.len == 2 && fr2.frame_end);
                fr.frame_kind = 2;
                CHECK(strcmp(ENCODE(demo_Frame, &fr), "BadSize") == 0);

                demo_Tune_init(&tu);
                tu.mode = demo_ModeArgType_SCIENCE;
                tu.setting.len = 3;
                CHECK(strcmp(ENCODE(demo_Tune, &tu), "Ok") == 0);
                CHECK(demo_Tune_decode(&tu2, buf, len, &err) == 0 &&
                      tu2.setting.len == 3);
                tu.mode = demo_ModeArgType_NOMINAL; /* the default of 1 byte */
                CHECK(strcmp(ENCODE(demo_Tune, &tu), "BadSize") == 0);
        }

        /* References to aggregate members. */
        {
                static demo_Route ro, ro2;
                static demo_Plan pl, pl2;
                static const uint8_t body[] = {0x23, 1, 2, 3, 9};
                demo_Route_init(&ro);
                ro.sec_hdr_flag = true;
                ro.route_hdr.kind = 2;
                ro.route_hdr.count = 3;
                ro.stops.len = 3;
                ro.stops.data[0] = 1;
                ro.stops.data[1] = 2;
                ro.stops.data[2] = 3;
                ro.has_detour = true;
                ro.detour = 9;
                CHECK(strcmp(ENCODE(demo_Route, &ro), "Ok") == 0);
                CHECK(len == 11 && memcmp(buf + 6, body, sizeof body) == 0);
                CHECK(demo_Route_decode(&ro2, buf, len, &err) == 0 &&
                      ro2.stops.len == 3 && ro2.has_detour && ro2.detour == 9);
                /* RouteHdr.Kind 2 includes the detour, and RouteHdr.Count
                 * counts the stops. */
                ro.has_detour = false;
                CHECK(strcmp(ENCODE(demo_Route, &ro), "Missing") == 0);
                ro.route_hdr.kind = 1;
                CHECK(strcmp(ENCODE(demo_Route, &ro), "Ok") == 0 && len == 10);
                ro.route_hdr.count = 2;
                CHECK(strcmp(ENCODE(demo_Route, &ro), "BadSize") == 0);

                demo_Plan_init(&pl);
                pl.leg.mode = demo_ModeArgType_NOMINAL;
                pl.leg.count = 2;
                pl.points.len = 2;
                pl.points.data[0] = 10;
                pl.points.data[1] = 20;
                CHECK(strcmp(ENCODE(demo_Plan, &pl), "Ok") == 0);
                CHECK(demo_Plan_decode(&pl2, buf, len, &err) == 0 &&
                      pl2.leg.count == 2 && pl2.points.data[1] == 20);
        }

        /* Boolean expressions. */
        {
                static demo_Gate g, g2;
                static demo_Open o, o2;
                /* GateSpan is there when GateLo < GateHi, or both are zero. */
                demo_Gate_init(&g);
                g.sec_hdr_flag = true;
                g.gate_lo = 1;
                g.gate_hi = 2;
                g.has_gate_span = true;
                g.gate_span = 0xabcd;
                CHECK(strcmp(ENCODE(demo_Gate, &g), "Ok") == 0 && len == 10);
                CHECK(demo_Gate_decode(&g2, buf, len, &err) == 0 &&
                      g2.has_gate_span && g2.gate_span == 0xabcd);
                g.gate_lo = 0;
                g.gate_hi = 0;
                CHECK(strcmp(ENCODE(demo_Gate, &g), "Ok") == 0 && len == 10);
                g.gate_lo = 3;
                CHECK(strcmp(ENCODE(demo_Gate, &g), "Ok") == 0 && len == 8);
                CHECK(demo_Gate_decode(&g2, buf, len, &err) == 0 &&
                      !g2.has_gate_span);

                /* Data is 2 bytes when Lo < Hi. */
                demo_Open_init(&o);
                o.lo = 1;
                o.hi = 2;
                o.data_.len = 2;
                CHECK(strcmp(ENCODE(demo_Open, &o), "Ok") == 0);
                CHECK(demo_Open_decode(&o2, buf, len, &err) == 0 &&
                      o2.data_.len == 2);
                o.lo = 2;
                CHECK(strcmp(ENCODE(demo_Open, &o), "BadSize") == 0);
        }

        /* References to array elements. */
        {
                static demo_ProbeOne pr;
                static demo_CCSDSPacketKind pk;
                static const uint8_t body[] = {0x12, 0x35, 0x20,
                                               0xab, 0xcd, 0x77};
                demo_ProbeOne_init(&pr);
                pr.sec_hdr_flag = true;
                pr.probes.len = 2;
                pr.probes.data[0].id_ = 1;
                pr.probes.data[0].gains.len = 2;
                pr.probes.data[0].gains.data[0] = 2;
                pr.probes.data[0].gains.data[1] = 3;
                pr.probes.data[1].id_ = 5;
                pr.probes.data[1].gains.len = 2;
                pr.probes.data[1].gains.data[0] = 2;
                pr.probe_data.len = 2;
                pr.probe_data.data[0] = 0xab;
                pr.probe_data.data[1] = 0xcd;
                pr.probe_note = 0x77;
                CHECK(strcmp(ENCODE(demo_ProbeOne, &pr), "Ok") == 0);
                CHECK(len == 12 && memcmp(buf + 6, body, sizeof body) == 0);
                /* Probes[0].Id == 1 picks ProbeOne; Probes[1].Gains[0] sizes
                 * the data. */
                CHECK(demo_CCSDSPacketKind_decode(&pk, buf, len, &err) == 0 &&
                      pk.tag == demo_CCSDSPacketKind_ProbeOne_tag);
                CHECK(pk.u.ProbeOne.probe_note == 0x77 &&
                      pk.u.ProbeOne.probe_data.len == 2);
                pr.probes.data[0].id_ = 4;
                CHECK(strcmp(ENCODE(demo_ProbeOne, &pr), "Mismatch") == 0);
                /* The criterion indexes Probes, so its length is checked first.
                 */
                pr.probes.len = 0;
                CHECK(strcmp(ENCODE(demo_ProbeOne, &pr), "BadSize") == 0);
        }

        /* Elements of arrays whose count varies. */
        {
                static demo_Tally t, back;
                static const uint8_t body[] = {0x02, 0x02, 0x07,
                                               0xab, 0xcd, 0x55};
                static const uint8_t one[] = {0x09, 0x72, 0xc0, 0x2a,
                                              0x00, 0x01, 0x01, 0x00};
                static const uint8_t none[] = {0x09, 0x72, 0xc0, 0x2a,
                                               0x00, 0x00, 0x00};
                demo_Tally_init(&t);
                t.sec_hdr_flag = true;
                t.tallies.len = 2;
                t.tallies.data[0] = 2;
                t.tallies.data[1] = 7;
                t.tally_data.len = 2;
                t.tally_data.data[0] = 0xab;
                t.tally_data.data[1] = 0xcd;
                t.has_tally_extra = true;
                t.tally_extra = 0x55;
                CHECK(strcmp(ENCODE(demo_Tally, &t), "Ok") == 0);
                CHECK(len == 12 && memcmp(buf + 6, body, sizeof body) == 0);
                CHECK(demo_Tally_decode(&back, buf, len, &err) == 0 &&
                      back.has_tally_extra && back.tally_extra == 0x55);
                /* Tallies[1] == 7 includes the extra, so with one tally the
                 * element it reads is missing. */
                t.tallies.len = 1;
                t.tallies.data[0] = 0;
                t.tally_data.len = 0;
                CHECK(strcmp(ENCODE(demo_Tally, &t), "BadSize") == 0 &&
                      strcmp(err.field, "Tallies") == 0);
                /* Tallies[0] sizes the data, so without tallies it is missing
                 * too. */
                t.tallies.len = 0;
                CHECK(strcmp(ENCODE(demo_Tally, &t), "BadSize") == 0);
                /* TallyCount is computed from the length of Tallies, and
                 * TallyCount >= 3 includes TallyMore. */
                t.tallies.len = 3;
                t.tallies.data[0] = 0;
                t.tallies.data[1] = 7;
                t.tallies.data[2] = 1;
                t.has_tally_more = true;
                t.tally_more = 0x66;
                CHECK(strcmp(ENCODE(demo_Tally, &t), "Ok") == 0);
                CHECK(len == 12 && buf[6] == 3 && buf[11] == 0x66);
                CHECK(demo_Tally_decode(&back, buf, len, &err) == 0 &&
                      back.has_tally_more && back.tally_more == 0x66);
                t.has_tally_more = false;
                CHECK(strcmp(ENCODE(demo_Tally, &t), "Missing") == 0);
                /* Decoding fails the same way. */
                CHECK(demo_Tally_decode(&back, one, sizeof one, &err) ==
                              XTCE_PK_BAD_SIZE &&
                      strcmp(err.field, "Tallies") == 0);
                CHECK(demo_Tally_decode(&back, none, sizeof none, &err) ==
                      XTCE_PK_BAD_SIZE);
        }

        /* Array subsets. */
        {
                static demo_MatrixMiddle mm, mm2;
                static demo_Fill fi, fi2;
                static const uint8_t body[] = {0x17, 0x26, 0xab};
                /* Rows 1..2 and columns 1..2 of the 3x4 Matrix; Matrix[1][2] is
                 * the second of them. */
                demo_MatrixMiddle_init(&mm);
                mm.sec_hdr_flag = true;
                mm.matrix.len = 4;
                mm.matrix.data[0] = 1;
                mm.matrix.data[1] = 7;
                mm.matrix.data[2] = 2;
                mm.matrix.data[3] = 6;
                mm.has_matrix_note = true;
                mm.matrix_note = 0xab;
                CHECK(strcmp(ENCODE(demo_MatrixMiddle, &mm), "Ok") == 0);
                CHECK(len == 9 && memcmp(buf + 6, body, sizeof body) == 0);
                CHECK(demo_MatrixMiddle_decode(&mm2, buf, len, &err) == 0 &&
                      mm2.has_matrix_note && mm2.matrix.data[3] == 6);

                /* Slots 2..Last: encoding sets Last to the last index. */
                demo_Fill_init(&fi);
                fi.slots.len = 2;
                fi.slots.data[0] = 0xaa;
                fi.slots.data[1] = 0xbb;
                CHECK(strcmp(ENCODE(demo_Fill, &fi), "Ok") == 0);
                CHECK(demo_Fill_decode(&fi2, buf, len, &err) == 0 &&
                      fi2.slots.len == 2 && fi2.slots.data[1] == 0xbb);
                fi.slots.len = 4; /* Last's ValidRange allows 4, three slots */
                CHECK(strcmp(ENCODE(demo_Fill, &fi), "BadSize") == 0);
        }

        /* Decode errors carry a message. */
        {
                char msg[128];
                CHECK(demo_Housekeeping_decode(&hk2, buf, 2, &err) ==
                      XTCE_PK_TRUNCATED);
                xtce_pk_error_message(&err, msg, sizeof msg);
                CHECK(strncmp(msg, "packet too short for ", 21) == 0);
        }

        /* Byte and zero writes, aligned and unaligned, overwrite bits written
         * before a seek back, as in the Rust runtime's test. */
        {
                static const uint8_t ff[6] = {0xff, 0xff, 0xff,
                                              0xff, 0xff, 0xff};
                static const uint8_t ab[2] = {0x12, 0x34}, c = 0xab;
                static const uint8_t expect[6] = {0x12, 0x34, 0xb5,
                                                  0x60, 0x00, 0x0f};
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
         * above FLT_MAX round down to it, and half an ulp above rounds to
         * infinity. */
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

        /* Parity is a kind of its own, and a kind the generator never
         * writes is refused rather than taken for it. */
        {
                static const uint8_t seven = 0x07;
                xtce_pk_checksum_spec spec;
                uint64_t sum = 2;
                memset(&spec, 0, sizeof spec);
                spec.kind = XTCE_PK_PARITY;
                CHECK(xtce_pk_checksum(&spec, &seven, 0, 8, "f", &err,
                                       &sum) == 0 &&
                      sum == 1);
                spec.odd = true;
                CHECK(xtce_pk_checksum(&spec, &seven, 0, 8, "f", &err,
                                       &sum) == 0 &&
                      sum == 0);
                spec.kind = -1;
                CHECK(xtce_pk_checksum(&spec, &seven, 0, 8, "f", &err,
                                       &sum) == XTCE_PK_NO_ALGORITHM);
        }

        /* A batch refuses a buffer too small for its length; packets added
         * to it are decoded back in order, and one that doesn't fit is
         * refused, leaving the batch as it was. */
        {
                static uint8_t space[XTCE_PK_BATCH_LENGTH_BYTES +
                                     2 * demo_Housekeeping_MAX_BYTES];
                xtce_pk_batch b;
                demo_Housekeeping first, second;
                size_t n;
                demo_Housekeeping_init(&first);
                demo_Housekeeping_init(&second);
                CHECK(xtce_pk_batch_init(&b, space,
                                         XTCE_PK_BATCH_LENGTH_BYTES - 1,
                                         &err) == XTCE_PK_NO_SPACE);
                CHECK(xtce_pk_batch_init(&b, NULL, sizeof space, &err) ==
                      XTCE_PK_NO_SPACE);
                CHECK(xtce_pk_batch_init(&b, space, sizeof space, &err) ==
                      0);
                hk = housekeeping();
                CHECK(demo_Housekeeping_batch_add(&hk, &b, &err) == 0);
                hk.seq_count = 43;
                CHECK(demo_Housekeeping_batch_add(&hk, &b, &err) == 0);
                CHECK(demo_Housekeeping_batch_add(&hk, &b, &err) ==
                      XTCE_PK_NO_SPACE);
                CHECK(b.len == sizeof space);
                CHECK(xtce_pk_batch_length(&b, &n, &err) == 0 &&
                      n == 2 * demo_Housekeeping_MAX_BYTES);
                CHECK(demo_Housekeeping_batch_next(&first, &b, &err) == 0 &&
                      first.seq_count == 42);
                CHECK(demo_Housekeeping_batch_next(&second, &b, &err) == 0 &&
                      second.seq_count == 43);
                CHECK(xtce_pk_batch_remaining(&b) == 0);
                CHECK(xtce_pk_batch_finish(&b, &err) == 0);
        }

        /* A batch received whole, or read length first, decodes the same
         * as the one sent; one whose length doesn't match its bytes, or
         * that ends early, is refused and left empty. */
        {
                static uint8_t sent[XTCE_PK_BATCH_LENGTH_BYTES +
                                    2 * demo_Housekeeping_MAX_BYTES];
                static uint8_t space[sizeof sent];
                xtce_pk_batch out, in;
                demo_Housekeeping got;
                source src;
                demo_Housekeeping_init(&got);
                CHECK(xtce_pk_batch_init(&out, sent, sizeof sent, &err) ==
                      0);
                hk = housekeeping();
                CHECK(demo_Housekeeping_batch_add(&hk, &out, &err) == 0);
                hk.seq_count = 43;
                CHECK(demo_Housekeeping_batch_add(&hk, &out, &err) == 0);
                CHECK(xtce_pk_batch_init(&in, space, sizeof space, &err) ==
                      0);

                memcpy(space, sent, out.len);
                CHECK(xtce_pk_batch_received(&in, out.len, &err) == 0);
                CHECK(demo_Housekeeping_batch_next(&got, &in, &err) == 0 &&
                      got.seq_count == 42);
                CHECK(xtce_pk_batch_finish(&in, &err) ==
                      XTCE_PK_BAD_LENGTH);
                CHECK(demo_Housekeeping_batch_next(&got, &in, &err) == 0 &&
                      got.seq_count == 43);
                CHECK(xtce_pk_batch_finish(&in, &err) == 0);

                memcpy(space, sent, out.len);
                CHECK(xtce_pk_batch_received(&in, out.len - 1, &err) ==
                      XTCE_PK_BAD_LENGTH);
                CHECK(in.len == XTCE_PK_BATCH_LENGTH_BYTES);
                CHECK(xtce_pk_batch_received(&in, 2, &err) ==
                      XTCE_PK_BAD_LENGTH);

                CHECK(xtce_pk_batch_load(&in, sent, out.len, &err) == 0);
                CHECK(demo_Housekeeping_batch_next(&got, &in, &err) == 0 &&
                      got.seq_count == 42);
                CHECK(xtce_pk_batch_load(&in, sent, out.len - 1, &err) ==
                      XTCE_PK_BAD_LENGTH);
                CHECK(xtce_pk_batch_load(&in, sent, 2, &err) ==
                      XTCE_PK_BAD_LENGTH);
                CHECK(in.len == out.len &&
                      in.pos == out.len - demo_Housekeeping_MAX_BYTES);
                CHECK(demo_Housekeeping_batch_next(&got, &in, &err) == 0 &&
                      got.seq_count == 43);
                CHECK(xtce_pk_batch_finish(&in, &err) == 0);
                CHECK(xtce_pk_batch_init(&in, space, out.len - 1, &err) ==
                      0);
                CHECK(xtce_pk_batch_load(&in, sent, out.len, &err) ==
                      XTCE_PK_BAD_LENGTH);
                CHECK(xtce_pk_batch_init(&in, space, sizeof space, &err) ==
                      0);

                src.at = sent;
                src.left = out.len;
                CHECK(xtce_pk_batch_read(&in, source_read, &src, &err) ==
                      0);
                CHECK(in.len == out.len && src.left == 0);
                CHECK(demo_Housekeeping_batch_next(&got, &in, &err) == 0 &&
                      got.seq_count == 42);
                CHECK(demo_Housekeeping_batch_next(&got, &in, &err) == 0 &&
                      got.seq_count == 43);
                CHECK(xtce_pk_batch_finish(&in, &err) == 0);

                src.at = sent;
                src.left = out.len - 1;
                CHECK(xtce_pk_batch_read(&in, source_read, &src, &err) ==
                      XTCE_PK_TRUNCATED);
                CHECK(in.len == XTCE_PK_BATCH_LENGTH_BYTES);
        }

        if (failures) return EXIT_FAILURE;
        printf("packet_test: all checks passed\n");
        return EXIT_SUCCESS;
}
