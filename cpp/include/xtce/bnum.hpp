// Fixed-width integers of any number of bytes, for C++20.
//
// xtce::bnum::Integer<S, N> is an integer of N bytes, signed if S, like
// Integer<S, N> in the Rust crate bnum (https://docs.rs/bnum), whose methods
// it has under the same names.  It is built on the C functions of
// xtce/bnum.h and holds the same bytes: least significant first, two's
// complement when signed.
//
// The operators wrap on overflow, as bnum's do in its Wrap overflow mode.
// Where bnum panics (dividing by zero, or a strict_ method overflowing),
// Integer calls XTCE_BNUM_PANIC, which aborts unless it is defined before
// this header is included.  Nothing is allocated except by to_string.
#ifndef XTCE_BNUM_HPP
#define XTCE_BNUM_HPP

#include <array>
#include <cstdint>
#include <cstdlib>
#include <optional>
#include <ostream>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>

#include "bnum.h"

#ifndef XTCE_BNUM_PANIC
#define XTCE_BNUM_PANIC(what) std::abort()
#endif

namespace xtce::bnum {

// Why from_str_radix failed, as Rust's IntErrorKind gives it.
enum class IntErrorKind {
        Empty = XTCE_BN_EMPTY,
        InvalidDigit = XTCE_BN_INVALID_DIGIT,
        PosOverflow = XTCE_BN_POS_OVERFLOW,
        NegOverflow = XTCE_BN_NEG_OVERFLOW,
};

template <bool S, std::size_t N>
class Integer {
        static_assert(N >= 1, "an Integer has at least one byte");

       public:
        using Bytes = std::array<std::uint8_t, N>;

        static constexpr bool SIGNED = S;
        static constexpr std::size_t BYTES = N;
        static constexpr std::uint32_t BITS = 8 * N;

        // Zero.
        constexpr Integer() : b_{} {}

        // A primitive integer, converted as Rust's `as` converts it.
        template <class T,
                  std::enable_if_t<std::is_integral_v<T> &&
                                           !std::is_same_v<T, bool>,
                                   int> = 0>
        Integer(T v) {  // NOLINT: implicit, like Rust's From
                if constexpr (std::is_signed_v<T>)
                        xtce_bn_from_i64(b_.data(), N,
                                         static_cast<std::int64_t>(v));
                else
                        xtce_bn_from_u64(b_.data(), N,
                                         static_cast<std::uint64_t>(v));
        }

        static Integer zero() { return Integer(); }
        static Integer one() { return Integer(1); }
        static Integer min_value() {
                Integer r;
                xtce_bn_min(r.p(), N, S);
                return r;
        }
        static Integer max_value() {
                Integer r;
                xtce_bn_max(r.p(), N, S);
                return r;
        }

        // --- bytes

        static Integer from_le_bytes(const Bytes& bytes) {
                Integer r;
                r.b_ = bytes;
                return r;
        }
        static Integer from_be_bytes(const Bytes& bytes) {
                Integer r;
                xtce_bn_swap_bytes(r.p(), bytes.data(), N);
                return r;
        }
        Bytes to_le_bytes() const { return b_; }
        Bytes to_be_bytes() const {
                Bytes r;
                xtce_bn_to_be_bytes(r.data(), p(), N);
                return r;
        }
        // The bytes themselves, least significant first.
        const Bytes& as_bytes() const { return b_; }
        Bytes& as_bytes_mut() { return b_; }

        static std::optional<Integer> from_le_slice(const std::uint8_t* s,
                                                    std::size_t len) {
                Integer r;
                if (!xtce_bn_from_le_slice(r.p(), N, S, s, len))
                        return std::nullopt;
                return r;
        }
        static std::optional<Integer> from_be_slice(const std::uint8_t* s,
                                                    std::size_t len) {
                Integer r;
                if (!xtce_bn_from_be_slice(r.p(), N, S, s, len))
                        return std::nullopt;
                return r;
        }

        // --- casts

