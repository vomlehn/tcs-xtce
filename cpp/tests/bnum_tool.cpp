// Computes integer operations with xtce::bnum::Integer, one case per line
// of standard input, as tests/bnum_check.sh asks;
// rust/examples/bnum_tool.rs describes the cases and what it prints, and
// does the same with bnum.
#include <cstdio>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>
#include <xtce/bnum.hpp>

using xtce::bnum::Integer;
using xtce::bnum::IntErrorKind;

namespace {

template <class T>
std::string opt(const std::optional<T>& o) {
        if (!o) return "none";
        if constexpr (std::is_integral_v<T>)
                return std::to_string(*o);
        else
                return o->to_string();
}

template <class T>
std::string ovf(const std::pair<T, bool>& p) {
        return p.first.to_string() + (p.second ? " true" : " false");
}

std::string hex(const std::uint8_t* b, std::size_t n) {
        std::string s;
        char x[3];
        for (std::size_t i = 0; i < n; ++i) {
                std::snprintf(x, sizeof x, "%02x", b[i]);
                s += x;
        }
        return s;
}

std::vector<std::uint8_t> unhex(const std::string& t) {
        std::vector<std::uint8_t> out;
        for (std::size_t i = 0; i + 1 < t.size(); i += 2)
                out.push_back(static_cast<std::uint8_t>(
                        std::stoul(t.substr(i, 2), nullptr, 16)));
        return out;
}

const char* kind(IntErrorKind k) {
        switch (k) {
                case IntErrorKind::Empty:
                        return "Empty";
                case IntErrorKind::InvalidDigit:
                        return "InvalidDigit";
                case IntErrorKind::PosOverflow:
                        return "PosOverflow";
                case IntErrorKind::NegOverflow:
                        return "NegOverflow";
        }
        return "?";
}

template <bool S, std::size_t N>
std::string cast(const Integer<S, N>& x, const std::string& to);

template <bool S, std::size_t N>
std::string run(const std::string& op, const std::vector<std::string>& a) {
        using T = Integer<S, N>;
        auto p = [](const std::string& s) { return *T::from_str_radix(s, 10); };
        auto x = [&] { return p(a[0]); };
        auto y = [&] { return p(a[1]); };
        auto k = [&] { return static_cast<std::uint32_t>(std::stoul(a[1])); };
        if (op == "from_str_radix") {
                IntErrorKind e{};
                auto r = T::from_str_radix(a.size() > 1 ? a[1] : "",
                                           std::stoul(a[0]), &e);
                return r ? r->to_string() : kind(e);
        }
        if (op == "from_le_slice" || op == "from_be_slice") {
                auto b = unhex(a[0]);
                return opt(op == "from_le_slice"
                                   ? T::from_le_slice(b.data(), b.size())
                                   : T::from_be_slice(b.data(), b.size()));
        }
        if (op == "wrapping_add") return x().wrapping_add(y()).to_string();
        if (op == "wrapping_sub") return x().wrapping_sub(y()).to_string();
        if (op == "wrapping_mul") return x().wrapping_mul(y()).to_string();
        if (op == "wrapping_div") return x().wrapping_div(y()).to_string();
        if (op == "wrapping_rem") return x().wrapping_rem(y()).to_string();
        if (op == "wrapping_div_euclid")
                return x().wrapping_div_euclid(y()).to_string();
        if (op == "wrapping_rem_euclid")
                return x().wrapping_rem_euclid(y()).to_string();
        if (op == "wrapping_neg") return x().wrapping_neg().to_string();
        if (op == "wrapping_abs") return x().wrapping_abs().to_string();
        if (op == "wrapping_shl") return x().wrapping_shl(k()).to_string();
        if (op == "wrapping_shr") return x().wrapping_shr(k()).to_string();
        if (op == "wrapping_pow") return x().wrapping_pow(k()).to_string();
        if (op == "overflowing_add") return ovf(x().overflowing_add(y()));
        if (op == "overflowing_sub") return ovf(x().overflowing_sub(y()));
        if (op == "overflowing_mul") return ovf(x().overflowing_mul(y()));
        if (op == "overflowing_div") return ovf(x().overflowing_div(y()));
        if (op == "overflowing_rem") return ovf(x().overflowing_rem(y()));
        if (op == "overflowing_neg") return ovf(x().overflowing_neg());
        if (op == "overflowing_abs") return ovf(x().overflowing_abs());
        if (op == "overflowing_shl") return ovf(x().overflowing_shl(k()));
        if (op == "overflowing_shr") return ovf(x().overflowing_shr(k()));
        if (op == "overflowing_pow") return ovf(x().overflowing_pow(k()));
        if (op == "checked_add") return opt(x().checked_add(y()));
        if (op == "checked_sub") return opt(x().checked_sub(y()));
        if (op == "checked_mul") return opt(x().checked_mul(y()));
        if (op == "checked_div") return opt(x().checked_div(y()));
        if (op == "checked_rem") return opt(x().checked_rem(y()));
        if (op == "checked_div_euclid")
                return opt(x().checked_div_euclid(y()));
        if (op == "checked_rem_euclid")
                return opt(x().checked_rem_euclid(y()));
        if (op == "checked_neg") return opt(x().checked_neg());
        if (op == "checked_abs") return opt(x().checked_abs());
        if (op == "checked_shl") return opt(x().checked_shl(k()));
        if (op == "checked_shr") return opt(x().checked_shr(k()));
        if (op == "checked_pow") return opt(x().checked_pow(k()));
        if (op == "checked_ilog2") return opt(x().checked_ilog2());
        if (op == "checked_ilog10") return opt(x().checked_ilog10());
        if (op == "saturating_add") return x().saturating_add(y()).to_string();
        if (op == "saturating_sub") return x().saturating_sub(y()).to_string();
        if (op == "saturating_mul") return x().saturating_mul(y()).to_string();
        if (op == "saturating_neg") return x().saturating_neg().to_string();
        if (op == "saturating_abs") return x().saturating_abs().to_string();
        if (op == "saturating_pow") return x().saturating_pow(k()).to_string();
        if (op == "unbounded_shl") return x().unbounded_shl(k()).to_string();
        if (op == "unbounded_shr") return x().unbounded_shr(k()).to_string();
        if (op == "rotate_left") return x().rotate_left(k()).to_string();
        if (op == "rotate_right") return x().rotate_right(k()).to_string();
        if (op == "unsigned_abs") return x().unsigned_abs().to_string();
        if (op == "abs_diff") return x().abs_diff(y()).to_string();
        if (op == "bitand") return (x() & y()).to_string();
        if (op == "bitor") return (x() | y()).to_string();
        if (op == "bitxor") return (x() ^ y()).to_string();
        if (op == "not") return (~x()).to_string();
        if (op == "cmp") return std::to_string(x().cmp(y()));
        if (op == "min") return x().min(y()).to_string();
        if (op == "max") return x().max(y()).to_string();
        if (op == "count_ones") return std::to_string(x().count_ones());
        if (op == "count_zeros") return std::to_string(x().count_zeros());
        if (op == "leading_zeros") return std::to_string(x().leading_zeros());
        if (op == "leading_ones") return std::to_string(x().leading_ones());
        if (op == "trailing_zeros")
                return std::to_string(x().trailing_zeros());
        if (op == "trailing_ones") return std::to_string(x().trailing_ones());
        if (op == "bit_width") return std::to_string(x().bit_width());
        if (op == "is_power_of_two")
                return x().is_power_of_two() ? "true" : "false";
        if (op == "signum") return x().signum().to_string();
        if (op == "swap_bytes") return x().swap_bytes().to_string();
        if (op == "reverse_bits") return x().reverse_bits().to_string();
        if (op == "isqrt") return x().isqrt().to_string();
        if (op == "to_be_bytes") return hex(x().to_be_bytes().data(), N);
        if (op == "to_str_radix") return x().to_str_radix(k());
        if (op == "cast") return cast(x(), a[1]);
        return "unknown " + op;
}

#define XTCE_SIZES(X) X(1) X(2) X(3) X(8) X(16) X(17) X(24) X(32)

template <bool S, std::size_t N>
std::string cast(const Integer<S, N>& x, const std::string& to) {
#define X(n)                                                    \
        if (to == "u" #n) return x.template as<Integer<false, n>>() \
                                         .to_string();          \
        if (to == "i" #n) return x.template as<Integer<true, n>>() \
                                         .to_string();
        XTCE_SIZES(X)
#undef X
        return "unknown type " + to;
}

std::string dispatch(const std::string& op, const std::string& ty,
                     const std::vector<std::string>& a) {
#define X(n)                                                     \
        if (ty == "u" #n) return run<false, n>(op, a);          \
        if (ty == "i" #n) return run<true, n>(op, a);
        XTCE_SIZES(X)
#undef X
        return "unknown type " + ty;
}

}  // namespace

int main() {
        std::string line;
        while (std::getline(std::cin, line)) {
                std::istringstream in(line);
                std::string op, ty, w;
                std::vector<std::string> args;
                if (!(in >> op >> ty)) continue;
                while (in >> w) args.push_back(w);
                std::cout << dispatch(op, ty, args) << '\n';
        }
        return 0;
}
