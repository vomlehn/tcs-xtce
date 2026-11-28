/* Tests the C interface to the run-time codec on the demo mission: every
 * vector of tests/mission/vectors.txt unpacks, and packs again to the
 * bytes the generated codecs give, with the demo's custom algorithms
 * supplied as C functions; and lookups, values, alarms and a stream work
 * as the Rust tests find them.
 *
 * Usage: c_test MISSION_DIR */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "tcs-dynxtce.h"

/** The checks that failed. */
static int failures;

/** Records a check.
 *
 * @param ok    Whether it held.
 * @param what  What it checked. */
static void check(int ok, const char *what)
{
        if (!ok) {
                fprintf(stderr, "FAIL: %s: %s\n", what,
                        tcs_dynxtce_last_error());
                failures++;
        }
}

/** Reads a whole file.
 *
 * @param path  Its path.
 * @param len   Where its length goes.
 * @return      Its bytes, to free, or NULL. */
static unsigned char *slurp(const char *path, size_t *len)
{
        FILE *f = fopen(path, "rb");
        unsigned char *b;
        long n;

        if (f == NULL)
                return NULL;
        fseek(f, 0, SEEK_END);
        n = ftell(f);
        fseek(f, 0, SEEK_SET);
        b = malloc((size_t)n + 1);
        if (b != NULL && fread(b, 1, (size_t)n, f) != (size_t)n) {
                free(b);
                b = NULL;
        }
        fclose(f);
        if (b != NULL) {
                b[n] = 0;
                *len = (size_t)n;
        }
        return b;
}

/** Reads hexadecimal.
 *
 * @param h    The digits, ended by a space, a NUL or a newline.
 * @param out  Where the bytes go.
 * @param cap  The room there.
 * @return     How many bytes. */
static size_t unhex(const char *h, unsigned char *out, size_t cap)
{
        size_t n = 0;
        unsigned int x;

        while (n < cap && sscanf(h, "%2x", &x) == 1 && h[0] != ' ') {
                out[n++] = (unsigned char)x;
                h += 2;
        }
        return n;
}

/* --- the demo's custom algorithms, as examples/packet_tool.rs has them */

/** Whether the first input is even.
 *
 * @param ctx  Unused.
 * @param v    The inputs.
 * @param n    How many.
 * @return     Whether it is. */
static bool is_even(void *ctx, const double *v, size_t n)
{
        (void)ctx;
        (void)n;
        return (long long)v[0] % 2 == 0;
}

/** The least and greatest inputs.
 *
 * @param ctx  Unused.
 * @param v    The inputs.
 * @param n    How many.
 * @param out  Where the two go.
 * @param m    The room there.
 * @return     True. */
static bool min_max(void *ctx, const double *v, size_t n, double *out,
                    size_t m)
{
        size_t i;

        (void)ctx;
        (void)m;
        out[0] = v[0];
        out[1] = v[0];
        for (i = 1; i < n; i++) {
                out[0] = v[i] < out[0] ? v[i] : out[0];
                out[1] = v[i] > out[1] ? v[i] : out[1];
        }
        return true;
}

/** The first input's alarm level, in bands of 50.
 *
 * @param ctx  Unused.
 * @param v    The inputs.
 * @param n    How many.
 * @return     The level. */
static uint8_t bands(void *ctx, const double *v, size_t n)
{
        (void)ctx;
        (void)n;
        return (uint8_t)(v[0] / 50.0);
}

/** Twice the sum of the bytes covered.
 *
 * @param ctx    Unused.
 * @param d      The data.
 * @param len    Its bytes.
 * @param start  The first bit covered.
 * @param end    The bit after the last.
 * @return       The checksum. */
static uint64_t twice(void *ctx, const uint8_t *d, size_t len, size_t start,
                      size_t end)
{
        uint64_t s = 0;
        size_t i;

        (void)ctx;
        (void)len;
        for (i = start / 8; i < end / 8; i++)
                s += d[i];
        return 2 * s;
}

/** Doubles every byte.
 *
 * @param ctx   Unused.
 * @param b     The bytes.
 * @param n     How many.
 * @param out   Where the result goes.
 * @param cap   The room there.
 * @param made  Where the bytes made, or the room needed, go.
 * @return      True. */
