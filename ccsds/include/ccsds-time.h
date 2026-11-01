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
 *
 * Warning: this is very early software and is subject to rapid change.
 * Until 1.0, any change to its API changes the minor version number (0.1 to
 * 0.2), so a release that changes only the patch number never breaks code
 * built against it.  Feedback is welcome, even at this stage: open an issue
 * at https://github.com/vomlehn/tcs-xtce/issues.
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

/* --- Calendar: CCS and the ASCII codes ---------------------------------- */

/* CCS, the CCSDS Calendar Segmented time code, and the ASCII time codes A
 * (YYYY-MM-DDThh:mm:ss.d...dZ) and B (YYYY-DDDThh:mm:ss.d...dZ) give a time
 * as a date and a time of day, usually UTC, held exactly as a
 * ccsds_calendar_time.  A CCS T-field is BCD digits: the year in two
 * octets, the month and day in one each or the day of the year in two, the
 * hour, minute and second in one each, then 0 to 6 octets of two decimal
 * digits of a second.  An ASCII code's fraction (here at most twelve
 * digits) and its Z are optional.  What is decoded is checked: digits, a
 * Gregorian date from year 1 to 9999, and a second of 60 only at 23:59.
 * Encoding rounds the picoseconds to the layout's unit, halves up, carrying
 * through the calendar; a carry out of second 59 goes to the next minute. */

/* Picoseconds in a second. */
#define CCSDS_PICOS UINT64_C(1000000000000)

/* A date and a time of day, exactly: year 1 to 9999, month 1 to 12, day of
 * the month, hour 0 to 23, minute 0 to 59, second 0 to 59 or 60 at 23:59 in
 * a leap second, and picoseconds of the second, less than 10^12. */
typedef struct ccsds_calendar_time {
        uint16_t year;
        uint8_t month;
        uint8_t day;
        uint8_t hour;
        uint8_t minute;
        uint8_t second;
        uint64_t picos;
} ccsds_calendar_time;

/** Says whether a year of the Gregorian calendar is a leap year.
 *
 * @param year  The year.
 * @return      Whether February has 29 days in it. */
static inline bool ccsds_is_leap_year(unsigned year) {
        return (year % 4 == 0 && year % 100 != 0) || year % 400 == 0;
}

/** Gives the days of a month.
 *
 * @param year   The year.
 * @param month  The month, 1 to 12.
 * @return       Its days; 0 for a month that is not 1 to 12. */
static inline unsigned ccsds_days_in_month(unsigned year, unsigned month) {
        static const unsigned char days[12] = {31, 28, 31, 30, 31, 30,
                                               31, 31, 30, 31, 30, 31};
        if (month < 1 || month > 12) return 0;
        return days[month - 1] + (month == 2 && ccsds_is_leap_year(year));
}

/** Counts the days from 1970-01-01 to a date, as the Rust crate does.
 *
 * @param year   The year.
 * @param month  The month, 1 to 12.
 * @param day    The day of the month.
 * @return       The days, negative before 1970. */
static inline int64_t ccsds_days_from_civil_(unsigned year, unsigned month,
                                             unsigned day) {
        int64_t y = (int64_t)year - (month <= 2);
        int64_t era = (y >= 0 ? y : y - 399) / 400;
        int64_t yoe = y - era * 400;
        int64_t m = (int64_t)month;
        int64_t doy = (153 * (m + (m > 2 ? -3 : 9)) + 2) / 5 + day - 1;
        int64_t doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        return era * 146097 + doe - 719468;
}

/** Gives the date a count of days from 1970-01-01 falls on.
 *
 * @param days  The days, negative before 1970.
 * @param t     Filled in with its year, month and day.
 * @return      True, or false outside years 1 to 9999. */
static inline bool ccsds_civil_from_days_(int64_t days,
                                          ccsds_calendar_time* t) {
        int64_t z = days + 719468;
        int64_t era = (z >= 0 ? z : z - 146096) / 146097;
        int64_t doe = z - era * 146097;
        int64_t yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
        int64_t doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        int64_t mp = (5 * doy + 2) / 153;
        int64_t day = doy - (153 * mp + 2) / 5 + 1;
        int64_t month = mp < 10 ? mp + 3 : mp - 9;
        int64_t year = yoe + era * 400 + (month <= 2);
        if (year < 1 || year > 9999) return false;
        t->year = (uint16_t)year;
        t->month = (uint8_t)month;
        t->day = (uint8_t)day;
        return true;
}

