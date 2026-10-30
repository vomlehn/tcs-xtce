/* Fixed-width integers of any number of bytes, for C11 and C++.
 *
 * This mirrors the Rust crate bnum (https://docs.rs/bnum), which the Rust
 * runtime uses for integers of more than 128 bits, so that C, C++ and Rust
 * hold and compute them alike.  An integer is N bytes, least significant
 * first, and two's complement when it is signed; xtce-bnum.hpp wraps these
 * functions in a C++ class template, xtce::bnum::Integer<S, N>, like bnum's
 * Integer<S, N>.
 *
 * Every function takes its integers as byte pointers, their size `n` in bytes
 * (at least 1) and whether they are signed `s`, and is named after the bnum
 * method it mirrors: xtce_bn_wrapping_add is bnum's wrapping_add.  The
 * overflowing_ functions return whether the operation overflowed, and the
 * checked_ ones whether it succeeded, leaving `out` unspecified when not.
 * The result `out` may be one of the operands, except where a function says
 * otherwise.  Shift amounts and radixes are unsigned ints, as in bnum.
 *
 * Nothing is allocated; where a function needs room to work it takes a
 * `scratch` buffer of `n` bytes.  The header needs only the C standard
 * library.
 */
#ifndef XTCE_BNUM_H
#define XTCE_BNUM_H

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Declares `name`, an integer of `n` bytes: a struct, so that it can be
 * assigned and returned, holding them in `bytes`. */
#define XTCE_BN_TYPEDEF(name, n)    \
        typedef struct name {       \
                uint8_t bytes[n];   \
        } name

/* An expression of struct type `T` from a brace-enclosed initializer, in C
 * (a compound literal) and in C++. */
#ifdef __cplusplus
#define XTCE_BN_LIT(T, ...) (T __VA_ARGS__)
#else
#define XTCE_BN_LIT(T, ...) ((T)__VA_ARGS__)
#endif

/* Why xtce_bn_from_str_radix failed, as Rust's IntErrorKind gives it. */
enum {
        XTCE_BN_OK = 0,
        XTCE_BN_EMPTY,
        XTCE_BN_INVALID_DIGIT,
        XTCE_BN_POS_OVERFLOW,
        XTCE_BN_NEG_OVERFLOW,
};

/* --- bits and constants -------------------------------------------------- */

static inline bool xtce_bn_is_negative(const uint8_t* a, size_t n, bool s) {
        return s && (a[n - 1] & 0x80) != 0;
}

/** Whether an integer is zero.
 *
 * @param a  The integer, least significant byte first.
 * @param n  Its size in bytes, at least 1.
 * @return   True if every byte is zero. */
static inline bool xtce_bn_is_zero(const uint8_t* a, size_t n) {
        size_t i;
        for (i = 0; i < n; ++i)
                if (a[i]) return false;
        return true;
}

/** Whether an integer is one.
 *
 * @param a  The integer, least significant byte first.
 * @param n  Its size in bytes, at least 1.
 * @return   True if its value is 1. */
static inline bool xtce_bn_is_one(const uint8_t* a, size_t n) {
        size_t i;
        for (i = 1; i < n; ++i)
                if (a[i]) return false;
        return a[0] == 1;
}

static inline bool xtce_bn_is_positive(const uint8_t* a, size_t n, bool s) {
        return !xtce_bn_is_negative(a, n, s) && !xtce_bn_is_zero(a, n);
}

/* -1, 0 or 1. */
static inline int xtce_bn_signum(const uint8_t* a, size_t n, bool s) {
        return xtce_bn_is_negative(a, n, s) ? -1 : !xtce_bn_is_zero(a, n);
}

/* Bit `k` (0 is the least significant). */
static inline bool xtce_bn_bit(const uint8_t* a, size_t n, unsigned k) {
        return k / 8 < n && ((a[k / 8] >> (k % 8)) & 1) != 0;
}

static inline void xtce_bn_set_bit(uint8_t* a, size_t n, unsigned k,
                                   bool value) {
        uint8_t m = (uint8_t)(1u << (k % 8));
        if (k / 8 >= n) return;
        a[k / 8] = value ? (uint8_t)(a[k / 8] | m) : (uint8_t)(a[k / 8] & ~m);
}

static inline void xtce_bn_zero(uint8_t* out, size_t n) {
        memset(out, 0, n);
}

static inline void xtce_bn_one(uint8_t* out, size_t n) {
        memset(out, 0, n);
        out[0] = 1;
}

/* MIN and MAX. */
static inline void xtce_bn_min(uint8_t* out, size_t n, bool s) {
        memset(out, 0, n);
        if (s) out[n - 1] = 0x80;
}

static inline void xtce_bn_max(uint8_t* out, size_t n, bool s) {
        memset(out, 0xff, n);
        if (s) out[n - 1] = 0x7f;
}

/* --- conversions --------------------------------------------------------- */

/* `a`, of `na` bytes and signedness `sa`, as an integer of `n` bytes, as
 * Rust's `as` converts: truncated, or extended with its sign if `sa`. */
static inline void xtce_bn_cast(uint8_t* out, size_t n, const uint8_t* a,
                                size_t na, bool sa) {
        uint8_t fill = xtce_bn_is_negative(a, na, sa) ? 0xff : 0;
        if (out != a) memmove(out, a, na < n ? na : n);
        if (n > na) memset(out + na, fill, n - na);
}

/** Whether an integer has the same value as an integer of another size and
 * signedness: whether xtce_bn_cast keeps it.
 *
 * @param a   The integer, least significant byte first.
 * @param na  Its size in bytes.
 * @param sa  Whether it is signed.
 * @param n   The size of the other integer in bytes.
 * @param s   Whether the other integer is signed.
 * @return    True if `a`'s value is in the other integer's range. */
static inline bool xtce_bn_fits(const uint8_t* a, size_t na, bool sa,
                                size_t n, bool s) {
        bool neg = xtce_bn_is_negative(a, na, sa);
        uint8_t fill = neg ? 0xff : 0;
        size_t i;
        if (neg && !s) return false;
        for (i = n; i < na; ++i)
                if (a[i] != fill) return false;
        if (n > na || !s) return true;
        /* The top bit kept must be the sign. */
        return ((a[n - 1] & 0x80) != 0) == neg;
}

static inline void xtce_bn_from_u64(uint8_t* out, size_t n, uint64_t v) {
        size_t i;
        for (i = 0; i < n; ++i, v >>= 8) out[i] = (uint8_t)(i < 8 ? v : 0);
}

static inline void xtce_bn_from_i64(uint8_t* out, size_t n, int64_t v) {
        xtce_bn_from_u64(out, n, (uint64_t)v);
        if (v < 0 && n > 8) memset(out + 8, 0xff, n - 8);
}

/** Converts an integer to a uint64_t, if it fits one.
 *
 * @param a    The integer, least significant byte first.
 * @param n    Its size in bytes, at least 1.
 * @param s    Whether it is signed.
 * @param out  Where to put the value; left alone if it doesn't fit.
 * @return     Whether `a` fits a uint64_t. */
static inline bool xtce_bn_to_u64(const uint8_t* a, size_t n, bool s,
                                  uint64_t* out) {
        uint64_t v = 0;
        size_t i;
        if (!xtce_bn_fits(a, n, s, 8, false)) return false;
        for (i = n < 8 ? n : 8; i-- > 0;) v = (v << 8) | a[i];
        *out = v;
        return true;
}

