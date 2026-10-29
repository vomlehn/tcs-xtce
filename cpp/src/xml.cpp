#include "xtce/xml.hpp"

#include <charconv>
#include <cmath>
#include <cstdio>
#include <limits>
#include <system_error>

namespace xtce {

// -- scalar values
// --------------------------------------------------------------

/** Trims XML whitespace (space, tab, CR and LF) from both ends.
 *
 * @param s  The text.
 * @return   The trimmed view into `s`. */
std::string_view trim(std::string_view s) {
        auto ws = [](char c) {
                return c == ' ' || c == '\t' || c == '\n' || c == '\r';
        };
        while (!s.empty() && ws(s.front())) s.remove_prefix(1);
        while (!s.empty() && ws(s.back())) s.remove_suffix(1);
        return s;
}

void format_value(std::string& out, const std::string& v) { out += v; }
void format_value(std::string& out, bool v) { out += v ? "true" : "false"; }

template <class I>
static void format_int(std::string& out, I v) {
        char buf[32];
        auto r = std::to_chars(buf, buf + sizeof buf, v);
        out.append(buf, r.ptr);
}

void format_value(std::string& out, std::int32_t v) { format_int(out, v); }
void format_value(std::string& out, std::int64_t v) { format_int(out, v); }
void format_value(std::string& out, std::uint16_t v) { format_int(out, v); }
void format_value(std::string& out, double v) { format_double(out, v); }

/** Appends the shortest text that reads back as the same double,
 * formatted like Python's repr, with NaN, INF and -INF for the special
 * values.
 *
 * @param out  The string to append to.
 * @param x    The value. */
void format_double(std::string& out, double x) {
        if (std::isnan(x)) {
                out += "NaN";
                return;
        }
        if (std::isinf(x)) {
                out += x > 0 ? "INF" : "-INF";
                return;
        }
        // Shortest round-trip digits in scientific form, e.g. "-1.25e-07".
        char buf[64];
        auto r = std::to_chars(buf, buf + sizeof buf, x,
                               std::chars_format::scientific);
        std::string_view sci(buf, static_cast<std::size_t>(r.ptr - buf));
        std::size_t e = sci.find('e');
        std::string_view mant = sci.substr(0, e);
        int exp = 0;
        std::string_view es = sci.substr(e + 1);
        if (!es.empty() && es.front() == '+') es.remove_prefix(1);
        std::from_chars(es.data(), es.data() + es.size(), exp);
        bool neg = !mant.empty() && mant.front() == '-';
        if (neg) mant.remove_prefix(1);
        std::string digits;
        for (char c : mant)
                if (c != '.') digits += c;
        if (neg) out += '-';
        if (exp >= -4 && exp < 16) {
                if (exp < 0) {
                        out += "0.";
                        out.append(static_cast<std::size_t>(-exp - 1), '0');
                        out += digits;
                } else {
                        std::size_t point = static_cast<std::size_t>(exp) + 1;
                        if (digits.size() <= point) {
                                out += digits;
                                out.append(point - digits.size(), '0');
                                out += ".0";
                        } else {
                                out.append(digits, 0, point);
                                out += '.';
                                out.append(digits, point);
                        }
                }
        } else {
                out += digits[0];
                if (digits.size() > 1) {
                        out += '.';
                        out.append(digits, 1);
                }
                out += 'e';
                out += exp < 0 ? '-' : '+';
                unsigned ae = static_cast<unsigned>(exp < 0 ? -exp : exp);
                if (ae < 10) out += '0';
                format_int(out, ae);
        }
}

void parse_value(std::string_view s, std::string& v) { v.assign(s); }

/** Parses an xs:boolean: true, false, 1 or 0, ignoring surrounding
 * whitespace.  Throws std::invalid_argument on anything else.
 *
 * @param s  The text.
 * @param v  Set to the value parsed. */
void parse_value(std::string_view s, bool& v) {
        std::string_view t = trim(s);
        if (t == "true" || t == "1") {
                v = true;
        } else if (t == "false" || t == "0") {
                v = false;
        } else {
                throw std::invalid_argument("invalid boolean \"" +
                                            std::string(s) + "\"");
        }
}

/** Parses a decimal integer with an optional sign, ignoring surrounding
 * whitespace.  Throws std::invalid_argument if it is malformed or out of
 * range of `I`.
 *
 * @param s     The text.
 * @param v     Set to the value parsed.
 * @param kind  The type's name, for the error message. */
template <class I>
static void parse_int(std::string_view s, I& v, const char* kind) {
        std::string_view t = trim(s);
        if (!t.empty() && t.front() == '+') t.remove_prefix(1);
        auto r = std::from_chars(t.data(), t.data() + t.size(), v);
        if (t.empty() || r.ec != std::errc() || r.ptr != t.data() + t.size()) {
                throw std::invalid_argument(std::string("invalid ") + kind +
                                            " \"" + std::string(s) + "\"");
        }
}

void parse_value(std::string_view s, std::int32_t& v) {
        parse_int(s, v, "i32");
}
void parse_value(std::string_view s, std::int64_t& v) {
        parse_int(s, v, "i64");
}
/** Parses an unsigned 16-bit integer, rejecting a minus sign (even on
 * zero).  Throws std::invalid_argument on bad input.
 *
 * @param s  The text.
 * @param v  Set to the value parsed. */
void parse_value(std::string_view s, std::uint16_t& v) {
        std::string_view t = trim(s);
        if (!t.empty() && t.front() == '-')
                throw std::invalid_argument("invalid u16 \"" + std::string(s) +
                                            "\"");
        parse_int(s, v, "u16");
}

/** Parses an xs:double, including INF, +INF, -INF and NaN but not the
 * other spellings std::from_chars accepts.  Throws std::invalid_argument
 * on bad input.
 *
 * @param s  The text.
 * @param v  Set to the value parsed. */
void parse_value(std::string_view s, double& v) {
        std::string_view t = trim(s);
        if (t == "INF" || t == "+INF") {
                v = std::numeric_limits<double>::infinity();
                return;
        }
        if (t == "-INF") {
                v = -std::numeric_limits<double>::infinity();
                return;
        }
        if (t == "NaN") {
                v = std::numeric_limits<double>::quiet_NaN();
                return;
        }
        // from_chars accepts "inf"/"nan" spellings that XML Schema does not.
        bool ok = !t.empty();
        for (char c : t) {
                if ((c >= 'a' && c <= 'z' && c != 'e') ||
                    (c >= 'A' && c <= 'Z' && c != 'E'))
                        ok = false;
        }
        std::string_view u = t;
        if (!u.empty() && u.front() == '+') u.remove_prefix(1);
        auto r = std::from_chars(u.data(), u.data() + u.size(), v);
        if (!ok || r.ec != std::errc() || r.ptr != u.data() + u.size()) {
                throw std::invalid_argument("invalid double \"" +
                                            std::string(s) + "\"");
        }
}

/** Checks an element's number of occurrences against its bounds, throwing
 * xtce::Error if `n` is outside [lo, hi].
 *
 * @param n     The number of occurrences found.
 * @param lo    The fewest allowed.
 * @param hi    The most allowed, or negative for unbounded.
 * @param what  The element's name, for the error message. */
void check_count(std::size_t n, std::size_t lo, long hi, const char* what) {
        if (n < lo || (hi >= 0 && n > static_cast<std::size_t>(hi))) {
                throw Error(std::string(what) + ": " + std::to_string(n) +
                            " occurrences, expected " + std::to_string(lo) +
                            ".." +
                            (hi < 0 ? std::string("unbounded")
                                    : std::to_string(hi)));
        }
}

// -- writer
// ---------------------------------------------------------------------

void Writer::declaration() {
        buf_.append("<?xml version=\"1.0\" encoding=\"UTF-8\"?>");
        last_ = Last::End;
}

void Writer::newline() {
        if (indent_ && last_ != Last::Nothing) {
                buf_.push('\n');
                for (std::size_t i = 0; i < depth_; ++i) buf_.append("  ");
        }
}

void Writer::close_start() {
        if (open_) {
                buf_.push('>');
                open_ = false;
        }
}

void Writer::start(std::string_view name) {
        close_start();
        newline();
        buf_.push('<');
        buf_.append(name);
        open_ = true;
        ++depth_;
        last_ = Last::Start;
        if (root_ns_) {
                std::string_view ns = *root_ns_;
                root_ns_.reset();
                attr_str("xmlns", ns);
        }
}

void Writer::escape(std::string_view s, bool attr) {
        std::size_t start = 0;
        for (std::size_t i = 0; i < s.size(); ++i) {
                const char* rep = nullptr;
                switch (s[i]) {
                        case '&':
                                rep = "&amp;";
                                break;
                        case '<':
                                rep = "&lt;";
                                break;
                        case '>':
                                rep = "&gt;";
                                break;
                        case '"':
                                rep = attr ? "&quot;" : nullptr;
                                break;
                        case '\n':
                                rep = attr ? "&#10;" : nullptr;
                                break;
                        case '\r':
                                rep = "&#13;";
                                break;
                        case '\t':
                                rep = attr ? "&#9;" : nullptr;
                                break;
                        default:
                                break;
                }
                if (rep) {
                        buf_.append(s.substr(start, i - start));
                        buf_.append(rep);
                        start = i + 1;
                }
        }
        buf_.append(s.substr(start));
}

void Writer::attr_str(std::string_view name, std::string_view value) {
        buf_.push(' ');
        buf_.append(name);
        buf_.append("=\"");
        escape(value, true);
        buf_.push('"');
}

void Writer::text_str(std::string_view value) {
        close_start();
        escape(value, false);
        last_ = Last::Text;
}

void Writer::end(std::string_view name) {
        --depth_;
        if (open_) {
                buf_.append("/>");
                open_ = false;
        } else {
                if (last_ == Last::End) newline();
                buf_.append("</");
                buf_.append(name);
                buf_.push('>');
        }
        last_ = Last::End;
}

void Writer::finish() {
        if (indent_) buf_.push('\n');
}

// -- reader
// ---------------------------------------------------------------------

static std::string_view local_of(std::string_view name) {
        std::size_t i = name.rfind(':');
        return i == std::string_view::npos ? name : name.substr(i + 1);
}

std::string_view Tag::local_name() const { return local_of(name); }

static bool is_ws(char c) {
        return c == ' ' || c == '\t' || c == '\n' || c == '\r';
}

bool AttrIter::next(const Reader& r) {
        std::string_view s = rest_;
        while (!s.empty() && is_ws(s.front())) s.remove_prefix(1);
        if (s.empty()) return false;
        std::size_t eq = s.find('=');
        if (eq == std::string_view::npos)
                throw r.error_at(offset_, "malformed attribute \"" +
                                                  std::string(s) + "\"");
        name_ = s.substr(0, eq);
        while (!name_.empty() && is_ws(name_.back())) name_.remove_suffix(1);
        std::string_view after = s.substr(eq + 1);
        while (!after.empty() && is_ws(after.front())) after.remove_prefix(1);
        if (after.empty() || (after.front() != '"' && after.front() != '\'')) {
                throw r.error_at(offset_,
                                 "unquoted attribute " + std::string(name_));
        }
        char q = after.front();
        std::size_t close = after.find(q, 1);
        if (close == std::string_view::npos)
                throw r.error_at(offset_, "unterminated attribute " +
                                                  std::string(name_));
        value_ = after.substr(1, close - 1);
        rest_ = after.substr(close + 1);
        return true;
}

/** Appends a code point's UTF-8 encoding.
 *
 * @param out  The string to append to.
 * @param cp   The code point, at most 0x10FFFF. */
static void push_utf8(std::string& out, std::uint32_t cp) {
        if (cp < 0x80) {
                out += static_cast<char>(cp);
        } else if (cp < 0x800) {
                out += static_cast<char>(0xC0 | (cp >> 6));
                out += static_cast<char>(0x80 | (cp & 0x3F));
        } else if (cp < 0x10000) {
                out += static_cast<char>(0xE0 | (cp >> 12));
                out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
                out += static_cast<char>(0x80 | (cp & 0x3F));
        } else {
                out += static_cast<char>(0xF0 | (cp >> 18));
                out += static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
                out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
                out += static_cast<char>(0x80 | (cp & 0x3F));
        }
}

/** Replaces entity and character references and normalizes line endings;
 * in attribute values literal whitespace becomes a space.
 *
 * @param raw    The text as it appears in the document.
 * @param attr   Whether it is an attribute value.
 * @param out    The string the result is appended to.
 * @param error  Set to a description when a reference is bad.
 * @return       True, or false on an unterminated or unknown reference. */
bool unescape(std::string_view raw, bool attr, std::string& out,
              std::string& error) {
        for (std::size_t i = 0; i < raw.size(); ++i) {
                char c = raw[i];
                if (c == '&') {
                        std::size_t semi = raw.find(';', i);
                        if (semi == std::string_view::npos) {
                                error = "unterminated entity in \"" +
                                        std::string(raw) + "\"";
                                return false;
                        }
                        std::string_view ent = raw.substr(i + 1, semi - i - 1);
                        if (ent == "amp") {
                                out += '&';
                        } else if (ent == "lt") {
                                out += '<';
                        } else if (ent == "gt") {
                                out += '>';
                        } else if (ent == "quot") {
                                out += '"';
                        } else if (ent == "apos") {
                                out += '\'';
                        } else {
                                std::uint32_t cp = 0;
                                std::from_chars_result r{};
                                bool ok = false;
                                if (ent.size() > 2 && ent[0] == '#' &&
                                    ent[1] == 'x') {
                                        r = std::from_chars(
                                                ent.data() + 2,
                                                ent.data() + ent.size(), cp,
                                                16);
                                        ok = r.ec == std::errc() &&
                                             r.ptr == ent.data() + ent.size();
                                } else if (ent.size() > 1 && ent[0] == '#') {
                                        r = std::from_chars(
                                                ent.data() + 1,
                                                ent.data() + ent.size(), cp,
                                                10);
                                        ok = r.ec == std::errc() &&
                                             r.ptr == ent.data() + ent.size();
                                }
                                if (!ok || cp > 0x10FFFF ||
                                    (cp >= 0xD800 && cp <= 0xDFFF)) {
                                        error = "unknown entity &" +
                                                std::string(ent) + ";";
                                        return false;
                                }
                                push_utf8(out, cp);
                        }
                        i = semi;
                } else if (c == '\r') {
                        if (i + 1 < raw.size() && raw[i + 1] == '\n') ++i;
                        out += attr ? ' ' : '\n';
                } else if (attr && (c == '\n' || c == '\t')) {
                        out += ' ';
                } else {
                        out += c;
                }
        }
        return true;
}

Reader::Reader(std::string_view src) : src_(src) {
        if (src_.substr(0, 3) == "\xEF\xBB\xBF") src_.remove_prefix(3);
        stack_.reserve(32);
        scratch_.reserve(256);
}

Error Reader::error_at(std::size_t offset, const std::string& message) const {
        std::size_t end = offset < src_.size() ? offset : src_.size();
        std::size_t line = 1, col_start = 0;
        for (std::size_t i = 0; i < end; ++i) {
                if (src_[i] == '\n') {
                        ++line;
                        col_start = i + 1;
                }
        }
        return Error(message, line, end - col_start + 1);
}

std::size_t Reader::skip_past(std::size_t from, std::string_view pat) const {
        std::size_t i = src_.find(pat, from);
        if (i == std::string_view::npos) {
                throw error_at(from, "unterminated construct, expected \"" +
                                             std::string(pat) + "\"");
        }
        return i + pat.size();
}

Reader::Token Reader::lex() {
        for (;;) {
                std::size_t start = pos_;
                Token t;
                t.offset = start;
                if (start >= src_.size()) {
                        t.kind = Kind::Eof;
                        return t;
                }
                std::string_view rest = src_.substr(start);
                if (rest.front() != '<') {
                        std::size_t lt = rest.find('<');
                        std::size_t end = lt == std::string_view::npos
                                                  ? src_.size()
                                                  : start + lt;
                        pos_ = end;
                        t.kind = Kind::Text;
                        t.text = src_.substr(start, end - start);
                        return t;
                }
                if (rest.substr(0, 2) == "<?") {
                        pos_ = skip_past(start, "?>");
                } else if (rest.substr(0, 4) == "<!--") {
                        pos_ = skip_past(start + 4, "-->");
                } else if (rest.substr(0, 9) == "<![CDATA[") {
                        std::size_t end = skip_past(start, "]]>");
                        pos_ = end;
                        t.kind = Kind::CData;
                        t.text = src_.substr(start + 9, end - 3 - (start + 9));
                        return t;
                } else if (rest.substr(0, 2) == "<!") {
                        std::size_t depth = 0, i = 0;
                        bool found = false;
                        for (; i < rest.size(); ++i) {
                                char c = rest[i];
                                if (c == '[') {
                                        ++depth;
                                } else if (c == ']') {
                                        if (depth) --depth;
                                } else if (c == '>' && depth == 0) {
                                        found = true;
                                        break;
                                }
                        }
                        if (!found) throw error_at(start, "unterminated <!");
                        pos_ = start + i + 1;
                } else if (rest.substr(0, 2) == "</") {
                        std::size_t close = rest.find('>');
                        if (close == std::string_view::npos)
                                throw error_at(start, "unterminated end tag");
                        std::string_view name = rest.substr(2, close - 2);
                        while (!name.empty() && is_ws(name.back()))
                                name.remove_suffix(1);
                        pos_ = start + close + 1;
                        t.kind = Kind::End;
                        t.text = name;
                        return t;
                } else {
                        char quote = 0;
                        std::size_t close = std::string_view::npos;
                        for (std::size_t i = 1; i < rest.size(); ++i) {
                                char c = rest[i];
                                if (quote) {
                                        if (c == quote) quote = 0;
                                } else if (c == '"' || c == '\'') {
                                        quote = c;
                                } else if (c == '>') {
                                        close = i;
                                        break;
                                }
                        }
                        if (close == std::string_view::npos)
                                throw error_at(start, "unterminated start tag");
                        std::string_view inner = rest.substr(1, close - 1);
                        bool empty = !inner.empty() && inner.back() == '/';
                        if (empty) inner.remove_suffix(1);
                        std::size_t name_end = 0;
                        while (name_end < inner.size() &&
                               !is_ws(inner[name_end]))
                                ++name_end;
                        if (name_end == 0)
                                throw error_at(start, "missing element name");
                        pos_ = start + close + 1;
                        t.kind = Kind::Start;
                        t.tag = Tag{inner.substr(0, name_end),
                                    inner.substr(name_end), empty, start};
                        return t;
                }
        }
}

const Reader::Token& Reader::peek_token() {
        if (!has_peeked_) {
                peeked_ = lex();
                has_peeked_ = true;
        }
        return peeked_;
}

Reader::Token Reader::take_token() {
        peek_token();
        has_peeked_ = false;
        return peeked_;
}

void Reader::opened(const Tag& tag) {
        if (tag.empty) {
                pending_end_ = true;
        } else {
                stack_.push_back(tag.name);
        }
}

Tag Reader::root() {
        for (;;) {
                Token t = take_token();
                if (t.kind == Kind::Text && trim(t.text).empty()) continue;
                if (t.kind == Kind::Start) {
                        opened(t.tag);
                        return t.tag;
                }
                throw error_at(t.offset, "expected the root element");
        }
}

void Reader::finish() {
        for (;;) {
                Token t = take_token();
                if (t.kind == Kind::Eof) return;
                if (t.kind == Kind::Text && trim(t.text).empty()) continue;
                throw error_at(t.offset, "content after the root element");
        }
}

std::optional<std::string_view> Reader::peek_child() {
        if (pending_end_) return std::nullopt;
        for (;;) {
                const Token& t = peek_token();
                switch (t.kind) {
                        case Kind::Start:
                                return t.tag.local_name();
                        case Kind::End:
                                return std::nullopt;
                        case Kind::Text:
                                if (trim(t.text).empty()) {
                                        has_peeked_ = false;
                                        continue;
                                }
                                throw error_at(
                                        t.offset,
                                        "unexpected text in element content");
                        case Kind::Eof:
                                throw error_at(t.offset,
                                               "unexpected end of input");
                        default:
                                throw error_at(
                                        t.offset,
                                        "unexpected text in element content");
                }
        }
}

Tag Reader::open() {
        Token t = take_token();
        if (t.kind != Kind::Start)
                throw error_at(t.offset, "expected a start tag");
        opened(t.tag);
        return t.tag;
}

void Reader::consume_end() {
        Token t = take_token();
        if (t.kind != Kind::End)
                throw error_at(t.offset, "expected an end tag");
        if (stack_.empty())
                throw error_at(t.offset,
                               "unbalanced </" + std::string(t.text) + ">");
        std::string_view open = stack_.back();
        stack_.pop_back();
        if (open != t.text) {
                throw error_at(t.offset, "</" + std::string(t.text) +
                                                 "> does not match <" +
                                                 std::string(open) + ">");
        }
}

void Reader::close() {
        if (pending_end_) {
                pending_end_ = false;
                return;
        }
        if (auto name = peek_child())
                throw error_at(here(), "unexpected element <" +
                                               std::string(*name) + ">");
        consume_end();
}

std::string Reader::read_text() {
        std::string out;
        if (pending_end_) {
                pending_end_ = false;
                return out;
        }
        for (;;) {
                const Token& t = peek_token();
                if (t.kind == Kind::Text) {
                        std::string err;
                        if (!unescape(t.text, false, out, err))
                                throw error_at(t.offset, err);
                } else if (t.kind == Kind::CData) {
                        out += t.text;
                } else if (t.kind == Kind::End) {
                        break;
                } else if (t.kind == Kind::Start) {
                        throw error_at(t.offset,
                                       "unexpected element <" +
                                               std::string(t.tag.name) +
                                               "> in text content");
                } else {
                        throw error_at(t.offset, "unexpected end of input");
                }
                has_peeked_ = false;
        }
        consume_end();
        return out;
}

void Reader::unknown_attr(const Tag& tag, std::string_view name) const {
        if (name == "xmlns" || name.find(':') != std::string_view::npos) return;
        throw error_at(tag.offset, "unexpected attribute \"" +
                                           std::string(name) + "\" on <" +
                                           std::string(tag.name) + ">");
}

void Reader::missing_attr(const Tag& tag, std::string_view name) const {
        throw error_at(tag.offset, "missing required attribute \"" +
                                           std::string(name) + "\" on <" +
                                           std::string(tag.name) + ">");
}

Error Reader::expected(std::string_view what) {
        auto found = peek_child();
        if (found) {
                return error_at(here(), "expected <" + std::string(what) +
                                                ">, found <" +
                                                std::string(*found) + ">");
        }
        return error_at(here(), "expected <" + std::string(what) + ">");
}

Error Reader::too_few(std::string_view what, std::size_t min,
                      std::size_t found) {
        return error_at(here(), "expected at least " + std::to_string(min) +
                                        " <" + std::string(what) +
                                        "> elements, found " +
                                        std::to_string(found));
}

}  // namespace xtce
