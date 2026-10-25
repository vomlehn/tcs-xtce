/* Runtime for packet codecs generated from a mission's XTCE document.
 *
 * tcs-xtce (in generator/) turns the containers and meta-commands of a mission
 * database into C structs and functions.  The generated code reads packets
 * with an xtce_pk_reader and writes them with an xtce_pk_writer into a buffer
 * the caller owns; this header provides those and the integer, float, string
 * and calibration helpers they call.  It mirrors xtce::packet in Rust and
 * xtce.packet in Python.
 *
 * The header is self-contained, needs only the C standard library (link with
 * -lm) and compiles both as C11 and as C++.  Nothing is allocated: callers
 * preallocate buffers, typically of the generated *_MAX_BYTES size, and reuse
 * them for every packet.
 *
 * Every fallible function returns 0 (XTCE_PK_OK) on success or an error code,
 * and fills in the xtce_pk_error it was given (which may be NULL).
 */
#ifndef XTCE_PACKET_H
#define XTCE_PACKET_H

#include <float.h>
#include <math.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Why a packet could not be decoded or encoded.  The kind names returned by
 * xtce_pk_error_kind() are the same in every runtime, except NoSpace, which
 * only the C runtime has (the output buffer is smaller than the packet). */
enum {
        XTCE_PK_OK = 0,
        XTCE_PK_TRUNCATED,
        XTCE_PK_BAD_POSITION,
        XTCE_PK_NO_MATCH,
        XTCE_PK_MISMATCH,
        XTCE_PK_OUT_OF_RANGE,
        XTCE_PK_INVALID_STRING,
        XTCE_PK_TOO_LONG,
        XTCE_PK_CALIBRATION,
        XTCE_PK_NOT_INVERTIBLE,
        XTCE_PK_MISSING,
        XTCE_PK_BAD_SIZE,
        XTCE_PK_BAD_LENGTH,
        XTCE_PK_INVALID_VALUE,
        XTCE_PK_NO_SPACE,
};

/* `field` names the field or container involved and `detail` holds the
 * criterion of a Mismatch; both point at string constants. */
typedef struct xtce_pk_error {
        int code;
        const char* field;
        const char* detail;
} xtce_pk_error;

static inline const char* xtce_pk_error_kind(int code) {
        static const char* const kinds[] = {
                "Ok",          "Truncated",     "BadPosition",   "NoMatch",
                "Mismatch",    "OutOfRange",    "InvalidString", "TooLong",
                "Calibration", "NotInvertible", "Missing",       "BadSize",
                "BadLength",   "InvalidValue",  "NoSpace"};
        return code >= 0 && code <= XTCE_PK_NO_SPACE ? kinds[code] : "Unknown";
}

/* Writes the error message into `buf` like snprintf and returns its length. */
static inline int xtce_pk_error_message(const xtce_pk_error* e, char* buf,
                                        size_t cap) {
        const char* f = e->field ? e->field : "";
        switch (e->code) {
                case XTCE_PK_OK:
                        return snprintf(buf, cap, "no error");
                case XTCE_PK_TRUNCATED:
                        return snprintf(buf, cap, "packet too short for %s", f);
                case XTCE_PK_BAD_POSITION:
                        return snprintf(buf, cap,
                                        "%s is placed before the packet start",
                                        f);
                case XTCE_PK_NO_MATCH:
                        return snprintf(buf, cap,
                                        "no container derived from %s matches",
                                        f);
                case XTCE_PK_MISMATCH:
                        return snprintf(buf, cap, "%s requires %s", f,
                                        e->detail ? e->detail : "");
                case XTCE_PK_OUT_OF_RANGE:
                        return snprintf(buf, cap,
                                        "value of %s does not fit its encoding",
                                        f);
                case XTCE_PK_INVALID_STRING:
                        return snprintf(buf, cap, "invalid string in %s", f);
                case XTCE_PK_TOO_LONG:
                        return snprintf(buf, cap,
                                        "%s is too long for its encoding", f);
                case XTCE_PK_CALIBRATION:
                        return snprintf(buf, cap,
                                        "%s is outside its calibration range",
                                        f);
                case XTCE_PK_NOT_INVERTIBLE:
                        return snprintf(buf, cap,
                                        "the calibrator of %s cannot be "
                                        "inverted for encoding",
                                        f);
                case XTCE_PK_MISSING:
                        return snprintf(
                                buf, cap,
                                "%s is required by its include condition", f);
                case XTCE_PK_BAD_SIZE:
                        return snprintf(buf, cap,
                                        "invalid size or count for %s", f);
                case XTCE_PK_BAD_LENGTH:
                        return snprintf(
                                buf, cap,
                                "%s does not match the length of the packet",
                                f);
                case XTCE_PK_INVALID_VALUE:
                        return snprintf(buf, cap,
                                        "the bits of %s are not a value of "
                                        "its encoding",
                                        f);
                case XTCE_PK_NO_SPACE:
                        return snprintf(buf, cap,
                                        "output buffer too small for %s", f);
        }
        return snprintf(buf, cap, "unknown error %d", e->code);
}

static inline int xtce_pk_fail(xtce_pk_error* e, int code, const char* field,
                               const char* detail) {
        if (e) {
                e->code = code;
                e->field = field;
                e->detail = detail;
        }
        return code;
}

/* Returns from the enclosing function if `expr` reports an error. */
#define XTCE_PK_TRY(expr)                            \
        do {                                         \
                int xtce_pk_rc_ = (expr);            \
                if (xtce_pk_rc_) return xtce_pk_rc_; \
        } while (0)

/* Zero initializer for any struct; `{0}` draws warnings in C++ and `{}`
 * needs C23. */
#ifdef __cplusplus
#define XTCE_PK_ZERO \
        {            \
        }
#else
#define XTCE_PK_ZERO {0}
#endif

static inline uint64_t xtce_pk_mask(unsigned bits) {
        return bits >= 64 ? ~(uint64_t)0 : ((uint64_t)1 << bits) - 1;
}

static inline int xtce_pk_position(size_t base, int64_t offset, const char* f,
                                   xtce_pk_error* err, size_t* out) {
        int64_t p = (int64_t)base + offset;
        if (p < 0) return xtce_pk_fail(err, XTCE_PK_BAD_POSITION, f, NULL);
        *out = (size_t)p;
        return 0;
}

static inline uint64_t xtce_pk_swap_bytes(uint64_t v, unsigned bits) {
        uint64_t out = 0;
        unsigned i;
        for (i = 0; i < bits / 8; ++i)
                out = (out << 8) | ((v >> (8 * i)) & 0xff);
        return out;
}

/* --- reading -----------------------------------------------------------------
 */

/* Reads big-endian bit fields from `len` bytes at `data`; `pos` is in bits,
 * and `end` is the furthest bit read. */
typedef struct xtce_pk_reader {
        const uint8_t* data;
        size_t len;
        size_t pos;
        size_t end;
        xtce_pk_error* err;
} xtce_pk_reader;

static inline xtce_pk_reader xtce_pk_reader_init(const uint8_t* data,
                                                 size_t len,
                                                 xtce_pk_error* err) {
        xtce_pk_reader r;
        r.data = data;
        r.len = len;
        r.pos = 0;
        r.end = 0;
        r.err = err;
        return r;
}

static inline void xtce_pk_read_moved(xtce_pk_reader* r) {
        if (r->end < r->pos) r->end = r->pos;
}

/* Moves to `offset` bits from `start`.  Bits skipped over count as part of
 * the packet, as the writer fills them. */
static inline int xtce_pk_read_seek(xtce_pk_reader* r, size_t start,
                                    int64_t offset, const char* f) {
        XTCE_PK_TRY(xtce_pk_position(start, offset, f, r->err, &r->pos));
        xtce_pk_read_moved(r);
        return 0;
}

static inline int xtce_pk_read_skip(xtce_pk_reader* r, int64_t offset,
                                    const char* f) {
        return xtce_pk_read_seek(r, r->pos, offset, f);
}

static inline int xtce_pk_read_need(const xtce_pk_reader* r, size_t bits,
                                    const char* f) {
        if (r->pos + bits > r->len * 8)
                return xtce_pk_fail(r->err, XTCE_PK_TRUNCATED, f, NULL);
        return 0;
}

/* Reads an unsigned big-endian field of `bits` (1 to 64) bits. */
static inline int xtce_pk_read_bits(xtce_pk_reader* r, unsigned bits,
                                    const char* f, uint64_t* out) {
        uint64_t v = 0;
        unsigned left = bits;
        XTCE_PK_TRY(xtce_pk_read_need(r, bits, f));
        while (left > 0) {
                uint8_t byte = r->data[r->pos / 8];
                unsigned avail = 8 - (unsigned)(r->pos % 8);
                unsigned take = avail < left ? avail : left;
                v = (v << take) |
                    ((byte >> (avail - take)) & xtce_pk_mask(take));
                r->pos += take;
                left -= take;
        }
        xtce_pk_read_moved(r);
        *out = v;
        return 0;
}

/* Like xtce_pk_read_bits, with the bytes swapped when `le` is set. */
static inline int xtce_pk_read_uint(xtce_pk_reader* r, unsigned bits, bool le,
                                    const char* f, uint64_t* out) {
        XTCE_PK_TRY(xtce_pk_read_bits(r, bits, f, out));
        if (le) *out = xtce_pk_swap_bytes(*out, bits);
        return 0;
}

/* How the groups of 8 bits of a field combine (see xtce_pk_read_ordered):
 * the first group most significant (big endian), least significant (little
 * endian), or each (whole) byte's significance from a list, as an XTCE byte
 * order list gives it. */
enum { XTCE_PK_BIG = 0, XTCE_PK_LITTLE, XTCE_PK_LIST };

/* ORs v << shift into the 128-bit value (*hi, *lo). */
static inline void xtce_pk_or128(uint64_t* hi, uint64_t* lo, uint64_t v,
                                 unsigned shift) {
        if (shift >= 128) return;
        if (shift >= 64) {
                *hi |= v << (shift - 64);
        } else {
                *lo |= v << shift;
                if (shift) *hi |= v >> (64 - shift);
        }
}

/* The 128-bit value (hi, lo) shifted right by `shift`, low 64 bits. */
static inline uint64_t xtce_pk_shr128(uint64_t hi, uint64_t lo,
                                      unsigned shift) {
        if (shift >= 128) return 0;
        if (shift >= 64) return hi >> (shift - 64);
        if (shift == 0) return lo;
        return (lo >> shift) | (hi << (64 - shift));
}