/** Converts an integer to an int64_t, if it fits one.
 *
 * @param a    The integer, least significant byte first.
 * @param n    Its size in bytes, at least 1.
 * @param s    Whether it is signed.
 * @param out  Where to put the value; left alone if it doesn't fit.
 * @return     Whether `a` fits an int64_t. */
static inline bool xtce_bn_to_i64(const uint8_t* a, size_t n, bool s,
                                  int64_t* out) {
        uint8_t b[8];
        uint64_t u = 0;
        size_t i;
        if (!xtce_bn_fits(a, n, s, 8, true)) return false;
        xtce_bn_cast(b, 8, a, n, s);
        for (i = 8; i-- > 0;) u = (u << 8) | b[i];
        /* Not a cast: converting an out of range value to int64_t is
         * implementation-defined in C. */
        *out = (b[7] & 0x80) ? -(int64_t)(~u) - 1 : (int64_t)u;
        return true;
}

/** Reads an integer from bytes least significant first.  A signed integer takes
 * the slice's top bit as its sign, as bnum does.
 *
 * @param out  Where to put the integer, `n` bytes; left alone if it doesn't
 *             fit.
 * @param n    The integer's size in bytes, at least 1.
 * @param s    Whether the integer, and so the slice, is signed.
 * @param src  The bytes, least significant first.
 * @param len  How many there are; none is zero.
 * @return     False if the slice's value doesn't fit the integer. */
static inline bool xtce_bn_from_le_slice(uint8_t* out, size_t n, bool s,
                                         const uint8_t* src, size_t len) {
        size_t i;
        if (!len) {
                memset(out, 0, n);
                return true;
        }
        if (!xtce_bn_fits(src, len, s, n, s)) return false;
        for (i = 0; i < n; ++i)
                out[i] = i < len ? src[i]
                                 : (xtce_bn_is_negative(src, len, s) ? 0xff
                                                                     : 0);
        return true;
}

/** Reverses the order of an integer's bytes: little to big endian, or back.
 *
 * @param out  Where to put the result, `n` bytes; it may be `a`.
 * @param a    The integer's bytes.
 * @param n    Its size in bytes, at least 1. */
static inline void xtce_bn_swap_bytes(uint8_t* out, const uint8_t* a,
                                      size_t n) {
        size_t i;
        for (i = 0; i < n / 2; ++i) {
                uint8_t t = a[i];
                out[i] = a[n - 1 - i];
                out[n - 1 - i] = t;
        }
        if (n % 2) out[n / 2] = a[n / 2];
}

/** Reads an integer from bytes most significant first, as xtce_bn_from_le_slice
 * does from bytes least significant first.
 *
 * @param out  Where to put the integer, `n` bytes; left alone if it doesn't
 *             fit.
 * @param n    The integer's size in bytes, at least 1.
 * @param s    Whether the integer, and so the slice, is signed.
 * @param src  The bytes, most significant first.
 * @param len  How many there are; none is zero.
 * @return     False if the slice's value doesn't fit the integer. */
static inline bool xtce_bn_from_be_slice(uint8_t* out, size_t n, bool s,
                                         const uint8_t* src, size_t len) {
        size_t i;
        uint8_t fill;
        if (!len) {
                memset(out, 0, n);
                return true;
        }
        fill = (s && (src[0] & 0x80)) ? 0xff : 0;
        if (len > n) {
                for (i = 0; i < len - n; ++i)
                        if (src[i] != fill) return false;
                src += len - n;
                len = n;
                if (s && ((src[0] & 0x80) ? 0xff : 0) != fill) return false;
        }
        for (i = 0; i < n; ++i) out[i] = i < len ? src[len - 1 - i] : fill;
        return true;
}

/* The bytes of `a` most significant first. */
static inline void xtce_bn_to_be_bytes(uint8_t* out, const uint8_t* a,
                                       size_t n) {
        xtce_bn_swap_bytes(out, a, n);
}

/** Reverses the order of an integer's bits.
 *
 * @param out  Where to put the result, `n` bytes; it may be `a`.
 * @param a    The integer, least significant byte first.
 * @param n    Its size in bytes, at least 1. */
static inline void xtce_bn_reverse_bits(uint8_t* out, const uint8_t* a,
                                        size_t n) {
        size_t i;
        xtce_bn_swap_bytes(out, a, n);
        for (i = 0; i < n; ++i) {
                uint8_t b = out[i], r = 0;
                int k;
                for (k = 0; k < 8; ++k) r = (uint8_t)(r | ((b >> k) & 1)
                                                               << (7 - k));
                out[i] = r;
        }
}

/* --- comparison ---------------------------------------------------------- */

/** Compares two integers of the same type.
 *
 * @param a  The first operand.
 * @param b  The second operand.
 * @param n  The size of each integer in bytes, at least 1.
 * @param s  Whether they are signed.
 * @return   -1, 0 or 1 as `a` is less than, equal to or greater than `b`. */
static inline int xtce_bn_cmp(const uint8_t* a, const uint8_t* b, size_t n,
                              bool s) {
        bool an = xtce_bn_is_negative(a, n, s);
        size_t i = n;
        if (an != xtce_bn_is_negative(b, n, s)) return an ? -1 : 1;
        while (i-- > 0)
                if (a[i] != b[i]) return a[i] < b[i] ? -1 : 1;
        return 0;
}

/** Compares two integers of any sizes and signedness as their values compare.
 * (Rust compares them after casting both to one type.)
 *
 * @param a   The first integer, least significant byte first.
 * @param na  Its size in bytes.
 * @param sa  Whether it is signed.
 * @param b   The second integer, least significant byte first.
 * @param nb  Its size in bytes.
 * @param sb  Whether it is signed.
 * @return    -1, 0 or 1 as `a` is less than, equal to or greater than `b`. */
static inline int xtce_bn_cmp_mixed(const uint8_t* a, size_t na, bool sa,
                                    const uint8_t* b, size_t nb, bool sb) {
        bool an = xtce_bn_is_negative(a, na, sa);
        bool bn = xtce_bn_is_negative(b, nb, sb);
        size_t i = na > nb ? na : nb;
        if (an != bn) return an ? -1 : 1;
        while (i-- > 0) {
                unsigned x = i < na ? a[i] : (an ? 0xffu : 0u);
                unsigned y = i < nb ? b[i] : (bn ? 0xffu : 0u);
                if (x != y) return x < y ? -1 : 1;
        }
        return 0;
}

static inline void xtce_bn_min_of(uint8_t* out, const uint8_t* a,
                                  const uint8_t* b, size_t n, bool s) {
        memmove(out, xtce_bn_cmp(a, b, n, s) <= 0 ? a : b, n);
}

static inline void xtce_bn_max_of(uint8_t* out, const uint8_t* a,
                                  const uint8_t* b, size_t n, bool s) {
        memmove(out, xtce_bn_cmp(a, b, n, s) >= 0 ? a : b, n);
}

/* --- bitwise operations -------------------------------------------------- */

static inline void xtce_bn_not(uint8_t* out, const uint8_t* a, size_t n) {
        size_t i;
        for (i = 0; i < n; ++i) out[i] = (uint8_t)~a[i];
}

static inline void xtce_bn_bitand(uint8_t* out, const uint8_t* a,
                                  const uint8_t* b, size_t n) {
        size_t i;
        for (i = 0; i < n; ++i) out[i] = a[i] & b[i];
}