/** Says whether every field of a calendar time is in its range.
 *
 * @param t  The time.
 * @return   Whether the calendar codes can hold it. */
static inline bool ccsds_calendar_valid(const ccsds_calendar_time* t) {
        return t->year >= 1 && t->year <= 9999 && t->month >= 1 &&
               t->month <= 12 && t->day >= 1 &&
               t->day <= ccsds_days_in_month(t->year, t->month) &&
               t->hour <= 23 && t->minute <= 59 &&
               (t->second <= 59 ||
                (t->second == 60 && t->hour == 23 && t->minute == 59)) &&
               t->picos < CCSDS_PICOS;
}

/** Gives a calendar time's day of the year.
 *
 * @param t  The time.
 * @return   1 for the first of January, up to 366. */
static inline unsigned ccsds_day_of_year(const ccsds_calendar_time* t) {
        return (unsigned)(ccsds_days_from_civil_(t->year, t->month, t->day) -
                          ccsds_days_from_civil_(t->year, 1, 1) + 1);
}

/** Makes midnight at the start of a day of the year.
 *
 * @param t     Filled in with the time; untouched on failure.
 * @param year  The year, 1 to 9999.
 * @param doy   The day of the year, 1 to 365, or 366 in a leap year.
 * @return      True, or false for a year or day out of range. */
static inline bool ccsds_from_day_of_year(ccsds_calendar_time* t,
                                          unsigned year, unsigned doy) {
        ccsds_calendar_time r;
        unsigned days = ccsds_is_leap_year(year) ? 366 : 365;
        if (year < 1 || year > 9999 || doy < 1 || doy > days) return false;
        memset(&r, 0, sizeof r);
        if (!ccsds_civil_from_days_(
                    ccsds_days_from_civil_(year, 1, 1) + doy - 1, &r))
                return false;
        *t = r;
        return true;
}

/** Rounds a calendar time's picoseconds to a unit, halves up, carrying
 * into the second and through the calendar.  A carry out of second 60
 * ends the day; out of second 59 it goes to the next minute.
 *
 * @param t     The time, rounded in place; untouched on failure.
 * @param unit  The unit, in picoseconds: a power of ten up to 10^12.
 * @return      True, or false if the time is not valid or the carry runs
 *              past year 9999. */
static inline bool ccsds_calendar_round(ccsds_calendar_time* t,
                                        uint64_t unit) {
        ccsds_calendar_time r;
        uint64_t n;
        if (!ccsds_calendar_valid(t)) return false;
        r = *t;
        n = (r.picos + unit / 2) / unit;
        if (n * unit < CCSDS_PICOS) {
                r.picos = n * unit;
                *t = r;
                return true;
        }
        r.picos = 0;
        if (r.second < 59) {
                ++r.second;
        } else if ((r.second = 0, r.minute < 59)) {
                ++r.minute;
        } else if ((r.minute = 0, r.hour < 23)) {
                ++r.hour;
        } else {
                r.hour = 0;
                if (!ccsds_civil_from_days_(
                            ccsds_days_from_civil_(r.year, r.month, r.day) +
                                    1,
                            &r))
                        return false;
        }
        *t = r;
        return true;
}

/** Reads one octet of two BCD digits.
 *
 * @param b  The octet.
 * @return   Its value, or -1 if a nibble is not a digit. */
static inline int ccsds_bcd_(uint8_t b) {
        if ((b >> 4) > 9 || (b & 15) > 9) return -1;
        return (b >> 4) * 10 + (b & 15);
}

/* The layout of a CCS time: the date as a month and day or (`day_of_year`)
 * a day of the year, then `sub` octets (0 to 6) of two decimal digits of a
 * second. */
typedef struct ccsds_ccs {
        bool day_of_year;
        uint8_t sub;
} ccsds_ccs;

/** Gives the length of a CCS layout's T-field.
 *
 * @param c  The layout.
 * @return   The octets of its T-field: 7 to 13. */
static inline size_t ccsds_ccs_t_len(const ccsds_ccs* c) {
        return 7 + (size_t)c->sub;
}

