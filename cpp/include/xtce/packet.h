/* Runtime for packet codecs generated from a mission's XTCE document.
 *
 * tcsxtce (in generator/) turns the containers and meta-commands of a mission
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
  static const char* const kinds[] = {"Ok",        "Truncated",     "BadPosition", "NoMatch",
                                      "Mismatch",  "OutOfRange",    "InvalidString", "TooLong",
                                      "Calibration", "NotInvertible", "Missing",     "BadSize",
                                      "BadLength", "NoSpace"};
  return code >= 0 && code <= XTCE_PK_NO_SPACE ? kinds[code] : "Unknown";
}

/* Writes the error message into `buf` like snprintf and returns its length. */
static inline int xtce_pk_error_message(const xtce_pk_error* e, char* buf, size_t cap) {
  const char* f = e->field ? e->field : "";
  switch (e->code) {
    case XTCE_PK_OK:
      return snprintf(buf, cap, "no error");
    case XTCE_PK_TRUNCATED:
      return snprintf(buf, cap, "packet too short for %s", f);
    case XTCE_PK_BAD_POSITION:
      return snprintf(buf, cap, "%s is placed before the packet start", f);
    case XTCE_PK_NO_MATCH:
      return snprintf(buf, cap, "no container derived from %s matches", f);
    case XTCE_PK_MISMATCH:
      return snprintf(buf, cap, "%s requires %s", f, e->detail ? e->detail : "");
    case XTCE_PK_OUT_OF_RANGE:
      return snprintf(buf, cap, "value of %s does not fit its encoding", f);
    case XTCE_PK_INVALID_STRING:
      return snprintf(buf, cap, "invalid string in %s", f);
    case XTCE_PK_TOO_LONG:
      return snprintf(buf, cap, "%s is too long for its encoding", f);
    case XTCE_PK_CALIBRATION:
      return snprintf(buf, cap, "%s is outside its calibration range", f);
    case XTCE_PK_NOT_INVERTIBLE:
      return snprintf(buf, cap, "the calibrator of %s cannot be inverted for encoding", f);
    case XTCE_PK_MISSING:
      return snprintf(buf, cap, "%s is required by its include condition", f);
    case XTCE_PK_BAD_SIZE:
      return snprintf(buf, cap, "invalid size or count for %s", f);
    case XTCE_PK_BAD_LENGTH:
      return snprintf(buf, cap, "%s does not match the length of the packet", f);
    case XTCE_PK_NO_SPACE:
      return snprintf(buf, cap, "output buffer too small for %s", f);
  }
  return snprintf(buf, cap, "unknown error %d", e->code);
}

static inline int xtce_pk_fail(xtce_pk_error* e, int code, const char* field, const char* detail) {
  if (e) {
    e->code = code;
    e->field = field;
    e->detail = detail;
  }
  return code;
}

/* Returns from the enclosing function if `expr` reports an error. */
#define XTCE_PK_TRY(expr)          \
  do {                             \
    int xtce_pk_rc_ = (expr);      \
    if (xtce_pk_rc_) return xtce_pk_rc_; \
  } while (0)

/* Zero initializer for any struct; `{0}` draws warnings in C++ and `{}`
 * needs C23. */
#ifdef __cplusplus
#define XTCE_PK_ZERO \
  {}
#else
#define XTCE_PK_ZERO \
  { 0 }
#endif

static inline uint64_t xtce_pk_mask(unsigned bits) {
  return bits >= 64 ? ~(uint64_t)0 : ((uint64_t)1 << bits) - 1;
}

static inline int xtce_pk_position(size_t base, int64_t offset, const char* f, xtce_pk_error* err, size_t* out) {
  int64_t p = (int64_t)base + offset;
  if (p < 0) return xtce_pk_fail(err, XTCE_PK_BAD_POSITION, f, NULL);
  *out = (size_t)p;
  return 0;
}

static inline uint64_t xtce_pk_swap_bytes(uint64_t v, unsigned bits) {
  uint64_t out = 0;
  unsigned i;
  for (i = 0; i < bits / 8; ++i) out = (out << 8) | ((v >> (8 * i)) & 0xff);
  return out;
}

/* --- reading ----------------------------------------------------------------- */