static inline void xtce_bn_bitor(uint8_t* out, const uint8_t* a,
                                 const uint8_t* b, size_t n) {
        size_t i;
        for (i = 0; i < n; ++i) out[i] = a[i] | b[i];
}

static inline void xtce_bn_bitxor(uint8_t* out, const uint8_t* a,
                                  const uint8_t* b, size_t n) {
        size_t i;
        for (i = 0; i < n; ++i) out[i] = a[i] ^ b[i];
}

/** Counts the bits of an integer that are set.
 *
 * @param a  The integer, least significant byte first.
 * @param n  Its size in bytes, at least 1.
 * @return   The number of one bits. */
static inline unsigned xtce_bn_count_ones(const uint8_t* a, size_t n) {
        unsigned c = 0;
        size_t i;
        for (i = 0; i < n; ++i) {
                uint8_t b = a[i];
                for (; b; b &= (uint8_t)(b - 1)) ++c;
        }
        return c;
}

static inline unsigned xtce_bn_count_zeros(const uint8_t* a, size_t n) {
        return (unsigned)(8 * n) - xtce_bn_count_ones(a, n);
}

/** Counts an integer's leading (most significant) bits that equal `one`.
 *
 * @param a    The integer, least significant byte first.
 * @param n    Its size in bytes, at least 1.
 * @param one  Whether to count ones rather than zeros.
 * @return     The number of such bits, up to 8 * `n`. */
static inline unsigned xtce_bn_leading_(const uint8_t* a, size_t n,
                                        bool one) {
        unsigned c = 0;
        size_t i = n;
        while (i-- > 0) {
                uint8_t b = one ? (uint8_t)~a[i] : a[i];
                if (b) {
                        while (!(b & 0x80)) {
                                ++c;
                                b = (uint8_t)(b << 1);
                        }
                        return c;
                }
                c += 8;
        }
        return c;
}

/** Counts an integer's trailing (least significant) bits that equal `one`.
 *
 * @param a    The integer, least significant byte first.
 * @param n    Its size in bytes, at least 1.
 * @param one  Whether to count ones rather than zeros.
 * @return     The number of such bits, up to 8 * `n`. */
static inline unsigned xtce_bn_trailing_(const uint8_t* a, size_t n,
                                         bool one) {
        unsigned c = 0;
        size_t i;
        for (i = 0; i < n; ++i) {
                uint8_t b = one ? (uint8_t)~a[i] : a[i];
                if (b) {
                        while (!(b & 1)) {
                                ++c;
                                b >>= 1;
                        }
                        return c;
                }
                c += 8;
        }
        return c;
}

static inline unsigned xtce_bn_leading_zeros(const uint8_t* a, size_t n) {
        return xtce_bn_leading_(a, n, false);
}

static inline unsigned xtce_bn_leading_ones(const uint8_t* a, size_t n) {
        return xtce_bn_leading_(a, n, true);
}

static inline unsigned xtce_bn_trailing_zeros(const uint8_t* a, size_t n) {
        return xtce_bn_trailing_(a, n, false);
}

static inline unsigned xtce_bn_trailing_ones(const uint8_t* a, size_t n) {
        return xtce_bn_trailing_(a, n, true);
}

/* The bits needed to hold `a` as unsigned: the bits less its leading
 * zeros. */
static inline unsigned xtce_bn_bit_width(const uint8_t* a, size_t n) {
        return (unsigned)(8 * n) - xtce_bn_leading_zeros(a, n);
}

static inline bool xtce_bn_is_power_of_two(const uint8_t* a, size_t n) {
        return xtce_bn_count_ones(a, n) == 1;
}

/* --- shifts and rotations ------------------------------------------------ */

/** Shifts an integer left by any number of bits, zeros coming in.
 *
 * @param out  Where to put the result, `n` bytes; it may be `a`.
 * @param a    The integer, least significant byte first.
 * @param n    Its size in bytes, at least 1.
 * @param k    The number of bits; 8 * `n` or more gives zero. */
static inline void xtce_bn_shl_bits_(uint8_t* out, const uint8_t* a,
                                     size_t n, size_t k) {
        size_t q = k / 8, i = n;
        unsigned r = (unsigned)(k % 8);
        /* From the top down, each byte reads only those below it. */
        while (i-- > 0) {
                unsigned hi = i >= q ? a[i - q] : 0;
                unsigned lo = i >= q + 1 ? a[i - q - 1] : 0;
                out[i] = (uint8_t)((hi << r) | (r ? lo >> (8 - r) : 0));
        }
}

/** Shifts an integer right by any number of bits, copies of `fill` coming in.
 *
 * @param out   Where to put the result, `n` bytes; it may be `a`.
 * @param a     The integer, least significant byte first.
 * @param n     Its size in bytes, at least 1.
 * @param k     The number of bits; 8 * `n` or more leaves only `fill`.
 * @param fill  The byte shifted in: 0, or 0xff to extend a sign. */
static inline void xtce_bn_shr_bits_(uint8_t* out, const uint8_t* a,
                                     size_t n, size_t k, uint8_t fill) {
        size_t q = k / 8, i;
        unsigned r = (unsigned)(k % 8);
        /* From the bottom up, each byte reads only those above it. */
        for (i = 0; i < n; ++i) {
                unsigned lo = i + q < n ? a[i + q] : fill;
                unsigned hi = i + q + 1 < n ? a[i + q + 1] : fill;
                out[i] = (uint8_t)((lo >> r) | (r ? hi << (8 - r) : 0));
        }
}

/* bnum's overflowing_shl: the shift is taken modulo the number of bits,
 * and it overflowed if it was that many or more. */
static inline bool xtce_bn_overflowing_shl(uint8_t* out, const uint8_t* a,
                                           size_t n, unsigned k) {
        xtce_bn_shl_bits_(out, a, n, k % (8 * n));
        return k >= 8 * n;
}

/* A signed integer shifts in copies of its sign. */
static inline bool xtce_bn_overflowing_shr(uint8_t* out, const uint8_t* a,
                                           size_t n, bool s, unsigned k) {
        uint8_t fill = xtce_bn_is_negative(a, n, s) ? 0xff : 0;
        xtce_bn_shr_bits_(out, a, n, k % (8 * n), fill);
        return k >= 8 * n;
}

static inline void xtce_bn_wrapping_shl(uint8_t* out, const uint8_t* a,
                                        size_t n, unsigned k) {
        (void)xtce_bn_overflowing_shl(out, a, n, k);
}

static inline void xtce_bn_wrapping_shr(uint8_t* out, const uint8_t* a,
                                        size_t n, bool s, unsigned k) {
        (void)xtce_bn_overflowing_shr(out, a, n, s, k);
}

static inline bool xtce_bn_checked_shl(uint8_t* out, const uint8_t* a,
                                       size_t n, unsigned k) {
        return !xtce_bn_overflowing_shl(out, a, n, k);
}

static inline bool xtce_bn_checked_shr(uint8_t* out, const uint8_t* a,
                                       size_t n, bool s, unsigned k) {
        return !xtce_bn_overflowing_shr(out, a, n, s, k);
}

/* Shifts of any size: every bit shifted out, for that many or more. */
static inline void xtce_bn_unbounded_shl(uint8_t* out, const uint8_t* a,
                                         size_t n, unsigned k) {
        xtce_bn_shl_bits_(out, a, n, k < 8 * n ? k : 8 * n);
}

