// Managed byte buffers.
//
// A Buffer is preallocated when it is created and keeps its allocation
// across uses: clear() only resets the length.  Buffers are owned by the data
// structures that use them (normally an xtce::Document), so the memory lives
// exactly as long as those structures and is released with them.
#pragma once

#include <cstddef>
#include <cstdint>
#include <istream>
#include <string>
#include <string_view>

namespace xtce {

// Capacity used when no explicit capacity is requested.
inline constexpr std::size_t kDefaultCapacity = 64 * 1024;

class Buffer {
public:
        explicit Buffer(std::size_t capacity = kDefaultCapacity) {
                data_.reserve(capacity);
        }

        // Discards the contents but keeps the allocation.
        void clear() noexcept { data_.clear(); }
        void reserve(std::size_t additional) {
                data_.reserve(data_.size() + additional);
        }

        // Replaces the contents, reusing the allocation if it fits.
        void load(const void* bytes, std::size_t n) {
                data_.clear();
                data_.append(static_cast<const char*>(bytes), n);
        }
        void load(std::string_view s) { load(s.data(), s.size()); }

        // Replaces the contents with everything read from `in`.
        void load_from(std::istream& in) {
                data_.clear();
                char chunk[16 * 1024];
                while (in.read(chunk, sizeof chunk) || in.gcount() > 0) {
                        data_.append(chunk,
                                     static_cast<std::size_t>(in.gcount()));
                }
        }

        // Sets the length to n bytes, zero filling any new bytes.
        void resize(std::size_t n) { data_.resize(n, '\0'); }

        void push(char c) { data_.push_back(c); }
        void append(std::string_view s) { data_.append(s); }

        const std::uint8_t* data() const noexcept {
                return reinterpret_cast<const std::uint8_t*>(data_.data());
        }
        std::uint8_t* mutable_data() noexcept {
                return reinterpret_cast<std::uint8_t*>(data_.data());
        }
        std::string_view view() const noexcept { return data_; }
        std::size_t size() const noexcept { return data_.size(); }
        bool empty() const noexcept { return data_.empty(); }
        // Bytes currently allocated (always at least size()).
        std::size_t capacity() const noexcept { return data_.capacity(); }

private:
        std::string data_;
};

}  // namespace xtce