static bool expand(void *ctx, const uint8_t *b, size_t n, uint8_t *out,
                   size_t cap, size_t *made)
{
        size_t i;

        (void)ctx;
        *made = 2 * n;
        if (2 * n > cap)
                return true;
        for (i = 0; i < n; i++) {
                out[2 * i] = b[i];
                out[2 * i + 1] = b[i];
        }
        return true;
}

/** Takes every other byte; nothing when a pair differs.
 *
 * @param ctx   Unused.
 * @param b     The bytes.
 * @param n     How many.
 * @param out   Where the result goes.
 * @param cap   The room there.
 * @param took  Where the bytes taken go.
 * @param made  Where the bytes made, or the room needed, go.
 * @return      Whether they decode. */
static bool shrink(void *ctx, const uint8_t *b, size_t n, uint8_t *out,
                   size_t cap, size_t *took, size_t *made)
{
        size_t i;

        (void)ctx;
        if (n % 2 != 0)
                return false;
        for (i = 0; i < n; i += 2)
                if (b[i] != b[i + 1])
                        return false;
        *took = n;
        *made = n / 2;
        if (n / 2 > cap)
                return true;
        for (i = 0; i < n / 2; i++)
                out[i] = b[2 * i];
        return true;
}

/** XORs every byte with 0x5A.
 *
 * @param ctx   Unused.
 * @param b     The bytes.
 * @param n     How many.
 * @param out   Where the result goes.
 * @param cap   The room there.
 * @param made  Where the bytes made go.
 * @return      True. */
static bool mask(void *ctx, const uint8_t *b, size_t n, uint8_t *out,
                 size_t cap, size_t *made)
{
        size_t i;

        (void)ctx;
        for (i = 0; i < n && i < cap; i++)
                out[i] = b[i] ^ 0x5A;
        *made = n;
        return true;
}

/** XORs every byte with 0x5A, decoding.
 *
 * @param ctx   Unused.
 * @param b     The bytes.
 * @param n     How many.
 * @param out   Where the result goes.
 * @param cap   The room there.
 * @param took  Where the bytes taken go.
 * @param made  Where the bytes made go.
 * @return      True. */
static bool unmask(void *ctx, const uint8_t *b, size_t n, uint8_t *out,
                   size_t cap, size_t *took, size_t *made)
{
        size_t i;

        (void)ctx;
        for (i = 0; i < n && i < cap; i++)
                out[i] = b[i] ^ 0x5A;
        *took = n;
        *made = n;
        return true;
}

/** Frames bytes with their length.
 *
 * @param ctx   Unused.
 * @param b     The bytes.
 * @param n     How many.
 * @param out   Where the result goes.
 * @param cap   The room there.
 * @param made  Where the bytes made, or the room needed, go.
 * @return      Whether they encode: no more than 255. */
static bool stuff(void *ctx, const uint8_t *b, size_t n, uint8_t *out,
                  size_t cap, size_t *made)
{
        (void)ctx;
        if (n > 255)
                return false;
        *made = n + 1;
        if (n + 1 > cap)
                return true;
        out[0] = (uint8_t)n;
        memcpy(out + 1, b, n);
        return true;
}

/** Takes bytes framed with their length.
 *
 * @param ctx   Unused.
 * @param b     The bytes.
 * @param n     How many.
 * @param out   Where the result goes.
 * @param cap   The room there.
 * @param took  Where the bytes taken go.
 * @param made  Where the bytes made, or the room needed, go.
 * @return      Whether they decode. */
static bool unstuff(void *ctx, const uint8_t *b, size_t n, uint8_t *out,
                    size_t cap, size_t *took, size_t *made)
{
        size_t k;

        (void)ctx;
        if (n == 0)
                return false;
        k = b[0];
        if (n <= k)
                return false;
        *took = k + 1;
        *made = k;
        if (k > cap)
                return true;
        memcpy(out, b + 1, k);
        return true;
}

/** Seconds as whole milliseconds.
 *
 * @param s   The seconds.
 * @param ms  Where the milliseconds go.
 * @return    Whether they lie in 0 to 2^32 - 1. */