static inline void xtce_bn_unbounded_shr(uint8_t* out, const uint8_t* a,
                                         size_t n, bool s, unsigned k) {
        uint8_t fill = xtce_bn_is_negative(a, n, s) ? 0xff : 0;
        xtce_bn_shr_bits_(out, a, n, k < 8 * n ? k : 8 * n, fill);
}

/** Rotates an integer's bits left (toward the most significant).
 *
 * @param out  Where to put the result, `n` bytes; it must not be `a`.
 * @param a    The integer, least significant byte first.
 * @param n    Its size in bytes, at least 1.
 * @param k    The number of bits, taken modulo 8 * `n`. */
static inline void xtce_bn_rotate_left(uint8_t* out, const uint8_t* a,
                                       size_t n, unsigned k) {
        size_t bits = 8 * n, i;
        k %= (unsigned)bits;
        for (i = 0; i < bits; ++i)
                xtce_bn_set_bit(out, n, (unsigned)((i + k) % bits),
                                xtce_bn_bit(a, n, (unsigned)i));
}

/** Rotates an integer's bits right (toward the least significant).
 *
 * @param out  Where to put the result, `n` bytes; it must not be `a`.
 * @param a    The integer, least significant byte first.
 * @param n    Its size in bytes, at least 1.
 * @param k    The number of bits, taken modulo 8 * `n`. */
static inline void xtce_bn_rotate_right(uint8_t* out, const uint8_t* a,
                                        size_t n, unsigned k) {
        xtce_bn_rotate_left(out, a, n,
                            (unsigned)(8 * n - k % (8 * n)) %
                                    (unsigned)(8 * n));
}

/* --- addition and subtraction -------------------------------------------- */

/** Adds two integers and a carry, as unsigned.
 *
 * @param out    Where to put the result, `n` bytes; it may be `a` or `b`.
 * @param a      The first operand.
 * @param b      The second operand.
 * @param n      The size of each integer in bytes, at least 1.
 * @param carry  The carry in, 0 or 1.
 * @return       The carry out, 0 or 1. */
static inline unsigned xtce_bn_add_(uint8_t* out, const uint8_t* a,
                                    const uint8_t* b, size_t n,
                                    unsigned carry) {
        size_t i;
        for (i = 0; i < n; ++i) {
                unsigned x = (unsigned)a[i] + b[i] + carry;
                out[i] = (uint8_t)x;
                carry = x >> 8;
        }
        return carry;
}

/** Subtracts one integer from another, as unsigned.
 *
 * @param out  Where to put the result, `n` bytes; it may be `a` or `b`.
 * @param a    The integer subtracted from.
 * @param b    The integer subtracted.
 * @param n    The size of each integer in bytes, at least 1.
 * @return     The borrow out, 0 or 1. */
static inline unsigned xtce_bn_sub_(uint8_t* out, const uint8_t* a,
                                    const uint8_t* b, size_t n) {
        unsigned borrow = 0;
        size_t i;
        for (i = 0; i < n; ++i) {
                unsigned x = (unsigned)a[i] - b[i] - borrow;
                out[i] = (uint8_t)x;
                borrow = (x >> 8) & 1;
        }
        return borrow;
}

/** Adds two integers, wrapping on overflow.
 *
 * @param out  Where to put the result, `n` bytes; it may be `a` or `b`.
 * @param a    The first operand.
 * @param b    The second operand.
 * @param n    The size of each integer in bytes, at least 1.
 * @param s    Whether they are signed.
 * @return     Whether the sum overflowed. */
static inline bool xtce_bn_overflowing_add(uint8_t* out, const uint8_t* a,
                                           const uint8_t* b, size_t n,
                                           bool s) {
        bool an = xtce_bn_is_negative(a, n, s);
        bool bn = xtce_bn_is_negative(b, n, s);
        unsigned carry = xtce_bn_add_(out, a, b, n, 0);
        if (!s) return carry != 0;
        return an == bn && xtce_bn_is_negative(out, n, s) != an;
}

/** Subtracts one integer from another, wrapping on overflow.
 *
 * @param out  Where to put the result, `n` bytes; it may be `a` or `b`.
 * @param a    The integer subtracted from.
 * @param b    The integer subtracted.
 * @param n    The size of each integer in bytes, at least 1.
 * @param s    Whether they are signed.
 * @return     Whether the difference overflowed. */
static inline bool xtce_bn_overflowing_sub(uint8_t* out, const uint8_t* a,
                                           const uint8_t* b, size_t n,
                                           bool s) {
        bool an = xtce_bn_is_negative(a, n, s);
        bool bn = xtce_bn_is_negative(b, n, s);
        unsigned borrow = xtce_bn_sub_(out, a, b, n);
        if (!s) return borrow != 0;
        return an != bn && xtce_bn_is_negative(out, n, s) != an;
}

/** Negates an integer, wrapping on overflow.  An unsigned integer overflows
 * unless it is zero, and a signed one only if it is MIN.
 *
 * @param out  Where to put the result, `n` bytes; it may be `a`.
 * @param a    The integer, least significant byte first.
 * @param n    Its size in bytes, at least 1.
 * @param s    Whether it is signed.
 * @return     Whether the negation overflowed. */
static inline bool xtce_bn_overflowing_neg(uint8_t* out, const uint8_t* a,
                                           size_t n, bool s) {
        unsigned carry = 1;
        bool zero = xtce_bn_is_zero(a, n), an = xtce_bn_is_negative(a, n, s);
        size_t i;
        for (i = 0; i < n; ++i) {
                unsigned x = (unsigned)(uint8_t)~a[i] + carry;
                out[i] = (uint8_t)x;
                carry = x >> 8;
        }
        if (!s) return !zero;
        /* Only MIN is its own negation, apart from zero. */
        return an && xtce_bn_is_negative(out, n, s);
}

/** Takes an integer's absolute value, wrapping on overflow (MIN stays MIN).
 *
 * @param out  Where to put the result, `n` bytes; it may be `a`.
 * @param a    The integer, least significant byte first.
 * @param n    Its size in bytes, at least 1.
 * @param s    Whether it is signed.
 * @return     Whether it overflowed, which only MIN does. */
static inline bool xtce_bn_overflowing_abs(uint8_t* out, const uint8_t* a,
                                           size_t n, bool s) {
        if (!xtce_bn_is_negative(a, n, s)) {
                memmove(out, a, n);
                return false;
        }
        return xtce_bn_overflowing_neg(out, a, n, s);
}

/* |a| as unsigned, which always fits. */
static inline void xtce_bn_unsigned_abs(uint8_t* out, const uint8_t* a,
                                        size_t n, bool s) {
        (void)xtce_bn_overflowing_abs(out, a, n, s);
}

/** Takes the absolute difference |a - b| of two integers, as unsigned, which
 * always fits.
 *
 * @param out  Where to put the difference, `n` bytes; it must not be `a` or
 *             `b`.
 * @param a    The first operand.
 * @param b    The second operand.
 * @param n    The size of each integer in bytes, at least 1.
 * @param s    Whether they are signed. */
static inline void xtce_bn_abs_diff(uint8_t* out, const uint8_t* a,
                                    const uint8_t* b, size_t n, bool s) {
        if (xtce_bn_cmp(a, b, n, s) >= 0)
                (void)xtce_bn_sub_(out, a, b, n);
        else
                (void)xtce_bn_sub_(out, b, a, n);
}

/* --- multiplication and division ----------------------------------------- */

/* The magnitude of an integer, read a byte at a time without storing it:
 * the bytes of -a are 0 below its lowest non-zero byte z, -a[z] at z, and
 * ~a[k] above. */
