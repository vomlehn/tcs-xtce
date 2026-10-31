/* Sends two arrays of lorem ipsum lines from one thread to another over one
 * TCP connection, each as a Paragraph packet described by variable_array.xtce,
 * using the code tcs-xtce generated into variable_array.h.
 *
 * A Paragraph is a two-dimensional array whose dimensions both vary: an
 * array of lines, each an array of words.  The sender first writes the digest
 * of variable_array.xtce, which the receiver checks against the digest of its
 * own copy, so that both agree on the packets.  Before each packet the sender
 * writes a Length packet, also described by variable_array.xtce, giving its
 * size in bytes, so the receiver knows how many bytes to read; it then
 * writes the whole Paragraph in a single write.  The receiver checks that every
 * line and word arrived intact and in order, then prints each line with its
 * words separated by spaces.
 *
 * The same file is built as C and as C++; both, and the Rust and Python
 * versions of this program, print identical output.  It uses POSIX threads
 * and sockets.  Run it in the directory that holds variable_array.xtce.
 *
 * The C build finds variable_array.h in c/ (--lang c) and the C++ build in
 * cxx/ (--lang c++), so it is included with <> rather than "": a quoted
 * include would always find the copy next to this file.
 */
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <pthread.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
#include <variable_array.h>
#include <xtce-digest.h>

/* The lorem ipsum paragraph in five lines, as two arrays: three lines, then
 * two.  Each array ends with NULL. */
#define N_PARAGRAPHS 2

/* The XTCE file that describes the packets, and the size of its digest. */
#define XTCE "variable_array.xtce"
#define DIGEST_BYTES 16
static const char* const PARAGRAPH_1[] = {
        "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do "
        "eiusmod tempor incididunt ut",
        "labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud "
        "exercitation ullamco",
        "laboris nisi ut aliquip ex ea commodo consequat. Duis aute irure "
        "dolor in reprehenderit in",
        NULL,
};
static const char* const PARAGRAPH_2[] = {
        "voluptate velit esse cillum dolore eu fugiat nulla pariatur. "
        "Excepteur sint occaecat",
        "cupidatat non proident, sunt in culpa qui officia deserunt mollit "
        "anim id est laborum.",
        NULL,
};
static const char* const* const PARAGRAPHS[N_PARAGRAPHS] = {PARAGRAPH_1,
                                                            PARAGRAPH_2};

/** Prints an error message to standard error and exits with status 1.
 *
 * @param fmt  The message's printf format.
 * @param ...  The values it formats. */
static void fail(const char* fmt, ...) {
        va_list ap;
        va_start(ap, fmt);
        fputs("variable-array: ", stderr);
        vfprintf(stderr, fmt, ap);
        fputc('\n', stderr);
        va_end(ap);
        exit(1);
}

/** Makes the Paragraph for one array: one Line per line, one word per
 * element; exits if it doesn't fit the structure.
 *
 * @param n  The array's index in PARAGRAPHS.
 * @param p  Filled in with the Paragraph. */
static void paragraph(size_t n, variable_array_Paragraph* p) {
        size_t i;
        variable_array_Paragraph_init(p);
        for (i = 0; PARAGRAPHS[n][i]; ++i) {
                if (i == sizeof p->lines.data / sizeof p->lines.data[0])
                        fail("too many lines");
                variable_array_Line* line = &p->lines.data[i];
                const char* s = PARAGRAPHS[n][i];
                while (*s) {
                        variable_array_str15* word;
                        size_t len = strcspn(s, " ");
                        if (line->words.len ==
                            sizeof line->words.data /
                                    sizeof line->words.data[0])
                                fail("line %zu has too many words", i + 1);
                        word = &line->words.data[line->words.len++];
                        if (len >= sizeof word->data)
                                fail("\"%.*s\" is too long", (int)len, s);
                        memcpy(word->data, s, len);
                        word->len = len;
                        s += len;
                        if (*s == ' ') ++s;
                }
                /* WordCount precedes Words and gives their number. */
                line->word_count = (uint8_t)line->words.len;
        }
        p->lines.len = i;
}

/** Exits, describing the first difference between two Paragraphs, if
 * there is one.
 *
 * @param n     The array's index, for the message.
 * @param got   The Paragraph received.
 * @param want  The Paragraph expected. */