/** Gives the picoseconds of a CCS layout's last digit.
 *
 * @param c  The layout.
 * @return   10^12 / 100^sub. */
static inline uint64_t ccsds_ccs_unit_(const ccsds_ccs* c) {
        uint64_t unit = CCSDS_PICOS;
        unsigned i;
        for (i = 0; i < c->sub; ++i) unit /= 100;
        return unit;
}

/** Gives a CCS layout's P-field.
 *
 * @param c  The layout.
 * @return   Its one-octet P-field. */
static inline uint8_t ccsds_ccs_pfield(const ccsds_ccs* c) {
        return (uint8_t)((5u << 4) | ((unsigned)c->day_of_year << 3) | c->sub);
}

/** Reads a CCS P-field.
 *
 * @param data  The octets, starting with the P-field.
 * @param n     How many octets there are at `data`.
 * @param c     Filled in with the layout.
 * @return      True, or false if there are none or it isn't a CCS P-field
 *              (an extension, or the reserved resolution 7). */
static inline bool ccsds_ccs_from_pfield(const uint8_t* data, size_t n,
                                         ccsds_ccs* c) {
        if (!n || (data[0] & 0x80u) || ((data[0] >> 4) & 7u) != 5u ||
            (data[0] & 7u) == 7u)
                return false;
        c->day_of_year = (data[0] & 8u) != 0;
        c->sub = (uint8_t)(data[0] & 7u);
        return true;
}

/** Decodes a CCS T-field.
 *
 * @param c     The layout of the T-field.
 * @param data  The octets, starting with the T-field.
 * @param n     How many octets there are at `data`.
 * @param k     Filled in with how many octets were read.
 * @param t     Filled in with the time.
 * @return      True, or false if the T-field is longer than `n` octets, an
 *              octet is not BCD digits, or the time is not valid. */
static inline bool ccsds_ccs_decode_t(const ccsds_ccs* c, const uint8_t* data,
                                      size_t n, size_t* k,
                                      ccsds_calendar_time* t) {
        ccsds_calendar_time r;
        int hi = ccsds_bcd_(data[0]), lo, v[3];
        uint64_t picos = 0;
        size_t i, len = ccsds_ccs_t_len(c);
        if (n < len) return false;
        lo = ccsds_bcd_(data[1]);
        if (hi < 0 || lo < 0) return false;
        memset(&r, 0, sizeof r);
        if (c->day_of_year) {
                int d = ccsds_bcd_(data[3]);
                if ((data[2] >> 4) || (data[2] & 15) > 9 || d < 0 ||
                    !ccsds_from_day_of_year(&r, (unsigned)(hi * 100 + lo),
                                            (data[2] & 15u) * 100u +
                                                    (unsigned)d))
                        return false;
        } else {
                int m = ccsds_bcd_(data[2]), d = ccsds_bcd_(data[3]);
                if (m < 0 || d < 0) return false;
                r.year = (uint16_t)(hi * 100 + lo);
                r.month = (uint8_t)m;
                r.day = (uint8_t)d;
        }
        for (i = 0; i < 3; ++i)
                if ((v[i] = ccsds_bcd_(data[4 + i])) < 0) return false;
        r.hour = (uint8_t)v[0];
        r.minute = (uint8_t)v[1];
        r.second = (uint8_t)v[2];
        for (i = 7; i < len; ++i) {
                int b = ccsds_bcd_(data[i]);
                if (b < 0) return false;
                picos = picos * 100 + (uint64_t)b;
        }
        r.picos = picos * ccsds_ccs_unit_(c);
        if (!ccsds_calendar_valid(&r)) return false;
        *t = r;
        *k = len;
        return true;
}

/** Encodes a calendar time as a CCS T-field, rounding the picoseconds to
 * the layout's unit, halves up, carrying through the calendar.
 *
 * @param c    The layout of the T-field.
 * @param t    The time.
 * @param out  Where the T-field goes.
 * @param cap  How many octets `out` holds.
 * @return     The octets written; more than `cap`, writing nothing, if
 *             they don't fit; CCSDS_NONE if the time is not valid or
 *             rounds past year 9999. */
