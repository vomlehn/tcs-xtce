/* The binary time codes of CCSDS 301.0-B-4, Time Code Formats: CUC, the
 * CCSDS Unsegmented time Code, and CDS, the CCSDS Day Segmented time code.
 * Part of tcs-ccsds, with the Rust crate tcs-ccsds and the Python package
 * tcs_ccsds, which give the same results.
 *
 * A time code is an optional P-field (preamble), which says which code it is
 * and how it is laid out, then a T-field holding the time.  A time is given
 * as seconds since the code's epoch, as a double: either the CCSDS epoch
 * (1958-01-01, TAI) or one the agency, that is the mission, defines.
 * Decoding gives how many octets it read and the seconds; encoding writes
 * the octets and returns how many.  Encoding into too small an output
 * writes nothing and returns the number it needs, more than `cap`, and a
 * time the layout can't hold gives CCSDS_NONE.
 *
 * Every computation is IEEE double arithmetic that the Rust and Python
 * versions do the same way.  Encoding rounds to the nearest unit of the
 * layout, halves up, carrying into the seconds or days.
 *
 * A double holds about a quarter of a microsecond at today's times, which
 * is coarser than a CUC time of more than three fine octets or a CDS time
 * of microseconds or picoseconds.  So each code also has an exact form, in
 * integers, that holds every time its layouts can: ccsds_cuc_time, whose
 * fraction is ten octets of binary fraction, and ccsds_cds_time, in days,
 * milliseconds and picoseconds.  Its decoders and encoders are the _exact
 * ones, and the double decoders are the exact ones then a conversion.
 *
 * The header compiles as C11 and as C++, needs only the C standard library
 * and allocates nothing.
 */
#ifndef CCSDS_TIME_H
#define CCSDS_TIME_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

/* What encoding returns for a time its layout can't hold. */
#define CCSDS_NONE ((size_t)-1)

/* The epoch a time code counts from: 1958-01-01T00:00:00 TAI, or one that
 * the agency defines, such as the Unix epoch. */
typedef enum ccsds_epoch { CCSDS_EPOCH_CCSDS, CCSDS_EPOCH_AGENCY } ccsds_epoch;

/** Reads a big-endian unsigned integer.
 *
 * @param b  Its octets, most significant first.
 * @param n  How many octets it has, 0 to 8.
 * @return   Its value. */
static inline uint64_t ccsds_be_(const uint8_t* b, size_t n) {
        uint64_t v = 0;
        size_t i;
        for (i = 0; i < n; ++i) v = (v << 8) | b[i];
        return v;
}

static inline void ccsds_put_be_(uint8_t* b, size_t n, uint64_t v) {
        size_t i;
        for (i = 0; i < n; ++i) b[i] = (uint8_t)(v >> (8 * (n - 1 - i)));
}

/* --- CUC ----------------------------------------------------------------- */

/* The octets of binary fraction a CUC time can hold: ten fine octets. */
#define CCSDS_CUC_FRACTION_OCTETS 10

/* A CUC time, exactly: whole seconds since the epoch, and the fraction of a
 * second as ten octets of binary fraction, most significant first, 2^-8 for
 * the first -- a layout's fine octets, with the unused ones nought.  The
 * same number as the Rust crate's 80-bit `fraction`. */
typedef struct ccsds_cuc_time {
        uint64_t seconds;
        uint8_t fraction[CCSDS_CUC_FRACTION_OCTETS];
} ccsds_cuc_time;

/** Gives a CUC time as seconds since the epoch, as the double decoders do.
 *
 * @param t  The time.
 * @return   The seconds. */
static inline double ccsds_cuc_time_seconds(const ccsds_cuc_time* t) {
        double frac = 0.0;
        size_t i;
        /* The fraction, least significant octet first, as Rust does. */
        for (i = CCSDS_CUC_FRACTION_OCTETS; i-- > 0;)
                frac = (frac + t->fraction[i]) / 256.0;
        return (double)t->seconds + frac;
}

/* The layout of a CUC time: `coarse` octets (1 to 7) of whole seconds, most
 * significant first, then `fine` octets (0 to 10) of binary fractions of a
 * second, 2^-8 for the first. */
