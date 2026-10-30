// Minimal XML writer and pull reader used by the generated bindings.
//
// The writer appends directly into a managed Buffer.  The reader works in
// place over the bytes of a buffer: tag names, attribute values and text are
// string_views into the input and are only copied when stored into a
// generated data structure.
#pragma once

#include <cstddef>
#include <cstdint>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "xtce-buffer.hpp"

namespace xtce {

// Raised for malformed or schema-invalid input, or invalid data on output.
class Error : public std::runtime_error {
public:
        explicit Error(const std::string& message, std::size_t line = 0,
                       std::size_t column = 0)
                : std::runtime_error(line ? std::to_string(line) + ":" +
                                                     std::to_string(column) +
                                                     ": " + message
                                          : message),
                  message_(message),
                  line_(line),
                  column_(column) {}
        const std::string& message() const noexcept { return message_; }
        // 1-based input line, or 0 when not tied to an input position.
        std::size_t line() const noexcept { return line_; }
        std::size_t column() const noexcept { return column_; }

private:
        std::string message_;
        std::size_t line_;
        std::size_t column_;
};

// An always-present, deep-copying owning pointer; used to break recursive
// type cycles.
template <class T>
class Box {
public:
        Box() : p_(std::make_unique<T>()) {}
        Box(T value)
                : p_(std::make_unique<T>(std::move(value))) {
        }  // NOLINT: implicit by design
        Box(const Box& o) : p_(std::make_unique<T>(*o)) {}
        Box(Box&& o) noexcept = default;
        Box& operator=(const Box& o) {
                if (this != &o) p_ = std::make_unique<T>(*o);
                return *this;
        }
        Box& operator=(Box&& o) noexcept = default;
        ~Box() = default;

        T& operator*() noexcept { return *p_; }
        const T& operator*() const noexcept { return *p_; }
        T* operator->() noexcept { return p_.get(); }
        const T* operator->() const noexcept { return p_.get(); }
        T& get() noexcept { return *p_; }
        const T& get() const noexcept { return *p_; }

        friend bool operator==(const Box& a, const Box& b) { return *a == *b; }

private:
        std::unique_ptr<T> p_;
};

// -- scalar values
// --------------------------------------------------------------

/** Appends a value's XML Schema text form to a string.  There is one
 * overload for each scalar type the bindings use.
 *
 * @param out  The string to append to.
 * @param v    The value. */
void format_value(std::string& out, const std::string& v);
void format_value(std::string& out, bool v);
void format_value(std::string& out, std::int32_t v);
void format_value(std::string& out, std::int64_t v);
void format_value(std::string& out, std::uint16_t v);
void format_value(std::string& out, double v);

/** Parses a value from its XML Schema text form.  There is one overload
 * for each scalar type the bindings use; surrounding whitespace is ignored
 * except for strings, which are taken as they are.  Each throws
 * std::invalid_argument with a description on bad input.
 *
 * @param s  The text.
 * @param v  Set to the value parsed. */
void parse_value(std::string_view s, std::string& v);
void parse_value(std::string_view s, bool& v);
void parse_value(std::string_view s, std::int32_t& v);
void parse_value(std::string_view s, std::int64_t& v);
void parse_value(std::string_view s, std::uint16_t& v);
void parse_value(std::string_view s, double& v);

/** Appends the shortest text that reads back as the same double,
 * formatted like Python's repr (matching the Rust and Python libraries),
 * with NaN, INF and -INF for the special values.
 *
 * @param out  The string to append to.
 * @param v    The value. */
void format_double(std::string& out, double v);

/** Trims XML whitespace (space, tab, CR and LF) from both ends.
 *
 * @param s  The text.
 * @return   The trimmed view into `s`. */
std::string_view trim(std::string_view s);

/** Checks an element's number of occurrences against its bounds, throwing
 * xtce::Error if `n` is outside [lo, hi].
 *
 * @param n     The number of occurrences found.
 * @param lo    The fewest allowed.
 * @param hi    The most allowed, or negative for unbounded.
 * @param what  The element's name, for the error message. */
void check_count(std::size_t n, std::size_t lo, long hi, const char* what);

// -- writer
// ---------------------------------------------------------------------

class Writer {
public:
        Writer(Buffer& buf, bool indent) : buf_(buf), indent_(indent) {}

        /** Writes the XML declaration, `<?xml version="1.0"
         * encoding="UTF-8"?>`. */
        void declaration();
        // Declares `ns` as the default namespace on the next element written.
        void set_root_namespace(std::string_view ns) { root_ns_ = ns; }