typedef struct xtce_bn_mag_ {
        const uint8_t* p;
        size_t z;
        bool neg;
} xtce_bn_mag_;

/** Starts reading an integer's magnitude a byte at a time, without storing it.
 *
 * @param a  The integer, least significant byte first; it must outlive the
 *           result.
 * @param n  Its size in bytes, at least 1.
 * @param s  Whether it is signed.
 * @return   The reader, for xtce_bn_mag_byte_. */
static inline xtce_bn_mag_ xtce_bn_mag_of_(const uint8_t* a, size_t n,
                                           bool s) {
        xtce_bn_mag_ m;
        m.p = a;
        m.neg = xtce_bn_is_negative(a, n, s);
        for (m.z = 0; m.z < n && !a[m.z]; ++m.z) {
        }
        return m;
}

/** Reads one byte of an integer's magnitude.
 *
 * @param m  The reader xtce_bn_mag_of_ made.
 * @param k  The byte's index, below the integer's size; 0 is the least
 *           significant.
 * @return   The byte, 0 to 255. */
static inline unsigned xtce_bn_mag_byte_(const xtce_bn_mag_* m, size_t k) {
        if (!m->neg) return m->p[k];
        if (k < m->z) return 0;
        if (k == m->z) return (uint8_t)(0u - m->p[k]);
        return (uint8_t)~m->p[k];
}

/** Whether an unsigned magnitude fits a signed integer of the same size:
 * whether it is at most MAX, or at most 2^(8n-1), MIN's magnitude, if `neg`.
 *
 * @param a    The magnitude, least significant byte first.
 * @param n    Its size in bytes, at least 1.
 * @param neg  Whether the value is negative.
 * @return     True if the signed value fits. */
static inline bool xtce_bn_mag_fits_(const uint8_t* a, size_t n, bool neg) {
        size_t i;
        if (!(a[n - 1] & 0x80)) return true;
        if (!neg || a[n - 1] != 0x80) return false;
        for (i = 0; i + 1 < n; ++i)
                if (a[i]) return false;
        return true;
}

/* In place: `a` negated, ignoring overflow. */
static inline void xtce_bn_negate_(uint8_t* a, size_t n) {
        (void)xtce_bn_overflowing_neg(a, a, n, false);
}

/** Multiplies two integers, wrapping on overflow.
 *
 * @param out  Where to put the product, `n` bytes; it must not be `a` or `b`.
 * @param a    The first operand.
 * @param b    The second operand.
 * @param n    The size of each integer in bytes, at least 1.
 * @param s    Whether they are signed.
 * @return     Whether the product overflowed. */
static inline bool xtce_bn_overflowing_mul(uint8_t* out, const uint8_t* a,
                                           const uint8_t* b, size_t n,
                                           bool s) {
        xtce_bn_mag_ ma = xtce_bn_mag_of_(a, n, s);
        xtce_bn_mag_ mb = xtce_bn_mag_of_(b, n, s);
        bool neg = ma.neg != mb.neg, high;
        uint64_t acc = 0;
        size_t i, j, k;
        /* The low n bytes of the product of the magnitudes, a column at a
         * time. */
        for (k = 0; k < n; ++k) {
                for (i = 0; i <= k; ++i)
                        acc += (uint64_t)xtce_bn_mag_byte_(&ma, i) *
                               xtce_bn_mag_byte_(&mb, k - i);
                out[k] = (uint8_t)acc;
                acc >>= 8;
        }
        /* The bytes above are non-zero if a carry or a term reaches them. */
        high = acc != 0;
        for (i = 1; i < n && !high; ++i) {
                if (!xtce_bn_mag_byte_(&ma, i)) continue;
                for (j = n - i; j < n && !high; ++j)
                        high = xtce_bn_mag_byte_(&mb, j) != 0;
        }
        if (s) high = high || !xtce_bn_mag_fits_(out, n, neg);
        if (neg) xtce_bn_negate_(out, n);
        return high;
}

/** Divides one integer by another, rounding toward zero as Rust does, so that
 * the remainder has the sign of the dividend.  MIN / -1 overflows to MIN, with
 * a remainder of 0.  Apart from `q` and `a`, none of the integers may be the
 * same.
 *
 * @param q         Where to put the quotient, `n` bytes; it may be `a`.
 * @param r         Where to put the remainder, `n` bytes.
 * @param a         The dividend.
 * @param b         The divisor.
 * @param n         The size of each integer in bytes, at least 1.
 * @param s         Whether they are signed.
 * @param overflow  If not NULL, set to whether the quotient overflowed.
 * @return          False, leaving `q` and `r` unspecified, if `b` is zero. */
static inline bool xtce_bn_div_rem_(uint8_t* q, uint8_t* r, const uint8_t* a,
                                    const uint8_t* b, size_t n, bool s,
                                    bool* overflow) {
        xtce_bn_mag_ ma = xtce_bn_mag_of_(a, n, s);
        xtce_bn_mag_ mb = xtce_bn_mag_of_(b, n, s);
        size_t bits = 8 * n, i, k;
        if (overflow) *overflow = false;
        if (mb.z == n) return false;
        /* The dividend's magnitude in `q`: byte k reads only a[k] (and the
         * already read a[z]), so `q` may be `a`. */
        for (k = 0; k < n; ++k) q[k] = (uint8_t)xtce_bn_mag_byte_(&ma, k);
        memset(r, 0, n);
        /* Restoring division: shift (r, q) left a bit at a time, and take the
         * divisor's magnitude from r when r holds it. */
        for (i = 0; i < bits; ++i) {
                unsigned top = r[n - 1] >> 7, in = q[n - 1] >> 7;
                bool ge = top != 0;
                xtce_bn_shl_bits_(r, r, n, 1);
                r[0] = (uint8_t)(r[0] | in);
                xtce_bn_shl_bits_(q, q, n, 1);
                for (k = n; !ge && k-- > 0;) {
                        unsigned d = xtce_bn_mag_byte_(&mb, k);
                        if (r[k] != d) {
                                ge = r[k] > d;
                                break;
                        }
                        if (k == 0) ge = true;
                }
                if (ge) {
                        unsigned borrow = 0;
                        for (k = 0; k < n; ++k) {
                                unsigned x = (unsigned)r[k] -
                                             xtce_bn_mag_byte_(&mb, k) -
                                             borrow;
                                r[k] = (uint8_t)x;
                                borrow = (x >> 8) & 1;
                        }
                        q[0] |= 1;
                }
        }
        if (ma.neg != mb.neg) xtce_bn_negate_(q, n);
        if (ma.neg) xtce_bn_negate_(r, n);
        if (s && overflow) {
                /* Only MIN / -1 has a quotient that doesn't fit. */
                *overflow = ma.neg == mb.neg && xtce_bn_is_negative(q, n, s);
        }
        return true;
}

/** Divides one integer by another, rounding toward zero.
 *
 * @param out      Where to put the quotient, `n` bytes; it may be `a`, but not
 *                 `b`.
 * @param a        The dividend.
 * @param b        The divisor.
 * @param n        The size of each integer in bytes, at least 1.
 * @param s        Whether they are signed.
 * @param scratch  `n` bytes of room for the remainder.
 * @return         False, leaving `out` unspecified, if `b` is zero or the
 *                 quotient overflows (MIN / -1). */