typedef struct ccsds_cuc {
        uint8_t coarse;
        uint8_t fine;
        ccsds_epoch epoch;
} ccsds_cuc;

/** Makes a CUC layout.
 *
 * @param c       Filled in with the layout; untouched on failure.
 * @param coarse  The octets of whole seconds, 1 to 7.
 * @param fine    The octets of fractions of a second, 0 to 10.
 * @param epoch   The epoch the time counts from.
 * @return        True, or false unless `coarse` is 1 to 7 and `fine` 0 to
 *                10. */
static inline bool ccsds_cuc_init(ccsds_cuc* c, unsigned coarse,
                                  unsigned fine, ccsds_epoch epoch) {
        if (coarse < 1 || coarse > 7 || fine > 10) return false;
        c->coarse = (uint8_t)coarse;
        c->fine = (uint8_t)fine;
        c->epoch = epoch;
        return true;
}

/* The octets of its T-field. */
static inline size_t ccsds_cuc_t_len(const ccsds_cuc* c) {
        return (size_t)c->coarse + c->fine;
}

/** Writes a CUC layout's P-field.
 *
 * @param c  The layout.
 * @param p  Filled in with the P-field; the second octet is 0 when unused.
 * @return   The P-field's length: one octet, or two when the layout has
 *           more than 4 coarse or 3 fine octets. */
static inline size_t ccsds_cuc_pfield(const ccsds_cuc* c, uint8_t p[2]) {
        unsigned id = c->epoch == CCSDS_EPOCH_CCSDS ? 1u : 2u;
        unsigned co = c->coarse < 4 ? c->coarse : 4u;
        unsigned fi = c->fine < 3 ? c->fine : 3u;
        bool more = c->coarse > 4 || c->fine > 3;
        p[0] = (uint8_t)(((unsigned)more << 7) | (id << 4) | ((co - 1) << 2) |
                         fi);
        p[1] = 0;
        if (!more) return 1;
        p[1] = (uint8_t)(((c->coarse - co) << 5) | ((c->fine - fi) << 2));
        return 2;
}

/** Reads the CUC layout that a P-field gives.
 *
 * @param data  The octets, starting with the P-field.
 * @param n     How many octets there are at `data`.
 * @param c     Filled in with the layout.
 * @return      The P-field's length, 1 or 2; 0 if it isn't a CUC P-field
 *              or `data` ends inside it. */
static inline size_t ccsds_cuc_from_pfield(const uint8_t* data, size_t n,
                                           ccsds_cuc* c) {
        unsigned first, second, coarse, fine;
        ccsds_epoch epoch;
        if (n < 1) return 0;
        first = data[0];
        switch ((first >> 4) & 7u) {
                case 1:
                        epoch = CCSDS_EPOCH_CCSDS;
                        break;
                case 2:
                        epoch = CCSDS_EPOCH_AGENCY;
                        break;
                default:
                        return 0;
        }
        coarse = ((first >> 2) & 3u) + 1;
        fine = first & 3u;
        if (!(first & 0x80u))
                return ccsds_cuc_init(c, coarse, fine, epoch) ? 1 : 0;
        if (n < 2) return 0;
        second = data[1];
        /* A further extension, or the reserved bits, aren't defined. */
        if (second & 0x83u) return 0;
        coarse += (second >> 5) & 3u;
        fine += (second >> 2) & 7u;
        return ccsds_cuc_init(c, coarse, fine, epoch) ? 2 : 0;
}

/** Decodes a CUC T-field exactly.
 *
 * @param c     The layout of the T-field.
 * @param data  The octets, starting with the T-field.
 * @param n     How many octets there are at `data`.
 * @param k     Filled in with how many octets were read.
 * @param t     Filled in with the time since the epoch.
 * @return      True, or false if the T-field is longer than `n` octets. */
static inline bool ccsds_cuc_decode_t_exact(const ccsds_cuc* c,
                                            const uint8_t* data, size_t n,
                                            size_t* k, ccsds_cuc_time* t) {
        size_t i, len = ccsds_cuc_t_len(c);
        if (n < len) return false;
        t->seconds = 0;
        for (i = 0; i < c->coarse; ++i)
                t->seconds = (t->seconds << 8) | data[i];
        memset(t->fraction, 0, sizeof t->fraction);
        memcpy(t->fraction, data + c->coarse, c->fine);
        *k = len;
        return true;
}