/* Reads an unsigned field of `bits` (1 to 128) bits in any bit and byte order
 * into (*hi, *lo).  The field's bits are taken in groups of 8 (the last may
 * be shorter).  Within a group, `lsb` false numbers the bits of each byte
 * from the most significant (the usual order), and the group's first bit is
 * its most significant; `lsb` true numbers them from the least significant,
 * and the group's first bit is its least significant.  `order` combines the
 * groups; for XTCE_PK_LIST, `list` gives each byte's significance. */
static inline int xtce_pk_read_ordered(xtce_pk_reader* r, unsigned bits,
                                       bool lsb, int order,
                                       const uint8_t* list, const char* f,
                                       uint64_t* hi, uint64_t* lo) {
        uint8_t g[16];
        unsigned n[16], k = 0, j, shift = 0, total = 0;
        size_t q;
        XTCE_PK_TRY(xtce_pk_read_need(r, bits, f));
        q = r->pos;
        while (total < bits) {
                unsigned len = bits - total < 8 ? bits - total : 8, i;
                uint8_t v = 0;
                for (i = 0; i < len; ++i) {
                        uint8_t byte = r->data[(q + i) / 8];
                        if (lsb)
                                v |= (uint8_t)(((byte >> ((q + i) % 8)) & 1)
                                               << i);
                        else
                                v = (uint8_t)((v << 1) |
                                              ((byte >> (7 - (q + i) % 8)) &
                                               1));
                }
                g[k] = v;
                n[k++] = len;
                q += len;
                total += len;
        }
        r->pos = q;
        xtce_pk_read_moved(r);
        *hi = *lo = 0;
        for (j = 0; j < k; ++j) {
                if (order == XTCE_PK_BIG) {
                        total -= n[j];
                        xtce_pk_or128(hi, lo, g[j], total);
                } else if (order == XTCE_PK_LITTLE) {
                        xtce_pk_or128(hi, lo, g[j], shift);
                        shift += n[j];
                } else {
                        xtce_pk_or128(hi, lo, g[j], 8u * list[j]);
                }
        }
        return 0;
}

/* xtce_pk_read_ordered for fields of at most 64 bits. */
static inline int xtce_pk_read_ordered64(xtce_pk_reader* r, unsigned bits,
                                         bool lsb, int order,
                                         const uint8_t* list, const char* f,
                                         uint64_t* out) {
        uint64_t hi;
        return xtce_pk_read_ordered(r, bits, lsb, order, list, f, &hi, out);
}

/* Skips `bits` bits of padding, which must be in the packet. */
static inline int xtce_pk_read_pad(xtce_pk_reader* r, size_t bits,
                                   const char* f) {
        XTCE_PK_TRY(xtce_pk_read_need(r, bits, f));
        r->pos += bits;
        xtce_pk_read_moved(r);
        return 0;
}

/* Ends the packet where the packet length field `f`, whose value `v` was
 * just read, says: `v - adjust` bytes after the field.  Fails with
 * XTCE_PK_TRUNCATED if the data is shorter. */
static inline int xtce_pk_read_limit(xtce_pk_reader* r, uint64_t v,
                                     int64_t adjust, const char* f) {
        size_t start = (r->pos + 7) / 8;
        uint64_t after;
        if (adjust >= 0) {
                if (v < (uint64_t)adjust)
                        return xtce_pk_fail(r->err, XTCE_PK_BAD_LENGTH, f,
                                            NULL);
                after = v - (uint64_t)adjust;
        } else {
                uint64_t a = 0 - (uint64_t)adjust;
                if (v > UINT64_MAX - a)
                        return xtce_pk_fail(r->err, XTCE_PK_TRUNCATED, f, NULL);
                after = v + a;
        }
        if (after > r->len - start)
                return xtce_pk_fail(r->err, XTCE_PK_TRUNCATED, f, NULL);
        r->len = start + (size_t)after;
        return 0;
}

/* Checks that the packet's entries reach the end that the packet length
 * field `f` gave. */
static inline int xtce_pk_read_finish(const xtce_pk_reader* r, const char* f) {
        if ((r->end + 7) / 8 != r->len)
                return xtce_pk_fail(r->err, XTCE_PK_BAD_LENGTH, f, NULL);
        return 0;
}

/* The bytes from the current position to the end of the packet, for a field
 * that takes the rest of it.  Fails with XTCE_PK_BAD_SIZE unless they are
 * whole bytes, at most `max` of them. */
static inline int xtce_pk_read_rest(const xtce_pk_reader* r, size_t max,
                                    const char* f, size_t* out) {
        size_t bits = r->len * 8 > r->pos ? r->len * 8 - r->pos : 0;
        if (bits % 8 || bits / 8 > max)
                return xtce_pk_fail(r->err, XTCE_PK_BAD_SIZE, f, NULL);
        *out = bits / 8;
        return 0;
}

static inline int xtce_pk_read_bytes(xtce_pk_reader* r, size_t n, const char* f,
                                     uint8_t* out) {
        size_t i;
        XTCE_PK_TRY(xtce_pk_read_need(r, n * 8, f));
        if (r->pos % 8 == 0) {
                if (n) memcpy(out, r->data + r->pos / 8, n);
                r->pos += n * 8;
                xtce_pk_read_moved(r);
                return 0;
        }
        for (i = 0; i < n; ++i) {
                uint64_t b;
                XTCE_PK_TRY(xtce_pk_read_bits(r, 8, f, &b));
                out[i] = (uint8_t)b;
        }
        return 0;
}

/* --- writing
 * ------------------------------------------------------------------- */

/* Writes big-endian bit fields into `cap` bytes at `buf`.  `len` grows (zero
 * filled) to cover the furthest bit written. */
typedef struct xtce_pk_writer {
        uint8_t* buf;
        size_t cap;
        size_t len;
        size_t pos;
        xtce_pk_error* err;
} xtce_pk_writer;

static inline xtce_pk_writer xtce_pk_writer_init(uint8_t* buf, size_t cap,
                                                 xtce_pk_error* err) {
        xtce_pk_writer w;
        w.buf = buf;
        w.cap = cap;
        w.len = 0;
        w.pos = 0;
        w.err = err;
        return w;
}

static inline int xtce_pk_write_seek(xtce_pk_writer* w, size_t start,
                                     int64_t offset, const char* f) {
        return xtce_pk_position(start, offset, f, w->err, &w->pos);
}

static inline int xtce_pk_write_skip(xtce_pk_writer* w, int64_t offset,
                                     const char* f) {
        return xtce_pk_position(w->pos, offset, f, w->err, &w->pos);
}

static inline int xtce_pk_write_bits(xtce_pk_writer* w, unsigned bits,
                                     uint64_t v) {
        size_t end = (w->pos + bits + 7) / 8;
        unsigned left = bits;
        if (end > w->cap)
                return xtce_pk_fail(w->err, XTCE_PK_NO_SPACE, "packet", NULL);
        if (w->len < end) {
                memset(w->buf + w->len, 0, end - w->len);
                w->len = end;
        }
        while (left > 0) {
                unsigned avail = 8 - (unsigned)(w->pos % 8);
                unsigned take = avail < left ? avail : left;
                uint8_t chunk =
                        (uint8_t)((v >> (left - take)) & xtce_pk_mask(take));
                unsigned shift = avail - take;
                uint8_t m = (uint8_t)(xtce_pk_mask(take) << shift);
                uint8_t* b = &w->buf[w->pos / 8];
                *b = (uint8_t)((*b & ~m) | (chunk << shift));
                w->pos += take;
                left -= take;
        }
        return 0;
}

static inline int xtce_pk_write_uint(xtce_pk_writer* w, unsigned bits, bool le,
                                     uint64_t v) {
        return xtce_pk_write_bits(w, bits,
                                  le ? xtce_pk_swap_bytes(v, bits) : v);
}

/* Writes the low `bits` bits of (hi, lo) in the bit and byte order that
 * xtce_pk_read_ordered reads. */
static inline int xtce_pk_write_ordered(xtce_pk_writer* w, unsigned bits,
                                        bool lsb, int order,
                                        const uint8_t* list, uint64_t hi,
                                        uint64_t lo) {
        size_t end = (w->pos + bits + 7) / 8, q = w->pos;
        unsigned k = (bits + 7) / 8, j, before = 0;
        if (end > w->cap)
                return xtce_pk_fail(w->err, XTCE_PK_NO_SPACE, "packet", NULL);
        if (w->len < end) {
                memset(w->buf + w->len, 0, end - w->len);
                w->len = end;
        }
        for (j = 0; j < k; ++j) {
                unsigned n = (j + 1) * 8 <= bits ? 8 : bits % 8, i, shift;
                uint8_t g;
                if (order == XTCE_PK_BIG)
                        shift = bits - before - n;
                else if (order == XTCE_PK_LITTLE)
                        shift = before;
                else
                        shift = 8u * list[j];
                g = (uint8_t)(xtce_pk_shr128(hi, lo, shift) &
                              xtce_pk_mask(n));
                for (i = 0; i < n; ++i) {
                        unsigned bit =
                                lsb ? (g >> i) & 1 : (g >> (n - 1 - i)) & 1;
                        unsigned at = lsb ? (unsigned)((q + i) % 8)
                                          : 7 - (unsigned)((q + i) % 8);
                        uint8_t* b = &w->buf[(q + i) / 8];
                        *b = (uint8_t)((*b & ~(1u << at)) | (bit << at));
                }
                q += n;
                before += n;
        }
        w->pos = q;
        return 0;
}

/* Bytes start..start + n of the buffer, extending its length (zero filled)
 * to cover them; NULL, after failing with NO_SPACE, if they don't fit. */
static inline uint8_t* xtce_pk_write_span(xtce_pk_writer* w, size_t start,
                                          size_t n) {
        if (n > w->cap || start > w->cap - n) {
                xtce_pk_fail(w->err, XTCE_PK_NO_SPACE, "packet", NULL);
                return NULL;
        }
        if (w->len < start + n) {
                memset(w->buf + w->len, 0, start + n - w->len);
                w->len = start + n;
        }
        return w->buf + start;
}

/* Writes whole bytes (which need not be byte aligned). */
static inline int xtce_pk_write_bytes(xtce_pk_writer* w, const uint8_t* data,
                                      size_t n) {
        size_t i;
        uint8_t* out;
        if (w->pos % 8) {
                for (i = 0; i < n; ++i)
                        XTCE_PK_TRY(xtce_pk_write_bits(w, 8, data[i]));
                return 0;
        }
        if (n == 0) return 0;
        out = xtce_pk_write_span(w, w->pos / 8, n);
        if (!out) return XTCE_PK_NO_SPACE;
        memcpy(out, data, n);
        w->pos += n * 8;
        return 0;
}

