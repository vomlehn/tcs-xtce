// XTCE documents bound to managed input and output buffers.
#pragma once

#include <cstddef>
#include <istream>
#include <ostream>
#include <string_view>

#include "xtce/buffer.hpp"
#include "xtce/generated.hpp"
#include "xtce/xml.hpp"

namespace xtce {

/** Serializes a value as an XML document, replacing a buffer's contents.
 *
 * @param value   The value, a generated XTCE type.
 * @param tag     The name of its (root) element.
 * @param buf     The buffer to write the document into.
 * @param indent  Whether to pretty-print. */
template <class T>
void to_xml(const T& value, std::string_view tag, Buffer& buf,
            bool indent = true) {
        buf.clear();
        Writer w(buf, indent);
        w.declaration();
        w.set_root_namespace(kNamespace);
        value.write_xml(w, tag);
        w.finish();
}

/** Deserializes a value from an XML document.
 *
 * @param bytes  The document.
 * @param tag    The local name its root element must have.
 * @return       The value.  Throws xtce::Error if the document is
 *               malformed, its root is not `tag`, or it is not a valid
 *               `T`. */
template <class T>
T from_xml(std::string_view bytes, std::string_view tag) {
        Reader r(bytes);
        Tag root = r.root();
        if (root.local_name() != tag) {
                throw Error("root element is <" + std::string(root.name) +
                            ">, expected <" + std::string(tag) + ">");
        }
        T value = T::read_xml(r, root);
        r.finish();
        return value;
}

// An XTCE SpaceSystem together with the buffers used to (de)serialize it.
//
// Both buffers are preallocated on construction and reused by every load()
// and serialize(); they are released when the document is destroyed.
class Document {
public:
        explicit Document(RootType root = {},
                          std::size_t input_capacity = kDefaultCapacity,
                          std::size_t output_capacity = kDefaultCapacity)
                : root(std::move(root)),
                  input_(input_capacity),
                  output_(output_capacity) {}

        /** Deserializes a document, sizing its buffers to fit.
         *
         * @param bytes  The XML.
         * @return       The document.  Throws xtce::Error if `bytes` is not
         *               a valid XTCE document. */
        static Document from_bytes(std::string_view bytes) {
                std::size_t cap = bytes.size() > kDefaultCapacity
                                          ? bytes.size()
                                          : kDefaultCapacity;
                Document doc(RootType{}, cap, cap);
                doc.load(bytes);
                return doc;
        }

        // Replaces the root by deserializing `bytes`, reusing the input buffer.
        void load(std::string_view bytes) {
                input_.load(bytes);
                parse_input();
        }

        // Replaces the root by deserializing everything read from `in`.
        void read_from(std::istream& in) {
                input_.load_from(in);
                parse_input();
        }

        // Serializes the root into the output buffer; the view is valid until
        // the document is next modified.
        std::string_view serialize() {
                to_xml(root, kRootElement, output_, indent);
                return output_.view();
        }

        void write_to(std::ostream& out) {
                std::string_view bytes = serialize();
                out.write(bytes.data(),
                          static_cast<std::streamsize>(bytes.size()));
        }

        const Buffer& input() const noexcept { return input_; }
        const Buffer& output() const noexcept { return output_; }

        // The deserialized (or to-be-serialized) root SpaceSystem.
        RootType root;
        // Pretty-print serialized output (the default).
        bool indent = true;

private:
        void parse_input() {
                root = from_xml<RootType>(input_.view(), kRootElement);
        }

        Buffer input_;
        Buffer output_;
};

}  // namespace xtce