/** Decodes a CUC T-field.
 *
 * @param c     The layout of the T-field.
 * @param data  The octets, starting with the T-field.
 * @param n     How many octets there are at `data`.
 * @param k     Filled in with how many octets were read.
 * @param s     Filled in with the seconds since the epoch.
 * @return      True, or false if the T-field is longer than `n` octets. */
static inline bool ccsds_cuc_decode_t(const ccsds_cuc* c, const uint8_t* data,
                                      size_t n, size_t* k, double* s) {
        ccsds_cuc_time t;
        if (!ccsds_cuc_decode_t_exact(c, data, n, k, &t)) return false;
        *s = ccsds_cuc_time_seconds(&t);
        return true;
}

/** Encodes a CUC time as a T-field, exactly where the layout has the bits
 * and otherwise rounding the fraction to its fine octets, halves up,
 * carrying into the seconds.
 *
 * @param c    The layout of the T-field.
 * @param t    The time.
 * @param out  Where the T-field goes.
 * @param cap  How many octets `out` holds.
 * @return     The octets written; more than `cap`, writing nothing, if
 *             they don't fit; CCSDS_NONE if the layout can't hold `t`. */
static inline size_t ccsds_cuc_encode_t_exact(const ccsds_cuc* c,
                                              const ccsds_cuc_time* t,
                                              uint8_t* out, size_t cap) {
        uint8_t fine[CCSDS_CUC_FRACTION_OCTETS];
        uint64_t seconds = t->seconds;
        size_t i, len = ccsds_cuc_t_len(c);
        memcpy(fine, t->fraction, c->fine);
        /* Half a unit or more rounds up: the next octet's top bit. */
        if (c->fine < CCSDS_CUC_FRACTION_OCTETS &&
            (t->fraction[c->fine] & 0x80u)) {
                bool carry = true;
                for (i = c->fine; carry && i-- > 0;) {
                        fine[i] = (uint8_t)(fine[i] + 1u);
                        carry = fine[i] == 0;
                }
                if (carry) {
                        if (seconds == UINT64_MAX) return CCSDS_NONE;
                        ++seconds;
                }
        }
        if (seconds >> (8 * c->coarse)) return CCSDS_NONE;
        if (cap < len) return len;
        ccsds_put_be_(out, c->coarse, seconds);
        memcpy(out + c->coarse, fine, c->fine);
        return len;
}

/** Encodes a time as a CUC T-field.
 *
 * @param c    The layout of the T-field.
 * @param s    The seconds since the epoch.
 * @param out  Where the T-field goes.
 * @param cap  How many octets `out` holds.
 * @return     The octets written; more than `cap`, writing nothing, if
 *             they don't fit; CCSDS_NONE if the layout can't hold `s`. */
static inline size_t ccsds_cuc_encode_t(const ccsds_cuc* c, double s,
                                        uint8_t* out, size_t cap) {
        double limit = (double)((uint64_t)1 << (8 * c->coarse));
        uint64_t whole;
        uint8_t fine[10];
        size_t i, f = c->fine, len = ccsds_cuc_t_len(c);
        double frac;
        if (!(s >= 0.0 && s < limit)) return CCSDS_NONE;
        whole = (uint64_t)s;
        frac = s - (double)whole;
        if (f <= 6) {
                /* To the nearest 2^-8f, which may carry a whole second. */
                double unit = (double)((uint64_t)1 << (8 * f));
                uint64_t m = (uint64_t)(frac * unit + 0.5);
                if (m == (uint64_t)1 << (8 * f)) {
                        m = 0;
                        ++whole;
                }
                for (i = 0; i < f; ++i)
                        fine[f - 1 - i] = (uint8_t)(m >> (8 * i));
        } else {
                /* More fine octets than a double has bits: the fraction is
                 * exact. */
                double x = frac;
                for (i = 0; i < f; ++i) {
                        x *= 256.0;
                        fine[i] = (uint8_t)x;
                        x -= fine[i];
                }
        }
        if ((double)whole >= limit) return CCSDS_NONE;
        if (cap < len) return len;
        for (i = 0; i < c->coarse; ++i)
                out[c->coarse - 1 - i] = (uint8_t)(whole >> (8 * i));
        if (f) memcpy(out + c->coarse, fine, f);
        return len;
}