/* Writes `bits` zero bits, overwriting whatever a seek left there. */
/* Fills in the packet length field `f` of `bits` bits at bit `at`, once the
 * whole packet is written: the number of bytes after the field plus
 * `adjust`. */
static inline int xtce_pk_patch_length(xtce_pk_writer* w, size_t at,
                                       unsigned bits, bool le, int64_t adjust,
                                       const char* f) {
        size_t after = w->len - (at + bits + 7) / 8, end = w->pos;
        uint64_t v;
        if (adjust >= 0) {
                v = (uint64_t)after + (uint64_t)adjust;
                if (v < (uint64_t)after)
                        return xtce_pk_fail(w->err, XTCE_PK_OUT_OF_RANGE, f,
                                            NULL);
        } else {
                uint64_t a = 0 - (uint64_t)adjust;
                if ((uint64_t)after < a)
                        return xtce_pk_fail(w->err, XTCE_PK_OUT_OF_RANGE, f,
                                            NULL);
                v = (uint64_t)after - a;
        }
        if (v > xtce_pk_mask(bits))
                return xtce_pk_fail(w->err, XTCE_PK_OUT_OF_RANGE, f, NULL);
        w->pos = at;
        XTCE_PK_TRY(xtce_pk_write_uint(w, bits, le, v));
        w->pos = end;
        return 0;
}

static inline int xtce_pk_write_zeros(xtce_pk_writer* w, size_t bits) {
        size_t lead = (8 - w->pos % 8) % 8, whole;
        uint8_t* out;
        if (lead > bits) lead = bits;
        if (lead) XTCE_PK_TRY(xtce_pk_write_bits(w, (unsigned)lead, 0));
        whole = (bits - lead) / 8;
        if (whole) {
                out = xtce_pk_write_span(w, w->pos / 8, whole);
                if (!out) return XTCE_PK_NO_SPACE;
                memset(out, 0, whole);
                w->pos += whole * 8;
        }
        if ((bits - lead) % 8)
                XTCE_PK_TRY(xtce_pk_write_bits(w, (unsigned)((bits - lead) % 8),
                                               0));
        return 0;
}

/* --- integers
 * ------------------------------------------------------------------- */

enum {
        XTCE_PK_UNSIGNED,
        XTCE_PK_TWOS_COMPLEMENT,
        XTCE_PK_SIGN_MAGNITUDE,
        XTCE_PK_ONES_COMPLEMENT
};

static inline int64_t xtce_pk_to_signed(uint64_t raw, unsigned bits, int enc) {
        bool sign = (raw >> (bits - 1)) & 1;
        uint64_t low = raw & xtce_pk_mask(bits - 1);
        switch (enc) {
                case XTCE_PK_TWOS_COMPLEMENT:
                        return (int64_t)(sign && bits < 64
                                                 ? (raw | ~xtce_pk_mask(bits))
                                                 : raw);
                case XTCE_PK_SIGN_MAGNITUDE:
                        return sign ? -(int64_t)low : (int64_t)low;
                case XTCE_PK_ONES_COMPLEMENT:
                        return sign ? -(int64_t)(~raw & xtce_pk_mask(bits - 1))
                                    : (int64_t)low;
        }
        return (int64_t)raw;
}

static inline int xtce_pk_from_unsigned(uint64_t v, unsigned bits,
                                        const char* f, xtce_pk_error* err,
                                        uint64_t* out) {
        if (v > xtce_pk_mask(bits))
                return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
        *out = v;
        return 0;
}

static inline int xtce_pk_from_signed(int64_t v, unsigned bits, int enc,
                                      const char* f, xtce_pk_error* err,
                                      uint64_t* out) {
        uint64_t limit = xtce_pk_mask(bits - 1);
        uint64_t m;
        switch (enc) {
                case XTCE_PK_UNSIGNED:
                        if (v < 0)
                                return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE,
                                                    f, NULL);
                        return xtce_pk_from_unsigned((uint64_t)v, bits, f, err,
                                                     out);
                case XTCE_PK_TWOS_COMPLEMENT:
                        if (v < -(int64_t)limit - 1 ||
                            (v > 0 && (uint64_t)v > limit)) {
                                return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE,
                                                    f, NULL);
                        }
                        *out = (uint64_t)v & xtce_pk_mask(bits);
                        return 0;
        }
        m = v < 0 ? (uint64_t)0 - (uint64_t)v : (uint64_t)v;
        if (m > limit) return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
        if (v >= 0) {
                *out = m;
        } else if (enc == XTCE_PK_SIGN_MAGNITUDE) {
                *out = ((uint64_t)1 << (bits - 1)) | m;
        } else {
                *out = ~m & xtce_pk_mask(bits);
        }
        return 0;
}

static inline double xtce_pk_int_to_double(uint64_t raw, unsigned bits,
                                           int enc) {
        if (enc == XTCE_PK_UNSIGNED) return (double)raw;
        return (double)xtce_pk_to_signed(raw, bits, enc);
}

/* Rounds (half away from zero) an engineering value to a raw integer field. */
static inline int xtce_pk_double_to_int(double x, unsigned bits, int enc,
                                        const char* f, xtce_pk_error* err,
                                        uint64_t* out) {
        double r = round(x);
        if (!isfinite(r) || fabs(r) >= 18446744073709551616.0)
                return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
        if (enc == XTCE_PK_UNSIGNED) {
                if (r < 0)
                        return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
                return xtce_pk_from_unsigned((uint64_t)r, bits, f, err, out);
        }
        if (r < -9223372036854775808.0 || r >= 9223372036854775808.0) {
                return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
        }
        return xtce_pk_from_signed((int64_t)r, bits, enc, f, err, out);
}

/* Rounds a calibrated value (half away from zero) to an integer from `lo` up
 * to but not including `lim`; fails with XTCE_PK_OUT_OF_RANGE otherwise. */
/* The value of a BCD field: packed BCD has a decimal digit in every 4 bits,
 * and (unpacked) BCD one in the low 4 bits of every byte, whose high 4 bits
 * are zero; the first digit is the most significant.  Fails with
 * XTCE_PK_INVALID_VALUE for any other bits. */
static inline int xtce_pk_bcd_decode(uint64_t raw, unsigned bits, bool packed,
                                     const char* f, xtce_pk_error* err,
                                     uint64_t* out) {
        unsigned width = packed ? 4 : 8, shift = bits;
        uint64_t v = 0;
        while (shift >= width) {
                uint64_t d;
                shift -= width;
                d = (raw >> shift) & xtce_pk_mask(width);
                if (d > 9)
                        return xtce_pk_fail(err, XTCE_PK_INVALID_VALUE, f,
                                            NULL);
                v = v * 10 + d;
        }
        *out = v;
        return 0;
}

/* The BCD field holding `v`; fails with XTCE_PK_OUT_OF_RANGE unless the
 * field's digits hold it. */
static inline int xtce_pk_bcd_encode(uint64_t v, unsigned bits, bool packed,
                                     const char* f, xtce_pk_error* err,
                                     uint64_t* out) {
        unsigned width = packed ? 4 : 8, digits = bits / width, i;
        uint64_t limit = 1, raw = 0;
        for (i = 0; i < digits && i < 19; ++i) limit *= 10;
        if (digits < 20 && v >= limit)
                return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
        for (i = 0; i < digits; ++i) {
                raw |= (v % 10) << (i * width);
                v /= 10;
        }
        *out = raw;
        return 0;
}

static inline int xtce_pk_round_to_int(double y, double lo, double lim,
                                       const char* f, xtce_pk_error* err,
                                       double* out) {
        double r = round(y);
        if (!(r >= lo && r < lim))
                return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
        *out = r;
        return 0;
}

/* Three-way comparisons of integers of different signedness. */
static inline int xtce_pk_cmp_u(uint64_t a, uint64_t b) {
        return a < b ? -1 : a > b;
}
static inline int xtce_pk_cmp_i(int64_t a, int64_t b) {
        return a < b ? -1 : a > b;
}

/* --- floats
 * ----------------------------------------------------------------------- */

static inline double xtce_pk_float_from_raw(uint64_t raw, unsigned bits) {
        if (bits == 32) {
                uint32_t u = (uint32_t)raw;
                float v;
                memcpy(&v, &u, 4);
                return v;
        } else {
                double v;
                memcpy(&v, &raw, 8);
                return v;
        }
}

static inline uint64_t xtce_pk_float_to_raw(double x, unsigned bits) {
        if (bits == 32) {
                /* Like Rust's `as f32`: round to nearest, ties to even.  Finite
                 * values from half an ulp above FLT_MAX (0x1.ffffffp+127) up
                 * round to infinity; smaller ones above FLT_MAX round to
                 * FLT_MAX.  Both are handled here because converting an
                 * out-of-range double to float is undefined in C. */
                float v;
                if (isfinite(x) && fabs(x) >= 0x1.ffffffp+127) {
                        v = x > 0 ? INFINITY : -INFINITY;
                } else if (isfinite(x) && fabs(x) > FLT_MAX) {
                        v = x > 0 ? FLT_MAX : -FLT_MAX;
                } else {
                        v = (float)x;
                }
                uint32_t u;
                memcpy(&u, &v, 4);
                return u;
        } else {
                uint64_t u;
                memcpy(&u, &x, 8);
                return u;
        }
}

/* Floating-point encodings of XTCE's FloatDataEncoding: IEEE 754 (16, 32, 64,
 * 80 or 128 bits), MIL-STD-1750A (32 or 48), DEC (32 for F_floating, 64 for
 * D_floating), IBM hexadecimal (32, 64 or 128) and TI TMS320C3x (16, 32 or
 * 40).  Raw values of more than 64 bits are passed as (hi, lo). */
enum {
        XTCE_PK_IEEE = 0,
        XTCE_PK_MIL1750A,
        XTCE_PK_DEC,
        XTCE_PK_IBM,
        XTCE_PK_TI,
};

/* The number of leading zero bits of `v` (64 for 0). */
static inline int xtce_pk_clz64(uint64_t v) {
        int n = 0;
        if (!v) return 64;
        while (!(v >> 63)) {
                v <<= 1;
                ++n;
        }
        return n;
}

/* m / 2^shift rounded to the nearest integer, ties to even, where `sticky`
 * says that nonzero bits follow m; m * 2^-shift when shift is negative. */