static inline bool xtce_bn_checked_div(uint8_t* out, const uint8_t* a,
                                       const uint8_t* b, size_t n, bool s,
                                       uint8_t* scratch) {
        bool overflow;
        return xtce_bn_div_rem_(out, scratch, a, b, n, s, &overflow) &&
               !overflow;
}

/** Takes the remainder of dividing one integer by another, rounding toward
 * zero, so that it has the sign of `a`.
 *
 * @param out      Where to put the remainder, `n` bytes; it may be `a`, but not
 *                 `b`.
 * @param a        The dividend.
 * @param b        The divisor.
 * @param n        The size of each integer in bytes, at least 1.
 * @param s        Whether they are signed.
 * @param scratch  `n` bytes of room for the quotient.
 * @return         False, leaving `out` unspecified, if `b` is zero or the
 *                 quotient overflows (MIN % -1). */
static inline bool xtce_bn_checked_rem(uint8_t* out, const uint8_t* a,
                                       const uint8_t* b, size_t n, bool s,
                                       uint8_t* scratch) {
        bool overflow;
        memmove(scratch, a, n);
        return xtce_bn_div_rem_(scratch, out, scratch, b, n, s, &overflow) &&
               !overflow;
}

/** bnum's overflowing_div: divides one integer by another, rounding toward zero
 * and wrapping on overflow (MIN / -1 is MIN).
 *
 * @param out      Where to put the quotient, `n` bytes; it may be `a`, but not
 *                 `b`.
 * @param a        The dividend.
 * @param b        The divisor.
 * @param n        The size of each integer in bytes, at least 1.
 * @param s        Whether they are signed.
 * @param scratch  `n` bytes of room for the remainder.
 * @param ok       Set to false if `b` is zero, where bnum panics, leaving `out`
 *                 unspecified; to true otherwise.
 * @return         Whether the quotient overflowed. */
static inline bool xtce_bn_overflowing_div(uint8_t* out, const uint8_t* a,
                                           const uint8_t* b, size_t n,
                                           bool s, uint8_t* scratch,
                                           bool* ok) {
        bool overflow = false;
        *ok = xtce_bn_div_rem_(out, scratch, a, b, n, s, &overflow);
        return overflow;
}

/** bnum's overflowing_rem: takes the remainder of dividing one integer by
 * another, rounding toward zero (MIN % -1 is 0, and overflowed).
 *
 * @param out      Where to put the remainder, `n` bytes; it may be `a`, but not
 *                 `b`.
 * @param a        The dividend.
 * @param b        The divisor.
 * @param n        The size of each integer in bytes, at least 1.
 * @param s        Whether they are signed.
 * @param scratch  `n` bytes of room for the quotient.
 * @param ok       Set to false if `b` is zero, where bnum panics, leaving `out`
 *                 unspecified; to true otherwise.
 * @return         Whether the quotient overflowed. */
static inline bool xtce_bn_overflowing_rem(uint8_t* out, const uint8_t* a,
                                           const uint8_t* b, size_t n,
                                           bool s, uint8_t* scratch,
                                           bool* ok) {
        bool overflow = false;
        memmove(scratch, a, n);
        *ok = xtce_bn_div_rem_(scratch, out, scratch, b, n, s, &overflow);
        return overflow;
}

/** Divides one integer by another, Euclidean style: the remainder is never
 * negative, and a = b * q + r.  Apart from `q` and `a`, none of the integers
 * may be the same.
 *
 * @param q         Where to put the quotient, `n` bytes; it may be `a`.
 * @param r         Where to put the remainder, `n` bytes.
 * @param a         The dividend.
 * @param b         The divisor.
 * @param n         The size of each integer in bytes, at least 1.
 * @param s         Whether they are signed.
 * @param overflow  If not NULL, set to whether the quotient overflowed.
 * @return          False, leaving `q` and `r` unspecified, if `b` is zero. */
static inline bool xtce_bn_div_rem_euclid_(uint8_t* q, uint8_t* r,
                                           const uint8_t* a,
                                           const uint8_t* b, size_t n,
                                           bool s, bool* overflow) {
        bool bn = xtce_bn_is_negative(b, n, s);
        if (!xtce_bn_div_rem_(q, r, a, b, n, s, overflow)) return false;
        if (xtce_bn_is_negative(r, n, s)) {
                /* r + |b|, and the quotient one further from zero. */
                if (bn)
                        (void)xtce_bn_sub_(r, r, b, n);
                else
                        (void)xtce_bn_add_(r, r, b, n, 0);
                if (bn) {
                        size_t i;
                        unsigned carry = 1;
                        for (i = 0; i < n && carry; ++i) {
                                unsigned x = (unsigned)q[i] + carry;
                                q[i] = (uint8_t)x;
                                carry = x >> 8;
                        }
                } else {
                        size_t i;
                        unsigned borrow = 1;
                        for (i = 0; i < n && borrow; ++i) {
                                unsigned x = (unsigned)q[i] - borrow;
                                q[i] = (uint8_t)x;
                                borrow = (x >> 8) & 1;
                        }
                }
        }
        return true;
}

/** Divides one integer by another, Euclidean style (see
 * xtce_bn_div_rem_euclid_).
 *
 * @param out      Where to put the quotient, `n` bytes; it may be `a`, but not
 *                 `b`.
 * @param a        The dividend.
 * @param b        The divisor.
 * @param n        The size of each integer in bytes, at least 1.
 * @param s        Whether they are signed.
 * @param scratch  `n` bytes of room for the remainder.
 * @return         False, leaving `out` unspecified, if `b` is zero or the
 *                 quotient overflows (MIN / -1). */
static inline bool xtce_bn_checked_div_euclid(uint8_t* out, const uint8_t* a,
                                              const uint8_t* b, size_t n,
                                              bool s, uint8_t* scratch) {
        bool overflow;
        return xtce_bn_div_rem_euclid_(out, scratch, a, b, n, s,
                                       &overflow) &&
               !overflow;
}

/** Takes the Euclidean remainder, never negative, of dividing one integer by
 * another (see xtce_bn_div_rem_euclid_).
 *
 * @param out      Where to put the remainder, `n` bytes; it may be `a`, but not
 *                 `b`.
 * @param a        The dividend.
 * @param b        The divisor.
 * @param n        The size of each integer in bytes, at least 1.
 * @param s        Whether they are signed.
 * @param scratch  `n` bytes of room for the quotient.
 * @return         False, leaving `out` unspecified, if `b` is zero or the
 *                 quotient overflows (MIN % -1). */
static inline bool xtce_bn_checked_rem_euclid(uint8_t* out, const uint8_t* a,
                                              const uint8_t* b, size_t n,
                                              bool s, uint8_t* scratch) {
        bool overflow;
        memmove(scratch, a, n);
        return xtce_bn_div_rem_euclid_(scratch, out, scratch, b, n, s,
                                       &overflow) &&
               !overflow;
}

/* --- wrapping, checked and saturating forms ------------------------------ */

static inline void xtce_bn_wrapping_add(uint8_t* out, const uint8_t* a,
                                        const uint8_t* b, size_t n) {
        (void)xtce_bn_add_(out, a, b, n, 0);
}

static inline void xtce_bn_wrapping_sub(uint8_t* out, const uint8_t* a,
                                        const uint8_t* b, size_t n) {
        (void)xtce_bn_sub_(out, a, b, n);
}

static inline void xtce_bn_wrapping_mul(uint8_t* out, const uint8_t* a,
                                        const uint8_t* b, size_t n, bool s) {
        (void)xtce_bn_overflowing_mul(out, a, b, n, s);
}