/** Encodes a time as a CUC time code, its P-field first.
 *
 * @param c    The layout of the time code.
 * @param s    The seconds since the epoch.
 * @param out  Where the time code goes.
 * @param cap  How many octets `out` holds.
 * @return     The octets written; more than `cap`, writing nothing, if
 *             they don't fit; CCSDS_NONE if the layout can't hold `s`. */
static inline size_t ccsds_cuc_encode(const ccsds_cuc* c, double s,
                                      uint8_t* out, size_t cap) {
        uint8_t p[2];
        size_t n = ccsds_cuc_pfield(c, p), t;
        t = ccsds_cuc_encode_t(c, s, out, 0);
        if (t == CCSDS_NONE) return t;
        if (cap < n + t) return n + t;
        memcpy(out, p, n);
        return n + ccsds_cuc_encode_t(c, s, out + n, cap - n);
}

/** Decodes a CUC time code that starts with its P-field.
 *
 * @param data  The octets, starting with the P-field.
 * @param n     How many octets there are at `data`.
 * @param k     Filled in with how many octets were read, P-field included.
 * @param s     Filled in with the seconds since the epoch the P-field
 *              names.
 * @return      True, or false if the P-field isn't a CUC one or the time
 *              code is longer than `n` octets. */
static inline bool ccsds_decode_cuc(const uint8_t* data, size_t n, size_t* k,
                                    double* s) {
        ccsds_cuc c;
        size_t p = ccsds_cuc_from_pfield(data, n, &c);
        if (!p || !ccsds_cuc_decode_t(&c, data + p, n - p, k, s)) return false;
        *k += p;
        return true;
}

/** Encodes a CUC time exactly, as a time code with its P-field first.
 *
 * @param c    The layout of the time code.
 * @param t    The time.
 * @param out  Where the time code goes.
 * @param cap  How many octets `out` holds.
 * @return     The octets written; more than `cap`, writing nothing, if
 *             they don't fit; CCSDS_NONE if the layout can't hold `t`. */
static inline size_t ccsds_cuc_encode_exact(const ccsds_cuc* c,
                                            const ccsds_cuc_time* t,
                                            uint8_t* out, size_t cap) {
        uint8_t p[2];
        size_t np = ccsds_cuc_pfield(c, p);
        size_t len = ccsds_cuc_encode_t_exact(c, t, out, 0);
        if (len == CCSDS_NONE) return len;
        if (cap < np + len) return np + len;
        memcpy(out, p, np);
        return np + ccsds_cuc_encode_t_exact(c, t, out + np, cap - np);
}

/** Decodes a CUC time code that starts with its P-field, exactly.
 *
 * @param data  The octets, starting with the P-field.
 * @param n     How many octets there are at `data`.
 * @param k     Filled in with how many octets were read, P-field included.
 * @param t     Filled in with the time since the epoch the P-field names.
 * @return      True, or false if the P-field isn't a CUC one or the time
 *              code is longer than `n` octets. */
static inline bool ccsds_decode_cuc_exact(const uint8_t* data, size_t n,
                                          size_t* k, ccsds_cuc_time* t) {
        ccsds_cuc c;
        size_t p = ccsds_cuc_from_pfield(data, n, &c);
        if (!p || !ccsds_cuc_decode_t_exact(&c, data + p, n - p, k, t))
                return false;
        *k += p;
        return true;
}

/* --- CDS ----------------------------------------------------------------- */

/* The resolution of a CDS time below a millisecond: none, 16 bits of
 * microseconds or 32 bits of picoseconds of the millisecond. */
typedef enum ccsds_sub_ms {
        CCSDS_SUB_NONE,
        CCSDS_SUB_MICRO,
        CCSDS_SUB_PICO
} ccsds_sub_ms;

/* The layout of a CDS time: days since the epoch, in 16 bits or (`day24`)
 * 24, then milliseconds of the day in 32 bits, then `sub`. */