static inline uint64_t xtce_pk_round_shift(uint64_t m, int shift,
                                           bool sticky) {
        uint64_t q, r, half;
        if (shift <= 0) return m << -shift;
        if (shift > 64) return 0;
        if (shift == 64) {
                q = 0;
                r = m;
                half = (uint64_t)1 << 63;
        } else {
                q = m >> shift;
                r = m & xtce_pk_mask((unsigned)shift);
                half = (uint64_t)1 << (shift - 1);
        }
        return q + (r > half || (r == half && (sticky || (q & 1))));
}

/* (-1)^neg * (m + a fraction if sticky) * 2^e rounded to the nearest double,
 * ties to even; infinite when too large. */
static inline double xtce_pk_make_double(bool neg, uint64_t m, int e,
                                         bool sticky) {
        double sign = neg ? -1.0 : 1.0, d;
        int top, low;
        uint64_t q, bits;
        if (!m) return 0.0 * sign;
        top = e + 63 - xtce_pk_clz64(m); /* m * 2^e < 2^(top + 1) */
        if (top > 1023) return HUGE_VAL * sign;
        low = (top > -1022 ? top : -1022) - 52;
        q = xtce_pk_round_shift(m, low - e, sticky);
        if (q >> 53) {
                q >>= 1;
                ++low;
        }
        if (!(q >> 52)) {
                bits = q; /* subnormal */
        } else {
                int biased = low + 52 + 1023;
                if (biased >= 2047) return HUGE_VAL * sign;
                bits = ((uint64_t)biased << 52) | (q & xtce_pk_mask(52));
        }
        memcpy(&d, &bits, 8);
        return d * sign;
}

/* xtce_pk_make_double for a 128-bit m = (hi, lo). */
static inline double xtce_pk_make_double128(bool neg, uint64_t hi,
                                            uint64_t lo, int e) {
        int n;
        if (!hi) return xtce_pk_make_double(neg, lo, e, false);
        n = 64 - xtce_pk_clz64(hi); /* the top 64 bits, and a sticky bit */
        if (n == 64)
                return xtce_pk_make_double(neg, hi, e + 64, lo != 0);
        return xtce_pk_make_double(neg, (hi << (64 - n)) | (lo >> n), e + n,
                                   (lo & xtce_pk_mask((unsigned)n)) != 0);
}

/* |x| == m * 2^e for finite nonzero x, with m of 53 bits; *power says
 * whether |x| is a power of two. */
static inline void xtce_pk_magnitude(double x, uint64_t* m, int* e,
                                     bool* power) {
        uint64_t b;
        int exp, shift;
        memcpy(&b, &x, 8);
        exp = (int)((b >> 52) & 0x7ff);
        *m = b & xtce_pk_mask(52);
        if (exp) {
                *m |= (uint64_t)1 << 52;
                *e = exp - 1075;
        } else {
                *e = -1074;
        }
        shift = xtce_pk_clz64(*m) - 11; /* to 53 bits */
        *m <<= shift;
        *e -= shift;
        *power = *m == (uint64_t)1 << 52;
}

static inline int64_t xtce_pk_sign_extend(uint64_t v, unsigned bits) {
        return (v >> (bits - 1)) & 1 ? (int64_t)(v | ~xtce_pk_mask(bits))
                                      : (int64_t)v;
}

static inline double xtce_pk_ieee_decode(uint64_t hi, uint64_t lo,
                                         unsigned bits) {
        unsigned ebits = bits == 16 ? 5 : bits == 32 ? 8 : bits == 64 ? 11 : 15;
        unsigned fbits = bits == 16 ? 10 : bits == 32 ? 23 : 52;
        int bias = (1 << (ebits - 1)) - 1, exp;
        bool neg;
        uint64_t frac;
        if (bits == 80) {
                neg = (hi >> 15) & 1;
                exp = (int)(hi & 0x7fff);
                if (exp == 0x7fff) {
                        if (lo & xtce_pk_mask(63)) return NAN;
                        return neg ? -HUGE_VAL : HUGE_VAL;
                }
                return xtce_pk_make_double(neg, lo, (exp ? exp : 1) - bias - 63,
                                           false);
        }
        if (bits == 128) {
                uint64_t fhi = hi & xtce_pk_mask(48);
                neg = hi >> 63;
                exp = (int)((hi >> 48) & 0x7fff);
                if (exp == 0x7fff) {
                        if (fhi || lo) return NAN;
                        return neg ? -HUGE_VAL : HUGE_VAL;
                }
                if (exp) fhi |= (uint64_t)1 << 48;
                return xtce_pk_make_double128(neg, fhi, lo,
                                              (exp ? exp : 1) - bias - 112);
        }
        neg = (lo >> (bits - 1)) & 1;
        exp = (int)((lo >> fbits) & xtce_pk_mask(ebits));
        frac = lo & xtce_pk_mask(fbits);
        if (exp == (1 << ebits) - 1) {
                if (frac) return NAN;
                return neg ? -HUGE_VAL : HUGE_VAL;
        }
        if (!exp) return xtce_pk_make_double(neg, frac, 1 - bias - (int)fbits,
                                             false);
        return xtce_pk_make_double(neg, frac | ((uint64_t)1 << fbits),
                                   exp - bias - (int)fbits, false);
}

static inline void xtce_pk_ieee_encode(double x, unsigned bits, uint64_t* hi,
                                       uint64_t* lo) {
        uint64_t m, q;
        int e, low, p, bias, top;
        unsigned ebits, fbits;
        bool power, neg = signbit(x) != 0;
        *hi = *lo = 0;
        if (bits == 80 || bits == 128) {
                /* Every double is a normal number in these formats. */
                uint64_t sign = neg ? 0x8000 : 0, biased;
                if (bits == 128) sign <<= 48;
                if (isnan(x)) {
                        if (bits == 80) {
                                *hi = 0x7fff | sign;
                                *lo = (uint64_t)3 << 62;
                        } else {
                                *hi = sign | ((uint64_t)0x7fff << 48) |
                                      ((uint64_t)1 << 47);
                        }
                        return;
                }
                if (isinf(x)) {
                        if (bits == 80) {
                                *hi = 0x7fff | sign;
                                *lo = (uint64_t)1 << 63;
                        } else {
                                *hi = sign | ((uint64_t)0x7fff << 48);
                        }
                        return;
                }
                if (x == 0) {
                        *hi = sign;
                        return;
                }
                xtce_pk_magnitude(x, &m, &e, &power);
                biased = (uint64_t)(e + 52 + 16383);
                if (bits == 80) {
                        *hi = sign | biased;
                        *lo = m << 11;
                } else {
                        m &= xtce_pk_mask(52); /* the fraction, 112 bits */
                        *hi = sign | (biased << 48) | (m >> 4);
                        *lo = m << 60;
                }
                return;
        }
        ebits = bits == 16 ? 5 : bits == 32 ? 8 : 11;
        fbits = bits == 16 ? 10 : bits == 32 ? 23 : 52;
        top = (1 << ebits) - 1;
        if (isnan(x)) {
                *lo = ((uint64_t)neg << (bits - 1)) | ((uint64_t)top << fbits) |
                      ((uint64_t)1 << (fbits - 1));
                return;
        }
        if (isinf(x)) {
                *lo = ((uint64_t)neg << (bits - 1)) | ((uint64_t)top << fbits);
                return;
        }
        if (x == 0) {
                *lo = (uint64_t)neg << (bits - 1);
                return;
        }
        bias = (1 << (ebits - 1)) - 1;
        p = (int)fbits + 1;
        xtce_pk_magnitude(x, &m, &e, &power);
        low = (e + 52 > 1 - bias ? e + 52 : 1 - bias) - (p - 1);
        q = xtce_pk_round_shift(m, low - e, false);
        if (q >> p) {
                q >>= 1;
                ++low;
        }
        *lo = (uint64_t)neg << (bits - 1);
        if (!(q >> (p - 1))) {
                *lo |= q; /* subnormal */
                return;
        }
        if (low + p - 1 + bias >= top) {
                *lo |= (uint64_t)top << fbits;
                return;
        }
        *lo |= ((uint64_t)(low + p - 1 + bias) << fbits) |
               (q & xtce_pk_mask(fbits));
}

static inline double xtce_pk_mil1750a_decode(uint64_t raw, unsigned bits) {
        uint64_t mant;
        unsigned mbits = bits == 32 ? 24 : 40;
        int64_t m, exp;
        if (bits == 32) {
                mant = raw >> 8;
                exp = xtce_pk_sign_extend(raw & 0xff, 8);
        } else {
                mant = ((raw >> 24) << 16) | (raw & 0xffff);
                exp = xtce_pk_sign_extend((raw >> 16) & 0xff, 8);
        }
        m = xtce_pk_sign_extend(mant, mbits);
        return xtce_pk_make_double(m < 0, m < 0 ? (uint64_t)-m : (uint64_t)m,
                                   (int)exp - (int)(mbits - 1), false);
}

static inline int xtce_pk_mil1750a_encode(double x, unsigned bits,
                                          const char* f, xtce_pk_error* err,
                                          uint64_t* out) {
        unsigned mbits = bits == 32 ? 24 : 40;
        int fb = (int)mbits - 1, e, top, exp;
        uint64_t m, mag, mant;
        bool power;
        *out = 0;
        if (x == 0) return 0;
        xtce_pk_magnitude(x, &m, &e, &power);
        top = e + 52;
        if (x > 0) {
                exp = top + 1;
                mag = xtce_pk_round_shift(m, exp - fb - e, false);
                if (mag == (uint64_t)1 << fb) {
                        mag >>= 1;
                        ++exp;
                }
                mant = mag;
        } else {
                /* Negative mantissas are normalized to [-1, -1/2). */
                exp = power ? top : top + 1;
                mag = xtce_pk_round_shift(m, exp - fb - e, false);
                if (mag == (uint64_t)1 << (fb - 1)) {
                        mag = (uint64_t)1 << fb;
                        --exp;
                }
                mant = 0 - mag;
        }
        if (exp > 127) return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
        if (exp < -128) {
                /* Too small to normalize: unnormalized at the smallest
                 * exponent. */
                exp = -128;
                mag = xtce_pk_round_shift(m, exp - fb - e, false);
                mant = x > 0 ? mag : 0 - mag;
        }
        mant &= xtce_pk_mask(mbits);
        if (bits == 32)
                *out = (mant << 8) | ((uint64_t)exp & 0xff);
        else
                *out = ((mant >> 16) << 24) | (((uint64_t)exp & 0xff) << 16) |
                       (mant & 0xffff);
        return 0;
}

