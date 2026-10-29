/* Writes or reads the words of the lorem ipsum paragraph as Word packets
 * described by to_from.xml, using the code tcs-xtce generated into
 * to_from.h.
 *
 * `to_from_c write` writes the digest of to_from.xml, then one packet per
 * word, to standard output.  `to_from_c read` reads the digest from standard
 * input and checks that it is the digest of its own to_from.xml, so that both
 * programs agree on what a Word is; it then reads packets and prints the
 * words as they arrive, separated by spaces, and a newline at the end of the
 * input.  It checks that every word arrives intact and in order.  Run it in
 * the directory that holds to_from.xml.
 *
 * The same file is built as C and as C++.  Both, and the Rust and Python
 * versions of this program, write the same bytes and print the same words,
 * so any of them can read what any other wrote.  The to-from script in the
 * directory above connects a writer to a reader.
 *
 * The C build finds to_from.h in c/ (--lang c) and the C++ build in cxx/
 * (--lang c++), so it is included with <> rather than "": a quoted include
 * would always find the copy next to this file.
 */
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <to_from.h>
#include <xtce/digest.h>

static const char LOREM_IPSUM[] =
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do "
        "eiusmod tempor incididunt ut labore et dolore magna aliqua. Ut enim "
        "ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut "
        "aliquip ex ea commodo consequat. Duis aute irure dolor in "
        "reprehenderit in voluptate velit esse cillum dolore eu fugiat nulla "
        "pariatur. Excepteur sint occaecat cupidatat non proident, sunt in "
        "culpa qui officia deserunt mollit anim id est laborum.";

/* The XTCE file that describes a Word, and the size of its digest. */
#define XTCE "to_from.xml"
#define DIGEST_BYTES 16

/* The size of a Word's size tag: 32 bits, most significant byte first, as
 * to_from.xml gives it. */
#define TAG_BYTES 4

/** Prints an error message to standard error and exits with status 1.
 *
 * @param fmt  The message's printf format.
 * @param ...  The values it formats. */
static void fail(const char* fmt, ...) {
        va_list ap;
        va_start(ap, fmt);
        fputs("to-from: ", stderr);
        vfprintf(stderr, fmt, ap);
        fputc('\n', stderr);
        va_end(ap);
        exit(1);
}

/** Finds the next word of LOREM_IPSUM.
 *
 * @param pos   Where to start looking; moved past the word found.
 * @param word  Filled in with where the word starts.
 * @param len   Filled in with its length in bytes.
 * @return      1, or 0 after the last word. */
static int next_word(size_t* pos, const char** word, size_t* len) {
        size_t i = *pos, start;
        while (LOREM_IPSUM[i] == ' ') ++i;
        if (LOREM_IPSUM[i] == '\0') return 0;
        start = i;
        while (LOREM_IPSUM[i] != ' ' && LOREM_IPSUM[i] != '\0') ++i;
        *word = LOREM_IPSUM + start;
        *len = i - start;
        *pos = i;
        return 1;
}

/** Writes the XTCE file's digest, then every word, one Word packet each,
 * to standard output; exits on an error. */
static void write_words(void) {
        /* The output buffer is preallocated to to_from_Word_MAX_BYTES (36) and
         * reused for every packet. */
        uint8_t output[to_from_Word_MAX_BYTES];
        uint8_t digest[DIGEST_BYTES];
        size_t pos = 0, n, len;
        const char* text;
        xtce_digest_error derr;

        /* First the digest of the XTCE file, so the reader can check that it
         * uses the same definition of a Word. */
        if (xtce_digest_file_bytes(XTCE, digest, &derr))
                fail("%s: %s", XTCE, derr.message);
        if (fwrite(digest, 1, sizeof digest, stdout) != sizeof digest)
                fail("write failed");
        while (next_word(&pos, &text, &n)) {
                to_from_Word word;
                xtce_pk_error err;

                to_from_Word_init(&word);
                if (n >= sizeof word.text.data)
                        fail("\"%.*s\" is too long", (int)n, text);
                memcpy(word.text.data, text, n);
                word.text.len = n;

                /* Serialize into the output buffer. */
                if (to_from_Word_encode(&word, output, sizeof output, &len,
                                        &err)) {
                        char msg[128];
                        xtce_pk_error_message(&err, msg, sizeof msg);
                        fail("serialize \"%.*s\": %s", (int)n, text, msg);
                }
                if (fwrite(output, 1, len, stdout) != len) fail("write failed");
        }
        if (fflush(stdout) != 0) fail("write failed");
}