        // This integer as another, as Rust's `as` converts.
        template <class T>
        T as() const {
                if constexpr (std::is_same_v<T, double>) {
                        return xtce_bn_to_f64(p(), N, S);
                } else if constexpr (std::is_integral_v<T>) {
                        std::uint8_t out[sizeof(T)];
                        xtce_bn_cast(out, sizeof(T), p(), N, S);
                        std::uint64_t u = 0;
                        for (std::size_t i = sizeof(T); i-- > 0;)
                                u = (u << 8) | out[i];
                        return static_cast<T>(u);
                } else {
                        T r;
                        xtce_bn_cast(r.as_bytes_mut().data(), T::BYTES, p(),
                                     N, S);
                        return r;
                }
        }
        // A double as this type, as bnum's `cast_from` converts: toward
        // zero, saturating, and a NaN as 0.
        static Integer from_f64(double x) {
                Integer r;
                xtce_bn_from_f64(r.p(), N, S, x);
                return r;
        }
        Integer<true, N> cast_signed() const {
                return Integer<true, N>::from_le_bytes(b_);
        }
        Integer<false, N> cast_unsigned() const {
                return Integer<false, N>::from_le_bytes(b_);
        }

        // --- predicates and bits

        bool is_zero() const { return xtce_bn_is_zero(p(), N); }
        bool is_one() const { return xtce_bn_is_one(p(), N); }
        bool is_negative() const { return xtce_bn_is_negative(p(), N, S); }
        bool is_positive() const { return xtce_bn_is_positive(p(), N, S); }
        bool is_power_of_two() const {
                return xtce_bn_is_power_of_two(p(), N);
        }
        Integer signum() const { return Integer(xtce_bn_signum(p(), N, S)); }
        bool bit(std::uint32_t k) const { return xtce_bn_bit(p(), N, k); }
        void set_bit(std::uint32_t k, bool v) {
                xtce_bn_set_bit(p(), N, k, v);
        }
        std::uint32_t count_ones() const {
                return xtce_bn_count_ones(p(), N);
        }
        std::uint32_t count_zeros() const {
                return xtce_bn_count_zeros(p(), N);
        }
        std::uint32_t leading_zeros() const {
                return xtce_bn_leading_zeros(p(), N);
        }
        std::uint32_t leading_ones() const {
                return xtce_bn_leading_ones(p(), N);
        }
        std::uint32_t trailing_zeros() const {
                return xtce_bn_trailing_zeros(p(), N);
        }
        std::uint32_t trailing_ones() const {
                return xtce_bn_trailing_ones(p(), N);
        }
        std::uint32_t bit_width() const { return xtce_bn_bit_width(p(), N); }
        Integer swap_bytes() const {
                Integer r;
                xtce_bn_swap_bytes(r.p(), p(), N);
                return r;
        }
        Integer reverse_bits() const {
                Integer r;
                xtce_bn_reverse_bits(r.p(), p(), N);
                return r;
        }
        Integer rotate_left(std::uint32_t k) const {
                Integer r;
                xtce_bn_rotate_left(r.p(), p(), N, k);
                return r;
        }
        Integer rotate_right(std::uint32_t k) const {
                Integer r;
                xtce_bn_rotate_right(r.p(), p(), N, k);
                return r;
        }

        // --- comparison

        int cmp(const Integer& o) const {
                return xtce_bn_cmp(p(), o.p(), N, S);
        }
        friend bool operator==(const Integer& a, const Integer& b) {
                return a.b_ == b.b_;
        }
        friend bool operator!=(const Integer& a, const Integer& b) {
                return !(a == b);
        }
        friend bool operator<(const Integer& a, const Integer& b) {
                return a.cmp(b) < 0;
        }
        friend bool operator<=(const Integer& a, const Integer& b) {
                return a.cmp(b) <= 0;
        }
        friend bool operator>(const Integer& a, const Integer& b) {
                return a.cmp(b) > 0;
        }
        friend bool operator>=(const Integer& a, const Integer& b) {
                return a.cmp(b) >= 0;
        }
        Integer min(const Integer& o) const { return *this <= o ? *this : o; }
        Integer max(const Integer& o) const { return *this >= o ? *this : o; }
        Integer clamp(const Integer& lo, const Integer& hi) const {
                return max(lo).min(hi);
        }

        // --- arithmetic: overflowing, then the rest from it