        /** Writes a start tag, left open for attributes, closing the
         * previous one first.  It carries the namespace given to
         * set_root_namespace(), if that is still pending.
         *
         * @param name  The element's name. */
        void start(std::string_view name);
        /** Writes an attribute into the start tag just written, escaping
         * its value.
         *
         * @param name   The attribute's name.
         * @param value  Its value, unescaped. */
        void attr_str(std::string_view name, std::string_view value);
        template <class V>
        void attr(std::string_view name, const V& value) {
                scratch_.clear();
                format_value(scratch_, value);
                attr_str(name, scratch_);
        }
        /** Writes text content, escaped, into the current element.
         *
         * @param value  The text. */
        void text_str(std::string_view value);
        template <class V>
        void text(const V& value) {
                scratch_.clear();
                format_value(scratch_, value);
                text_str(scratch_);
        }
        /** Ends the current element, as `/>` if nothing was written
         * into it and as an end tag otherwise.
         *
         * @param name  The element's name. */
        void end(std::string_view name);
        template <class V>
        void leaf(std::string_view name, const V& value) {
                start(name);
                text(value);
                end(name);
        }
        /** Ends the document, with a final newline when indenting. */
        void finish();

private:
        enum class Last { Nothing, Start, Text, End };
        /** Starts a new, indented line, when indenting and after the
         * first output. */
        void newline();
        /** Closes the open start tag with `>`, if there is one. */
        void close_start();
        /** Appends text with the characters XML requires (and, in
         * attributes, the whitespace it would normalize) escaped.
         *
         * @param s     The text.
         * @param attr  Whether it is an attribute value. */
        void escape(std::string_view s, bool attr);

        Buffer& buf_;
        std::string scratch_;
        std::size_t depth_ = 0;
        bool open_ = false;
        Last last_ = Last::Nothing;
        bool indent_;
        std::optional<std::string_view> root_ns_;
};

// -- reader
// ---------------------------------------------------------------------

// A start tag.  Attributes are parsed lazily from the raw tag text.
struct Tag {
        std::string_view name;
        std::string_view raw_attrs;
        bool empty = false;
        std::size_t offset = 0;

        /** Gives the element's name without its namespace prefix.
         *
         * @return  The part of `name` after its last colon. */
        std::string_view local_name() const;
};

// Iterates over the attributes of a tag:
//   for (AttrIter it(tag); it.next(r);) { it.name(); it.value(); }
class Reader;
class AttrIter {
public:
        explicit AttrIter(const Tag& tag)
                : rest_(tag.raw_attrs), offset_(tag.offset) {}
        /** Moves to the next attribute.
         *
         * @param r  The reader the tag came from, for error positions.
         * @return   Whether there was another attribute; throws
         *           xtce::Error if it is malformed. */
        bool next(const Reader& r);
        std::string_view name() const { return name_; }
        std::string_view value() const { return value_; }

private:
        std::string_view rest_, name_, value_;
        std::size_t offset_;
};

/** Replaces entity and character references and normalizes line endings;
 * in attribute values literal whitespace becomes a space.
 *
 * @param raw    The text as it appears in the document.
 * @param attr   Whether it is an attribute value.
 * @param out    The string the result is appended to.
 * @param error  Set to a description when a reference is bad.
 * @return       True, or false on an unterminated or unknown reference. */
bool unescape(std::string_view raw, bool attr, std::string& out,
              std::string& error);

class Reader {
public:
        /** Starts reading a document, skipping any byte order mark.
         *
         * @param src  The document, which must outlive the reader and
         *             every Tag it returns. */
        explicit Reader(std::string_view src);

        /** Makes an error located in the document.
         *
         * @param offset   The byte offset the error is at.
         * @param message  What is wrong.
         * @return         The error, with the offset's 1-based line and
         *                 column. */
        Error error_at(std::size_t offset, const std::string& message) const;

        /** Reads up to and opens the root element, skipping the prolog.
         *
         * @return  The root's start tag.  Throws xtce::Error if anything
         *          but whitespace, comments and declarations comes
         *          first. */
        Tag root();
        /** Checks that nothing but whitespace, comments and processing
         * instructions follows the root element; throws xtce::Error if
         * something else does. */
        void finish();