typedef struct ccsds_cds {
        bool day24;
        ccsds_sub_ms sub;
        ccsds_epoch epoch;
} ccsds_cds;

/* A CDS time, exactly: days since the epoch, milliseconds of the day (less
 * than 86 401 000, a day with a leap second having a thousand more), and
 * picoseconds of the millisecond (less than 10^9). */
typedef struct ccsds_cds_time {
        uint32_t day;
        uint32_t ms;
        uint32_t picos;
} ccsds_cds_time;

/** Gives a CDS time as seconds since the epoch, as a double: its
 * picoseconds divided by 10^12, as the double decoder of a picosecond
 * layout gives.
 *
 * @param t  The time.
 * @return   The seconds. */
static inline double ccsds_cds_time_seconds(const ccsds_cds_time* t) {
        return (double)t->day * 86400.0 + (double)t->ms / 1000.0 +
               (double)t->picos / 1e12;
}

/** Gives the length of a CDS layout's T-field.
 *
 * @param c  The layout.
 * @return   The octets of its T-field. */
static inline size_t ccsds_cds_t_len(const ccsds_cds* c) {
        return (size_t)(c->day24 ? 3 : 2) + 4 +
               (c->sub == CCSDS_SUB_MICRO  ? 2
                : c->sub == CCSDS_SUB_PICO ? 4
                                           : 0);
}

/* Its one-octet P-field. */
static inline uint8_t ccsds_cds_pfield(const ccsds_cds* c) {
        return (uint8_t)((4u << 4) |
                         ((unsigned)(c->epoch == CCSDS_EPOCH_AGENCY) << 3) |
                         ((unsigned)c->day24 << 2) | (unsigned)c->sub);
}

/** Reads the CDS layout that a P-field gives.
 *
 * @param data  The octets, starting with the P-field.
 * @param n     How many octets there are at `data`.
 * @param c     Filled in with the layout.
 * @return      True, or false if there are no octets or the first isn't a
 *              CDS P-field. */
static inline bool ccsds_cds_from_pfield(const uint8_t* data, size_t n,
                                         ccsds_cds* c) {
        unsigned p;
        if (n < 1) return false;
        p = data[0];
        if ((p & 0x80u) || ((p >> 4) & 7u) != 4 || (p & 3u) == 3)
                return false;
        c->day24 = (p & 4u) != 0;
        c->sub = (ccsds_sub_ms)(p & 3u);
        c->epoch = (p & 8u) ? CCSDS_EPOCH_AGENCY : CCSDS_EPOCH_CCSDS;
        return true;
}

/** Decodes a CDS T-field exactly.
 *
 * @param c     The layout of the T-field.
 * @param data  The octets, starting with the T-field.
 * @param n     How many octets there are at `data`.
 * @param k     Filled in with how many octets were read.
 * @param t     Filled in with the time since the epoch.
 * @return      True, or false if the T-field is longer than `n` octets or
 *              a field is beyond its range (milliseconds of a day count a
 *              leap second's too). */
static inline bool ccsds_cds_decode_t_exact(const ccsds_cds* c,
                                            const uint8_t* data, size_t n,
                                            size_t* k, ccsds_cds_time* t) {
        size_t d = c->day24 ? 3 : 2, len = ccsds_cds_t_len(c);
        uint64_t sub = 0;
        if (n < len) return false;
        t->day = (uint32_t)ccsds_be_(data, d);
        t->ms = (uint32_t)ccsds_be_(data + d, 4);
        if (t->ms >= 86401000u) return false;
        if (c->sub == CCSDS_SUB_MICRO) {
                sub = ccsds_be_(data + d + 4, 2);
                if (sub >= 1000u) return false;
                sub *= 1000000u;
        } else if (c->sub == CCSDS_SUB_PICO) {
                sub = ccsds_be_(data + d + 4, 4);
                if (sub >= 1000000000u) return false;
        }
        t->picos = (uint32_t)sub;
        *k = len;
        return true;
}