/* Reads big-endian bit fields from `len` bytes at `data`; `pos` is in bits,
 * and `end` is the furthest bit read. */
typedef struct xtce_pk_reader {
  const uint8_t* data;
  size_t len;
  size_t pos;
  size_t end;
  xtce_pk_error* err;
} xtce_pk_reader;

static inline xtce_pk_reader xtce_pk_reader_init(const uint8_t* data, size_t len, xtce_pk_error* err) {
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
static inline int xtce_pk_read_seek(xtce_pk_reader* r, size_t start, int64_t offset, const char* f) {
  XTCE_PK_TRY(xtce_pk_position(start, offset, f, r->err, &r->pos));
  xtce_pk_read_moved(r);
  return 0;
}

static inline int xtce_pk_read_skip(xtce_pk_reader* r, int64_t offset, const char* f) {
  return xtce_pk_read_seek(r, r->pos, offset, f);
}

static inline int xtce_pk_read_need(const xtce_pk_reader* r, size_t bits, const char* f) {
  if (r->pos + bits > r->len * 8) return xtce_pk_fail(r->err, XTCE_PK_TRUNCATED, f, NULL);
  return 0;
}

/* Reads an unsigned big-endian field of `bits` (1 to 64) bits. */
static inline int xtce_pk_read_bits(xtce_pk_reader* r, unsigned bits, const char* f, uint64_t* out) {
  uint64_t v = 0;
  unsigned left = bits;
  XTCE_PK_TRY(xtce_pk_read_need(r, bits, f));
  while (left > 0) {
    uint8_t byte = r->data[r->pos / 8];
    unsigned avail = 8 - (unsigned)(r->pos % 8);
    unsigned take = avail < left ? avail : left;
    v = (v << take) | ((byte >> (avail - take)) & xtce_pk_mask(take));
    r->pos += take;
    left -= take;
  }
  xtce_pk_read_moved(r);
  *out = v;
  return 0;
}

/* Like xtce_pk_read_bits, with the bytes swapped when `le` is set. */
static inline int xtce_pk_read_uint(xtce_pk_reader* r, unsigned bits, bool le, const char* f, uint64_t* out) {
  XTCE_PK_TRY(xtce_pk_read_bits(r, bits, f, out));
  if (le) *out = xtce_pk_swap_bytes(*out, bits);
  return 0;
}

/* Skips `bits` bits of padding, which must be in the packet. */
static inline int xtce_pk_read_pad(xtce_pk_reader* r, size_t bits, const char* f) {
  XTCE_PK_TRY(xtce_pk_read_need(r, bits, f));
  r->pos += bits;
  xtce_pk_read_moved(r);
  return 0;
}

/* Ends the packet where the packet length field `f`, whose value `v` was
 * just read, says: `v - adjust` bytes after the field.  Fails with
 * XTCE_PK_TRUNCATED if the data is shorter. */
static inline int xtce_pk_read_limit(xtce_pk_reader* r, uint64_t v, int64_t adjust, const char* f) {
  size_t start = (r->pos + 7) / 8;
  uint64_t after;
  if (adjust >= 0) {
    if (v < (uint64_t)adjust) return xtce_pk_fail(r->err, XTCE_PK_BAD_LENGTH, f, NULL);
    after = v - (uint64_t)adjust;
  } else {
    uint64_t a = 0 - (uint64_t)adjust;
    if (v > UINT64_MAX - a) return xtce_pk_fail(r->err, XTCE_PK_TRUNCATED, f, NULL);
    after = v + a;
  }
  if (after > r->len - start) return xtce_pk_fail(r->err, XTCE_PK_TRUNCATED, f, NULL);
  r->len = start + (size_t)after;
  return 0;
}

/* Checks that the packet's entries reach the end that the packet length
 * field `f` gave. */
static inline int xtce_pk_read_finish(const xtce_pk_reader* r, const char* f) {
  if ((r->end + 7) / 8 != r->len) return xtce_pk_fail(r->err, XTCE_PK_BAD_LENGTH, f, NULL);
  return 0;
}

/* The bytes from the current position to the end of the packet, for a field
 * that takes the rest of it.  Fails with XTCE_PK_BAD_SIZE unless they are
 * whole bytes, at most `max` of them. */
static inline int xtce_pk_read_rest(const xtce_pk_reader* r, size_t max, const char* f, size_t* out) {
  size_t bits = r->len * 8 > r->pos ? r->len * 8 - r->pos : 0;
  if (bits % 8 || bits / 8 > max) return xtce_pk_fail(r->err, XTCE_PK_BAD_SIZE, f, NULL);
  *out = bits / 8;
  return 0;
}

static inline int xtce_pk_read_bytes(xtce_pk_reader* r, size_t n, const char* f, uint8_t* out) {
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

/* --- writing ------------------------------------------------------------------- */

/* Writes big-endian bit fields into `cap` bytes at `buf`.  `len` grows (zero
 * filled) to cover the furthest bit written. */
typedef struct xtce_pk_writer {
  uint8_t* buf;
  size_t cap;
  size_t len;
  size_t pos;
  xtce_pk_error* err;
} xtce_pk_writer;

static inline xtce_pk_writer xtce_pk_writer_init(uint8_t* buf, size_t cap, xtce_pk_error* err) {
  xtce_pk_writer w;
  w.buf = buf;
  w.cap = cap;
  w.len = 0;
  w.pos = 0;
  w.err = err;
  return w;
}

static inline int xtce_pk_write_seek(xtce_pk_writer* w, size_t start, int64_t offset, const char* f) {
  return xtce_pk_position(start, offset, f, w->err, &w->pos);
}

static inline int xtce_pk_write_skip(xtce_pk_writer* w, int64_t offset, const char* f) {
  return xtce_pk_position(w->pos, offset, f, w->err, &w->pos);
}

static inline int xtce_pk_write_bits(xtce_pk_writer* w, unsigned bits, uint64_t v) {
  size_t end = (w->pos + bits + 7) / 8;
  unsigned left = bits;
  if (end > w->cap) return xtce_pk_fail(w->err, XTCE_PK_NO_SPACE, "packet", NULL);
  if (w->len < end) {
    memset(w->buf + w->len, 0, end - w->len);
    w->len = end;
  }
  while (left > 0) {
    unsigned avail = 8 - (unsigned)(w->pos % 8);
    unsigned take = avail < left ? avail : left;
    uint8_t chunk = (uint8_t)((v >> (left - take)) & xtce_pk_mask(take));
    unsigned shift = avail - take;
    uint8_t m = (uint8_t)(xtce_pk_mask(take) << shift);
    uint8_t* b = &w->buf[w->pos / 8];
    *b = (uint8_t)((*b & ~m) | (chunk << shift));
    w->pos += take;
    left -= take;
  }
  return 0;
}

static inline int xtce_pk_write_uint(xtce_pk_writer* w, unsigned bits, bool le, uint64_t v) {
  return xtce_pk_write_bits(w, bits, le ? xtce_pk_swap_bytes(v, bits) : v);
}

/* Bytes start..start + n of the buffer, extending its length (zero filled)
 * to cover them; NULL, after failing with NO_SPACE, if they don't fit. */
static inline uint8_t* xtce_pk_write_span(xtce_pk_writer* w, size_t start, size_t n) {
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
static inline int xtce_pk_write_bytes(xtce_pk_writer* w, const uint8_t* data, size_t n) {
  size_t i;
  uint8_t* out;
  if (w->pos % 8) {
    for (i = 0; i < n; ++i) XTCE_PK_TRY(xtce_pk_write_bits(w, 8, data[i]));
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
static inline int xtce_pk_patch_length(xtce_pk_writer* w, size_t at, unsigned bits, bool le, int64_t adjust,
                                        const char* f) {
  size_t after = w->len - (at + bits + 7) / 8, end = w->pos;
  uint64_t v;
  if (adjust >= 0) {
    v = (uint64_t)after + (uint64_t)adjust;
    if (v < (uint64_t)after) return xtce_pk_fail(w->err, XTCE_PK_OUT_OF_RANGE, f, NULL);
  } else {
    uint64_t a = 0 - (uint64_t)adjust;
    if ((uint64_t)after < a) return xtce_pk_fail(w->err, XTCE_PK_OUT_OF_RANGE, f, NULL);
    v = (uint64_t)after - a;
  }
  if (v > xtce_pk_mask(bits)) return xtce_pk_fail(w->err, XTCE_PK_OUT_OF_RANGE, f, NULL);
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
  if ((bits - lead) % 8) XTCE_PK_TRY(xtce_pk_write_bits(w, (unsigned)((bits - lead) % 8), 0));
  return 0;
}

/* --- integers ------------------------------------------------------------------- */

enum { XTCE_PK_UNSIGNED, XTCE_PK_TWOS_COMPLEMENT, XTCE_PK_SIGN_MAGNITUDE, XTCE_PK_ONES_COMPLEMENT };

static inline int64_t xtce_pk_to_signed(uint64_t raw, unsigned bits, int enc) {
  bool sign = (raw >> (bits - 1)) & 1;
  uint64_t low = raw & xtce_pk_mask(bits - 1);
  switch (enc) {
    case XTCE_PK_TWOS_COMPLEMENT:
      return (int64_t)(sign && bits < 64 ? (raw | ~xtce_pk_mask(bits)) : raw);
    case XTCE_PK_SIGN_MAGNITUDE:
      return sign ? -(int64_t)low : (int64_t)low;
    case XTCE_PK_ONES_COMPLEMENT:
      return sign ? -(int64_t)(~raw & xtce_pk_mask(bits - 1)) : (int64_t)low;
  }
  return (int64_t)raw;
}

static inline int xtce_pk_from_unsigned(uint64_t v, unsigned bits, const char* f, xtce_pk_error* err,
                                        uint64_t* out) {
  if (v > xtce_pk_mask(bits)) return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
  *out = v;
  return 0;
}

static inline int xtce_pk_from_signed(int64_t v, unsigned bits, int enc, const char* f, xtce_pk_error* err,
                                      uint64_t* out) {
  uint64_t limit = xtce_pk_mask(bits - 1);
  uint64_t m;
  switch (enc) {
    case XTCE_PK_UNSIGNED:
      if (v < 0) return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
      return xtce_pk_from_unsigned((uint64_t)v, bits, f, err, out);
    case XTCE_PK_TWOS_COMPLEMENT:
      if (v < -(int64_t)limit - 1 || (v > 0 && (uint64_t)v > limit)) {
        return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
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

static inline double xtce_pk_int_to_double(uint64_t raw, unsigned bits, int enc) {
  if (enc == XTCE_PK_UNSIGNED) return (double)raw;
  return (double)xtce_pk_to_signed(raw, bits, enc);
}

/* Rounds (half away from zero) an engineering value to a raw integer field. */
static inline int xtce_pk_double_to_int(double x, unsigned bits, int enc, const char* f, xtce_pk_error* err,
                                        uint64_t* out) {
  double r = round(x);
  if (!isfinite(r) || fabs(r) >= 18446744073709551616.0) return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
  if (enc == XTCE_PK_UNSIGNED) {
    if (r < 0) return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
    return xtce_pk_from_unsigned((uint64_t)r, bits, f, err, out);
  }
  if (r < -9223372036854775808.0 || r >= 9223372036854775808.0) {
    return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
  }
  return xtce_pk_from_signed((int64_t)r, bits, enc, f, err, out);
}

/* Rounds a calibrated value (half away from zero) to an integer from `lo` up
 * to but not including `lim`; fails with XTCE_PK_OUT_OF_RANGE otherwise. */
static inline int xtce_pk_round_to_int(double y, double lo, double lim, const char* f, xtce_pk_error* err,
                                       double* out) {
  double r = round(y);
  if (!(r >= lo && r < lim)) return xtce_pk_fail(err, XTCE_PK_OUT_OF_RANGE, f, NULL);
  *out = r;
  return 0;
}

/* Three-way comparisons of integers of different signedness. */
static inline int xtce_pk_cmp_u(uint64_t a, uint64_t b) { return a < b ? -1 : a > b; }
static inline int xtce_pk_cmp_i(int64_t a, int64_t b) { return a < b ? -1 : a > b; }

/* --- floats ----------------------------------------------------------------------- */

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
    /* Like Rust's `as f32`: round to nearest, ties to even.  Finite values
     * from half an ulp above FLT_MAX (0x1.ffffffp+127) up round to infinity;
     * smaller ones above FLT_MAX round to FLT_MAX.  Both are handled here
     * because converting an out-of-range double to float is undefined in C. */
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

/* --- calibrators ------------------------------------------------------------------- */

typedef struct xtce_pk_term {
  double coefficient;
  int exponent;
} xtce_pk_term;

typedef struct xtce_pk_point {
  double raw;
  double calibrated;
} xtce_pk_point;

static inline double xtce_pk_polynomial(double x, const xtce_pk_term* terms, size_t n) {
  double y = 0.0;
  size_t i;
  for (i = 0; i < n; ++i) {
    const xtce_pk_term* t = &terms[i];
    y += t->exponent == 0   ? t->coefficient
         : t->exponent == 1 ? t->coefficient * x
                            : t->coefficient * pow(x, (double)t->exponent);
  }
  return y;
}

static inline double xtce_pk_polynomial_inverse(double y, double intercept, double slope) {
  return (y - intercept) / slope;
}

static inline double xtce_pk_lerp(double x, double x0, double y0, double x1, double y1) {
  return y0 + (x - x0) * (y1 - y0) / (x1 - x0);
}

static inline int xtce_pk_spline(double x, const xtce_pk_point* p, size_t n, unsigned order, bool extrapolate,
                                 const char* f, xtce_pk_error* err, double* out) {
  size_t i;
  if (x < p[0].raw || x > p[n - 1].raw) {
    if (!extrapolate) return xtce_pk_fail(err, XTCE_PK_CALIBRATION, f, NULL);
    if (order == 0) {
      *out = x < p[0].raw ? p[0].calibrated : p[n - 1].calibrated;
    } else if (x < p[0].raw) {
      *out = xtce_pk_lerp(x, p[0].raw, p[0].calibrated, p[1].raw, p[1].calibrated);
    } else {
      *out = xtce_pk_lerp(x, p[n - 2].raw, p[n - 2].calibrated, p[n - 1].raw, p[n - 1].calibrated);
    }
    return 0;
  }
  for (i = 0; i + 1 < n; ++i) {
    if (x < p[i + 1].raw) {
      *out = order == 0 ? p[i].calibrated
                        : xtce_pk_lerp(x, p[i].raw, p[i].calibrated, p[i + 1].raw, p[i + 1].calibrated);
      return 0;
    }
  }
  *out = p[n - 1].calibrated;
  return 0;
}

static inline int xtce_pk_spline_inverse(double y, const xtce_pk_point* p, size_t n, bool extrapolate,
                                         const char* f, xtce_pk_error* err, double* out) {
  size_t i;
  bool increasing;
  for (i = 0; i + 1 < n; ++i) {
    double a = p[i].calibrated, b = p[i + 1].calibrated;
    if ((a <= y && y <= b) || (b <= y && y <= a)) {
      *out = xtce_pk_lerp(y, p[i].calibrated, p[i].raw, p[i + 1].calibrated, p[i + 1].raw);
      return 0;
    }
  }
  if (!extrapolate) return xtce_pk_fail(err, XTCE_PK_CALIBRATION, f, NULL);
  increasing = p[n - 1].calibrated > p[0].calibrated;
  if ((y < p[0].calibrated) == increasing) {
    *out = xtce_pk_lerp(y, p[0].calibrated, p[0].raw, p[1].calibrated, p[1].raw);
  } else {
    *out = xtce_pk_lerp(y, p[n - 2].calibrated, p[n - 2].raw, p[n - 1].calibrated, p[n - 1].raw);
  }
  return 0;
}

/* --- sizes from other fields ---------------------------------------------------------- */

/* A size or count that another field gives (an XTCE DynamicValue):
 * (slope * x + intercept) / unit, where x is that field's value and unit is 8
 * for a size in bits used in whole bytes.  Fails with XTCE_PK_BAD_SIZE unless
 * the result is a whole number from 0 to `max`. */
static inline int xtce_pk_dynamic_size(double x, double slope, double intercept, unsigned unit, size_t max,
                                       const char* f, xtce_pk_error* err, size_t* out) {
  double y = slope * x + intercept;
  double q = y / unit;
  if (!(y >= 0.0) || y != floor(y) || q != floor(q) || q > (double)max) {
    return xtce_pk_fail(err, XTCE_PK_BAD_SIZE, f, NULL);
  }
  *out = (size_t)q;
  return 0;
}

/* --- strings and binary --------------------------------------------------------------- */

/* Layout of a string field; exactly one of terminator and size_tag_bits is used. */
typedef struct xtce_pk_string_spec {
  bool has_fixed;
  size_t fixed_bits; /* bits the field always occupies, if has_fixed */
  size_t max_bits;   /* upper bound on the field's bits */
  bool has_terminator;
  uint8_t terminator;
  unsigned size_tag_bits;
} xtce_pk_string_spec;

/* The spec of a string whose size another field gives: exactly `bytes` bytes. */
static inline xtce_pk_string_spec xtce_pk_string_spec_sized(const xtce_pk_string_spec* spec, size_t bytes) {
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
      if (d < (k == 1 ? lo : 0x80) || d > (k == 1 ? hi : 0xbf)) return false;
    }
    i += more + 1;
  }
  return true;
}

/* Reads a string into `out`, which holds `cap` bytes plus a terminating NUL
 * that is always written; the content length goes to `*len`. */
static inline int xtce_pk_read_string(xtce_pk_reader* r, const xtce_pk_string_spec* spec, const char* f, char* out,
                                      size_t cap, size_t* len) {
  uint8_t* b = (uint8_t*)out;
  size_t start = r->pos, n = 0, i;
  uint64_t v;
  if (spec->has_fixed && spec->has_terminator) {
    n = spec->fixed_bits / 8;
    if (n > cap) return xtce_pk_fail(r->err, XTCE_PK_TOO_LONG, f, NULL);
    XTCE_PK_TRY(xtce_pk_read_bytes(r, n, f, b));
    for (i = 0; i < n; ++i) {
      if (b[i] == spec->terminator) {
        n = i;
        break;
      }
    }
  } else if (spec->has_fixed) {
    XTCE_PK_TRY(xtce_pk_read_bits(r, spec->size_tag_bits, f, &v));
    if (v > cap || v * 8 + spec->size_tag_bits > spec->fixed_bits) {
      return xtce_pk_fail(r->err, XTCE_PK_TOO_LONG, f, NULL);
    }
    n = (size_t)v;
    XTCE_PK_TRY(xtce_pk_read_bytes(r, n, f, b));
    XTCE_PK_TRY(xtce_pk_read_seek(r, start, (int64_t)spec->fixed_bits, f));
  } else if (spec->has_terminator) {
    for (;;) {
      if ((n + 1) * 8 > spec->max_bits) return xtce_pk_fail(r->err, XTCE_PK_TOO_LONG, f, NULL);
      XTCE_PK_TRY(xtce_pk_read_bits(r, 8, f, &v));
      if ((uint8_t)v == spec->terminator) break;
      if (n == cap) return xtce_pk_fail(r->err, XTCE_PK_TOO_LONG, f, NULL);
      b[n++] = (uint8_t)v;
    }
  } else {
    XTCE_PK_TRY(xtce_pk_read_bits(r, spec->size_tag_bits, f, &v));
    if (v > cap || v * 8 + spec->size_tag_bits > spec->max_bits) {
      return xtce_pk_fail(r->err, XTCE_PK_TOO_LONG, f, NULL);
    }
    n = (size_t)v;
    XTCE_PK_TRY(xtce_pk_read_bytes(r, n, f, b));
  }
  b[n] = 0;
  *len = n;
  if (!xtce_pk_valid_utf8(b, n)) return xtce_pk_fail(r->err, XTCE_PK_INVALID_STRING, f, NULL);
  return 0;
}

/* Reads a string field encoded as binary: `n` bytes, which must be UTF-8,
 * into `out`, which has room for `n + 1`; NUL-terminates them. */
static inline int xtce_pk_read_utf8(xtce_pk_reader* r, size_t n, const char* f, char* out, size_t* len) {
  XTCE_PK_TRY(xtce_pk_read_bytes(r, n, f, (uint8_t*)out));
  out[n] = 0;
  *len = n;
  if (!xtce_pk_valid_utf8((const uint8_t*)out, n)) return xtce_pk_fail(r->err, XTCE_PK_INVALID_STRING, f, NULL);
  return 0;
}

/* Writes a string field encoded as binary: the `n` bytes of UTF-8 at `s`. */
static inline int xtce_pk_write_utf8(xtce_pk_writer* w, const char* s, size_t n, const char* f) {
  if (!xtce_pk_valid_utf8((const uint8_t*)s, n)) return xtce_pk_fail(w->err, XTCE_PK_INVALID_STRING, f, NULL);
  return xtce_pk_write_bytes(w, (const uint8_t*)s, n);
}

static inline int xtce_pk_write_string(xtce_pk_writer* w, const xtce_pk_string_spec* spec, const char* s, size_t n,
                                        const char* f) {
  const uint8_t* b = (const uint8_t*)s;
  size_t tag = spec->size_tag_bits;
  if (!xtce_pk_valid_utf8(b, n)) return xtce_pk_fail(w->err, XTCE_PK_INVALID_STRING, f, NULL);
  if (spec->has_terminator) {
    size_t room = (spec->has_fixed ? spec->fixed_bits : spec->max_bits) / 8;
    size_t need = spec->has_fixed ? n : n + 1;
    if (n && memchr(b, spec->terminator, n)) return xtce_pk_fail(w->err, XTCE_PK_INVALID_STRING, f, NULL);
    if (need > room) return xtce_pk_fail(w->err, XTCE_PK_TOO_LONG, f, NULL);
    XTCE_PK_TRY(xtce_pk_write_bytes(w, b, n));
    if (n < room) XTCE_PK_TRY(xtce_pk_write_bits(w, 8, spec->terminator));
    if (spec->has_fixed && n + 1 < spec->fixed_bits / 8) {
      XTCE_PK_TRY(xtce_pk_write_zeros(w, (spec->fixed_bits / 8 - n - 1) * 8));
    }
  } else {
    size_t room = spec->has_fixed ? spec->fixed_bits : spec->max_bits;
    if (n * 8 + tag > room || n > xtce_pk_mask(spec->size_tag_bits)) {
      return xtce_pk_fail(w->err, XTCE_PK_TOO_LONG, f, NULL);
    }
    XTCE_PK_TRY(xtce_pk_write_bits(w, spec->size_tag_bits, n));
    XTCE_PK_TRY(xtce_pk_write_bytes(w, b, n));
    if (spec->has_fixed) XTCE_PK_TRY(xtce_pk_write_zeros(w, spec->fixed_bits - tag - n * 8));
  }
  return 0;
}

/* Three-way comparison of byte strings, shorter first on a common prefix. */
static inline int xtce_pk_bytes_cmp(const void* a, size_t na, const void* b, size_t nb) {
  int c = memcmp(a, b, na < nb ? na : nb);
  if (c) return c < 0 ? -1 : 1;
  return na < nb ? -1 : na > nb;
}

/* --- canonical text (identical in every runtime) ------------------------------------------ */

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

static inline void xtce_pk_text_puts(xtce_pk_text* t, const char* s) { xtce_pk_text_putn(t, s, strlen(s)); }

static inline void xtce_pk_text_putc(xtce_pk_text* t, char c) { xtce_pk_text_putn(t, &c, 1); }

static inline void xtce_pk_text_int(xtce_pk_text* t, int64_t v) {
  char b[24];
  xtce_pk_text_putn(t, b, (size_t)snprintf(b, sizeof b, "%lld", (long long)v));
}

static inline void xtce_pk_text_uint(xtce_pk_text* t, uint64_t v) {
  char b[24];
  xtce_pk_text_putn(t, b, (size_t)snprintf(b, sizeof b, "%llu", (unsigned long long)v));
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
      for (i = 0; i < -exp - 1; ++i) xtce_pk_text_putc(t, '0');
      xtce_pk_text_puts(t, digits);
    } else {
      int point = exp + 1;
      if (nd <= point) {
        int i;
        xtce_pk_text_puts(t, digits);
        for (i = nd; i < point; ++i) xtce_pk_text_putc(t, '0');
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
    xtce_pk_text_putn(t, e, (size_t)snprintf(e, sizeof e, "e%c%02d", exp < 0 ? '-' : '+', exp < 0 ? -exp : exp));
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
static inline void xtce_pk_text_bytes(xtce_pk_text* t, const uint8_t* b, size_t n) {
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