        /** Looks at the next child of the current element without
         * consuming it, skipping whitespace and comments.
         *
         * @return  The child's local name, or nullopt at the element's end.
         *          Throws xtce::Error on non-whitespace text or the end of
         *          input. */
        std::optional<std::string_view> peek_child();
        /** Opens the next element.
         *
         * @return  Its start tag.  Throws xtce::Error if the next token is
         *          not a start tag. */
        Tag open();
        /** Closes the current element, which must have no children left;
         * throws xtce::Error if one remains or the end tag doesn't match. */
        void close();
        /** Reads the text content and the end tag of the current element.
         *
         * @return  The text, unescaped, with CDATA sections included.
         *          Throws xtce::Error if the element has child elements or
         *          bad references. */
        std::string read_text();

        /** Reads the current element's text as a value, with its end tag.
         *
         * @return  The value.  Throws xtce::Error if the text is not a valid
         *          `V`. */
        template <class V>
        V read_value() {
                std::size_t at = pos_;
                std::string text = read_text();
                V v{};
                try {
                        parse_value(text, v);
                } catch (const std::invalid_argument& e) {
                        throw error_at(at, e.what());
                }
                return v;
        }

        /** Reads a child element that has only text, as a value.
         *
         * @return  The value.  Throws xtce::Error if the element has an
         *          attribute or the text is not a valid `V`. */
        template <class V>
        V read_leaf() {
                Tag tag = open();
                for (AttrIter it(tag); it.next(*this);)
                        unknown_attr(tag, it.name());
                return read_value<V>();
        }

        template <class T>
        T read_child() {
                Tag tag = open();
                return T::read_xml(*this, tag);
        }

        /** Unescapes and parses an attribute's value, throwing
         * xtce::Error on a bad reference or an invalid value.
         *
         * @param tag  The tag the attribute is on, for error positions.
         * @param raw  The value as it appears in the document.
         * @param v    Set to the value parsed. */
        template <class V>
        void attr_value(const Tag& tag, std::string_view raw, V& v) {
                std::string err;
                scratch_.clear();
                if (!unescape(raw, true, scratch_, err))
                        throw error_at(tag.offset, err);
                try {
                        parse_value(scratch_, v);
                } catch (const std::invalid_argument& e) {
                        throw error_at(tag.offset, e.what());
                }
        }

        /** Handles an attribute the element's type doesn't declare.
         * Namespace declarations and prefixed (xsi:, xml:) attributes are
         * ignored; any other throws xtce::Error.
         *
         * @param tag   The tag the attribute is on.
         * @param name  The attribute's name. */
        void unknown_attr(const Tag& tag, std::string_view name) const;
        /** Throws xtce::Error for a required attribute that is absent.
         *
         * @param tag   The tag it is missing from.
         * @param name  The attribute's name. */
        [[noreturn]] void missing_attr(const Tag& tag,
                                       std::string_view name) const;
        /** Makes the error for a required child element that is absent.
         *
         * @param what  The element expected.
         * @return      The error, naming the element found instead, if
         *              any, at the current position. */
        Error expected(std::string_view what);
        /** Makes the error for too few occurrences of an element.
         *
         * @param what   The element's name.
         * @param min    The fewest allowed.
         * @param found  How many there were.
         * @return       The error, at the current position. */
        Error too_few(std::string_view what, std::size_t min,
                      std::size_t found);

private:
        enum class Kind { Start, End, Text, CData, Eof };
        struct Token {
                Kind kind = Kind::Eof;
                std::size_t offset = 0;
                std::string_view text;  // name for End, content for Text/CData
                Tag tag;
        };

        /** Reads the next token, skipping comments, processing
         * instructions and DOCTYPE declarations.
         *
         * @return  The token; an Eof token at the end of input.  Throws
         *          xtce::Error on an unterminated construct. */
        Token lex();
        /** Looks at the next token without consuming it.
         *
         * @return  The token, valid until the next token is read. */
        const Token& peek_token();
        /** Consumes the next token.
         *
         * @return  The token. */
        Token take_token();
        /** Finds the end of a construct.
         *
         * @param from  Where to start looking.
         * @param pat   The text that ends the construct.
         * @return      The offset just past `pat`.  Throws xtce::Error if
         *              it doesn't occur. */
        std::size_t skip_past(std::size_t from, std::string_view pat) const;
        /** Records that an element was opened, so close() or read_text()
         * expect its end.
         *
         * @param tag  Its start tag. */
        void opened(const Tag& tag);
        /** Consumes the current element's end tag; throws xtce::Error if
         * the next token is not one or doesn't match the open element. */
        void consume_end();
        std::size_t here() const { return has_peeked_ ? peeked_.offset : pos_; }

        std::string_view src_;
        std::size_t pos_ = 0;
        Token peeked_;
        bool has_peeked_ = false;
        std::vector<std::string_view> stack_;
        bool pending_end_ = false;
        std::string scratch_;
};

}  // namespace xtce