static inline size_t ccsds_ccs_encode_t(const ccsds_ccs* c,
                                        const ccsds_calendar_time* t,
                                        uint8_t* out, size_t cap) {
        ccsds_calendar_time r = *t;
        uint64_t unit = ccsds_ccs_unit_(c), n;
        size_t i, len = ccsds_ccs_t_len(c);
#define CCSDS_BCD_(v) ((uint8_t)((((v) / 10u) << 4) | ((v) % 10u)))
        if (!ccsds_calendar_round(&r, unit)) return CCSDS_NONE;
        if (cap < len) return len;
        out[0] = CCSDS_BCD_(r.year / 100u);
        out[1] = CCSDS_BCD_(r.year % 100u);
        if (c->day_of_year) {
                unsigned doy = ccsds_day_of_year(&r);
                out[2] = (uint8_t)(doy / 100u);
                out[3] = CCSDS_BCD_(doy % 100u);
        } else {
                out[2] = CCSDS_BCD_(r.month);
                out[3] = CCSDS_BCD_(r.day);
        }
        out[4] = CCSDS_BCD_(r.hour);
        out[5] = CCSDS_BCD_(r.minute);
        out[6] = CCSDS_BCD_(r.second);
        n = r.picos / unit;
        for (i = len; i-- > 7;) {
                out[i] = CCSDS_BCD_((unsigned)(n % 100u));
                n /= 100u;
        }
#undef CCSDS_BCD_
        return len;
}

/** Encodes a calendar time as a CCS time code, its P-field first.
 *
 * @param c    The layout of the time code.
 * @param t    The time.
 * @param out  Where the time code goes.
 * @param cap  How many octets `out` holds.
 * @return     The octets written; more than `cap`, writing nothing, if
 *             they don't fit; CCSDS_NONE if the time is not valid or
 *             rounds past year 9999. */
static inline size_t ccsds_ccs_encode(const ccsds_ccs* c,
                                      const ccsds_calendar_time* t,
                                      uint8_t* out, size_t cap) {
        size_t len = ccsds_ccs_encode_t(c, t, out, 0);
        if (len == CCSDS_NONE) return len;
        if (cap < 1 + len) return 1 + len;
        out[0] = ccsds_ccs_pfield(c);
        return 1 + ccsds_ccs_encode_t(c, t, out + 1, cap - 1);
}

/** Decodes a CCS time code that starts with its P-field.
 *
 * @param data  The octets, starting with the P-field.
 * @param n     How many octets there are at `data`.
 * @param k     Filled in with how many octets were read, P-field included.
 * @param t     Filled in with the time.
 * @return      True, or false if the P-field isn't a CCS one or the
 *              T-field can't be decoded. */
static inline bool ccsds_decode_ccs(const uint8_t* data, size_t n, size_t* k,
                                    ccsds_calendar_time* t) {
        ccsds_ccs c;
        if (!ccsds_ccs_from_pfield(data, n, &c) ||
            !ccsds_ccs_decode_t(&c, data + 1, n - 1, k, t))
                return false;
        *k += 1;
        return true;
}

/* Which ASCII time code: A, with a month and day, or B, with a day of the
 * year. */
typedef enum ccsds_ascii_form { CCSDS_ASCII_A, CCSDS_ASCII_B } ccsds_ascii_form;

/* The most digits of a fraction of a second the ASCII codes hold here. */
#define CCSDS_ASCII_MAX_DIGITS 12

/** Reads a fixed number of decimal digits.
 *
 * @param s  The characters.
 * @param n  How many digits.
 * @param v  Filled in with their value.
 * @return   True, or false if one is not a digit. */
static inline bool ccsds_digits_(const char* s, size_t n, uint64_t* v) {
        size_t i;
        *v = 0;
        for (i = 0; i < n; ++i) {
                if (s[i] < '0' || s[i] > '9') return false;
                *v = *v * 10 + (uint64_t)(s[i] - '0');
        }
        return true;
}

/** Parses an ASCII time code, A or B.
 *
 * @param s     The characters, starting with the time code; any after it
 *              are left.
 * @param n     How many characters there are at `s`.
 * @param k     Filled in with how many characters were read.
 * @param form  Filled in with which code it is.
 * @param t     Filled in with the time.
 * @return      True, or false if `s` does not start with one, its fraction
 *              has more than CCSDS_ASCII_MAX_DIGITS digits, or the time is
 *              not valid. */