/** Decodes a CDS T-field.
 *
 * @param c     The layout of the T-field.
 * @param data  The octets, starting with the T-field.
 * @param n     How many octets there are at `data`.
 * @param k     Filled in with how many octets were read.
 * @param s     Filled in with the seconds since the epoch.
 * @return      True, or false if the T-field is longer than `n` octets or
 *              a field is beyond its range (milliseconds of a day count a
 *              leap second's too). */
static inline bool ccsds_cds_decode_t(const ccsds_cds* c, const uint8_t* data,
                                      size_t n, size_t* k, double* s) {
        ccsds_cds_time t;
        double whole;
        if (!ccsds_cds_decode_t_exact(c, data, n, k, &t)) return false;
        whole = (double)t.day * 86400.0 + (double)t.ms / 1000.0;
        /* In the layout's own unit, so microseconds are divided as such. */
        if (c->sub == CCSDS_SUB_MICRO)
                *s = whole + (double)(t.picos / 1000000u) / 1e6;
        else if (c->sub == CCSDS_SUB_PICO)
                *s = ccsds_cds_time_seconds(&t);
        else
                *s = whole;
        return true;
}

/** Encodes a CDS time as a T-field, exactly where the layout has the
 * resolution and otherwise rounding to its unit, halves up, carrying into
 * the milliseconds and the days.  A time in a leap second carries into the
 * next day only past the leap second.
 *
 * @param c    The layout of the T-field.
 * @param t    The time.
 * @param out  Where the T-field goes.
 * @param cap  How many octets `out` holds.
 * @return     The octets written; more than `cap`, writing nothing, if
 *             they don't fit; CCSDS_NONE if a field of `t` is beyond its
 *             range or the layout can't hold it. */
static inline size_t ccsds_cds_encode_t_exact(const ccsds_cds* c,
                                              const ccsds_cds_time* t,
                                              uint8_t* out, size_t cap) {
        uint32_t days = (uint32_t)1 << (c->day24 ? 24 : 16);
        uint32_t unit = c->sub == CCSDS_SUB_MICRO  ? 1000000u
                        : c->sub == CCSDS_SUB_PICO ? 1u
                                                   : 1000000000u;
        uint32_t day = t->day, ms = t->ms, sub;
        size_t d = c->day24 ? 3 : 2, len = ccsds_cds_t_len(c);
        if (t->ms >= 86401000u || t->picos >= 1000000000u) return CCSDS_NONE;
        sub = (t->picos + unit / 2) / unit;
        if ((uint64_t)sub * unit == 1000000000u) {
                /* The day ends a second later when this millisecond was in
                 * its leap second. */
                uint32_t end = t->ms >= 86400000u ? 86401000u : 86400000u;
                sub = 0;
                if (++ms == end) {
                        ms = 0;
                        if (day == UINT32_MAX) return CCSDS_NONE;
                        ++day;
                }
        }
        if (day >= days) return CCSDS_NONE;
        if (cap < len) return len;
        ccsds_put_be_(out, d, day);
        ccsds_put_be_(out + d, 4, ms);
        if (c->sub == CCSDS_SUB_MICRO)
                ccsds_put_be_(out + d + 4, 2, sub);
        else if (c->sub == CCSDS_SUB_PICO)
                ccsds_put_be_(out + d + 4, 4, sub);
        return len;
}

/** Encodes a time as a CDS T-field.
 *
 * @param c    The layout of the T-field.
 * @param s    The seconds since the epoch.
 * @param out  Where the T-field goes.
 * @param cap  How many octets `out` holds.
 * @return     The octets written; more than `cap`, writing nothing, if
 *             they don't fit; CCSDS_NONE if the layout can't hold `s`. */