        std::pair<Integer, bool> overflowing_add(const Integer& o) const {
                Integer r;
                bool v = xtce_bn_overflowing_add(r.p(), p(), o.p(), N, S);
                return {r, v};
        }
        std::pair<Integer, bool> overflowing_sub(const Integer& o) const {
                Integer r;
                bool v = xtce_bn_overflowing_sub(r.p(), p(), o.p(), N, S);
                return {r, v};
        }
        std::pair<Integer, bool> overflowing_mul(const Integer& o) const {
                Integer r;
                bool v = xtce_bn_overflowing_mul(r.p(), p(), o.p(), N, S);
                return {r, v};
        }
        std::pair<Integer, bool> overflowing_neg() const {
                Integer r;
                bool v = xtce_bn_overflowing_neg(r.p(), p(), N, S);
                return {r, v};
        }
        std::pair<Integer, bool> overflowing_abs() const {
                Integer r;
                bool v = xtce_bn_overflowing_abs(r.p(), p(), N, S);
                return {r, v};
        }
        std::pair<Integer, bool> overflowing_shl(std::uint32_t k) const {
                Integer r;
                bool v = xtce_bn_overflowing_shl(r.p(), p(), N, k);
                return {r, v};
        }
        std::pair<Integer, bool> overflowing_shr(std::uint32_t k) const {
                Integer r;
                bool v = xtce_bn_overflowing_shr(r.p(), p(), N, S, k);
                return {r, v};
        }
        // Division by zero panics, as in bnum.
        std::pair<Integer, bool> overflowing_div(const Integer& o) const {
                auto [q, r, v] = div_rem_(o, false);
                (void)r;
                return {q, v};
        }
        std::pair<Integer, bool> overflowing_rem(const Integer& o) const {
                auto [q, r, v] = div_rem_(o, false);
                (void)q;
                // MIN % -1 is 0, which wrapping gives.
                return {r, v};
        }
        std::pair<Integer, bool> overflowing_div_euclid(
                const Integer& o) const {
                auto [q, r, v] = div_rem_(o, true);
                (void)r;
                return {q, v};
        }
        std::pair<Integer, bool> overflowing_rem_euclid(
                const Integer& o) const {
                auto [q, r, v] = div_rem_(o, true);
                (void)q;
                return {r, v};
        }
        std::pair<Integer, bool> overflowing_pow(std::uint32_t e) const {
                Integer r = one(), base = *this;
                bool v = false;
                while (e) {
                        if (e & 1) {
                                auto [x, o] = r.overflowing_mul(base);
                                r = x;
                                v = v || o;
                        }
                        e >>= 1;
                        if (e) {
                                auto [x, o] = base.overflowing_mul(base);
                                base = x;
                                v = v || o;
                        }
                }
                return {r, v};
        }

#define XTCE_BNUM_FORMS_(name, ...)                                         \
        Integer wrapping_##name(XTCE_BNUM_PARAMS_##name) const {            \
                return overflowing_##name(__VA_ARGS__).first;                \
        }                                                                   \
        std::optional<Integer> checked_##name(XTCE_BNUM_PARAMS_##name)     \
                const {                                                     \
                auto [r, v] = overflowing_##name(__VA_ARGS__);               \
                if (v) return std::nullopt;                                  \
                return r;                                                   \
        }                                                                   \
        Integer strict_##name(XTCE_BNUM_PARAMS_##name) const {              \
                auto [r, v] = overflowing_##name(__VA_ARGS__);               \
                if (v) XTCE_BNUM_PANIC(#name " overflowed");                \
                return r;                                                   \
        }
#define XTCE_BNUM_PARAMS_add const Integer &o
#define XTCE_BNUM_PARAMS_sub const Integer &o
#define XTCE_BNUM_PARAMS_mul const Integer &o
#define XTCE_BNUM_PARAMS_div const Integer &o
#define XTCE_BNUM_PARAMS_rem const Integer &o
#define XTCE_BNUM_PARAMS_div_euclid const Integer &o
#define XTCE_BNUM_PARAMS_rem_euclid const Integer &o
#define XTCE_BNUM_PARAMS_neg
#define XTCE_BNUM_PARAMS_abs
#define XTCE_BNUM_PARAMS_shl std::uint32_t o
#define XTCE_BNUM_PARAMS_shr std::uint32_t o
#define XTCE_BNUM_PARAMS_pow std::uint32_t o
        XTCE_BNUM_FORMS_(add, o)
        XTCE_BNUM_FORMS_(sub, o)
        XTCE_BNUM_FORMS_(mul, o)
        XTCE_BNUM_FORMS_(neg)
        XTCE_BNUM_FORMS_(abs)
        XTCE_BNUM_FORMS_(shl, o)
        XTCE_BNUM_FORMS_(shr, o)
        XTCE_BNUM_FORMS_(pow, o)
#undef XTCE_BNUM_FORMS_