static bool to_ms(double s, uint32_t *ms)
{
        double v = s * 1000.0 + 0.5;

        v = v < 0 ? -1 : (double)(long long)v;
        if (!(v >= 0.0 && v < 4294967296.0))
                return false;
        *ms = (uint32_t)v;
        return true;
}

/** Reads milliseconds in four bytes.
 *
 * @param ctx   Unused.
 * @param b     The bytes.
 * @param n     How many.
 * @param took  Where the bytes taken go.
 * @param s     Where the seconds go.
 * @return      Whether there are four. */
static bool ms_from(void *ctx, const uint8_t *b, size_t n, size_t *took,
                    double *s)
{
        (void)ctx;
        if (n < 4)
                return false;
        *took = 4;
        *s = (double)((uint32_t)b[0] << 24 | (uint32_t)b[1] << 16
                      | (uint32_t)b[2] << 8 | b[3]) / 1000.0;
        return true;
}

/** Writes milliseconds in four bytes.
 *
 * @param ctx   Unused.
 * @param s     The seconds.
 * @param out   Where the bytes go.
 * @param cap   The room there.
 * @param made  Where the bytes made, or the room needed, go.
 * @return      Whether the seconds encode. */
static bool ms_to(void *ctx, double s, uint8_t *out, size_t cap,
                  size_t *made)
{
        uint32_t v;

        (void)ctx;
        if (!to_ms(s, &v))
                return false;
        *made = 4;
        if (cap < 4)
                return true;
        out[0] = (uint8_t)(v >> 24);
        out[1] = (uint8_t)(v >> 16);
        out[2] = (uint8_t)(v >> 8);
        out[3] = (uint8_t)v;
        return true;
}

/** Reads milliseconds in a varint.
 *
 * @param ctx   Unused.
 * @param b     The bytes.
 * @param n     How many.
 * @param took  Where the bytes taken go.
 * @param s     Where the seconds go.
 * @return      Whether a varint ends in five bytes. */
static bool var_from(void *ctx, const uint8_t *b, size_t n, size_t *took,
                     double *s)
{
        uint64_t v = 0;
        size_t i;

        (void)ctx;
        for (i = 0; i < n && i < 5; i++) {
                v |= (uint64_t)(b[i] & 0x7F) << (7 * i);
                if ((b[i] & 0x80) == 0) {
                        *took = i + 1;
                        *s = (double)v / 1000.0;
                        return true;
                }
        }
        return false;
}

/** Writes milliseconds in a varint.
 *
 * @param ctx   Unused.
 * @param s     The seconds.
 * @param out   Where the bytes go.
 * @param cap   The room there.
 * @param made  Where the bytes made, or the room needed, go.
 * @return      Whether the seconds encode. */
static bool var_to(void *ctx, double s, uint8_t *out, size_t cap,
                   size_t *made)
{
        uint32_t v;
        uint8_t buf[5];
        size_t n = 0;

        (void)ctx;
        if (!to_ms(s, &v))
                return false;
        while (v >= 0x80) {
                buf[n++] = (uint8_t)((v & 0x7F) | 0x80);
                v >>= 7;
        }
        buf[n++] = (uint8_t)v;
        *made = n;
        if (n <= cap)
                memcpy(out, buf, n);
        return true;
}

/** XORs each byte with 0x55, in pieces.
 *
 * @param ctx       Unused.
 * @param b         The bytes.
 * @param n         How many.
 * @param emit      Called with each piece.
 * @param emit_ctx  What it is given first. */
static void descramble(void *ctx, const uint8_t *b, size_t n,
                       tcs_dynxtce_emit_fn emit, void *emit_ctx)
{
        uint8_t buf[64];
        size_t i, k;

        (void)ctx;
        for (i = 0; i < n; i += k) {
                for (k = 0; k < sizeof buf && i + k < n; k++)
                        buf[k] = b[i + k] ^ 0x55;
                emit(emit_ctx, buf, k);
        }
}

/** Flips a bit.
 *
 * @param ctx  Unused.
 * @param bit  The bit.
 * @return     The other bit. */
static uint8_t invert(void *ctx, uint8_t bit)
{
        (void)ctx;
        return bit ^ 1;
}

/** Counts the contexts freed. */
static int freed;

