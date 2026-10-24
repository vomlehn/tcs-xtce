/* C interface to the XTCE library.
 *
 * A document owns its input and output buffers, which are preallocated by
 * xtce_document_new() and reused by every load and serialize call until
 * xtce_document_free().  Pointers returned by the library point into those
 * buffers and stay valid until the next call that modifies the document.
 *
 * Full typed access to the data model is available from C++ through
 * xtce_document_cxx() and the headers in xtce/.
 */
#ifndef XTCE_XTCE_H
#define XTCE_XTCE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct xtce_document xtce_document;

/* Status codes. */
enum {
        XTCE_OK = 0,
        XTCE_ERROR = 1,     /* malformed or schema-invalid data; see
                               xtce_document_error() */
        XTCE_NO_MEMORY = 2, /* allocation failed */
};

/* XTCE version and XML namespace of the bundled schema. */
const char* xtce_schema_version(void);
const char* xtce_namespace(void);

/* Creates an empty document with preallocated buffers (0 = default size).
 * Returns NULL if allocation fails. */
xtce_document* xtce_document_new(size_t input_capacity, size_t output_capacity);
void xtce_document_free(xtce_document* doc);

/* Replaces the document by deserializing `len` bytes of XML. */
int xtce_document_load(xtce_document* doc, const void* data, size_t len);

/* Serializes into the document's output buffer.  On success *out and *out_len
 * describe the bytes (not NUL-terminated). */
int xtce_document_serialize(xtce_document* doc, const uint8_t** out,
                            size_t* out_len);

/* Pretty-print serialized output (default 1). */
void xtce_document_set_indent(xtce_document* doc, int indent);

/* Name of the root SpaceSystem (NUL-terminated, owned by the document). */
const char* xtce_document_name(const xtce_document* doc);
int xtce_document_set_name(xtce_document* doc, const char* name);

/* Message and 1-based line (0 if unknown) of the most recent error. */
const char* xtce_document_error(const xtce_document* doc);
size_t xtce_document_error_line(const xtce_document* doc);

/* Allocated sizes of the managed buffers. */
size_t xtce_document_input_capacity(const xtce_document* doc);
size_t xtce_document_output_capacity(const xtce_document* doc);

#ifdef __cplusplus
} /* extern "C" */

namespace xtce {
class Document;
}
/* The underlying C++ document, for typed access from C++. */
xtce::Document* xtce_document_cxx(xtce_document* doc);
#endif

#endif /* XTCE_XTCE_H */