        // Division by zero gives no value from checked_, rather than
        // panicking.
#define XTCE_BNUM_DIV_FORMS_(name)                                          \
        Integer wrapping_##name(const Integer& o) const {                   \
                return overflowing_##name(o).first;                          \
        }                                                                   \
        std::optional<Integer> checked_##name(const Integer& o) const {     \
                if (o.is_zero()) return std::nullopt;                        \
                auto [r, v] = overflowing_##name(o);                         \
                if (v) return std::nullopt;                                  \
                return r;                                                   \
        }                                                                   \
        Integer strict_##name(const Integer& o) const {                     \
                auto [r, v] = overflowing_##name(o);                         \
                if (v) XTCE_BNUM_PANIC(#name " overflowed");                \
                return r;                                                   \
        }
        XTCE_BNUM_DIV_FORMS_(div)
        XTCE_BNUM_DIV_FORMS_(rem)
        XTCE_BNUM_DIV_FORMS_(div_euclid)
        XTCE_BNUM_DIV_FORMS_(rem_euclid)
#undef XTCE_BNUM_DIV_FORMS_
#undef XTCE_BNUM_PARAMS_add
#undef XTCE_BNUM_PARAMS_sub
#undef XTCE_BNUM_PARAMS_mul
#undef XTCE_BNUM_PARAMS_div
#undef XTCE_BNUM_PARAMS_rem
#undef XTCE_BNUM_PARAMS_div_euclid
#undef XTCE_BNUM_PARAMS_rem_euclid
#undef XTCE_BNUM_PARAMS_neg
#undef XTCE_BNUM_PARAMS_abs
#undef XTCE_BNUM_PARAMS_shl
#undef XTCE_BNUM_PARAMS_shr
#undef XTCE_BNUM_PARAMS_pow

        Integer saturating_add(const Integer& o) const {
                Integer r;
                xtce_bn_saturating_add(r.p(), p(), o.p(), N, S);
                return r;
        }
        Integer saturating_sub(const Integer& o) const {
                Integer r;
                xtce_bn_saturating_sub(r.p(), p(), o.p(), N, S);
                return r;
        }
        Integer saturating_mul(const Integer& o) const {
                Integer r;
                xtce_bn_saturating_mul(r.p(), p(), o.p(), N, S);
                return r;
        }
        Integer saturating_neg() const {
                Integer r;
                xtce_bn_saturating_neg(r.p(), p(), N, S);
                return r;
        }
        Integer saturating_abs() const {
                Integer r;
                xtce_bn_saturating_abs(r.p(), p(), N, S);
                return r;
        }
        Integer saturating_pow(std::uint32_t e) const {
                auto [r, v] = overflowing_pow(e);
                if (!v) return r;
                // Negative only for a negative base and an odd power.
                return is_negative() && (e & 1) ? min_value() : max_value();
        }

        Integer unbounded_shl(std::uint32_t k) const {
                Integer r;
                xtce_bn_unbounded_shl(r.p(), p(), N, k);
                return r;
        }
        Integer unbounded_shr(std::uint32_t k) const {
                Integer r;
                xtce_bn_unbounded_shr(r.p(), p(), N, S, k);
                return r;
        }

        Integer<false, N> unsigned_abs() const {
                Integer<false, N> r;
                xtce_bn_unsigned_abs(r.as_bytes_mut().data(), p(), N, S);
                return r;
        }
        Integer<false, N> abs_diff(const Integer& o) const {
                Integer<false, N> r;
                xtce_bn_abs_diff(r.as_bytes_mut().data(), p(), o.p(), N, S);
                return r;
        }
        Integer pow(std::uint32_t e) const { return wrapping_pow(e); }

        // The integer square root (rounded down); panics if negative.
        Integer isqrt() const {
                if (is_negative()) XTCE_BNUM_PANIC("isqrt of a negative");
                // Newton's method from a power of two at least the root.
                if (is_zero()) return *this;
                Integer x = one().unbounded_shl((bit_width() + 1) / 2);
                for (;;) {
                        Integer y = (x + *this / x).unbounded_shr(1);
                        if (y >= x) return x;
                        x = y;
                }
        }
        // Logarithms rounded down: none unless positive (and base >= 2).
        std::optional<std::uint32_t> checked_ilog2() const {
                if (!is_positive()) return std::nullopt;
                return bit_width() - 1;
        }
        std::optional<std::uint32_t> checked_ilog(const Integer& base) const {
                if (!is_positive() || base <= Integer(1)) return std::nullopt;
                std::uint32_t n = 0;
                Integer x = *this;
                while (x >= base) {
                        x = x / base;
                        ++n;
                }
                return n;
        }
        std::optional<std::uint32_t> checked_ilog10() const {
                return checked_ilog(Integer(10));
        }

        // --- operators: wrapping, and panicking on division by zero

        friend Integer operator+(const Integer& a, const Integer& b) {
                return a.wrapping_add(b);
        }
        friend Integer operator-(const Integer& a, const Integer& b) {
                return a.wrapping_sub(b);
        }
        friend Integer operator*(const Integer& a, const Integer& b) {
                return a.wrapping_mul(b);
        }
        friend Integer operator/(const Integer& a, const Integer& b) {
                return a.wrapping_div(b);
        }
        friend Integer operator%(const Integer& a, const Integer& b) {
                return a.wrapping_rem(b);
        }
        friend Integer operator&(const Integer& a, const Integer& b) {
                Integer r;
                xtce_bn_bitand(r.p(), a.p(), b.p(), N);
                return r;
        }
        friend Integer operator|(const Integer& a, const Integer& b) {
                Integer r;
                xtce_bn_bitor(r.p(), a.p(), b.p(), N);
                return r;
        }
        friend Integer operator^(const Integer& a, const Integer& b) {
                Integer r;
                xtce_bn_bitxor(r.p(), a.p(), b.p(), N);
                return r;
        }
        friend Integer operator<<(const Integer& a, std::uint32_t k) {
                return a.wrapping_shl(k);
        }
        friend Integer operator>>(const Integer& a, std::uint32_t k) {
                return a.wrapping_shr(k);
        }
        Integer operator~() const {
                Integer r;
                xtce_bn_not(r.p(), p(), N);
                return r;
        }
        Integer operator-() const { return wrapping_neg(); }
        Integer operator+() const { return *this; }
        Integer& operator+=(const Integer& o) { return *this = *this + o; }
        Integer& operator-=(const Integer& o) { return *this = *this - o; }
        Integer& operator*=(const Integer& o) { return *this = *this * o; }
        Integer& operator/=(const Integer& o) { return *this = *this / o; }
        Integer& operator%=(const Integer& o) { return *this = *this % o; }
        Integer& operator&=(const Integer& o) { return *this = *this & o; }
        Integer& operator|=(const Integer& o) { return *this = *this | o; }
        Integer& operator^=(const Integer& o) { return *this = *this ^ o; }
        Integer& operator<<=(std::uint32_t k) { return *this = *this << k; }
        Integer& operator>>=(std::uint32_t k) { return *this = *this >> k; }

        // --- text

        // The digits in `radix` (2 to 36), lowercase, after '-' if negative.
        std::string to_str_radix(unsigned radix) const {
                std::uint8_t scratch[N];
                // Binary takes the most digits: one a bit, and a sign.
                std::string s(8 * N + 1, '\0');
                std::size_t len = xtce_bn_to_str_radix(
                        p(), N, S, radix, scratch, s.data(), s.size() + 1);
                s.resize(len);
                return s;
        }
        std::string to_string() const { return to_str_radix(10); }

        // The value of `s` in `radix`, or, in *err if given, why not.
        static std::optional<Integer> from_str_radix(
                std::string_view s, unsigned radix,
                IntErrorKind* err = nullptr) {
                Integer r;
                int rc = xtce_bn_from_str_radix(r.p(), N, S, s.data(),
                                                s.size(), radix);
                if (!rc) return r;
                if (err) *err = static_cast<IntErrorKind>(rc);
                return std::nullopt;
        }

        friend std::ostream& operator<<(std::ostream& os, const Integer& v) {
                return os << v.to_string();
        }

       private:
        std::uint8_t* p() { return b_.data(); }
        const std::uint8_t* p() const { return b_.data(); }

        struct DivRem {
                Integer q, r;
                bool overflow;
        };
        DivRem div_rem_(const Integer& o, bool euclid) const {
                DivRem d{*this, Integer(), false};
                bool ok = euclid ? xtce_bn_div_rem_euclid_(
                                           d.q.p(), d.r.p(), d.q.p(), o.p(),
                                           N, S, &d.overflow)
                                 : xtce_bn_div_rem_(d.q.p(), d.r.p(),
                                                    d.q.p(), o.p(), N, S,
                                                    &d.overflow);
                if (!ok) XTCE_BNUM_PANIC("division by zero");
                return d;
        }

        Bytes b_;
};

template <std::size_t N>
using Uint = Integer<false, N>;
template <std::size_t N>
using Int = Integer<true, N>;

}  // namespace xtce::bnum

#endif  // XTCE_BNUM_HPP
