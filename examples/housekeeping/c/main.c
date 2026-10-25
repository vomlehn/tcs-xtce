/* Encodes housekeeping Status packets described by housekeeping.xml into one
 * stream, then walks the stream and decodes them again, using the code
 * tcs-xtce generated into housekeeping.h.
 *
 * A Status packet shows three things the generated code does:
 *
 * - PacketLength holds the number of bytes after it. Encoding computes it,
 *   and decoding checks it, so packets stored one after another can be split
 *   apart.
 * - Temperature, BusVoltage and HeaterPower are calibrated: the structure
 *   holds degrees, volts and watts, and the packet holds raw counts.
 * - HeaterPower is in the packet only when the heater is on.
 *
 * The same file is built as C and as C++; both, and the Rust and Python
 * versions of this program, print identical output.
 *
 * The C build finds housekeeping.h in c/ (--lang c) and the C++ build in
 * cxx/ (--lang c++), so it is included with <> rather than "": a quoted
 * include would always find the copy next to this file.
 */
#include <housekeeping.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define N_READINGS 4

/* A Status to encode, and the values decoding it must give: the nearest the
 * calibrators can represent.  The heater powers are ignored when the heater
 * is off. */
struct reading {
        bool heater_on;
        double temperature, bus_voltage, heater_power;
        double want_temperature, want_bus_voltage, want_heater_power;
};

static const struct reading READINGS[N_READINGS] = {
        {false, 21.53, 28.3, 0, 21.5, 28.25, 0},
        {true, -12.25, 27.5, 4.5, -12.25, 27.5, 4.5},
        {true, -20.01, 26.9, 12.74, -20.0, 26.875, 12.734375},
        {false, 5.0, 28.0, 0, 5.0, 28.0, 0},
};

static void fail(const char* fmt, ...) {
        va_list ap;
        va_start(ap, fmt);
        fputs("housekeeping: ", stderr);
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

static const char* text(const housekeeping_Status* s, char* buf, size_t cap) {
        housekeeping_Status_describe(s, buf, cap);
        return buf;
}

static void status(housekeeping_Status* s, bool heater_on, double temperature,
                   double bus_voltage, double heater_power) {
        housekeeping_Status_init(s);
        s->heater_on = heater_on;
        s->temperature = temperature;
        s->bus_voltage = bus_voltage;
        s->has_heater_power = heater_on;
        s->heater_power = s->has_heater_power ? heater_power : 0;
}

static int same(const housekeeping_Status* a, const housekeeping_Status* b) {
        return a->heater_on == b->heater_on &&
               a->temperature == b->temperature &&
               a->bus_voltage == b->bus_voltage &&
               a->has_heater_power == b->has_heater_power &&
               (!a->has_heater_power || a->heater_power == b->heater_power);
}

/* Encodes a Status that must fail, and prints why it did. */
static void try_encode(const housekeeping_Status* s) {
        uint8_t out[housekeeping_Status_MAX_BYTES];
        char t[256], h[64], msg[128];
        xtce_pk_error err;
        size_t len;
        if (housekeeping_Status_encode(s, out, sizeof out, &len, &err) == 0)
                fail("encoding %s gave %s", text(s, t, sizeof t),
                     hex(out, len, h, sizeof h));
        xtce_pk_error_message(&err, msg, sizeof msg);
        printf("encode %s -> error:%s (%s)\n", text(s, t, sizeof t),
               xtce_pk_error_kind(err.code), msg);
}

/* Decodes bytes that must fail, and prints why they did. */
static void try_decode(const uint8_t* data, size_t len, const char* why) {
        housekeeping_Status s;
        char t[256], h[64], msg[128];
        xtce_pk_error err;
        if (housekeeping_Status_decode(&s, data, len, &err) == 0)
                fail("decoding %s gave %s", hex(data, len, h, sizeof h),
                     text(&s, t, sizeof t));
        xtce_pk_error_message(&err, msg, sizeof msg);
        printf("decode %s (%s) -> error:%s (%s)\n", hex(data, len, h, sizeof h),
               why, xtce_pk_error_kind(err.code), msg);
}

int main(void) {
        /* The packets, one after another, and a buffer for one packet,
         * preallocated to housekeeping_Status_MAX_BYTES (7) and reused for
         * every packet. */
        uint8_t stream[N_READINGS * housekeeping_Status_MAX_BYTES];
        uint8_t out[housekeeping_Status_MAX_BYTES];
        uint8_t bad[housekeeping_Status_MAX_BYTES + 1];
        char t[256], h[64], h2[64];
        size_t i, len, n = 0, at = 0;
        xtce_pk_error err;
        housekeeping_Status s;

        /* Encode each reading and append it to the stream, as a recorder
         * would store packets: one after another, with nothing between
         * them. */
        for (i = 0; i < N_READINGS; ++i) {
                const struct reading* r = &READINGS[i];
                status(&s, r->heater_on, r->temperature, r->bus_voltage,
                       r->heater_power);
                if (housekeeping_Status_encode(&s, out, sizeof out, &len, &err))
                        fail("encoding %s failed", text(&s, t, sizeof t));
                printf("encode %s -> %s\n", text(&s, t, sizeof t),
                       hex(out, len, h, sizeof h));
                memcpy(stream + n, out, len);
                n += len;
        }
        printf("stream: %d packets, %zu bytes\n", N_READINGS, n);

        /* Walk the stream.  Each packet starts with PacketLength, the number
         * of bytes after it, so the packet is 2 + PacketLength bytes long. */
        for (i = 0; i < N_READINGS; ++i) {
                const struct reading* r = &READINGS[i];
                housekeeping_Status want;
                size_t size;
                if (n - at < 2)
                        fail("the stream ends inside a packet at byte %zu", at);
                size = 2 + ((size_t)stream[at] << 8 | stream[at + 1]);
                if (size > n - at) size = n - at;
                if (housekeeping_Status_decode(&s, stream + at, size, &err))
                        fail("decoding at %zu failed", at);
                printf("decode at %zu: %zu bytes -> %s\n", at, size,
                       text(&s, t, sizeof t));
                status(&want, r->heater_on, r->want_temperature,
                       r->want_bus_voltage, r->want_heater_power);
                if (!same(&s, &want)) {
                        char w[256];
                        fail("decoded %s, expected %s", text(&s, t, sizeof t),
                             text(&want, w, sizeof w));
                }
                if (housekeeping_Status_encode(&s, out, sizeof out, &len,
                                               &err) ||
                    len != size || memcmp(out, stream + at, size) != 0)
                        fail("re-encoding %s gave %s, expected %s",
                             text(&s, t, sizeof t), hex(out, len, h, sizeof h),
                             hex(stream + at, size, h2, sizeof h2));
                at += size;
        }
        if (at != n) fail("%zu bytes after the last packet", n - at);

        /* What the generated code rejects. */
        status(&s, true, 20.0, 28.0, 0);
        s.has_heater_power = false;
        try_encode(&s);
        status(&s, false, 300.0, 28.0, 0);
        try_encode(&s);
        try_decode(stream + 5, 4, "cut short");
        memcpy(bad, stream, 5);
        bad[1] += 1;
        bad[5] = 0;
        try_decode(bad, 6, "PacketLength one too many");
        return 0;
}