static inline int xtce_pk_dec_decode(uint64_t raw, unsigned bits,
                                     const char* f, xtce_pk_error* err,
                                     double* out) {
        unsigned fb = bits == 32 ? 23 : 55;
        bool neg = (raw >> (bits - 1)) & 1;
        int exp = (int)((raw >> fb) & 0xff);
        if (!exp) {
                if (neg) /* a reserved operand */
                        return xtce_pk_fail(err, XTCE_PK_INVALID_VALUE, f,
                                            NULL);
                *out = 0.0;
                return 0;
        }
        *out = xtce_pk_make_double(
                neg, (raw & xtce_pk_mask(fb)) | ((uint64_t)1 << fb),
                exp - 129 - (int)fb, false);
        return 0;
}

static inline int xtce_pk_dec_encode(double x, unsigned bits, const char* f,
                                     xtce_pk_error* err, uint64_t* out) {
        int fb = bits == 32 ? 23 : 55, e, top, exp;
        uint64_t m, q;
        bool power;
        *out = 0;
        if (x == 0) return 0;
        xtce_pk_magnitude(x, &m, &e, &power);
        top = e + 52;
        q = xtce_pk_round_shift(m, top - fb - e, false);
        if (q >> (fb + 1)) {
                q >>= 1;
                ++top;
        }
        exp = top + 129;
        if (exp > 255) return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
        if (exp < 1) return 0;
        *out = ((uint64_t)(x < 0) << (bits - 1)) | ((uint64_t)exp << fb) |
               (q - ((uint64_t)1 << fb));
        return 0;
}

static inline double xtce_pk_ibm_decode(uint64_t hi, uint64_t lo,
                                        unsigned bits) {
        uint64_t m56 = xtce_pk_mask(56);
        if (bits == 128) {
                bool neg = hi >> 63;
                int exp = (int)((hi >> 56) & 0x7f);
                /* The 112-bit fraction: 56 bits from each half. */
                uint64_t fhi = (hi & m56) >> 8;
                uint64_t flo = ((hi & 0xff) << 56) | (lo & m56);
                return xtce_pk_make_double128(neg, fhi, flo,
                                              4 * (exp - 64) - 112);
        } else {
                bool neg = (lo >> (bits - 1)) & 1;
                int exp = (int)((lo >> (bits - 8)) & 0x7f);
                return xtce_pk_make_double(neg, lo & xtce_pk_mask(bits - 8),
                                           4 * (exp - 64) - (int)(bits - 8),
                                           false);
        }
}

static inline int xtce_pk_ibm_encode(double x, unsigned bits, const char* f,
                                     xtce_pk_error* err, uint64_t* hi,
                                     uint64_t* lo) {
        int fb = bits == 128 ? 112 : (int)bits - 8, e, h, exp, shift;
        uint64_t m, sign;
        bool power;
        *hi = *lo = 0;
        if (x == 0) return 0;
        xtce_pk_magnitude(x, &m, &e, &power);
        h = (e + 52 >= 0 ? (e + 52) / 4 : -((3 - (e + 52)) / 4)) + 1;
        shift = 4 * h - fb - e; /* the fraction is m / 2^shift */
        sign = x < 0;
        if (bits != 128) {
                uint64_t q = xtce_pk_round_shift(m, shift, false);
                if (q >> fb) {
                        q >>= 4;
                        ++h;
                }
                exp = h + 64;
                if (exp > 127)
                        return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
                if (exp < 0) {
                        /* Too small to normalize: unnormalized at the
                         * smallest exponent. */
                        exp = 0;
                        q = xtce_pk_round_shift(m, -256 - fb - e, false);
                }
                *lo = (sign << (bits - 1)) | ((uint64_t)exp << fb) | q;
                return 0;
        }
        exp = h + 64;
        if (exp > 127) return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
        if (exp < 0) {
                /* Too small to normalize: unnormalized at the smallest
                 * exponent. */
                exp = 0;
                shift = -256 - fb - e;
        }
        {
                uint64_t qhi = 0, qlo = 0, m56 = xtce_pk_mask(56);
                /* 112 fraction bits hold all 53 of m exactly, unless it is
                 * unnormalized. */
                if (shift > 0)
                        qlo = xtce_pk_round_shift(m, shift, false);
                else
                        xtce_pk_or128(&qhi, &qlo, m, (unsigned)-shift);
                /* The low half has the sign, and a characteristic 14 less
                 * (modulo 128). */
                *hi = (sign << 63) | ((uint64_t)exp << 56) |
                      (xtce_pk_shr128(qhi, qlo, 56) & m56);
                *lo = (sign << 63) | ((uint64_t)((exp - 14) & 0x7f) << 56) |
                      (qlo & m56);
        }
        return 0;
}

static inline double xtce_pk_ti_decode(uint64_t raw, unsigned bits) {
        unsigned ebits = bits == 16 ? 4 : 8;
        unsigned fb = bits == 16 ? 11 : bits == 32 ? 23 : 31;
        int exp = (int)xtce_pk_sign_extend(raw >> (fb + 1), ebits);
        uint64_t frac = raw & xtce_pk_mask(fb);
        if (exp == -(1 << (ebits - 1))) return 0.0;
        if ((raw >> fb) & 1) /* (-2 + frac) * 2^exp */
                return xtce_pk_make_double(true, ((uint64_t)2 << fb) - frac,
                                           exp - (int)fb, false);
        return xtce_pk_make_double(false, ((uint64_t)1 << fb) | frac,
                                   exp - (int)fb, false);
}

static inline int xtce_pk_ti_encode(double x, unsigned bits, const char* f,
                                    xtce_pk_error* err, uint64_t* out) {
        unsigned ebits = bits == 16 ? 4 : 8;
        int fb = bits == 16 ? 11 : bits == 32 ? 23 : 31;
        int lo = -(1 << (ebits - 1)), e, top, exp; /* lo: the zero exponent */
        uint64_t m, q, sign, frac, emask = xtce_pk_mask(ebits);
        uint64_t zero = ((uint64_t)lo & emask) << (fb + 1);
        bool power;
        *out = zero;
        if (x == 0) return 0;
        xtce_pk_magnitude(x, &m, &e, &power);
        top = e + 52;
        if (x > 0) {
                exp = top;
                q = xtce_pk_round_shift(m, exp - fb - e, false);
                if (q >> (fb + 1)) {
                        q >>= 1;
                        ++exp;
                }
                sign = 0;
                frac = q - ((uint64_t)1 << fb);
        } else {
                /* |x| / 2^exp is in (1, 2]: x == (-2 + frac) * 2^exp. */
                exp = power ? top - 1 : top;
                q = xtce_pk_round_shift(m, exp - fb - e, false);
                if (q == (uint64_t)1 << fb) {
                        --exp;
                        q = (uint64_t)2 << fb;
                }
                sign = 1;
                frac = ((uint64_t)2 << fb) - q;
        }
        if (exp > -lo - 1)
                return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
        if (exp <= lo) return 0;
        *out = (((uint64_t)exp & emask) << (fb + 1)) | (sign << fb) | frac;
        return 0;
}

/* The value of a float field of `bits` bits in `fmt`, raw value (hi, lo),
 * rounded to the nearest double.  Fails with XTCE_PK_INVALID_VALUE for a DEC
 * reserved operand. */
static inline int xtce_pk_float_decode(int fmt, unsigned bits, uint64_t hi,
                                       uint64_t lo, const char* f,
                                       xtce_pk_error* err, double* out) {
        switch (fmt) {
                case XTCE_PK_IEEE:
                        *out = xtce_pk_ieee_decode(hi, lo, bits);
                        return 0;
                case XTCE_PK_MIL1750A:
                        *out = xtce_pk_mil1750a_decode(lo, bits);
                        return 0;
                case XTCE_PK_DEC:
                        return xtce_pk_dec_decode(lo, bits, f, err, out);
                case XTCE_PK_IBM:
                        *out = xtce_pk_ibm_decode(hi, lo, bits);
                        return 0;
                default:
                        *out = xtce_pk_ti_decode(lo, bits);
                        return 0;
        }
}

/* The float field nearest `x` (ties to even), as xtce_pk_float_decode reads
 * it.  IEEE formats overflow to infinity; the others, which have no infinity
 * or NaN, fail with XTCE_PK_OUT_OF_RANGE for values too large for them and
 * for infinities and NaN, and encode values too small for them as zero. */
static inline int xtce_pk_float_encode(int fmt, unsigned bits, double x,
                                       const char* f, xtce_pk_error* err,
                                       uint64_t* hi, uint64_t* lo) {
        *hi = 0;
        if (fmt == XTCE_PK_IEEE) {
                xtce_pk_ieee_encode(x, bits, hi, lo);
                return 0;
        }
        if (!isfinite(x))
                return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
        switch (fmt) {
                case XTCE_PK_MIL1750A:
                        return xtce_pk_mil1750a_encode(x, bits, f, err, lo);
                case XTCE_PK_DEC:
                        return xtce_pk_dec_encode(x, bits, f, err, lo);
                case XTCE_PK_IBM:
                        return xtce_pk_ibm_encode(x, bits, f, err, hi, lo);
                default:
                        return xtce_pk_ti_encode(x, bits, f, err, lo);
        }
}

/* --- calibrators
 * ------------------------------------------------------------------- */

typedef struct xtce_pk_term {
        double coefficient;
        int exponent;
} xtce_pk_term;

typedef struct xtce_pk_point {
        double raw;
        double calibrated;
} xtce_pk_point;

static inline double xtce_pk_polynomial(double x, const xtce_pk_term* terms,
                                        size_t n) {
        double y = 0.0;
        size_t i;
        for (i = 0; i < n; ++i) {
                const xtce_pk_term* t = &terms[i];
                y += t->exponent == 0 ? t->coefficient
                     : t->exponent == 1
                             ? t->coefficient * x
                             : t->coefficient * pow(x, (double)t->exponent);
        }
        return y;
}

static inline double xtce_pk_polynomial_inverse(double y, double intercept,
                                                double slope) {
        return (y - intercept) / slope;
}

static inline double xtce_pk_lerp(double x, double x0, double y0, double x1,
                                  double y1) {
        return y0 + (x - x0) * (y1 - y0) / (x1 - x0);
}

