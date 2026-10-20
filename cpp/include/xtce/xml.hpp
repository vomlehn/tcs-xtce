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

#include "xtce/buffer.hpp"

namespace xtce {

// Raised for malformed or schema-invalid input, or invalid data on output.
class Error : public std::runtime_error {
 public:
  explicit Error(const std::string& message, std::size_t line = 0, std::size_t column = 0)
      : std::runtime_error(line ? std::to_string(line) + ":" + std::to_string(column) + ": " + message
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
  Box(T value) : p_(std::make_unique<T>(std::move(value))) {}  // NOLINT: implicit by design
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

// -- scalar values --------------------------------------------------------------

void format_value(std::string& out, const std::string& v);
void format_value(std::string& out, bool v);
void format_value(std::string& out, std::int32_t v);
void format_value(std::string& out, std::int64_t v);
void format_value(std::string& out, std::uint16_t v);
void format_value(std::string& out, double v);

// Each parser throws std::invalid_argument with a description on bad input.
void parse_value(std::string_view s, std::string& v);
void parse_value(std::string_view s, bool& v);
void parse_value(std::string_view s, std::int32_t& v);
void parse_value(std::string_view s, std::int64_t& v);
void parse_value(std::string_view s, std::uint16_t& v);
void parse_value(std::string_view s, double& v);

// Shortest round-trip double, formatted like Python's repr (matches the
// Rust and Python libraries).
void format_double(std::string& out, double v);

// Trims XML whitespace.
std::string_view trim(std::string_view s);

// Throws if `n` is outside [lo, hi] (hi < 0 means unbounded).
void check_count(std::size_t n, std::size_t lo, long hi, const char* what);

// -- writer ---------------------------------------------------------------------

class Writer {
 public:
  Writer(Buffer& buf, bool indent) : buf_(buf), indent_(indent) {}

  void declaration();
  // Declares `ns` as the default namespace on the next element written.
  void set_root_namespace(std::string_view ns) { root_ns_ = ns; }

  void start(std::string_view name);
  void attr_str(std::string_view name, std::string_view value);
  template <class V>
  void attr(std::string_view name, const V& value) {
    scratch_.clear();
    format_value(scratch_, value);
    attr_str(name, scratch_);
  }
  void text_str(std::string_view value);
  template <class V>
  void text(const V& value) {
    scratch_.clear();
    format_value(scratch_, value);
    text_str(scratch_);
  }
  void end(std::string_view name);
  template <class V>
  void leaf(std::string_view name, const V& value) {
    start(name);
    text(value);
    end(name);
  }
  void finish();

 private:
  enum class Last { Nothing, Start, Text, End };
  void newline();
  void close_start();
  void escape(std::string_view s, bool attr);

  Buffer& buf_;
  std::string scratch_;
  std::size_t depth_ = 0;
  bool open_ = false;
  Last last_ = Last::Nothing;
  bool indent_;
  std::optional<std::string_view> root_ns_;
};

// -- reader ---------------------------------------------------------------------

// A start tag.  Attributes are parsed lazily from the raw tag text.
struct Tag {
  std::string_view name;
  std::string_view raw_attrs;
  bool empty = false;
  std::size_t offset = 0;

  std::string_view local_name() const;
};

// Iterates over the attributes of a tag:
//   for (AttrIter it(tag); it.next(r);) { it.name(); it.value(); }
class Reader;
class AttrIter {
 public:
  explicit AttrIter(const Tag& tag) : rest_(tag.raw_attrs), offset_(tag.offset) {}
  bool next(const Reader& r);
  std::string_view name() const { return name_; }
  std::string_view value() const { return value_; }

 private:
  std::string_view rest_, name_, value_;
  std::size_t offset_;
};

// Replaces entity/character references and normalizes line endings; in
// attribute values literal whitespace becomes a space.  Returns false and
// sets `error` on a bad reference.
bool unescape(std::string_view raw, bool attr, std::string& out, std::string& error);

class Reader {
 public:
  // `src` must outlive the reader and every Tag it returns.
  explicit Reader(std::string_view src);

  Error error_at(std::size_t offset, const std::string& message) const;

  Tag root();
  void finish();

  // Local name of the next child element, or nullopt at the parent's end.
  std::optional<std::string_view> peek_child();
  Tag open();
  void close();
  // Reads the text content and the end tag of the current element.
  std::string read_text();

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

  template <class V>
  V read_leaf() {
    Tag tag = open();
    for (AttrIter it(tag); it.next(*this);) unknown_attr(tag, it.name());
    return read_value<V>();
  }

  template <class T>
  T read_child() {
    Tag tag = open();
    return T::read_xml(*this, tag);
  }

  template <class V>
  void attr_value(const Tag& tag, std::string_view raw, V& v) {
    std::string err;
    scratch_.clear();
    if (!unescape(raw, true, scratch_, err)) throw error_at(tag.offset, err);
    try {
      parse_value(scratch_, v);
    } catch (const std::invalid_argument& e) {
      throw error_at(tag.offset, e.what());
    }
  }

  // Namespace declarations and prefixed (xsi:, xml:) attributes are ignored;
  // any other undeclared attribute is an error.
  void unknown_attr(const Tag& tag, std::string_view name) const;
  [[noreturn]] void missing_attr(const Tag& tag, std::string_view name) const;
  Error expected(std::string_view what);
  Error too_few(std::string_view what, std::size_t min, std::size_t found);

 private:
  enum class Kind { Start, End, Text, CData, Eof };
  struct Token {
    Kind kind = Kind::Eof;
    std::size_t offset = 0;
    std::string_view text;  // name for End, content for Text/CData
    Tag tag;
  };

  Token lex();
  const Token& peek_token();
  Token take_token();
  std::size_t skip_past(std::size_t from, std::string_view pat) const;
  void opened(const Tag& tag);
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