static void check(size_t n, const variable_array_Paragraph* got,
                  const variable_array_Paragraph* want) {
        size_t i, j;
        if (got->lines.len != want->lines.len)
                fail("array %zu: received %zu lines, expected %zu", n + 1,
                     got->lines.len, want->lines.len);
        for (i = 0; i < got->lines.len; ++i) {
                const variable_array_Line* g = &got->lines.data[i];
                const variable_array_Line* w = &want->lines.data[i];
                if (g->words.len != w->words.len)
                        fail("array %zu: line %zu has %zu words, expected %zu",
                             n + 1, i + 1, g->words.len, w->words.len);
                for (j = 0; j < g->words.len; ++j) {
                        const variable_array_str15* gw = &g->words.data[j];
                        const variable_array_str15* ww = &w->words.data[j];
                        if (gw->len != ww->len ||
                            memcmp(gw->data, ww->data, gw->len) != 0)
                                fail("array %zu: line %zu word %zu is \"%s\", "
                                     "expected \"%s\"",
                                     n + 1, i + 1, j + 1, gw->data, ww->data);
                }
        }
}

/** Sends bytes on a socket, in as many writes as it takes; exits on an
 * error.
 *
 * @param fd   The socket.
 * @param buf  The bytes.
 * @param len  How many there are. */
static void send_all(int fd, const uint8_t* buf, size_t len) {
        while (len > 0) {
                ssize_t r = send(fd, buf, len, 0);
                if (r < 0 && errno == EINTR) continue;
                if (r < 0) fail("send: %s", strerror(errno));
                buf += r;
                len -= (size_t)r;
        }
}

/** The sender thread: connects, sends the digest of the XTCE file, then
 * sends each array as a Length packet and then the Paragraph packet in a
 * single write, and closes the connection.  Exits on an error.
 *
 * @param arg  The receiver's address, a struct sockaddr_in.
 * @return     NULL. */
static void* send_paragraphs(void* arg) {
        const struct sockaddr_in* addr = (const struct sockaddr_in*)arg;
        /* The output buffers are preallocated to
         * variable_array_Paragraph_MAX_BYTES and
         * variable_array_Length_MAX_BYTES (4) and reused for both arrays. */
        static uint8_t output[variable_array_Paragraph_MAX_BYTES];
        uint8_t length_output[variable_array_Length_MAX_BYTES];
        static variable_array_Paragraph value;
        uint8_t digest[DIGEST_BYTES];
        xtce_digest_error derr;
        size_t n;
        int fd = socket(AF_INET, SOCK_STREAM, 0);

        if (fd < 0 ||
            connect(fd, (const struct sockaddr*)addr, sizeof *addr) != 0)
                fail("connect: %s", strerror(errno));
        /* First the digest, so the receiver can check that it uses the same
         * definitions of Length and Paragraph. */
        if (xtce_digest_file_bytes(XTCE, digest, &derr))
                fail("%s: %s", XTCE, derr.message);
        send_all(fd, digest, sizeof digest);

        for (n = 0; n < N_PARAGRAPHS; ++n) {
                xtce_pk_error err;
                variable_array_Length length;
                size_t len, length_len;
                ssize_t sent;

                /* Serialize into the output buffer. */
                paragraph(n, &value);
                if (variable_array_Paragraph_encode(
                            &value, output, sizeof output, &len, &err)) {
                        char msg[128];
                        xtce_pk_error_message(&err, msg, sizeof msg);
                        fail("serialize array %zu: %s", n + 1, msg);
                }
                variable_array_Length_init(&length);
                length.byte_count = (uint32_t)len;
                if (variable_array_Length_encode(&length, length_output,
                                                 sizeof length_output,
                                                 &length_len, &err)) {
                        char msg[128];
                        xtce_pk_error_message(&err, msg, sizeof msg);
                        fail("serialize length: %s", msg);
                }
                send_all(fd, length_output, length_len);
                do sent = send(fd, output, len, 0);
                while (sent < 0 && errno == EINTR);
                if (sent < 0) fail("send: %s", strerror(errno));
                if ((size_t)sent != len)
                        fail("one write sent %zd of %zu bytes", sent, len);
        }

        /* Closing the connection tells the receiver there are no more arrays.
         */
        close(fd);
        return NULL;
}

/** Reads bytes from a socket, exiting if the stream ends part way
 * through.
 *
 * @param fd    The socket.
 * @param buf   Filled in with the bytes.
 * @param len   How many to read.
 * @param what  What they are, for the error message.
 * @return      1, or 0 if the stream ended before the first byte. */
static int read_all(int fd, uint8_t* buf, size_t len, const char* what) {
        size_t got = 0;
        while (got < len) {
                ssize_t r = recv(fd, buf + got, len - got, 0);
                if (r < 0 && errno == EINTR) continue;
                if (r < 0) fail("receive: %s", strerror(errno));
                if (r == 0) {
                        if (got == 0) return 0;
                        fail("stream closed in the middle of %s", what);
                }
                got += (size_t)r;
        }
        return 1;
}