static inline int xtce_pk_spline(double x, const xtce_pk_point* p, size_t n,
                                 unsigned order, bool extrapolate,
                                 const char* f, xtce_pk_error* err,
                                 double* out) {
        size_t i;
        if (x < p[0].raw || x > p[n - 1].raw) {
                if (!extrapolate)
                        return xtce_pk_fail(err, XTCE_PK_CALIBRATION, f, NULL);
                if (order == 0) {
                        *out = x < p[0].raw ? p[0].calibrated
                                            : p[n - 1].calibrated;
                } else if (x < p[0].raw) {
                        *out = xtce_pk_lerp(x, p[0].raw, p[0].calibrated,
                                            p[1].raw, p[1].calibrated);
                } else {
                        *out = xtce_pk_lerp(x, p[n - 2].raw,
                                            p[n - 2].calibrated, p[n - 1].raw,
                                            p[n - 1].calibrated);
                }
                return 0;
        }
        for (i = 0; i + 1 < n; ++i) {
                if (x < p[i + 1].raw) {
                        *out = order == 0 ? p[i].calibrated
                                          : xtce_pk_lerp(x, p[i].raw,
                                                         p[i].calibrated,
                                                         p[i + 1].raw,
                                                         p[i + 1].calibrated);
                        return 0;
                }
        }
        *out = p[n - 1].calibrated;
        return 0;
}

static inline int xtce_pk_spline_inverse(double y, const xtce_pk_point* p,
                                         size_t n, bool extrapolate,
                                         const char* f, xtce_pk_error* err,
                                         double* out) {
        size_t i;
        bool increasing;
        for (i = 0; i + 1 < n; ++i) {
                double a = p[i].calibrated, b = p[i + 1].calibrated;
                if ((a <= y && y <= b) || (b <= y && y <= a)) {
                        *out = xtce_pk_lerp(y, p[i].calibrated, p[i].raw,
                                            p[i + 1].calibrated, p[i + 1].raw);
                        return 0;
                }
        }
        if (!extrapolate)
                return xtce_pk_fail(err, XTCE_PK_CALIBRATION, f, NULL);
        increasing = p[n - 1].calibrated > p[0].calibrated;
        if ((y < p[0].calibrated) == increasing) {
                *out = xtce_pk_lerp(y, p[0].calibrated, p[0].raw,
                                    p[1].calibrated, p[1].raw);
        } else {
                *out = xtce_pk_lerp(y, p[n - 2].calibrated, p[n - 2].raw,
                                    p[n - 1].calibrated, p[n - 1].raw);
        }
        return 0;
}

/* --- sizes from other fields
 * ---------------------------------------------------------- */

/* Checks that an array whose count another field gives, and that has `len`
 * elements, has element `i`.  Fails with XTCE_PK_BAD_SIZE, naming `f`, if it
 * doesn't. */
static inline int xtce_pk_element(size_t len, size_t i, const char* f,
                                  xtce_pk_error* err) {
        return i < len ? 0 : xtce_pk_fail(err, XTCE_PK_BAD_SIZE, f, NULL);
}

/* A size or count that another field gives (an XTCE DynamicValue):
 * (slope * x + intercept) / unit, where x is that field's value and unit is 8
 * for a size in bits used in whole bytes.  Fails with XTCE_PK_BAD_SIZE unless
 * the result is a whole number from 0 to `max`. */
static inline int xtce_pk_dynamic_size(double x, double slope, double intercept,
                                       unsigned unit, size_t max, const char* f,
                                       xtce_pk_error* err, size_t* out) {
        double y = slope * x + intercept;
        double q = y / unit;
        if (!(y >= 0.0) || y != floor(y) || q != floor(q) || q > (double)max) {
                return xtce_pk_fail(err, XTCE_PK_BAD_SIZE, f, NULL);
        }
        *out = (size_t)q;
        return 0;
}

/* The value of the field that gives a size or count of `n` (the inverse of
 * xtce_pk_dynamic_size followed by multiplying by `factor`), for encoding a
 * field computed from the length of the value it sizes.  Fails with
 * BAD_SIZE, naming `f` (the sized value), when no whole number gives
 * exactly `n`. */
static inline int xtce_pk_size_source(size_t n, double slope, double intercept,
                                      unsigned unit, size_t factor,
                                      const char* f, xtce_pk_error* err,
                                      int64_t* out) {
        if (n % factor != 0) {
                return xtce_pk_fail(err, XTCE_PK_BAD_SIZE, f, NULL);
        }
        double y = (double)(n / factor) * unit;
        double v = (y - intercept) / slope;
        if (!(fabs(v) < 9.2e18)) {
                return xtce_pk_fail(err, XTCE_PK_BAD_SIZE, f, NULL);
        }
        double x = floor(v + 0.5);
        /* Accept only a value that decoding maps back to exactly `n`. */
        if (slope * x + intercept != y) {
                return xtce_pk_fail(err, XTCE_PK_BAD_SIZE, f, NULL);
        }
        *out = (int64_t)x;
        return 0;
}

/* --- strings and binary
 * --------------------------------------------------------------- */

/* Character sets of XTCE's StringDataEncoding.  UTF-16 and UTF-32 without a
 * byte order are read big endian unless they start with a byte order mark,
 * and written big endian without one. */
enum {
        XTCE_PK_UTF8 = 0,
        XTCE_PK_ASCII,
        XTCE_PK_LATIN1,
        XTCE_PK_WINDOWS1252,
        XTCE_PK_UTF16,
        XTCE_PK_UTF16BE,
        XTCE_PK_UTF16LE,
        XTCE_PK_UTF32,
        XTCE_PK_UTF32BE,
        XTCE_PK_UTF32LE,
};

/* The bytes in one code unit of charset `cs`. */
static inline size_t xtce_pk_code_unit(int cs) {
        return cs >= XTCE_PK_UTF32 ? 4 : cs >= XTCE_PK_UTF16 ? 2 : 1;
}

/* Layout of a string field; exactly one of terminator and size_tag_bits is
 * used.  The terminator is found only where a code unit starts. */
typedef struct xtce_pk_string_spec {
        bool has_fixed;
        size_t fixed_bits; /* bits the field always occupies, if has_fixed */
        size_t max_bits;   /* upper bound on the field's bits */
        bool has_terminator;
        const uint8_t* terminator;
        size_t term_len;
        unsigned size_tag_bits;
        int charset;
} xtce_pk_string_spec;

/* The spec of a string whose size another field gives: exactly `bytes` bytes.
 */
static inline xtce_pk_string_spec xtce_pk_string_spec_sized(
        const xtce_pk_string_spec* spec, size_t bytes) {
        xtce_pk_string_spec s = *spec;
        s.has_fixed = true;
        s.fixed_bits = bytes * 8;
        s.max_bits = bytes * 8;
        return s;
}

/* True when the `n` bytes at `s` are well-formed UTF-8 (no overlongs,
 * surrogates or values beyond U+10FFFF), matching the Rust and Python
 * decoders. */
static inline bool xtce_pk_valid_utf8(const uint8_t* s, size_t n) {
        size_t i = 0;
        while (i < n) {
                unsigned c = s[i];
                size_t k, more;
                unsigned lo = 0x80, hi = 0xbf;
                if (c < 0x80) {
                        ++i;
                        continue;
                } else if (c >= 0xc2 && c <= 0xdf) {
                        more = 1;
                } else if (c >= 0xe0 && c <= 0xef) {
                        more = 2;
                        if (c == 0xe0) lo = 0xa0;
                        if (c == 0xed) hi = 0x9f;
                } else if (c >= 0xf0 && c <= 0xf4) {
                        more = 3;
                        if (c == 0xf0) lo = 0x90;
                        if (c == 0xf4) hi = 0x8f;
                } else {
                        return false;
                }
                for (k = 1; k <= more; ++k) {
                        unsigned d;
                        if (i + k >= n) return false;
                        d = s[i + k];
                        if (d < (k == 1 ? lo : 0x80) ||
                            d > (k == 1 ? hi : 0xbf))
                                return false;
                }
                i += more + 1;
        }
        return true;
}

/* Unicode code points of Windows-1252 bytes 0x80 to 0x9f; 0 for the five
 * bytes it leaves undefined. */
static const uint16_t xtce_pk_windows1252[32] = {
        0x20ac, 0,      0x201a, 0x0192, 0x201e, 0x2026, 0x2020, 0x2021,
        0x02c6, 0x2030, 0x0160, 0x2039, 0x0152, 0,      0x017d, 0,
        0,      0x2018, 0x2019, 0x201c, 0x201d, 0x2022, 0x2013, 0x2014,
        0x02dc, 0x2122, 0x0161, 0x203a, 0x0153, 0,      0x017e, 0x0178};

/* The first position of the `t` bytes at `term` in the `n` bytes at `b` that
 * starts a code unit of `unit` bytes, or `n` if there is none. */
static inline size_t xtce_pk_find_unit(const uint8_t* b, size_t n,
                                       const uint8_t* term, size_t t,
                                       size_t unit) {
        size_t i;
        for (i = 0; i + t <= n; i += unit)
                if (!memcmp(b + i, term, t)) return i;
        return n;
}

/* Reads the bytes of a string's content, before its terminator, into the
 * `cap` bytes at `b`; the count goes to `*n`. */
static inline int xtce_pk_read_wire(xtce_pk_reader* r,
                                    const xtce_pk_string_spec* spec,
                                    const char* f, uint8_t* b, size_t cap,
                                    size_t* n) {
        size_t start = r->pos, unit = xtce_pk_code_unit(spec->charset);
        uint64_t v;
        *n = 0;
        if (spec->has_fixed && spec->has_terminator) {
                size_t len = spec->fixed_bits / 8;
                if (len > cap)
                        return xtce_pk_fail(r->err, XTCE_PK_TOO_LONG, f, NULL);
                XTCE_PK_TRY(xtce_pk_read_bytes(r, len, f, b));
                *n = xtce_pk_find_unit(b, len, spec->terminator,
                                       spec->term_len, unit);
        } else if (spec->has_fixed) {
                XTCE_PK_TRY(xtce_pk_read_bits(r, spec->size_tag_bits, f, &v));
                if (v > cap || v * 8 + spec->size_tag_bits > spec->fixed_bits) {
                        return xtce_pk_fail(r->err, XTCE_PK_TOO_LONG, f, NULL);
                }
                *n = (size_t)v;
                XTCE_PK_TRY(xtce_pk_read_bytes(r, *n, f, b));
                XTCE_PK_TRY(xtce_pk_read_seek(r, start,
                                              (int64_t)spec->fixed_bits, f));
        } else if (spec->has_terminator) {
                /* The last bytes read wait in `win` until it is clear that
                 * they don't start the terminator. */
                size_t t = spec->term_len, total = 0, wn = 0, k;
                uint8_t win[20];
                for (;;) {
                        if ((total + unit) * 8 > spec->max_bits)
                                return xtce_pk_fail(r->err, XTCE_PK_TOO_LONG, f,
                                                    NULL);
                        XTCE_PK_TRY(xtce_pk_read_bytes(r, unit, f, win + wn));
                        wn += unit;
                        total += unit;
                        if (wn >= t && (total - t) % unit == 0 &&
                            !memcmp(win + wn - t, spec->terminator, t)) {
                                wn -= t;
                                break;
                        }
                        for (k = 0; wn >= t && k + t <= wn; ++k) {
                                if (*n == cap)
                                        return xtce_pk_fail(
                                                r->err, XTCE_PK_TOO_LONG, f,
                                                NULL);
                                b[(*n)++] = win[k];
                        }
                        if (k) {
                                memmove(win, win + k, wn - k);
                                wn -= k;
                        }
                }
                if (*n + wn > cap)
                        return xtce_pk_fail(r->err, XTCE_PK_TOO_LONG, f, NULL);
                memcpy(b + *n, win, wn);
                *n += wn;
        } else {
                XTCE_PK_TRY(xtce_pk_read_bits(r, spec->size_tag_bits, f, &v));
                if (v > cap || v * 8 + spec->size_tag_bits > spec->max_bits) {
                        return xtce_pk_fail(r->err, XTCE_PK_TOO_LONG, f, NULL);
                }
                *n = (size_t)v;
                XTCE_PK_TRY(xtce_pk_read_bytes(r, *n, f, b));
        }
        return 0;
}

