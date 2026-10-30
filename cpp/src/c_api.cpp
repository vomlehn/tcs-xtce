#include <new>
#include <string>

#include "xtce-document.hpp"
#include "xtce.h"

struct xtce_document {
        xtce::Document doc;
        std::string error;
        std::size_t error_line = 0;
        std::string version;
        std::string ns;

        xtce_document(std::size_t in, std::size_t out)
                : doc(xtce::RootType{}, in, out) {}
};

namespace {

/** Runs `f`, turning any exception it throws into a status code and
 * recording the error in the document.
 *
 * @param d  The document whose error and error line are set (cleared on
 *           success).
 * @param f  The operation to run.
 * @return   XTCE_OK, XTCE_NO_MEMORY if `f` ran out of memory, or
 *           XTCE_ERROR for any other exception. */
template <class F>
int guarded(xtce_document* d, F&& f) {
        try {
                f();
                d->error.clear();
                d->error_line = 0;
                return XTCE_OK;
        } catch (const xtce::Error& e) {
                d->error = e.message();
                d->error_line = e.line();
        } catch (const std::bad_alloc&) {
                d->error = "out of memory";
                d->error_line = 0;
                return XTCE_NO_MEMORY;
        } catch (const std::exception& e) {
                d->error = e.what();
                d->error_line = 0;
        }
        return XTCE_ERROR;
}

std::size_t or_default(std::size_t n) { return n ? n : xtce::kDefaultCapacity; }

const std::string kVersion(xtce::kSchemaVersion);
const std::string kNs(xtce::kNamespace);

}  // namespace

extern "C" {

const char* xtce_schema_version(void) { return kVersion.c_str(); }
const char* xtce_namespace(void) { return kNs.c_str(); }

/** Creates an empty document with preallocated buffers.
 *
 * @param input_capacity   Bytes for the input buffer; 0 for the default.
 * @param output_capacity  Bytes for the output buffer; 0 for the default.
 * @return                 The document, or NULL if allocation fails. */
xtce_document* xtce_document_new(size_t input_capacity,
                                 size_t output_capacity) {
        try {
                return new xtce_document(or_default(input_capacity),
                                         or_default(output_capacity));
        } catch (...) {
                return nullptr;
        }
}

void xtce_document_free(xtce_document* doc) { delete doc; }

/** Replaces the document by deserializing XML.
 *
 * @param doc   The document.
 * @param data  The XML.
 * @param len   The number of bytes at `data`.
 * @return      XTCE_OK, XTCE_ERROR or XTCE_NO_MEMORY. */
int xtce_document_load(xtce_document* doc, const void* data, size_t len) {
        return guarded(doc, [&] {
                doc->doc.load(
                        std::string_view(static_cast<const char*>(data), len));
        });
}

/** Serializes into the document's output buffer.
 *
 * @param doc      The document.
 * @param out      Set on success to the serialized bytes.
 * @param out_len  Set on success to the number of bytes at *out.
 * @return         XTCE_OK, XTCE_ERROR or XTCE_NO_MEMORY. */
int xtce_document_serialize(xtce_document* doc, const uint8_t** out,
                            size_t* out_len) {
        return guarded(doc, [&] {
                std::string_view bytes = doc->doc.serialize();
                *out = reinterpret_cast<const uint8_t*>(bytes.data());
                *out_len = bytes.size();
        });
}

void xtce_document_set_indent(xtce_document* doc, int indent) {
        doc->doc.indent = indent != 0;
}

const char* xtce_document_name(const xtce_document* doc) {
        return doc->doc.root.name.c_str();
}

int xtce_document_set_name(xtce_document* doc, const char* name) {
        return guarded(doc, [&] { doc->doc.root.name = name; });
}

const char* xtce_document_error(const xtce_document* doc) {
        return doc->error.c_str();
}
size_t xtce_document_error_line(const xtce_document* doc) {
        return doc->error_line;
}

size_t xtce_document_input_capacity(const xtce_document* doc) {
        return doc->doc.input().capacity();
}
size_t xtce_document_output_capacity(const xtce_document* doc) {
        return doc->doc.output().capacity();
}

}  // extern "C"

xtce::Document* xtce_document_cxx(xtce_document* doc) { return &doc->doc; }