static inline void xtce_bn_wrapping_neg(uint8_t* out, const uint8_t* a,
                                        size_t n) {
        (void)xtce_bn_overflowing_neg(out, a, n, false);
}

static inline void xtce_bn_wrapping_abs(uint8_t* out, const uint8_t* a,
                                        size_t n, bool s) {
        (void)xtce_bn_overflowing_abs(out, a, n, s);
}

static inline bool xtce_bn_checked_add(uint8_t* out, const uint8_t* a,
                                       const uint8_t* b, size_t n, bool s) {
        return !xtce_bn_overflowing_add(out, a, b, n, s);
}

static inline bool xtce_bn_checked_sub(uint8_t* out, const uint8_t* a,
                                       const uint8_t* b, size_t n, bool s) {
        return !xtce_bn_overflowing_sub(out, a, b, n, s);
}

static inline bool xtce_bn_checked_mul(uint8_t* out, const uint8_t* a,
                                       const uint8_t* b, size_t n, bool s) {
        return !xtce_bn_overflowing_mul(out, a, b, n, s);
}

static inline bool xtce_bn_checked_neg(uint8_t* out, const uint8_t* a,
                                       size_t n, bool s) {
        return !xtce_bn_overflowing_neg(out, a, n, s);
}

static inline bool xtce_bn_checked_abs(uint8_t* out, const uint8_t* a,
                                       size_t n, bool s) {
        return !xtce_bn_overflowing_abs(out, a, n, s);
}

/** Sets an integer to the value an overflowing operation saturates to.
 *
 * @param out       Where to put it, `n` bytes.
 * @param n         Its size in bytes, at least 1.
 * @param s         Whether it is signed.
 * @param negative  Whether the exact result is negative: MIN if so, else MAX.
 */
static inline void xtce_bn_saturate_(uint8_t* out, size_t n, bool s,
                                     bool negative) {
        if (negative)
                xtce_bn_min(out, n, s);
        else
                xtce_bn_max(out, n, s);
}

static inline void xtce_bn_saturating_add(uint8_t* out, const uint8_t* a,
                                          const uint8_t* b, size_t n,
                                          bool s) {
        bool an = xtce_bn_is_negative(a, n, s);
        if (xtce_bn_overflowing_add(out, a, b, n, s))
                xtce_bn_saturate_(out, n, s, an);
}

static inline void xtce_bn_saturating_sub(uint8_t* out, const uint8_t* a,
                                          const uint8_t* b, size_t n,
                                          bool s) {
        bool an = xtce_bn_is_negative(a, n, s);
        if (xtce_bn_overflowing_sub(out, a, b, n, s))
                xtce_bn_saturate_(out, n, s, !s || an);
}

/** Multiplies two integers, saturating at MIN or MAX on overflow.
 *
 * @param out  Where to put the product, `n` bytes; it must not be `a` or `b`.
 * @param a    The first operand.
 * @param b    The second operand.
 * @param n    The size of each integer in bytes, at least 1.
 * @param s    Whether they are signed. */
static inline void xtce_bn_saturating_mul(uint8_t* out, const uint8_t* a,
                                          const uint8_t* b, size_t n,
                                          bool s) {
        bool neg = xtce_bn_is_negative(a, n, s) !=
                   xtce_bn_is_negative(b, n, s);
        if (xtce_bn_overflowing_mul(out, a, b, n, s))
                xtce_bn_saturate_(out, n, s, neg);
}

static inline void xtce_bn_saturating_neg(uint8_t* out, const uint8_t* a,
                                          size_t n, bool s) {
        bool an = xtce_bn_is_negative(a, n, s);
        if (xtce_bn_overflowing_neg(out, a, n, s))
                xtce_bn_saturate_(out, n, s, !an);
}

static inline void xtce_bn_saturating_abs(uint8_t* out, const uint8_t* a,
                                          size_t n, bool s) {
        if (xtce_bn_overflowing_abs(out, a, n, s))
                xtce_bn_max(out, n, s);
}

/* --- small operands ------------------------------------------------------ */

/** Computes out = a * m + add, as unsigned.
 *
 * @param out  Where to put the result, `n` bytes; it may be `a`.
 * @param a    The integer, least significant byte first.
 * @param n    Its size in bytes, at least 1.
 * @param m    The multiplier, below 2^24.
 * @param add  The addend, below 2^24.
 * @return     The carry out: what is left above the `n` bytes. */
static inline uint32_t xtce_bn_mul_small(uint8_t* out, const uint8_t* a,
                                         size_t n, uint32_t m, uint32_t add) {
        uint64_t c = add;
        size_t i;
        for (i = 0; i < n; ++i) {
                c += (uint64_t)a[i] * m;
                out[i] = (uint8_t)c;
                c >>= 8;
        }
        return (uint32_t)c;
}

/** Computes out = a / d, as unsigned.
 *
 * @param out  Where to put the result, `n` bytes; it may be `a`.
 * @param a    The integer, least significant byte first.
 * @param n    Its size in bytes, at least 1.
 * @param d    The divisor, from 1 to 2^24.
 * @return     The remainder. */
static inline uint32_t xtce_bn_div_small(uint8_t* out, const uint8_t* a,
                                         size_t n, uint32_t d) {
        uint64_t r = 0;
        size_t i = n;
        while (i-- > 0) {
                r = (r << 8) | a[i];
                out[i] = (uint8_t)(r / d);
                r %= d;
        }
        return (uint32_t)r;
}

/* --- text ---------------------------------------------------------------- */

/** Writes an integer in a radix, with lowercase digits after a '-' if negative,
 * as bnum's to_str_radix does.
 *
 * @param a        The integer, least significant byte first.
 * @param n        Its size in bytes, at least 1.
 * @param s        Whether it is signed.
 * @param radix    The radix, 2 to 36.
 * @param scratch  `n` bytes of room to work in; `a` is left alone.
 * @param buf      Where to write the text, NUL-terminated.
 * @param cap      The size of `buf` in chars.
 * @return         The text's length without the NUL; if that is `cap` or more,
 *                 `buf` was too small and holds only part of it. */
static inline size_t xtce_bn_to_str_radix(const uint8_t* a, size_t n, bool s,
                                          unsigned radix, uint8_t* scratch,
                                          char* buf, size_t cap) {
        static const char digits[] = "0123456789abcdefghijklmnopqrstuvwxyz";
        bool neg = xtce_bn_is_negative(a, n, s);
        size_t len = 0, i;
        xtce_bn_unsigned_abs(scratch, a, n, s);
        /* The digits least significant first, then turned round. */
        do {
                uint32_t d = xtce_bn_div_small(scratch, scratch, n, radix);
                if (len < cap) buf[len] = digits[d];
                ++len;
        } while (!xtce_bn_is_zero(scratch, n));
        if (neg) {
                if (len < cap) buf[len] = '-';
                ++len;
        }
        if (len < cap) {
                for (i = 0; i < len / 2; ++i) {
                        char t = buf[i];
                        buf[i] = buf[len - 1 - i];
                        buf[len - 1 - i] = t;
                }
                buf[len] = '\0';
        } else if (cap) {
                buf[cap - 1] = '\0';
        }
        return len;
}

