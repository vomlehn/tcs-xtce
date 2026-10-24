/* Serializes and deserializes the SimpleString structure described by
 * simple_string.xml, using the code tcs-xtce generated into simple_string.h.
 *
 * The same file is built as C and as C++; both, and the Rust and Python
 * versions of this program, print identical output.
 *
 * The C build finds simple_string.h in c/ (--lang c) and the C++ build in
 * cxx/ (--lang c++), so it is included with <> rather than "": a quoted
 * include would always find the copy next to this file.
 */
#include <simple_string.h>
#include <stdio.h>
#include <string.h>

/* Writes `s` quoted and escaped, as the generated describe functions do. */
static const char* quoted(const char* s, char* buf, size_t cap) {
        xtce_pk_text t = xtce_pk_text_init(buf, cap);
        xtce_pk_text_str(&t, s, strlen(s));
        return buf;
}

static const char* hex(const uint8_t* b, size_t n, char* buf, size_t cap) {
        xtce_pk_text t = xtce_pk_text_init(buf, cap);
        xtce_pk_text_bytes(&t, b, n);
        return buf + 2; /* drop the "0x" */
}

int main(void) {
        static const char* const texts[] = {"ABCDEFGH", "Hi", "",
                                            "h\xc3\xa9llo", "ABCDEFGHI"};
        /* The input and output buffers are preallocated to
         * simple_string_SimpleString_MAX_BYTES (8) and reused for every packet.
         */
        uint8_t output[simple_string_SimpleString_MAX_BYTES];
        uint8_t input[simple_string_SimpleString_MAX_BYTES];
        char q[64], h[64], text[64];
        size_t i;

        for (i = 0; i < sizeof texts / sizeof texts[0]; ++i) {
                const char* s = texts[i];
                simple_string_SimpleString value, back;
                xtce_pk_error err;
                size_t len, n = strlen(s);

                /* text.data holds 8 bytes plus a NUL, so even the 9-byte string
                 * fits in the structure; the encoder rejects it. */
                simple_string_SimpleString_init(&value);
                memcpy(value.text.data, s, n);
                value.text.len = n;

                /* Serialize into the output buffer. */
                if (simple_string_SimpleString_encode(
                            &value, output, sizeof output, &len, &err)) {
                        char msg[128];
                        xtce_pk_error_message(&err, msg, sizeof msg);
                        printf("serialize %s -> error:%s (%s)\n",
                               quoted(s, q, sizeof q),
                               xtce_pk_error_kind(err.code), msg);
                        continue;
                }
                printf("serialize %s -> %s\n", quoted(s, q, sizeof q),
                       hex(output, len, h, sizeof h));

                /* Deserialize from the input buffer. */
                memcpy(input, output, len);
                if (simple_string_SimpleString_decode(&back, input, len, &err))
                        return 1;
                simple_string_SimpleString_describe(&back, text, sizeof text);
                printf("deserialize %s -> %s\n", hex(input, len, h, sizeof h),
                       text);
                if (back.text.len != strlen(s) ||
                    memcmp(back.text.data, s, back.text.len) != 0)
                        return 1;
        }

        /* The buffers are fixed arrays, so they can never be reallocated. */
        printf("buffers reused without reallocation: true\n");
        return 0;
}