static inline bool ccsds_parse_ascii(const char* s, size_t n, size_t* k,
                                     ccsds_ascii_form* form,
                                     ccsds_calendar_time* t) {
        ccsds_calendar_time r;
        uint64_t y, a, b, h, m, sec;
        size_t i;
        ccsds_ascii_form f;
        memset(&r, 0, sizeof r);
        if (n < 8 || s[4] != '-' || !ccsds_digits_(s, 4, &y)) return false;
        if (s[7] == '-') {
                if (n < 10 || !ccsds_digits_(s + 5, 2, &a) ||
                    !ccsds_digits_(s + 8, 2, &b))
                        return false;
                r.year = (uint16_t)y;
                r.month = (uint8_t)a;
                r.day = (uint8_t)b;
                f = CCSDS_ASCII_A;
                i = 10;
        } else {
                if (!ccsds_digits_(s + 5, 3, &a) ||
                    !ccsds_from_day_of_year(&r, (unsigned)y, (unsigned)a))
                        return false;
                f = CCSDS_ASCII_B;
                i = 8;
        }
        if (n < i + 9 || s[i] != 'T' || s[i + 3] != ':' || s[i + 6] != ':' ||
            !ccsds_digits_(s + i + 1, 2, &h) ||
            !ccsds_digits_(s + i + 4, 2, &m) ||
            !ccsds_digits_(s + i + 7, 2, &sec))
                return false;
        r.hour = (uint8_t)h;
        r.minute = (uint8_t)m;
        r.second = (uint8_t)sec;
        i += 9;
        if (i < n && s[i] == '.') {
                size_t d = 0, j;
                uint64_t frac, scale = 1;
                while (i + 1 + d < n && s[i + 1 + d] >= '0' &&
                       s[i + 1 + d] <= '9')
                        ++d;
                if (d == 0 || d > CCSDS_ASCII_MAX_DIGITS) return false;
                ccsds_digits_(s + i + 1, d, &frac);
                for (j = d; j < CCSDS_ASCII_MAX_DIGITS; ++j) scale *= 10;
                r.picos = frac * scale;
                i += 1 + d;
        }
        if (i < n && s[i] == 'Z') ++i;
        if (!ccsds_calendar_valid(&r)) return false;
        *t = r;
        *k = i;
        *form = f;
        return true;
}

/** Writes a number in a fixed number of decimal digits.
 *
 * @param out  Where the digits go.
 * @param n    How many digits.
 * @param v    The number. */
static inline void ccsds_put_digits_(char* out, size_t n, uint64_t v) {
        while (n-- > 0) {
                out[n] = (char)('0' + v % 10);
                v /= 10;
        }
}

/** Formats a calendar time as an ASCII time code, rounding the picoseconds
 * to the digits asked for, halves up, carrying through the calendar.  No
 * terminating nul is written.
 *
 * @param t       The time.
 * @param form    Code A or code B.
 * @param digits  The digits of a fraction of a second, 0 to
 *                CCSDS_ASCII_MAX_DIGITS; with none, no '.' is written.
 * @param z       Whether to end it with 'Z'.
 * @param out     Where the characters go.
 * @param cap     How many characters `out` holds.
 * @return        The characters written; more than `cap`, writing nothing,
 *                if they don't fit; CCSDS_NONE if `digits` is too many,
 *                the time is not valid, or it rounds past year 9999. */
static inline size_t ccsds_format_ascii(const ccsds_calendar_time* t,
                                        ccsds_ascii_form form,
                                        unsigned digits, bool z, char* out,
                                        size_t cap) {
        ccsds_calendar_time r = *t;
        uint64_t unit = 1;
        size_t date = form == CCSDS_ASCII_A ? 10 : 8, len, i;
        if (digits > CCSDS_ASCII_MAX_DIGITS) return CCSDS_NONE;
        for (i = digits; i < CCSDS_ASCII_MAX_DIGITS; ++i) unit *= 10;
        if (!ccsds_calendar_round(&r, unit)) return CCSDS_NONE;
        len = date + 9 + (digits ? 1 + digits : 0) + (z ? 1 : 0);
        if (cap < len) return len;
        ccsds_put_digits_(out, 4, r.year);
        out[4] = '-';
        if (form == CCSDS_ASCII_A) {
                ccsds_put_digits_(out + 5, 2, r.month);
                out[7] = '-';
                ccsds_put_digits_(out + 8, 2, r.day);
        } else {
                ccsds_put_digits_(out + 5, 3, ccsds_day_of_year(&r));
        }
        out[date] = 'T';
        ccsds_put_digits_(out + date + 1, 2, r.hour);
        out[date + 3] = ':';
        ccsds_put_digits_(out + date + 4, 2, r.minute);
        out[date + 6] = ':';
        ccsds_put_digits_(out + date + 7, 2, r.second);
        i = date + 9;
        if (digits) {
                out[i] = '.';
                ccsds_put_digits_(out + i + 1, digits, r.picos / unit);
                i += 1 + digits;
        }
        if (z) out[i] = 'Z';
        return len;
}