/** Parses an integer: an optional '+' (or, if signed, '-'), then digits in a
 * radix of either case, as bnum's from_str_radix does.
 *
 * @param out    Where to put the integer, `n` bytes; unspecified on failure.
 * @param n      The integer's size in bytes, at least 1.
 * @param s      Whether it is signed.
 * @param str    The text; it needn't be NUL-terminated.
 * @param len    Its length in chars.
 * @param radix  The radix, 2 to 36.
 * @return       XTCE_BN_OK, or why it failed: XTCE_BN_EMPTY,
 *               XTCE_BN_INVALID_DIGIT, XTCE_BN_POS_OVERFLOW or
 *               XTCE_BN_NEG_OVERFLOW. */
static inline int xtce_bn_from_str_radix(uint8_t* out, size_t n, bool s,
                                         const char* str, size_t len,
                                         unsigned radix) {
        bool neg = false;
        size_t i = 0;
        if (len && (str[0] == '+' || (str[0] == '-' && s))) {
                neg = str[0] == '-';
                i = 1;
        }
        if (i == len) return len ? XTCE_BN_INVALID_DIGIT : XTCE_BN_EMPTY;
        memset(out, 0, n);
        for (; i < len; ++i) {
                char c = str[i];
                unsigned d = c >= '0' && c <= '9'   ? (unsigned)(c - '0')
                             : c >= 'a' && c <= 'z' ? (unsigned)(c - 'a') + 10
                             : c >= 'A' && c <= 'Z' ? (unsigned)(c - 'A') + 10
                                                    : 36;
                if (d >= radix) return XTCE_BN_INVALID_DIGIT;
                /* `out` holds the magnitude, as unsigned.  As in bnum,
                 * overflowing that stops at once, before any invalid digit
                 * after it, but the range of a signed integer is checked
                 * only once every digit is. */
                if (xtce_bn_mul_small(out, out, n, radix, d))
                        return neg ? XTCE_BN_NEG_OVERFLOW
                                   : XTCE_BN_POS_OVERFLOW;
        }
        if (s && !xtce_bn_mag_fits_(out, n, neg))
                return neg ? XTCE_BN_NEG_OVERFLOW : XTCE_BN_POS_OVERFLOW;
        if (neg) xtce_bn_negate_(out, n);
        return XTCE_BN_OK;
}

/* --- floats -------------------------------------------------------------- */

/* Bit `k` of |a|, for `a` of `n` bytes, negative when `neg`, whose lowest
 * set bit is bit `low`: past it, a negative value's magnitude is its bits
 * inverted. */
static inline bool xtce_bn_mag_bit_(const uint8_t* a, size_t n, bool neg,
                                    unsigned low, unsigned k) {
        if (!neg || k < low) return xtce_bn_bit(a, n, k);
        return k == low || !xtce_bn_bit(a, n, k);
}

/** Converts an integer to a double, as bnum's `as` converts: to the nearest,
 * ties to even, and infinite past the largest double; a negative value is its
 * magnitude's negated.
 *
 * @param a  The integer, least significant byte first.
 * @param n  Its size in bytes, at least 1.
 * @param s  Whether it is signed.
 * @return   The double. */
static inline double xtce_bn_to_f64(const uint8_t* a, size_t n, bool s) {
        bool neg = xtce_bn_is_negative(a, n, s);
        unsigned low = xtce_bn_trailing_zeros(a, n), w, shift, i;
        uint64_t mant = 0, bits;
        double d;
        if (xtce_bn_is_zero(a, n)) return 0.0;
        /* The magnitude's width: a negative value's is its top bit's, less
         * one when all the bits above its lowest are set (a power of two). */
        for (w = (unsigned)(8 * n); w > 0; --w)
                if (xtce_bn_mag_bit_(a, n, neg, low, w - 1)) break;
        shift = w > 53 ? w - 53 : 0;
        for (i = w; i-- > shift;)
                mant = (mant << 1) | xtce_bn_mag_bit_(a, n, neg, low, i);
        if (w - 1 < 1024 && shift &&
            xtce_bn_mag_bit_(a, n, neg, low, shift - 1) &&
            ((mant & 1) || low != shift - 1)) {
                /* At least a half, and not a tie with an even mantissa. */
                if (++mant >> 53) {
                        mant >>= 1;
                        ++w;
                }
        }
        if (w - 1 >= 1024) {
                bits = UINT64_C(0x7ff0000000000000);
        } else {
                mant <<= 53 - (w < 53 ? w : 53);
                bits = ((uint64_t)(w - 1 + 1023) << 52) |
                       (mant & ((UINT64_C(1) << 52) - 1));
        }
        if (neg) bits |= UINT64_C(1) << 63;
        memcpy(&d, &bits, sizeof d);
        return d;
}

/** Converts a double to an unsigned integer, as bnum casts it: toward zero, but
 * [1/2, 1) other than 1/2 is 1; a negative value is 0, a NaN 0 and anything too
 * large MAX.
 *
 * @param out  Where to put the integer, `n` bytes.
 * @param n    Its size in bytes, at least 1.
 * @param x    The double. */
static inline void xtce_bn_uint_from_f64_(uint8_t* out, size_t n, double x) {
        uint64_t bits, mant;
        int exp;
        unsigned i;
        memcpy(&bits, &x, sizeof bits);
        exp = (int)((bits >> 52) & 0x7ff);
        mant = bits & ((UINT64_C(1) << 52) - 1);
        memset(out, 0, n);
        if (exp == 0x7ff && mant) return; /* NaN */
        if (exp == 0 && !mant) return;    /* zero */
        if (bits >> 63) return;           /* negative */
        if (exp == 0x7ff) {               /* infinite */
                xtce_bn_max(out, n, false);
                return;
        }
        if (exp == 0) return; /* subnormal: less than a half */
        mant |= UINT64_C(1) << 52;
        exp -= 1023;
        if (exp < -1) return;
        if (exp == -1) {
                /* In [1/2, 1): exactly a half is 0 (ties to even). */
                if (mant != UINT64_C(1) << 52) out[0] = 1;
                return;
        }
        if ((unsigned)exp >= 8 * n) {
                xtce_bn_max(out, n, false);
                return;
        }
        if (exp <= 52) {
                mant >>= 52 - exp;
                exp = 0;
        } else {
                exp -= 52;
        }
        for (i = 0; i < 64 && (unsigned)exp + i < 8 * n; ++i)
                if ((mant >> i) & 1) xtce_bn_set_bit(out, n, exp + i, true);
}

/** Converts a double to an integer, as bnum's `as` converts: toward zero (see
 * xtce_bn_uint_from_f64_), saturating at MIN and MAX, and a NaN as 0.
 *
 * @param out  Where to put the integer, `n` bytes.
 * @param n    Its size in bytes, at least 1.
 * @param s    Whether it is signed.
 * @param x    The double. */
static inline void xtce_bn_from_f64(uint8_t* out, size_t n, bool s,
                                    double x) {
        uint64_t bits;
        memcpy(&bits, &x, sizeof bits);
        if (!s) {
                xtce_bn_uint_from_f64_(out, n, x);
        } else if (bits >> 63) {
                xtce_bn_uint_from_f64_(out, n, -x);
                /* MIN's magnitude or more is MIN. */
                if (xtce_bn_bit(out, n, (unsigned)(8 * n - 1)))
                        xtce_bn_min(out, n, true);
                else
                        xtce_bn_wrapping_neg(out, out, n);
        } else {
                xtce_bn_uint_from_f64_(out, n, x);
                if (xtce_bn_bit(out, n, (unsigned)(8 * n - 1)))
                        xtce_bn_max(out, n, true);
        }
}

#ifdef __cplusplus
}
#endif

#endif /* XTCE_BNUM_H */