/** Reads bytes from standard input, exiting if it ends part way through.
 *
 * @param buf   Filled in with the bytes.
 * @param len   How many to read.
 * @param what  What they are, for the error message.
 * @return      1, or 0 if the input ended before the first byte. */
static int read_all(uint8_t* buf, size_t len, const char* what) {
        size_t got = fread(buf, 1, len, stdin);
        if (ferror(stdin)) fail("read failed");
        if (got == len) return 1;
        if (got == 0) return 0;
        fail("input ended in the middle of %s", what);
        return 0;
}

/** Reads the writer's digest and words from standard input until it ends,
 * printing each word and checking it against the next expected one;
 * exits on a mismatch or error. */
static void read_words(void) {
        /* The input buffer is preallocated to to_from_Word_MAX_BYTES and reused
         * for every packet. */
        uint8_t input[to_from_Word_MAX_BYTES];
        size_t pos = 0, count = 0, want_len;
        const char* want;
        uint8_t theirs[DIGEST_BYTES], ours[DIGEST_BYTES];
        char theirs_hex[33], ours_hex[33];
        xtce_digest_error derr;

        /* The writer's digest must be the digest of this program's XTCE
         * file: then both read and write a Word the same way. */
        if (!read_all(theirs, sizeof theirs, "the writer's XTCE digest"))
                fail("input ended before the writer's XTCE digest");
        if (xtce_digest_file_bytes(XTCE, ours, &derr))
                fail("%s: %s", XTCE, derr.message);
        xtce_digest_hex(theirs, theirs_hex);
        xtce_digest_hex(ours, ours_hex);
        if (memcmp(theirs, ours, sizeof ours) != 0)
                fail("the writer's XTCE digest %s is not %s, the digest of %s",
                     theirs_hex, ours_hex, XTCE);
        printf("XTCE digest: %s, the same on both sides\n", ours_hex);

        /* A packet is its 32-bit size tag followed by that many bytes of text,
         * so the tag says how much more of the input belongs to this word. */
        while (read_all(input, TAG_BYTES, "a word")) {
                to_from_Word word;
                xtce_pk_error err;
                uint32_t size = (uint32_t)input[0] << 24 |
                                (uint32_t)input[1] << 16 |
                                (uint32_t)input[2] << 8 | input[3];

                if (size > sizeof input - TAG_BYTES)
                        fail("size tag %lu is too large", (unsigned long)size);
                if (size > 0 && !read_all(input + TAG_BYTES, size, "a word"))
                        fail("input ended in the middle of a word");

                /* Deserialize from the input buffer. */
                if (to_from_Word_decode(&word, input, TAG_BYTES + size, &err)) {
                        char msg[128];
                        xtce_pk_error_message(&err, msg, sizeof msg);
                        fail("deserialize: %s", msg);
                }
                if (!next_word(&pos, &want, &want_len))
                        fail("unexpected extra word \"%s\"", word.text.data);
                if (word.text.len != want_len ||
                    memcmp(word.text.data, want, want_len) != 0)
                        fail("word %zu is \"%s\", expected \"%.*s\"", count + 1,
                             word.text.data, (int)want_len, want);

                if (count > 0) putchar(' ');
                fwrite(word.text.data, 1, word.text.len, stdout);
                fflush(stdout);
                ++count;
        }

        if (next_word(&pos, &want, &want_len))
                fail("input ended after %zu words, before \"%.*s\"", count,
                     (int)want_len, want);
        putchar('\n');
}

/** Writes or reads the words.
 *
 * @param argc  The number of arguments, 2.
 * @param argv  The arguments: "write" or "read".
 * @return      0, or 2 for a usage error; an error exits with status 1. */
int main(int argc, char** argv) {
        if (argc == 2 && strcmp(argv[1], "read") == 0) {
                read_words();
        } else if (argc == 2 && strcmp(argv[1], "write") == 0) {
                write_words();
        } else {
                fprintf(stderr, "usage: %s read|write\n", argv[0]);
                return 2;
        }
        return 0;
}