/** Frees a context: counts it.
 *
 * @param ctx  The context. */
static void free_ctx(void *ctx)
{
        (void)ctx;
        freed++;
}

/** Supplies the demo mission's custom algorithms.
 *
 * @param m  The mission. */
static void set_algorithms(tcs_dynxtce_mission *m)
{
        check(tcs_dynxtce_set_criterion(m, "/Demo/IsEven", is_even, NULL,
                                        free_ctx) == 0, "IsEven");
        check(tcs_dynxtce_set_outputs(m, "/Demo/MinMax", min_max, NULL, NULL)
              == 0, "MinMax");
        check(tcs_dynxtce_set_alarm(m, "/Demo/Bands", bands, NULL, NULL) == 0,
              "Bands");
        check(tcs_dynxtce_set_checksum(m, "/Demo/Twice", twice, NULL, NULL)
              == 0, "Twice");
        check(tcs_dynxtce_set_binary_to(m, "Expand", expand, NULL, NULL) == 0,
              "Expand");
        check(tcs_dynxtce_set_binary_from(m, "Shrink", shrink, NULL, NULL) == 0,
              "Shrink");
        check(tcs_dynxtce_set_binary_to(m, "Mask", mask, NULL, NULL) == 0,
              "Mask");
        check(tcs_dynxtce_set_binary_from(m, "Unmask", unmask, NULL, NULL) == 0,
              "Unmask");
        check(tcs_dynxtce_set_binary_to(m, "Stuff", stuff, NULL, NULL) == 0,
              "Stuff");
        check(tcs_dynxtce_set_binary_from(m, "Unstuff", unstuff, NULL, NULL)
              == 0, "Unstuff");
        check(tcs_dynxtce_set_time_from(m, "MsFrom", ms_from, NULL, NULL) == 0,
              "MsFrom");
        check(tcs_dynxtce_set_time_to(m, "MsTo", ms_to, NULL, NULL) == 0,
              "MsTo");
        check(tcs_dynxtce_set_time_from(m, "VarFrom", var_from, NULL, NULL)
              == 0, "VarFrom");
        check(tcs_dynxtce_set_time_to(m, "VarTo", var_to, NULL, NULL) == 0,
              "VarTo");
        check(tcs_dynxtce_set_stream_decode(m, "Descramble", descramble, NULL,
                                            NULL) == 0, "Descramble");
        check(tcs_dynxtce_set_bit_invert(m, "Invert", invert, NULL, NULL) == 0,
              "Invert");
        check(tcs_dynxtce_set_criterion(m, "/Demo/Nothing", is_even, NULL, NULL)
              == TCS_DYNXTCE_STATUS_LOOKUP, "an algorithm the mission lacks");
}

/** Runs every vector: unpacks it, and packs it again to the bytes the
 * generated codecs give.
 *
 * @param m    The mission.
 * @param dir  The directory of the demo mission's files. */