static inline size_t ccsds_cds_encode_t(const ccsds_cds* c, double s,
                                        uint8_t* out, size_t cap) {
        uint64_t days = (uint64_t)1 << (c->day24 ? 24 : 16);
        uint64_t per_ms = c->sub == CCSDS_SUB_MICRO  ? 1000u
                          : c->sub == CCSDS_SUB_PICO ? 1000000000u
                                                     : 1u;
        uint64_t per_day = 86400000u * per_ms, day, m;
        size_t d = c->day24 ? 3 : 2, len = ccsds_cds_t_len(c);
        double rem;
        if (!(s >= 0.0 && s < (double)days * 86400.0)) return CCSDS_NONE;
        /* To the nearest unit, which may carry a whole day. */
        day = (uint64_t)(s / 86400.0);
        rem = s - (double)day * 86400.0;
        m = (uint64_t)(rem * (double)(1000u * per_ms) + 0.5);
        if (m >= per_day) {
                m -= per_day;
                ++day;
        }
        if (day >= days) return CCSDS_NONE;
        if (cap < len) return len;
        ccsds_put_be_(out, d, day);
        ccsds_put_be_(out + d, 4, m / per_ms);
        if (c->sub == CCSDS_SUB_MICRO)
                ccsds_put_be_(out + d + 4, 2, m % per_ms);
        else if (c->sub == CCSDS_SUB_PICO)
                ccsds_put_be_(out + d + 4, 4, m % per_ms);
        return len;
}

/** Encodes a time as a CDS time code, its P-field first.
 *
 * @param c    The layout of the time code.
 * @param s    The seconds since the epoch.
 * @param out  Where the time code goes.
 * @param cap  How many octets `out` holds.
 * @return     The octets written; more than `cap`, writing nothing, if
 *             they don't fit; CCSDS_NONE if the layout can't hold `s`. */
static inline size_t ccsds_cds_encode(const ccsds_cds* c, double s,
                                      uint8_t* out, size_t cap) {
        size_t t = ccsds_cds_encode_t(c, s, out, 0);
        if (t == CCSDS_NONE) return t;
        if (cap < 1 + t) return 1 + t;
        out[0] = ccsds_cds_pfield(c);
        return 1 + ccsds_cds_encode_t(c, s, out + 1, cap - 1);
}

/** Decodes a CDS time code that starts with its P-field.
 *
 * @param data  The octets, starting with the P-field.
 * @param n     How many octets there are at `data`.
 * @param k     Filled in with how many octets were read, P-field included.
 * @param s     Filled in with the seconds since the epoch the P-field
 *              names.
 * @return      True, or false if the P-field isn't a CDS one, the time
 *              code is longer than `n` octets or a field is out of range. */
static inline bool ccsds_decode_cds(const uint8_t* data, size_t n, size_t* k,
                                    double* s) {
        ccsds_cds c;
        if (!ccsds_cds_from_pfield(data, n, &c) ||
            !ccsds_cds_decode_t(&c, data + 1, n - 1, k, s))
                return false;
        *k += 1;
        return true;
}

/** Encodes a CDS time exactly, as a time code with its P-field first.
 *
 * @param c    The layout of the time code.
 * @param t    The time.
 * @param out  Where the time code goes.
 * @param cap  How many octets `out` holds.
 * @return     The octets written; more than `cap`, writing nothing, if
 *             they don't fit; CCSDS_NONE if the layout can't hold `t`. */
static inline size_t ccsds_cds_encode_exact(const ccsds_cds* c,
                                            const ccsds_cds_time* t,
                                            uint8_t* out, size_t cap) {
        size_t len = ccsds_cds_encode_t_exact(c, t, out, 0);
        if (len == CCSDS_NONE) return len;
        if (cap < 1 + len) return 1 + len;
        out[0] = ccsds_cds_pfield(c);
        return 1 + ccsds_cds_encode_t_exact(c, t, out + 1, cap - 1);
}

/** Decodes a CDS time code that starts with its P-field, exactly.
 *
 * @param data  The octets, starting with the P-field.
 * @param n     How many octets there are at `data`.
 * @param k     Filled in with how many octets were read, P-field included.
 * @param t     Filled in with the time since the epoch the P-field names.
 * @return      True, or false if the P-field isn't a CDS one, the time
 *              code is longer than `n` octets or a field is out of range. */
static inline bool ccsds_decode_cds_exact(const uint8_t* data, size_t n,
                                          size_t* k, ccsds_cds_time* t) {
        ccsds_cds c;
        if (!ccsds_cds_from_pfield(data, n, &c) ||
            !ccsds_cds_decode_t_exact(&c, data + 1, n - 1, k, t))
                return false;
        *k += 1;
        return true;
}

#ifdef __cplusplus
}
#endif

#endif /* CCSDS_TIME_H */