/* --- TAI and UTC --------------------------------------------------------- */

/* The binary codes count seconds of TAI, which has no leap seconds; the
 * calendar codes give UTC, which since 1972 has inserted a leap second at
 * the end of a June or December now and then.  Converting takes a table of
 * them: ccsds_leap_seconds_table is the IERS's leap-seconds.list, updated
 * through Bulletin C and expiring on 28 June 2027, and a mission can give
 * its own.  A time is converted only from 1972-01-01, when UTC took its
 * present form, to the table's expiry. */

/* A time of TAI, exactly: seconds since 1958-01-01T00:00:00 TAI, and
 * picoseconds of the second, less than 10^12. */
typedef struct ccsds_tai_time {
        uint64_t seconds;
        uint64_t picos;
} ccsds_tai_time;

/* A change in TAI - UTC: from 00:00:00 UTC on this date, it is this many
 * seconds. */
typedef struct ccsds_leap_second {
        uint16_t year;
        uint8_t month;
        uint8_t day;
        int32_t tai_minus_utc;
} ccsds_leap_second;

/* A table of leap seconds: every change in TAI - UTC from 1972-01-01 on, in
 * order, and the first day it does not cover. */
typedef struct ccsds_leap_seconds {
        const ccsds_leap_second* changes;
        size_t n;
        uint16_t expires_year;
        uint8_t expires_month;
        uint8_t expires_day;
} ccsds_leap_seconds;

/* The changes of the IERS's leap-seconds.list. */
static const ccsds_leap_second ccsds_leap_second_changes_[] = {
        {1972, 1, 1, 10}, {1972, 7, 1, 11}, {1973, 1, 1, 12},
        {1974, 1, 1, 13}, {1975, 1, 1, 14}, {1976, 1, 1, 15},
        {1977, 1, 1, 16}, {1978, 1, 1, 17}, {1979, 1, 1, 18},
        {1980, 1, 1, 19}, {1981, 7, 1, 20}, {1982, 7, 1, 21},
        {1983, 7, 1, 22}, {1985, 7, 1, 23}, {1988, 1, 1, 24},
        {1990, 1, 1, 25}, {1991, 1, 1, 26}, {1992, 7, 1, 27},
        {1993, 7, 1, 28}, {1994, 7, 1, 29}, {1996, 1, 1, 30},
        {1997, 7, 1, 31}, {1999, 1, 1, 32}, {2006, 1, 1, 33},
        {2009, 1, 1, 34}, {2012, 7, 1, 35}, {2015, 7, 1, 36},
        {2017, 1, 1, 37}};

/* The leap seconds of the IERS's leap-seconds.list, updated through
 * Bulletin C and expiring on 28 June 2027. */
static const ccsds_leap_seconds ccsds_leap_seconds_table = {
        ccsds_leap_second_changes_,
        sizeof ccsds_leap_second_changes_ /
                sizeof ccsds_leap_second_changes_[0],
        2027, 6, 28};

/** Counts the days from 1958-01-01 to a date.
 *
 * @param year   The year.
 * @param month  The month.
 * @param day    The day of the month.
 * @return       The days. */
static inline int64_t ccsds_epoch_days_(unsigned year, unsigned month,
                                        unsigned day) {
        return ccsds_days_from_civil_(year, month, day) -
               ccsds_days_from_civil_(1958, 1, 1);
}

