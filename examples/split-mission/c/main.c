/* Records a rover's PUS telemetry, described by dt.xtce, pus.xtce and
 * rover.xtce, in one stream, and decodes it again, using the code tcs-xtce
 * generated into rover.h from the three documents together.
 *
 * The stream starts with the digest of the set of documents, which doesn't
 * depend on the order they are listed in, so that whatever reads it can
 * check that it was built from the same definitions.  The same file is
 * built as C and as C++; both, and the Rust and Python versions of this
 * program, print identical output.  Run it in the directory that holds the
 * documents.
 *
 * The C build finds rover.h in c/ (--lang c) and the C++ build in cxx/
 * (--lang c++), so it is included with <> rather than "": a quoted include
 * would always find the copy next to this file.
 */
#include <rover.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <xtce-digest.h>

#define N_PACKETS 3
#define DIGEST_BYTES 16

/* The mission's documents: those the others build on first. */
static const char* const DOCUMENTS[] = {"dt.xtce", "pus.xtce", "rover.xtce"};
static const char* const SHUFFLED[] = {"rover.xtce", "dt.xtce", "pus.xtce"};

/** Prints an error message to standard error and exits with status 1.
 *
 * @param fmt  The message's printf format.
 * @param ...  The values it formats. */
static void fail(const char* fmt, ...) {
        va_list ap;
        va_start(ap, fmt);
        fputs("split-mission: ", stderr);
        vfprintf(stderr, fmt, ap);
        fputc('\n', stderr);
        va_end(ap);
        exit(1);
}

static const char* hex(const uint8_t* b, size_t n, char* buf, size_t cap) {
        xtce_pk_text t = xtce_pk_text_init(buf, cap);
        xtce_pk_text_bytes(&t, b, n);
        return buf + 2; /* drop the "0x" */
}

static void digest(const char* const* paths, uint8_t* out) {
        xtce_digest_error err;
        if (xtce_digest_files_bytes(paths, 3, out, &err))
                fail("%s", err.message);
}

/** Makes a housekeeping packet: PUS service 3, subservice 25.
 *
 * @param k           Filled in with the packet.
 * @param count       Its CCSDS sequence count and PUS message counter.
 * @param battery     The battery voltage, in volts.
 * @param motor_temp  The motor temperature, in degrees Celsius.
 * @param left        The turns of the left wheel since the last report.
 * @param right       The turns of the right wheel since the last report. */
static void hk(rover_CcsdsKind* k, uint16_t count, double battery,
               double motor_temp, uint16_t left, uint16_t right) {
        rover_Hk* p = &k->u.Hk;
        k->tag = rover_CcsdsKind_Hk_tag;
        rover_Hk_init(p);
        p->ccsds_apid = 100;
        p->ccsds_seq_flags = 3;
        p->ccsds_seq_count = count;
        p->pus_version = 2;
        p->pus_counter = count;
        p->battery = battery;
        p->motor_temp = motor_temp;
        p->left_turns = left;
        p->right_turns = right;
}

/** Compares two packets by their kinds and descriptions.
 *
 * @param a  One packet.
 * @param b  The other.
 * @return   Nonzero if they are the same. */
static int same(const rover_CcsdsKind* a, const rover_CcsdsKind* b) {
        char ta[512], tb[512];
        rover_CcsdsKind_describe(a, ta, sizeof ta);
        rover_CcsdsKind_describe(b, tb, sizeof tb);
        return a->tag == b->tag && strcmp(ta, tb) == 0;
}

/** Writes a stream of the mission's packets after the digest of its
 * documents, reads it back, and shows what the generated code rejects;
 * exits with status 1 if a result is not what it should be.
 *
 * @return  0. */
int main(void) {
        static uint8_t stream[DIGEST_BYTES +
                              N_PACKETS * rover_CcsdsKind_MAX_BYTES];
        rover_CcsdsKind packets[N_PACKETS], got;
        uint8_t set[DIGEST_BYTES], other[DIGEST_BYTES];
        uint8_t out[rover_CcsdsKind_MAX_BYTES];
        char text[512], h[256], msg[128];
        size_t len, at, n, i;
        xtce_pk_error err;
        rover_Event* e;

        /* The digest of the set is the same in any order. */
        digest(DOCUMENTS, set);
        printf("set digest: %s (%s, %s, %s)\n",
               hex(set, DIGEST_BYTES, h, sizeof h), DOCUMENTS[0],
               DOCUMENTS[1], DOCUMENTS[2]);
        digest(SHUFFLED, other);
        if (memcmp(other, set, DIGEST_BYTES) != 0)
                fail("the digest depends on the order of the documents");
        printf("set digest: the same for %s, %s, %s\n", SHUFFLED[0],
               SHUFFLED[1], SHUFFLED[2]);

        /* The stream: the digest, then the packets, one after another. */
        hk(&packets[0], 1, 28.5, 41.25, 120, 118);
        packets[1].tag = rover_CcsdsKind_Event_tag;
        e = &packets[1].u.Event;
        rover_Event_init(e);
        e->ccsds_apid = 100;
        e->ccsds_seq_flags = 3;
        e->ccsds_seq_count = 2;
        e->pus_version = 2;
        e->pus_counter = 2;
        e->event = rover_EventType_OVERHEAT;
        e->event_data = 85;
        hk(&packets[2], 3, 27.75, 38.5, 0, 3);
        memcpy(stream, set, DIGEST_BYTES);
        n = DIGEST_BYTES;
        for (i = 0; i < N_PACKETS; ++i) {
                rover_CcsdsKind_describe(&packets[i], text, sizeof text);
                if (rover_CcsdsKind_encode(&packets[i], stream + n,
                                           sizeof stream - n, &len, &err)) {
                        xtce_pk_error_message(&err, msg, sizeof msg);
                        fail("%s: %s", text, msg);
                }
                printf("encode %s -> %s\n", text,
                       hex(stream + n, len, h, sizeof h));
                n += len;
        }
        printf("stream: the set digest, then %d packets, %d bytes\n",
               N_PACKETS, (int)n);

        /* Reading it back: check the digest, then split the stream by each
         * packet's length (the bytes after its 6-byte header, minus one). */
        digest(DOCUMENTS, other);
        if (memcmp(stream, other, DIGEST_BYTES) != 0)
                fail("the stream's digest isn't that of these documents");
        printf("set digest: the stream's matches\n");
        at = DIGEST_BYTES;
        for (i = 0; i < N_PACKETS; ++i) {
                len = (size_t)((stream[at + 4] << 8) | stream[at + 5]) + 7;
                if (rover_CcsdsKind_decode(&got, stream + at, len, &err)) {
                        xtce_pk_error_message(&err, msg, sizeof msg);
                        fail("decoding at %d: %s", (int)at, msg);
                }
                rover_CcsdsKind_describe(&got, text, sizeof text);
                printf("decode at %d: %d bytes -> %s\n", (int)at, (int)len,
                       text);
                if (!same(&got, &packets[i])) fail("%s isn't sent", text);
                at += len;
        }

        /* A wheel can't turn more than 1000 times between reports. */
        hk(&got, 4, 27.5, 30.0, 1001, 0);
        rover_CcsdsKind_describe(&got, text, sizeof text);
        if (rover_CcsdsKind_encode(&got, out, sizeof out, &len, &err) == 0)
                fail("encoding %s gave %s", text, hex(out, len, h, sizeof h));
        xtce_pk_error_message(&err, msg, sizeof msg);
        printf("encode %s -> error:%s (%s)\n", text,
               xtce_pk_error_kind(err.code), msg);
        return 0;
}