/* Appends code point `c` as UTF-8 to `out` at *len (of `cap` bytes); false if
 * it is not a Unicode scalar value or doesn't fit. */
static inline bool xtce_pk_put_utf8(uint32_t c, char* out, size_t cap,
                                    size_t* len) {
        uint8_t* o = (uint8_t*)out + *len;
        size_t k = c < 0x80 ? 1 : c < 0x800 ? 2 : c < 0x10000 ? 3 : 4;
        if (c > 0x10ffff || (c >= 0xd800 && c < 0xe000) || *len + k > cap)
                return false;
        if (k == 1) {
                o[0] = (uint8_t)c;
        } else if (k == 2) {
                o[0] = (uint8_t)(0xc0 | (c >> 6));
                o[1] = (uint8_t)(0x80 | (c & 0x3f));
        } else if (k == 3) {
                o[0] = (uint8_t)(0xe0 | (c >> 12));
                o[1] = (uint8_t)(0x80 | ((c >> 6) & 0x3f));
                o[2] = (uint8_t)(0x80 | (c & 0x3f));
        } else {
                o[0] = (uint8_t)(0xf0 | (c >> 18));
                o[1] = (uint8_t)(0x80 | ((c >> 12) & 0x3f));
                o[2] = (uint8_t)(0x80 | ((c >> 6) & 0x3f));
                o[3] = (uint8_t)(0x80 | (c & 0x3f));
        }
        *len += k;
        return true;
}

/* The code point of the UTF-8 sequence at s[*i], which is valid; advances
 * *i past it. */
static inline uint32_t xtce_pk_next_utf8(const uint8_t* s, size_t* i) {
        uint32_t c = s[*i];
        size_t more = c < 0x80 ? 0 : c < 0xe0 ? 1 : c < 0xf0 ? 2 : 3, k;
        if (more) c &= 0x3f >> more;
        for (k = 1; k <= more; ++k) c = (c << 6) | (s[*i + k] & 0x3f);
        *i += more + 1;
        return c;
}

/* Converts the `n` bytes at `b`, a string in charset `cs`, to UTF-8 in the
 * `cap` bytes at `out`.  The input may lie in `out`, at or after the place
 * where its own conversion would end, as xtce_pk_read_string arranges. */
static inline int xtce_pk_decode_text(int cs, const uint8_t* b, size_t n,
                                      char* out, size_t cap, size_t* len,
                                      const char* f, xtce_pk_error* err) {
        size_t i = 0, unit = xtce_pk_code_unit(cs);
        bool le = cs == XTCE_PK_UTF16LE || cs == XTCE_PK_UTF32LE;
        *len = 0;
        if (cs == XTCE_PK_UTF8 || cs == XTCE_PK_ASCII) {
                size_t k;
                if (cs == XTCE_PK_UTF8 ? !xtce_pk_valid_utf8(b, n) : 0)
                        return xtce_pk_fail(err, XTCE_PK_INVALID_STRING, f,
                                            NULL);
                for (k = 0; cs == XTCE_PK_ASCII && k < n; ++k)
                        if (b[k] >= 0x80)
                                return xtce_pk_fail(err, XTCE_PK_INVALID_STRING,
                                                    f, NULL);
                if (n > cap)
                        return xtce_pk_fail(err, XTCE_PK_TOO_LONG, f, NULL);
                memmove(out, b, n);
                *len = n;
                return 0;
        }
        if (cs == XTCE_PK_UTF16 && n >= 2 && b[0] == 0xfe && b[1] == 0xff) {
                i = 2;
        } else if (cs == XTCE_PK_UTF16 && n >= 2 && b[0] == 0xff &&
                   b[1] == 0xfe) {
                i = 2;
                le = true;
        } else if (cs == XTCE_PK_UTF32 && n >= 4 && !b[0] && !b[1] &&
                   b[2] == 0xfe && b[3] == 0xff) {
                i = 4;
        } else if (cs == XTCE_PK_UTF32 && n >= 4 && b[0] == 0xff &&
                   b[1] == 0xfe && !b[2] && !b[3]) {
                i = 4;
                le = true;
        }
        if ((n - i) % unit)
                return xtce_pk_fail(err, XTCE_PK_INVALID_STRING, f, NULL);
        while (i < n) {
                uint32_t c;
                if (unit == 1) {
                        c = b[i++];
                        if (cs == XTCE_PK_WINDOWS1252 && c >= 0x80 &&
                            c < 0xa0) {
                                c = xtce_pk_windows1252[c - 0x80];
                                if (!c) c = 0xd800; /* undefined: invalid */
                        }
                } else if (unit == 2) {
                        c = le ? b[i] | (uint32_t)b[i + 1] << 8
                               : (uint32_t)b[i] << 8 | b[i + 1];
                        i += 2;
                        if (c >= 0xd800 && c < 0xdc00 && i + 2 <= n) {
                                uint32_t d = le ? b[i] | (uint32_t)b[i + 1] << 8
                                                : (uint32_t)b[i] << 8 |
                                                          b[i + 1];
                                if (d >= 0xdc00 && d < 0xe000) {
                                        c = 0x10000 + ((c - 0xd800) << 10) +
                                            (d - 0xdc00);
                                        i += 2;
                                }
                        }
                } else {
                        c = le ? b[i] | (uint32_t)b[i + 1] << 8 |
                                         (uint32_t)b[i + 2] << 16 |
                                         (uint32_t)b[i + 3] << 24
                               : (uint32_t)b[i] << 24 |
                                         (uint32_t)b[i + 1] << 16 |
                                         (uint32_t)b[i + 2] << 8 | b[i + 3];
                        i += 4;
                }
                if (!xtce_pk_put_utf8(c, out, cap, len))
                        return xtce_pk_fail(err, XTCE_PK_INVALID_STRING, f,
                                            NULL);
        }
        return 0;
}

/* Reads a string into `out`, which holds `cap` bytes plus a terminating NUL
 * that is always written; the content length goes to `*len`.  In a charset
 * other than UTF-8 and US-ASCII, the bytes read go at the end of `out`, and
 * convert into its start: `cap` must be at least the most UTF-8 bytes the
 * most bytes the field holds can give, as the generated code makes it. */
static inline int xtce_pk_read_string(xtce_pk_reader* r,
                                      const xtce_pk_string_spec* spec,
                                      const char* f, char* out, size_t cap,
                                      size_t* len) {
        size_t wire = (spec->has_fixed ? spec->fixed_bits : spec->max_bits) / 8;
        size_t n, at = 0;
        int cs = spec->charset;
        if (cs != XTCE_PK_UTF8 && cs != XTCE_PK_ASCII && wire < cap)
                at = cap - wire;
        XTCE_PK_TRY(xtce_pk_read_wire(r, spec, f, (uint8_t*)out + at, cap - at,
                                      &n));
        XTCE_PK_TRY(xtce_pk_decode_text(cs, (const uint8_t*)out + at, n, out,
                                        cap, len, f, r->err));
        out[*len] = 0;
        return 0;
}

/* Reads a string field encoded as binary: `n` bytes, which must be UTF-8,
 * into `out`, which has room for `n + 1`; NUL-terminates them. */
static inline int xtce_pk_read_utf8(xtce_pk_reader* r, size_t n, const char* f,
                                    char* out, size_t* len) {
        XTCE_PK_TRY(xtce_pk_read_bytes(r, n, f, (uint8_t*)out));
        out[n] = 0;
        *len = n;
        if (!xtce_pk_valid_utf8((const uint8_t*)out, n))
                return xtce_pk_fail(r->err, XTCE_PK_INVALID_STRING, f, NULL);
        return 0;
}

/* Writes a string field encoded as binary: the `n` bytes of UTF-8 at `s`. */
static inline int xtce_pk_write_utf8(xtce_pk_writer* w, const char* s, size_t n,
                                     const char* f) {
        if (!xtce_pk_valid_utf8((const uint8_t*)s, n))
                return xtce_pk_fail(w->err, XTCE_PK_INVALID_STRING, f, NULL);
        return xtce_pk_write_bytes(w, (const uint8_t*)s, n);
}

/* The byte of code point `c` in the 8-bit charset `cs`, or -1. */
static inline int xtce_pk_byte_of(int cs, uint32_t c) {
        int i;
        if (c < 0x80) return (int)c;
        if (cs == XTCE_PK_ASCII) return -1;
        if (cs == XTCE_PK_LATIN1) return c < 0x100 ? (int)c : -1;
        if (c >= 0xa0 && c < 0x100) return (int)c;
        for (i = 0; i < 32; ++i)
                if (xtce_pk_windows1252[i] && xtce_pk_windows1252[i] == c)
                        return 0x80 + i;
        return -1;
}

/* The bytes of code point `c` in charset `cs` (not UTF-8) into `out`; their
 * count, or 0 if `cs` can't hold `c`. */
static inline size_t xtce_pk_char_bytes(int cs, uint32_t c, uint8_t out[4]) {
        size_t unit = xtce_pk_code_unit(cs), k;
        bool le = cs == XTCE_PK_UTF16LE || cs == XTCE_PK_UTF32LE;
        if (unit == 1) {
                int b = xtce_pk_byte_of(cs, c);
                if (b < 0) return 0;
                out[0] = (uint8_t)b;
                return 1;
        }
        if (unit == 4 || c < 0x10000) {
                for (k = 0; k < unit; ++k)
                        out[le ? k : unit - 1 - k] = (uint8_t)(c >> (8 * k));
                return unit;
        }
        c -= 0x10000;
        {
                uint32_t hi = 0xd800 + (c >> 10), lo = 0xdc00 + (c & 0x3ff);
                out[le ? 0 : 1] = (uint8_t)hi;
                out[le ? 1 : 0] = (uint8_t)(hi >> 8);
                out[le ? 2 : 3] = (uint8_t)lo;
                out[le ? 3 : 2] = (uint8_t)(lo >> 8);
        }
        return 4;
}