static void run_vectors(tcs_dynxtce_mission *m, const char *dir)
{
        char path[4096];
        size_t vlen, elen;
        unsigned char *vectors, *expected;
        char *v, *e, *vnext, *enext;
        tcs_dynxtce_space top = tcs_dynxtce_top(m);
        tcs_dynxtce_node roots[3];
        tcs_dynxtce_scratch *scratch = tcs_dynxtce_scratch_new(m);
        tcs_dynxtce_store *store = tcs_dynxtce_store_new(m);
        unsigned char data[70000], want[70000];
        int matched = 0;

        check(tcs_dynxtce_container(m, top, "/Demo/CCSDSPacket", &roots[0])
              == 0, "the telemetry root");
        check(tcs_dynxtce_meta_command(m, top, "/Demo/CCSDSCommand", &roots[1])
              == 0, "the command root");
        check(tcs_dynxtce_container(m, top, "/Demo/Housekeeping", &roots[2])
              == 0, "Housekeeping");
        snprintf(path, sizeof path, "%s/vectors.txt", dir);
        vectors = slurp(path, &vlen);
        snprintf(path, sizeof path, "%s/vectors.expected", dir);
        expected = slurp(path, &elen);
        check(vectors != NULL && expected != NULL, "the vector files");
        if (vectors == NULL || expected == NULL)
                return;
        for (v = (char *)vectors, e = (char *)expected; *v && *e;
             v = vnext, e = enext) {
                char kind[8], *hex, *again;
                size_t n, wn;
                tcs_dynxtce_node root, got;
                const uint8_t *out;
                size_t olen;
                int r;

                vnext = strchr(v, '\n');
                enext = strchr(e, '\n');
                if (vnext == NULL || enext == NULL)
                        break;
                *vnext++ = 0;
                *enext++ = 0;
                if (sscanf(v, "%7s", kind) != 1 || kind[0] == '#') {
                        enext = e;
                        continue;
                }
                root = strcmp(kind, "tm") == 0 ? roots[0]
                       : strcmp(kind, "tc") == 0 ? roots[1] : roots[2];
                hex = strchr(v, ' ');
                while (hex != NULL && *hex == ' ')
                        hex++;
                n = unhex(hex, data, sizeof data);
                again = strstr(e, " | ");
                again = again != NULL ? again + 3 : e;
                r = tcs_dynxtce_unpack(m, root, data, n, scratch, &got);
                if (strncmp(e, "error:", 6) == 0) {
                        check(r != 0, v);
                        matched += r != 0;
                        continue;
                }
                check(r == 0, v);
                if (r != 0)
                        continue;
                /* A store of its own, so that nothing an earlier packet
                 * set is packed. */
                tcs_dynxtce_store_free(store);
                store = tcs_dynxtce_store_new(m);
                check(tcs_dynxtce_commit(store, scratch) == 0, "commit");
                r = tcs_dynxtce_pack(m, got, store, &out, &olen);
                if (strncmp(again, "error:", 6) == 0) {
                        check(r != 0, v);
                        matched += r != 0;
                        continue;
                }
                wn = unhex(again, want, sizeof want);
                r = r == 0 && olen == wn && memcmp(out, want, wn) == 0;
                check(r, v);
                matched += r;
        }
        printf("c_test: %d vectors unpack and pack as expected\n", matched);
        check(matched > 290, "the vectors");
        free(vectors);
        free(expected);
        tcs_dynxtce_store_free(store);
        tcs_dynxtce_scratch_free(scratch);
}

/** Counts alarm levels.
 *
 * @param ctx    The count.
 * @param name   The field's name.
 * @param len    Its length.
 * @param level  Its level. */
static void count_level(void *ctx, const char *name, size_t len, int level)
{
        (void)name;
        (void)len;
        (void)level;
        (*(int *)ctx)++;
}

/** Counts frames.
 *
 * @param ctx    The count.
 * @param frame  The frame.
 * @param len    Its bytes. */
static void count_frame(void *ctx, const uint8_t *frame, size_t len)
{
        (void)frame;
        (void)len;
        (*(int *)ctx)++;
}

/** Checks lookups, values, alarms and a stream.
 *
 * @param m  The mission. */