/** Gives TAI - UTC on a day.
 *
 * @param table   The leap seconds.
 * @param days    The day, as days since 1958-01-01.
 * @param offset  Filled in with the seconds.
 * @return        True, or false before the first change or from the
 *                table's expiry. */
static inline bool ccsds_leap_on_(const ccsds_leap_seconds* table,
                                  int64_t days, int64_t* offset) {
        size_t i;
        if (days >= ccsds_epoch_days_(table->expires_year,
                                      table->expires_month,
                                      table->expires_day))
                return false;
        for (i = table->n; i-- > 0;) {
                const ccsds_leap_second* c = &table->changes[i];
                if (ccsds_epoch_days_(c->year, c->month, c->day) <= days) {
                        *offset = c->tai_minus_utc;
                        return true;
                }
        }
        return false;
}

/** Gives the TAI second a change takes effect at: 00:00:00 UTC on its day.
 *
 * @param c  The change.
 * @return   Seconds since the CCSDS epoch. */
static inline int64_t ccsds_leap_at_(const ccsds_leap_second* c) {
        return ccsds_epoch_days_(c->year, c->month, c->day) * 86400 +
               c->tai_minus_utc;
}

/** Converts a time of UTC to TAI.
 *
 * @param utc    The time.
 * @param table  The leap seconds.
 * @param tai    Filled in with the time of TAI.
 * @return       True, or false if the time is not valid, is before the
 *               table's first change or from its expiry, or has a second
 *               of 60 on a day that has no leap second. */
static inline bool ccsds_utc_to_tai(const ccsds_calendar_time* utc,
                                    const ccsds_leap_seconds* table,
                                    ccsds_tai_time* tai) {
        int64_t days, offset, next, s;
        if (!ccsds_calendar_valid(utc)) return false;
        days = ccsds_epoch_days_(utc->year, utc->month, utc->day);
        if (!ccsds_leap_on_(table, days, &offset)) return false;
        if (utc->second == 60 &&
            (!ccsds_leap_on_(table, days + 1, &next) || next <= offset))
                return false;
        s = days * 86400 + utc->hour * 3600 + utc->minute * 60 +
            utc->second + offset;
        if (s < 0) return false;
        tai->seconds = (uint64_t)s;
        tai->picos = utc->picos;
        return true;
}

/** Converts a time of TAI to UTC.
 *
 * @param tai    The time.
 * @param table  The leap seconds.
 * @param utc    Filled in with the time of UTC, with a second of 60 in a
 *               leap second.
 * @return       True, or false if its picoseconds are not less than 10^12,
 *               or it is before the table's first change or from its
 *               expiry. */
static inline bool ccsds_tai_to_utc(const ccsds_tai_time* tai,
                                    const ccsds_leap_seconds* table,
                                    ccsds_calendar_time* utc) {
        ccsds_calendar_time r;
        int64_t s, offset, u, days, of_day, unused;
        size_t i;
        bool leap;
        if (tai->picos >= CCSDS_PICOS || tai->seconds > (uint64_t)INT64_MAX)
                return false;
        s = (int64_t)tai->seconds;
        for (i = table->n; i-- > 0;)
                if (ccsds_leap_at_(&table->changes[i]) <= s) break;
        if (i == (size_t)-1) return false;
        offset = table->changes[i].tai_minus_utc;
        /* In the inserted second before the next change, it is 23:59:60 of
         * the day before. */
        leap = i + 1 < table->n &&
               table->changes[i + 1].tai_minus_utc > offset &&
               s >= ccsds_leap_at_(&table->changes[i + 1]) - 1;
        u = s - offset - (leap ? 1 : 0);
        days = u >= 0 ? u / 86400 : (u - 86399) / 86400;
        of_day = u - days * 86400;
        if (!ccsds_leap_on_(table, days, &unused)) return false;
        memset(&r, 0, sizeof r);
        if (!ccsds_civil_from_days_(days + ccsds_days_from_civil_(1958, 1, 1),
                                    &r))
                return false;
        r.hour = (uint8_t)(of_day / 3600);
        r.minute = (uint8_t)(of_day / 60 % 60);
        r.second = (uint8_t)(of_day % 60 + (leap ? 1 : 0));
        r.picos = tai->picos;
        *utc = r;
        return true;
}

#ifdef __cplusplus
}
#endif

#endif /* CCSDS_TIME_H */
