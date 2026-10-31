/* Encodes and decodes a probe's status, described by modes.xtce, using the
 * code tcs-xtce generated into probe.h.
 *
 * The status's mode is an enumeration, and everything that depends on it
 * names a label: Target is there only in SCIENCE mode and Station only in
 * DOWNLINK mode, a SafeStatus is a Status in SAFE mode, and the mode's alarm
 * is a warning in SAFE mode and a watch in TEST mode.  So this program names
 * labels too, never numbers.  The same file is built as C and as C++; both,
 * and the Rust and Python versions of this program, print identical output.
 *
 * The C build finds probe.h in c/ (--lang c) and the C++ build in cxx/
 * (--lang c++), so it is included with <> rather than "": a quoted include
 * would always find the copy next to this file.
 */
#include <probe.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define MAX_BYTES probe_StatusKind_MAX_BYTES

/** Prints an error message to standard error and exits with status 1.
 *
 * @param fmt  The message's printf format.
 * @param ...  The values it formats. */
static void fail(const char* fmt, ...) {
        va_list ap;
        va_start(ap, fmt);
        fputs("enum-labels: ", stderr);
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

/** Converts a string of hexadecimal digit pairs to bytes.
 *
 * @param h    The digits, two per byte; an odd last digit is ignored.
 * @param out  Filled in with the bytes; must hold strlen(h) / 2.
 * @return     How many bytes were written. */
static size_t from_hex(const char* h, uint8_t* out) {
        size_t n = 0;
        for (; h[0] && h[1]; h += 2) {
                char byte[3] = {h[0], h[1], 0};
                out[n++] = (uint8_t)strtoul(byte, NULL, 16);
        }
        return n;
}

/** Encodes a status of either kind, prints it, and stores the bytes;
 * exits if it can't be encoded.
 *
 * @param k    The status.
 * @param out  Filled in with the packet; must hold MAX_BYTES.
 * @return     The packet's length in bytes. */
static size_t encode(const probe_StatusKind* k, uint8_t* out) {
        char t[256], h[64], msg[128];
        xtce_pk_error err;
        size_t len;
        probe_StatusKind_describe(k, t, sizeof t);
        if (probe_StatusKind_encode(k, out, MAX_BYTES, &len, &err)) {
                xtce_pk_error_message(&err, msg, sizeof msg);
                fail("encoding %s: %s", t, msg);
        }
        printf("encode %s -> %s\n", t, hex(out, len, h, sizeof h));
        return len;
}

/** Decodes a status of either kind and prints it, with its alarms; exits
 * if it can't be decoded.
 *
 * @param data  The packet.
 * @param len   Its length in bytes.
 * @param k     Filled in with the status. */
static void decode(const uint8_t* data, size_t len, probe_StatusKind* k) {
        char t[256], h[64], a[256], msg[128];
        xtce_pk_alarm alarms[probe_StatusKind_ALARMS];
        xtce_pk_error err;
        if (probe_StatusKind_decode(k, data, len, &err)) {
                xtce_pk_error_message(&err, msg, sizeof msg);
                fail("decoding %s: %s", hex(data, len, h, sizeof h), msg);
        }
        probe_StatusKind_describe(k, t, sizeof t);
        xtce_pk_describe_alarms(alarms, probe_StatusKind_alarms(k, alarms), a,
                                sizeof a);
        printf("decode %s -> %s | alarms: %s\n", hex(data, len, h, sizeof h),
               t, a);
}

/** Compares two Status packets field by field, ignoring fields that are
 * absent.
 *
 * @param a  One status.
 * @param b  The other.
 * @return   Nonzero if they are the same. */
static int same_status(const probe_Status* a, const probe_Status* b) {
        return a->mode == b->mode && a->battery == b->battery &&
               a->has_target == b->has_target &&
               (!a->has_target || a->target == b->target) &&
               a->has_station == b->has_station &&
               (!a->has_station || a->station == b->station);
}

/** Shows how labels name the mode, encoding and decoding a status in each
 * mode; exits with status 1 if a result is not what it should be.
 *
 * @return  0. */
int main(void) {
        static const int64_t values[] = {0, 2, 20, 40};
        static const char* const odd[] = {"1463", "2850"};
        probe_StatusKind kinds[4], k;
        uint8_t packets[4][MAX_BYTES], data[MAX_BYTES], again[MAX_BYTES];
        size_t lens[4], i, n, len;
        char t[256], h[64], msg[128];
        xtce_pk_error err;
        probe_Status s;

        /* Labels name the values, and one label can name a range of them. */
        for (i = 0; i < 4; ++i) {
                const char* l = probe_ModeType_label(values[i]);
                if (l)
                        printf("mode %d is %s\n", (int)values[i], l);
                else
                        printf("mode %d has no label\n", (int)values[i]);
        }

        /* One status in each mode but SAFE, which is a SafeStatus: its
         * restriction fixes the mode, so the structure has no mode field. */
        for (i = 0; i < 4; ++i) memset(&kinds[i], 0, sizeof kinds[i]);
        for (i = 0; i < 3; ++i) kinds[i].tag = probe_StatusKind_Status_tag;
        probe_Status_init(&kinds[0].u.Status);
        kinds[0].u.Status.mode = probe_ModeType_IDLE;
        kinds[0].u.Status.battery = 80;
        probe_Status_init(&kinds[1].u.Status);
        kinds[1].u.Status.mode = probe_ModeType_SCIENCE;
        kinds[1].u.Status.battery = 75;
        kinds[1].u.Status.has_target = true;
        kinds[1].u.Status.target = 1234;
        probe_Status_init(&kinds[2].u.Status);
        kinds[2].u.Status.mode = probe_ModeType_DOWNLINK;
        kinds[2].u.Status.battery = 70;
        kinds[2].u.Status.has_station = true;
        kinds[2].u.Status.station = 7;
        kinds[3].tag = probe_StatusKind_SafeStatus_tag;
        probe_SafeStatus_init(&kinds[3].u.SafeStatus);
        kinds[3].u.SafeStatus.battery = 9;
        kinds[3].u.SafeStatus.reason = probe_ReasonType_LOW_POWER;
        for (i = 0; i < 4; ++i) lens[i] = encode(&kinds[i], packets[i]);

        /* Decoding picks the SafeStatus by its mode's label, SAFE. */
        for (i = 0; i < 4; ++i) {
                int back;
                decode(packets[i], lens[i], &k);
                if (i < 3)
                        back = k.tag == probe_StatusKind_Status_tag &&
                               same_status(&k.u.Status, &kinds[i].u.Status);
                else
                        back = k.tag == probe_StatusKind_SafeStatus_tag &&
                               k.u.SafeStatus.battery == 9 &&
                               k.u.SafeStatus.reason ==
                                       probe_ReasonType_LOW_POWER;
                if (!back)
                        fail("%s decoded to something else",
                             hex(packets[i], lens[i], h, sizeof h));
        }

        /* A value in TEST's range, and one with no label: decoding keeps
         * it. */
        for (i = 0; i < 2; ++i) {
                n = from_hex(odd[i], data);
                decode(data, n, &k);
                if (probe_StatusKind_encode(&k, again, sizeof again, &len,
                                            &err) ||
                    len != n || memcmp(again, data, n) != 0)
                        fail("%s encodes again as something else", odd[i]);
        }

        /* SCIENCE mode needs a Target. */
        probe_Status_init(&s);
        s.mode = probe_ModeType_SCIENCE;
        s.battery = 50;
        probe_Status_describe(&s, t, sizeof t);
        if (probe_Status_encode(&s, data, sizeof data, &len, &err) == 0)
                fail("encoding %s gave %s", t, hex(data, len, h, sizeof h));
        xtce_pk_error_message(&err, msg, sizeof msg);
        printf("encode %s -> error:%s (%s)\n", t, xtce_pk_error_kind(err.code),
               msg);
        return 0;
}