static void run_interface(tcs_dynxtce_mission *m)
{
        static const char hk[] =
                "0864c02a001201a04e2805dc040302013fc00000fffe8050fc";
        tcs_dynxtce_space top = tcs_dynxtce_top(m);
        tcs_dynxtce_node root, got;
        tcs_dynxtce_slot seq, mode;
        tcs_dynxtce_slots probes;
        tcs_dynxtce_datum d;
        tcs_dynxtce_scratch *scratch = tcs_dynxtce_scratch_new(m);
        tcs_dynxtce_store *store = tcs_dynxtce_store_new(m);
        tcs_dynxtce_deframer *dl;
        unsigned char data[64];
        char path[256];
        size_t n = unhex(hk, data, sizeof data);
        const uint8_t *out;
        size_t olen;
        int count = 0, frames = 0;
        static const uint8_t frame[] = {
                0x1a, 0xcf, 0xfc, 0x1d, 0x01, 0x11, 0x11, 0x11, 0x11, 0x11,
                0x11, 0x11, 0x11, 0x11, 0x11, 0x11,
        };

        check(tcs_dynxtce_container(m, top, "/Demo/CCSDSPacket", &root) == 0,
              "root");
        check(tcs_dynxtce_parameter(m, top, "/Demo/SeqCount", &seq) == 0,
              "SeqCount");
        check(tcs_dynxtce_parameter(m, top, "/Demo/Mode", &mode) == 0, "Mode");
        check(tcs_dynxtce_parameter(m, top, "/Demo/NoSuch", &seq)
              == TCS_DYNXTCE_STATUS_LOOKUP, "a missing parameter");
        check(tcs_dynxtce_parameter(m, top, "/Demo/SeqCount", &seq) == 0,
              "SeqCount again");
        check(tcs_dynxtce_parameter_slots(m, top, "/Demo/Probes", &probes) == 0
              && probes.count == 6, "Probes");
        tcs_dynxtce_path_of_slot(m, tcs_dynxtce_slots_get(probes, 4), path,
                                 sizeof path);
        check(strcmp(path, "/Demo/Probes[1].Gains[0]") == 0, path);
        check(tcs_dynxtce_store_get(m, store, seq, &d) == 0, "nothing yet");
        check(tcs_dynxtce_unpack(m, root, data, n, scratch, &got) == 0,
              "unpack");
        tcs_dynxtce_path_of_node(m, got, path, sizeof path);
        check(strcmp(path, "/Demo/Housekeeping") == 0, path);
        check(tcs_dynxtce_commit(store, scratch) == 0, "commit");
        check(tcs_dynxtce_store_get(m, store, seq, &d) == 1
              && d.kind == TCS_DYNXTCE_KIND_INT && d.i == 42, "SeqCount is 42");
        d.kind = TCS_DYNXTCE_KIND_ENUM;
        d.i = 99;
        check(tcs_dynxtce_store_set(m, store, mode, &d)
              == TCS_DYNXTCE_STATUS_SET, "Mode 99 is refused");
        d.kind = TCS_DYNXTCE_KIND_INT;
        d.i = 7;
        check(tcs_dynxtce_store_set(m, store, seq, &d) == 0, "SeqCount 7");
        check(tcs_dynxtce_pack(m, got, store, &out, &olen) == 0, "pack");
        check(tcs_dynxtce_unpack(m, root, out, olen, scratch, &got) == 0
              && tcs_dynxtce_commit(store, scratch) == 0
              && tcs_dynxtce_store_get(m, store, seq, &d) == 1 && d.i == 7,
              "SeqCount 7 packed");
        /* Housekeeping has no alarms of its own. */
        check(tcs_dynxtce_alarms(m, scratch, count_level, &count) == 0
              && count == 0, "alarms");
        check(tcs_dynxtce_stream(m, "/Demo/Downlink", &dl) == 0, "Downlink");
        /* The stream keeps the mission while it lives. */
        tcs_dynxtce_store_free(store);
        tcs_dynxtce_scratch_free(scratch);
        tcs_dynxtce_mission_free(m);
        for (int i = 0; i < 3; i++)
                tcs_dynxtce_deframer_push(dl, frame, sizeof frame, count_frame,
                                          &frames);
        check(frames > 0, "frames from Downlink");
        tcs_dynxtce_deframer_free(dl);
        check(freed == 1, "IsEven's context is freed with the mission");
}

/** Runs the tests.
 *
 * @param argc  The arguments' count.
 * @param argv  The arguments: the demo mission's directory.
 * @return      0 if every check held, 1 if not. */
int main(int argc, char **argv)
{
        char path[4096];
        size_t len;
        unsigned char *doc;
        const uint8_t *docs[1];
        tcs_dynxtce_mission *m;

        if (argc != 2) {
                fprintf(stderr, "usage: c_test MISSION_DIR\n");
                return 2;
        }
        snprintf(path, sizeof path, "%s/demo.xtce", argv[1]);
        doc = slurp(path, &len);
        check(doc != NULL, path);
        if (doc == NULL)
                return 1;
        docs[0] = doc;
        check(tcs_dynxtce_mission_read(docs, &len, 1, &m) == 0, "read");
        free(doc);
        set_algorithms(m);
        run_vectors(m, argv[1]);
        run_interface(m);
        if (failures != 0) {
                fprintf(stderr, "c_test: %d checks failed\n", failures);
                return 1;
        }
        printf("c_test: all checks hold\n");
        return 0;
}
