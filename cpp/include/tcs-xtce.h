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

/** Gives the XTCE version of the bundled schema.
 *
 * @return  The version, a NUL-terminated string the library owns. */
const char* xtce_schema_version(void);

/** Gives the XML namespace of the bundled schema.
 *
 * @return  The namespace URI, a NUL-terminated string the library owns. */
const char* xtce_namespace(void);

/** Creates an empty document with preallocated buffers.
 *
 * @param input_capacity   Bytes to allocate for the input buffer; 0 for the
 *                         default size.
 * @param output_capacity  Bytes to allocate for the output buffer; 0 for
 *                         the default size.
 * @return                 The document, to be released with
 *                         xtce_document_free(), or NULL if allocation
 *                         fails. */
xtce_document* xtce_document_new(size_t input_capacity, size_t output_capacity);

/** Frees a document and its buffers.
 *
 * @param doc  The document, from xtce_document_new(). */
void xtce_document_free(xtce_document* doc);

/** Replaces the document by deserializing XML.
 *
 * @param doc   The document.
 * @param data  The XML.
 * @param len   The number of bytes at `data`.
 * @return      XTCE_OK, XTCE_ERROR if the XML is malformed or
 *              schema-invalid (see xtce_document_error()), or
 *              XTCE_NO_MEMORY. */
int xtce_document_load(xtce_document* doc, const void* data, size_t len);

/** Serializes into the document's output buffer.
 *
 * @param doc      The document.
 * @param out      Set on success to the serialized bytes (not
 *                 NUL-terminated), valid until the document is next
 *                 modified.
 * @param out_len  Set on success to the number of bytes at *out.
 * @return         XTCE_OK, XTCE_ERROR (see xtce_document_error()) or
 *                 XTCE_NO_MEMORY. */
int xtce_document_serialize(xtce_document* doc, const uint8_t** out,
                            size_t* out_len);

/** Sets whether serialized output is pretty-printed.
 *
 * @param doc     The document.
 * @param indent  Nonzero to pretty-print (the default), 0 not to. */
void xtce_document_set_indent(xtce_document* doc, int indent);

/** Gives the name of the root SpaceSystem.
 *
 * @param doc  The document.
 * @return     The name, NUL-terminated and owned by the document. */
const char* xtce_document_name(const xtce_document* doc);

/** Renames the root SpaceSystem.
 *
 * @param doc   The document.
 * @param name  The new name, NUL-terminated; it is copied.
 * @return      XTCE_OK, or XTCE_NO_MEMORY or XTCE_ERROR (see
 *              xtce_document_error()). */
int xtce_document_set_name(xtce_document* doc, const char* name);

/** Gives the message of the most recent error.
 *
 * @param doc  The document.
 * @return     The message, NUL-terminated and owned by the document; empty
 *             if the last call succeeded. */
const char* xtce_document_error(const xtce_document* doc);

/** Gives the line of the most recent error.
 *
 * @param doc  The document.
 * @return     The 1-based line in the input, or 0 if unknown. */
size_t xtce_document_error_line(const xtce_document* doc);

/** Gives the allocated size of the input buffer.
 *
 * @param doc  The document.
 * @return     The buffer's capacity in bytes. */
size_t xtce_document_input_capacity(const xtce_document* doc);

/** Gives the allocated size of the output buffer.
 *
 * @param doc  The document.
 * @return     The buffer's capacity in bytes. */
size_t xtce_document_output_capacity(const xtce_document* doc);

#ifdef __cplusplus
} /* extern "C" */

namespace xtce {
class Document;
}
/** Gives the underlying C++ document, for typed access from C++.
 *
 * @param doc  The document.
 * @return     Its xtce::Document, owned by `doc`. */
xtce::Document* xtce_document_cxx(xtce_document* doc);
#endif

#endif /* XTCE_XTCE_H */