/** Accepts the connection, checks the sender's XTCE digest, and receives
 * arrays until it closes: for each, reads and decodes the Length packet,
 * then reads that many bytes, decodes the Paragraph, checks it and prints
 * it.  Exits on a mismatch or error.
 *
 * @param listener  The listening socket. */
static void receive_paragraphs(int listener) {
        /* The input buffers are preallocated to
         * variable_array_Paragraph_MAX_BYTES and
         * variable_array_Length_MAX_BYTES (4) and reused for both arrays. */
        static uint8_t input[variable_array_Paragraph_MAX_BYTES];
        static variable_array_Paragraph got, want;
        uint8_t length_input[variable_array_Length_MAX_BYTES];
        size_t n = 0, i, j;
        uint8_t theirs[DIGEST_BYTES], ours[DIGEST_BYTES];
        char theirs_hex[33], ours_hex[33];
        xtce_digest_error derr;
        int fd = accept(listener, NULL, NULL);

        if (fd < 0) fail("accept: %s", strerror(errno));
        /* The sender's digest must be the digest of this side's XTCE file:
         * then both read and write the packets the same way. */
        if (!read_all(fd, theirs, sizeof theirs, "the sender's XTCE digest"))
                fail("stream closed before the sender's XTCE digest");
        if (xtce_digest_file_bytes(XTCE, ours, &derr))
                fail("%s: %s", XTCE, derr.message);
        xtce_digest_hex(theirs, theirs_hex);
        xtce_digest_hex(ours, ours_hex);
        if (memcmp(theirs, ours, sizeof ours) != 0)
                fail("the sender's XTCE digest %s is not %s, the digest of %s",
                     theirs_hex, ours_hex, XTCE);
        printf("XTCE digest: %s, the same on both sides\n", ours_hex);
        while (read_all(fd, length_input, sizeof length_input, "an array")) {
                xtce_pk_error err;
                variable_array_Length length;
                size_t len;

                if (variable_array_Length_decode(&length, length_input,
                                                 sizeof length_input, &err)) {
                        char msg[128];
                        xtce_pk_error_message(&err, msg, sizeof msg);
                        fail("deserialize length: %s", msg);
                }
                len = length.byte_count;

                if (len > sizeof input)
                        fail("length %zu is more than a Paragraph's %zu", len,
                             (size_t)variable_array_Paragraph_MAX_BYTES);
                if (len > 0 && !read_all(fd, input, len, "an array"))
                        fail("stream closed in the middle of an array");
                if (n == N_PARAGRAPHS)
                        fail("received more arrays than were sent");

                /* Deserialize from the input buffer. */
                if (variable_array_Paragraph_decode(&got, input, len, &err)) {
                        char msg[128];
                        xtce_pk_error_message(&err, msg, sizeof msg);
                        fail("deserialize array %zu: %s", n + 1, msg);
                }
                paragraph(n, &want);
                check(n, &got, &want);

                for (i = 0; i < got.lines.len; ++i) {
                        const variable_array_Line* line = &got.lines.data[i];
                        for (j = 0; j < line->words.len; ++j) {
                                if (j > 0) putchar(' ');
                                fwrite(line->words.data[j].data, 1,
                                       line->words.data[j].len, stdout);
                        }
                        putchar('\n');
                }
                ++n;
        }
        close(fd);

        if (n != N_PARAGRAPHS)
                fail("stream closed after %zu of %d arrays", n, N_PARAGRAPHS);
}

/** Sends the arrays from a thread to this one over a loopback TCP
 * connection, printing them as they arrive; exits with status 1 on an
 * error.
 *
 * @return  0. */
int main(void) {
        struct sockaddr_in addr;
        socklen_t addr_len = sizeof addr;
        pthread_t sender;
        int listener = socket(AF_INET, SOCK_STREAM, 0);

        /* Listen on a port the system chooses, on the loopback interface only.
         */
        memset(&addr, 0, sizeof addr);
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
        addr.sin_port = 0;
        if (listener < 0 ||
            bind(listener, (struct sockaddr*)&addr, sizeof addr) != 0 ||
            listen(listener, 1) != 0 ||
            getsockname(listener, (struct sockaddr*)&addr, &addr_len) != 0)
                fail("listen: %s", strerror(errno));

        if (pthread_create(&sender, NULL, send_paragraphs, &addr) != 0)
                fail("cannot start the sender thread");
        receive_paragraphs(listener);
        pthread_join(sender, NULL);
        close(listener);
        return 0;
}