/* The bytes of the `n` bytes of UTF-8 at `s` in charset `cs`: their count
 * goes to *len, and whether the terminator occurs where a code unit starts
 * to *has_term; each byte goes to `w`, unless it is NULL.  Fails with
 * XTCE_PK_INVALID_STRING if `cs` can't hold a character of `s`. */
static inline int xtce_pk_wire_bytes(int cs, const uint8_t* s, size_t n,
                                     const xtce_pk_string_spec* spec,
                                     xtce_pk_writer* w, const char* f,
                                     xtce_pk_error* err, size_t* len,
                                     bool* has_term) {
        size_t i = 0, unit = xtce_pk_code_unit(cs), t = spec->term_len, k;
        uint8_t window[16] = {0};
        *len = 0;
        *has_term = false;
        while (i < n) {
                uint8_t bytes[4];
                size_t count;
                if (cs == XTCE_PK_UTF8) {
                        bytes[0] = s[i++];
                        count = 1;
                } else {
                        count = xtce_pk_char_bytes(
                                cs, xtce_pk_next_utf8(s, &i), bytes);
                        if (!count)
                                return xtce_pk_fail(err, XTCE_PK_INVALID_STRING,
                                                    f, NULL);
                }
                for (k = 0; k < count; ++k) {
                        memmove(window, window + 1, 15);
                        window[15] = bytes[k];
                        ++*len;
                        if (spec->has_terminator && *len >= t &&
                            (*len - t) % unit == 0 &&
                            !memcmp(window + 16 - t, spec->terminator, t))
                                *has_term = true;
                        if (w) XTCE_PK_TRY(xtce_pk_write_bits(w, 8, bytes[k]));
                }
        }
        return 0;
}

static inline int xtce_pk_write_string(xtce_pk_writer* w,
                                       const xtce_pk_string_spec* spec,
                                       const char* s, size_t n, const char* f) {
        const uint8_t* b = (const uint8_t*)s;
        size_t tag = spec->size_tag_bits, len;
        int cs = spec->charset;
        bool has_term;
        if (!xtce_pk_valid_utf8(b, n))
                return xtce_pk_fail(w->err, XTCE_PK_INVALID_STRING, f, NULL);
        XTCE_PK_TRY(xtce_pk_wire_bytes(cs, b, n, spec, NULL, f, w->err, &len,
                                       &has_term));
        if (spec->has_terminator) {
                size_t room =
                        (spec->has_fixed ? spec->fixed_bits : spec->max_bits) /
                        8;
                /* A fixed-size string may fill its field; otherwise the
                 * terminator follows it. */
                size_t need = spec->has_fixed && len == room
                                      ? room
                                      : len + spec->term_len;
                if (has_term)
                        return xtce_pk_fail(w->err, XTCE_PK_INVALID_STRING, f,
                                            NULL);
                if (need > room)
                        return xtce_pk_fail(w->err, XTCE_PK_TOO_LONG, f, NULL);
                if (cs == XTCE_PK_UTF8)
                        XTCE_PK_TRY(xtce_pk_write_bytes(w, b, n));
                else
                        XTCE_PK_TRY(xtce_pk_wire_bytes(cs, b, n, spec, w, f,
                                                       w->err, &len,
                                                       &has_term));
                if (len < room)
                        XTCE_PK_TRY(xtce_pk_write_bytes(w, spec->terminator,
                                                        spec->term_len));
                if (spec->has_fixed && need < room)
                        XTCE_PK_TRY(xtce_pk_write_zeros(w, (room - need) * 8));
        } else {
                size_t room =
                        spec->has_fixed ? spec->fixed_bits : spec->max_bits;
                if (len * 8 + tag > room ||
                    len > xtce_pk_mask(spec->size_tag_bits)) {
                        return xtce_pk_fail(w->err, XTCE_PK_TOO_LONG, f, NULL);
                }
                XTCE_PK_TRY(xtce_pk_write_bits(w, spec->size_tag_bits, len));
                if (cs == XTCE_PK_UTF8)
                        XTCE_PK_TRY(xtce_pk_write_bytes(w, b, n));
                else
                        XTCE_PK_TRY(xtce_pk_wire_bytes(cs, b, n, spec, w, f,
                                                       w->err, &len,
                                                       &has_term));
                if (spec->has_fixed)
                        XTCE_PK_TRY(xtce_pk_write_zeros(
                                w, spec->fixed_bits - tag - len * 8));
        }
        return 0;
}

/* Three-way comparison of byte strings, shorter first on a common prefix. */
static inline int xtce_pk_bytes_cmp(const void* a, size_t na, const void* b,
                                    size_t nb) {
        int c = memcmp(a, b, na < nb ? na : nb);
        if (c) return c < 0 ? -1 : 1;
        return na < nb ? -1 : na > nb;
}

/* --- canonical text (identical in every runtime)
 * ------------------------------------------ */

/* Accumulates text into `cap` bytes at `buf` (which may be NULL when `cap` is
 * 0), truncating what does not fit but counting it in `len`, like snprintf. */
typedef struct xtce_pk_text {
        char* buf;
        size_t cap;
        size_t len;
} xtce_pk_text;

static inline xtce_pk_text xtce_pk_text_init(char* buf, size_t cap) {
        xtce_pk_text t;
        t.buf = buf;
        t.cap = cap;
        t.len = 0;
        if (cap) buf[0] = 0;
        return t;
}

static inline void xtce_pk_text_putn(xtce_pk_text* t, const char* s, size_t n) {
        if (t->len + 1 < t->cap) {
                size_t room = t->cap - 1 - t->len;
                size_t k = n < room ? n : room;
                memcpy(t->buf + t->len, s, k);
                t->buf[t->len + k] = 0;
        }
        t->len += n;
}

static inline void xtce_pk_text_puts(xtce_pk_text* t, const char* s) {
        xtce_pk_text_putn(t, s, strlen(s));
}

static inline void xtce_pk_text_putc(xtce_pk_text* t, char c) {
        xtce_pk_text_putn(t, &c, 1);
}

static inline void xtce_pk_text_int(xtce_pk_text* t, int64_t v) {
        char b[24];
        xtce_pk_text_putn(t, b,
                          (size_t)snprintf(b, sizeof b, "%lld", (long long)v));
}

static inline void xtce_pk_text_uint(xtce_pk_text* t, uint64_t v) {
        char b[24];
        xtce_pk_text_putn(
                t, b,
                (size_t)snprintf(b, sizeof b, "%llu", (unsigned long long)v));
}

/* The shortest round-trip form of `x` in the style of Python's `repr`: fixed
 * notation for exponents in [-4, 16), otherwise scientific notation with a
 * signed, at least two digit exponent.  Assumes the "C" locale. */
static inline void xtce_pk_text_double(xtce_pk_text* t, double x) {
        char sci[48], digits[24];
        int prec, exp, nd = 0;
        const char* p;
        if (isnan(x)) {
                xtce_pk_text_puts(t, "NaN");
                return;
        }
        if (isinf(x)) {
                xtce_pk_text_puts(t, x > 0 ? "INF" : "-INF");
                return;
        }
        for (prec = 0; prec < 17; ++prec) {
                snprintf(sci, sizeof sci, "%.*e", prec, x);
                if (strtod(sci, NULL) == x) break;
        }
        p = sci;
        if (*p == '-') {
                xtce_pk_text_putc(t, '-');
                ++p;
        }
        for (; *p && *p != 'e'; ++p) {
                if (*p != '.') digits[nd++] = *p;
        }
        digits[nd] = 0;
        exp = atoi(p + 1);
        if (exp >= -4 && exp < 16) {
                if (exp < 0) {
                        int i;
                        xtce_pk_text_puts(t, "0.");
                        for (i = 0; i < -exp - 1; ++i)
                                xtce_pk_text_putc(t, '0');
                        xtce_pk_text_puts(t, digits);
                } else {
                        int point = exp + 1;
                        if (nd <= point) {
                                int i;
                                xtce_pk_text_puts(t, digits);
                                for (i = nd; i < point; ++i)
                                        xtce_pk_text_putc(t, '0');
                                xtce_pk_text_puts(t, ".0");
                        } else {
                                xtce_pk_text_putn(t, digits, (size_t)point);
                                xtce_pk_text_putc(t, '.');
                                xtce_pk_text_puts(t, digits + point);
                        }
                }
        } else {
                char e[8];
                xtce_pk_text_putc(t, digits[0]);
                if (nd > 1) {
                        xtce_pk_text_putc(t, '.');
                        xtce_pk_text_puts(t, digits + 1);
                }
                xtce_pk_text_putn(t, e,
                                  (size_t)snprintf(e, sizeof e, "e%c%02d",
                                                   exp < 0 ? '-' : '+',
                                                   exp < 0 ? -exp : exp));
        }
}

static inline void xtce_pk_text_str(xtce_pk_text* t, const char* s, size_t n) {
        static const char hex[] = "0123456789abcdef";
        size_t i;
        xtce_pk_text_putc(t, '"');
        for (i = 0; i < n; ++i) {
                unsigned char c = (unsigned char)s[i];
                if (c == '"') {
                        xtce_pk_text_puts(t, "\\\"");
                } else if (c == '\\') {
                        xtce_pk_text_puts(t, "\\\\");
                } else if (c < 0x20 || c == 0x7f) {
                        char e[4] = {'\\', 'x', hex[c >> 4], hex[c & 15]};
                        xtce_pk_text_putn(t, e, 4);
                } else {
                        xtce_pk_text_putc(t, (char)c);
                }
        }
        xtce_pk_text_putc(t, '"');
}

/* Bytes as "0x" followed by lower-case hex digits. */
static inline void xtce_pk_text_bytes(xtce_pk_text* t, const uint8_t* b,
                                      size_t n) {
        static const char hex[] = "0123456789abcdef";
        size_t i;
        xtce_pk_text_puts(t, "0x");
        for (i = 0; i < n; ++i) {
                char h[2] = {hex[b[i] >> 4], hex[b[i] & 15]};
                xtce_pk_text_putn(t, h, 2);
        }
}

#ifdef __cplusplus
}
#endif

#endif /* XTCE_PACKET_H */
