/* An MD5 digest of what an XTCE document means, for C and C++.
 *
 * Two programs that exchange packets were built from the same packet
 * definitions if their XTCE documents have the same digest, so each can send
 * the other its digest and compare, rather than describing the data in every
 * message.  The Python (xtce.digest) and Rust (tcs_xtce::digest) runtimes
 * compute the same digest; the Python module describes exactly what it
 * covers.  In short, it leaves out comments and processing instructions,
 * whitespace between elements and around text, the order of attributes,
 * namespace prefixes, and the order of the children of elements that hold
 * unordered collections (xtce_digest_unordered).
 *
 * The document is read as XML 1.0 in UTF-8.  It is an error for it to be
 * malformed, to declare another encoding, to have a DOCTYPE, or to have a
 * root element other than SpaceSystem.
 *
 * This header needs no library, like xtce/packet.h.  Computing a digest
 * allocates memory with malloc, so do it at start-up, not in code that must
 * not allocate.  The functions return 0 on success, or -1 after filling in
 * *err (which may be NULL).
 */
#ifndef XTCE_DIGEST_H
#define XTCE_DIGEST_H

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#ifdef __cplusplus
#include <stdexcept>
#include <string>
#include <string_view>
#endif

typedef struct xtce_digest_error {
        char message[160];
        size_t line; /* 1-based, or 0 when not tied to a position */
} xtce_digest_error;

/* Elements whose children may appear in any order without changing what the
 * document means.  Every runtime has the same list. */
static const char* const xtce_digest_unordered[] = {
        /* A SpaceSystem's nested SpaceSystems. */
        "SpaceSystem",
        /* Sets. */
        "AlgorithmSet", "AliasSet", "AncillaryDataSet", "ArgumentTypeSet",
        "AuthorSet", "CommandContainerSet", "ContainerRefSet", "ContainerSet",
        "ExternalAlgorithmSet", "HistorySet", "MessageRefSet", "MessageSet",
        "MetaCommandSet", "NoteSet", "ParameterSet", "ParameterTypeSet",
        "ParametersToSuspendAlarmsOnSet", "RateInStreamSet", "ServiceSet",
        "StreamSet", "TriggerSet", "UnitSet", "ValidRangeSet", "VerifierSet",
        /* Lists whose items combine without regard to order. */
        "ANDedConditions", "ArgumentAssignmentList", "ComparisonList",
        "EnumerationAlarmList", "EnumerationList", "ORedConditions",
        "PolynomialCalibrator", NULL};

/* --- MD5 (RFC 1321) ---------------------------------------------------- */

static inline uint32_t xtce_md5_rotl(uint32_t x, unsigned n) {
        return (x << n) | (x >> (32 - n));
}

/* The MD5 digest of the `len` bytes at `data`. */
static inline void xtce_md5(const uint8_t* data, size_t len, uint8_t out[16]) {
        static const uint32_t K[64] = {
                0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf,
                0x4787c62a, 0xa8304613, 0xfd469501, 0x698098d8, 0x8b44f7af,
                0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e,
                0x49b40821, 0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa,
                0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8, 0x21e1cde6,
                0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8,
                0x676f02d9, 0x8d2a4c8a, 0xfffa3942, 0x8771f681, 0x6d9d6122,
                0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
                0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039,
                0xe6db99e5, 0x1fa27cf8, 0xc4ac5665, 0xf4292244, 0x432aff97,
                0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d,
                0x85845dd1, 0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1,
                0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391};
        static const unsigned S[64] = {
                7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
                5, 9,  14, 20, 5, 9,  14, 20, 5, 9,  14, 20, 5, 9,  14, 20,
                4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
                6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21};
        uint32_t h[4] = {0x67452301, 0xefcdab89, 0x98badcfe, 0x10325476};
        uint64_t bits = (uint64_t)len * 8;
        size_t total = ((len + 8) / 64 + 1) * 64, off, i;
        for (off = 0; off < total; off += 64) {
                uint8_t block[64];
                uint32_t m[16], a = h[0], b = h[1], c = h[2], d = h[3];
                for (i = 0; i < 64; ++i) {
                        size_t k = off + i;
                        if (k < len)
                                block[i] = data[k];
                        else if (k == len)
                                block[i] = 0x80;
                        else if (k >= total - 8)
                                block[i] = (uint8_t)(bits >>
                                                     (8 * (k - (total - 8))));
                        else
                                block[i] = 0;
                }
                for (i = 0; i < 16; ++i)
                        m[i] = (uint32_t)block[4 * i] |
                               (uint32_t)block[4 * i + 1] << 8 |
                               (uint32_t)block[4 * i + 2] << 16 |
                               (uint32_t)block[4 * i + 3] << 24;
                for (i = 0; i < 64; ++i) {
                        uint32_t f, t;
                        size_t g;
                        if (i < 16) {
                                f = (b & c) | (~b & d);
                                g = i;
                        } else if (i < 32) {
                                f = (d & b) | (~d & c);
                                g = (5 * i + 1) % 16;
                        } else if (i < 48) {
                                f = b ^ c ^ d;
                                g = (3 * i + 5) % 16;
                        } else {
                                f = c ^ (b | ~d);
                                g = (7 * i) % 16;
                        }
                        t = d;
                        d = c;
                        c = b;
                        b = b + xtce_md5_rotl(a + f + K[i] + m[g], S[i]);
                        a = t;
                }
                h[0] += a;
                h[1] += b;
                h[2] += c;
                h[3] += d;
        }
        for (i = 0; i < 16; ++i) out[i] = (uint8_t)(h[i / 4] >> (8 * (i % 4)));
}

/* `d` as 32 lowercase hex digits and a NUL. */
static inline void xtce_digest_hex(const uint8_t d[16], char hex[33]) {
        static const char digits[] = "0123456789abcdef";
        int i;
        for (i = 0; i < 16; ++i) {
                hex[2 * i] = digits[d[i] >> 4];
                hex[2 * i + 1] = digits[d[i] & 15];
        }
        hex[32] = 0;
}

/* --- growable byte buffers ---------------------------------------------- */

typedef struct xtce_dbuf {
        uint8_t* p;
        size_t n, cap;
        int failed; /* an allocation failed */
} xtce_dbuf;

static inline void xtce_dbuf_put(xtce_dbuf* b, const void* data, size_t n) {
        if (b->failed) return;
        if (b->n + n > b->cap) {
                size_t cap = b->cap ? b->cap : 64;
                uint8_t* p;
                while (cap < b->n + n) cap *= 2;
                p = (uint8_t*)realloc(b->p, cap);
                if (!p) {
                        b->failed = 1;
                        return;
                }
                b->p = p;
                b->cap = cap;
        }
        if (n) memcpy(b->p + b->n, data, n);
        b->n += n;
}

static inline void xtce_dbuf_byte(xtce_dbuf* b, uint8_t c) {
        xtce_dbuf_put(b, &c, 1);
}

/* A field: the length of `data` in decimal, a colon, and `data`. */
static inline void xtce_dbuf_field(xtce_dbuf* b, const uint8_t* data,
                                   size_t n) {
        char len[24];
        int k = snprintf(len, sizeof len, "%zu:", n);
        xtce_dbuf_put(b, len, (size_t)k);
        xtce_dbuf_put(b, data, n);
}

static inline void xtce_dbuf_free(xtce_dbuf* b) {
        free(b->p);
        b->p = NULL;
        b->n = b->cap = 0;
}

/* Bytes compared as Python and Rust compare them: the shorter is first when
 * one starts the other. */
static inline int xtce_dbuf_cmp(const void* x, const void* y) {
        const xtce_dbuf* a = (const xtce_dbuf*)x;
        const xtce_dbuf* b = (const xtce_dbuf*)y;
        size_t n = a->n < b->n ? a->n : b->n;
        int c = n ? memcmp(a->p, b->p, n) : 0;
        if (c) return c;
        return a->n < b->n ? -1 : a->n > b->n ? 1 : 0;
}

/* --- the parser ---------------------------------------------------------- */

typedef struct xtce_dns {
        const uint8_t* prefix; /* "" for the default namespace */
        size_t prefix_len;
        xtce_dbuf uri; /* empty when undeclared */
        size_t depth;
} xtce_dns;

typedef struct xtce_dparser {
        const uint8_t* s;
        size_t n, pos, depth;
        xtce_dns* ns;
        size_t n_ns, cap_ns;
        xtce_digest_error* err;
        int failed;
} xtce_dparser;

static inline int xtce_dp_fail(xtce_dparser* p, const char* message) {
        if (!p->failed && p->err) {
                size_t i, line = 1;
                for (i = 0; i < p->pos && i < p->n; ++i)
                        if (p->s[i] == '\n') ++line;
                snprintf(p->err->message, sizeof p->err->message, "%s",
                         message);
                p->err->line = line;
        }
        p->failed = 1;
        return -1;
}

static inline int xtce_dp_space(uint8_t c) {
        return c == ' ' || c == '\t' || c == '\n' || c == '\r';
}

static inline int xtce_dp_starts(const xtce_dparser* p, const char* lit) {
        size_t k = strlen(lit);
        return p->n - p->pos >= k && memcmp(p->s + p->pos, lit, k) == 0;
}

static inline void xtce_dp_skip_space(xtce_dparser* p) {
        while (p->pos < p->n && xtce_dp_space(p->s[p->pos])) ++p->pos;
}

/* Skips past `end`; sets *start and *len to what came before it. */
static inline int xtce_dp_past(xtce_dparser* p, const char* end,
                               const char* what, size_t* start, size_t* len) {
        size_t k = strlen(end), i;
        for (i = p->pos; i + k <= p->n; ++i) {
                if (memcmp(p->s + i, end, k) == 0) {
                        if (start) *start = p->pos;
                        if (len) *len = i - p->pos;
                        p->pos = i + k;
                        return 0;
                }
        }
        return xtce_dp_fail(p, what);
}

/* Comments, processing instructions and whitespace. */
static inline int xtce_dp_misc(xtce_dparser* p) {
        for (;;) {
                xtce_dp_skip_space(p);
                if (xtce_dp_starts(p, "<!--")) {
                        p->pos += 4;
                        if (xtce_dp_past(p, "-->", "unclosed comment", NULL,
                                         NULL))
                                return -1;
                } else if (xtce_dp_starts(p, "<?")) {
                        p->pos += 2;
                        if (xtce_dp_past(p, "?>",
                                         "unclosed processing instruction",
                                         NULL, NULL))
                                return -1;
                } else {
                        return 0;
                }
        }
}

static inline int xtce_dp_name(xtce_dparser* p, size_t* start, size_t* len) {
        *start = p->pos;
        while (p->pos < p->n && !xtce_dp_space(p->s[p->pos]) &&
               !strchr("/>=<\"'&", p->s[p->pos]))
                ++p->pos;
        *len = p->pos - *start;
        return *len ? 0 : xtce_dp_fail(p, "expected a name");
}

/* Appends code point `c` as UTF-8. */
static inline void xtce_dp_utf8(xtce_dbuf* b, uint32_t c) {
        uint8_t u[4];
        size_t k;
        if (c < 0x80) {
                u[0] = (uint8_t)c;
                k = 1;
        } else if (c < 0x800) {
                u[0] = (uint8_t)(0xc0 | c >> 6);
                u[1] = (uint8_t)(0x80 | (c & 0x3f));
                k = 2;
        } else if (c < 0x10000) {
                u[0] = (uint8_t)(0xe0 | c >> 12);
                u[1] = (uint8_t)(0x80 | ((c >> 6) & 0x3f));
                u[2] = (uint8_t)(0x80 | (c & 0x3f));
                k = 3;
        } else {
                u[0] = (uint8_t)(0xf0 | c >> 18);
                u[1] = (uint8_t)(0x80 | ((c >> 12) & 0x3f));
                u[2] = (uint8_t)(0x80 | ((c >> 6) & 0x3f));
                u[3] = (uint8_t)(0x80 | (c & 0x3f));
                k = 4;
        }
        xtce_dbuf_put(b, u, k);
}

/* Whether XML allows code point `c` (the production Char). */
static inline int xtce_dp_xml_char(uint32_t c) {
        return c == 0x9 || c == 0xA || c == 0xD || (c >= 0x20 && c <= 0xD7FF) ||
               (c >= 0xE000 && c <= 0xFFFD) || (c >= 0x10000 && c <= 0x10FFFF);
}

/* Appends the `n` bytes at `raw` with their references expanded; in an
 * attribute value, whitespace characters become spaces. */
static inline int xtce_dp_unescape(xtce_dparser* p, xtce_dbuf* out,
                                   const uint8_t* raw, size_t n, int attr) {
        size_t i = 0;
        while (i < n) {
                uint8_t c = raw[i];
                size_t end;
                const uint8_t* r;
                size_t rn;
                if (c != '&') {
                        if (attr && c == '<')
                                return xtce_dp_fail(
                                        p, "'<' in an attribute value");
                        xtce_dbuf_byte(out, attr && xtce_dp_space(c) ? ' ' : c);
                        ++i;
                        continue;
                }
                for (end = i + 1; end < n && raw[end] != ';'; ++end) {
                }
                if (end == n) return xtce_dp_fail(p, "unterminated reference");
                r = raw + i + 1;
                rn = end - i - 1;
                if (rn == 2 && memcmp(r, "lt", 2) == 0)
                        xtce_dbuf_byte(out, '<');
                else if (rn == 2 && memcmp(r, "gt", 2) == 0)
                        xtce_dbuf_byte(out, '>');
                else if (rn == 3 && memcmp(r, "amp", 3) == 0)
                        xtce_dbuf_byte(out, '&');
                else if (rn == 4 && memcmp(r, "quot", 4) == 0)
                        xtce_dbuf_byte(out, '"');
                else if (rn == 4 && memcmp(r, "apos", 4) == 0)
                        xtce_dbuf_byte(out, '\'');
                else if (rn >= 2 && r[0] == '#') {
                        int hex = r[1] == 'x' || r[1] == 'X';
                        size_t k = hex ? 2 : 1;
                        uint32_t code = 0;
                        if (k == rn) return xtce_dp_fail(p, "undefined entity");
                        for (; k < rn; ++k) {
                                uint8_t d = r[k];
                                uint32_t v;
                                if (d >= '0' && d <= '9')
                                        v = d - '0';
                                else if (hex && d >= 'a' && d <= 'f')
                                        v = d - 'a' + 10;
                                else if (hex && d >= 'A' && d <= 'F')
                                        v = d - 'A' + 10;
                                else
                                        return xtce_dp_fail(p,
                                                            "undefined entity");
                                code = code * (hex ? 16 : 10) + v;
                                if (code > 0x10FFFF)
                                        return xtce_dp_fail(p,
                                                            "undefined entity");
                        }
                        if (!xtce_dp_xml_char(code))
                                return xtce_dp_fail(p, "undefined entity");
                        xtce_dp_utf8(out, code);
                } else {
                        return xtce_dp_fail(p, "undefined entity");
                }
                i = end + 1;
        }
        return 0;
}

static inline const xtce_dbuf* xtce_dp_lookup(const xtce_dparser* p,
                                              const uint8_t* prefix, size_t n) {
        size_t i = p->n_ns;
        while (i-- > 0)
                if (p->ns[i].prefix_len == n &&
                    memcmp(p->ns[i].prefix, prefix, n) == 0)
                        return &p->ns[i].uri;
        return NULL;
}

/* Appends a name in Clark notation, {namespace}local or local.  An
 * unprefixed attribute is in no namespace. */
static inline int xtce_dp_expand(xtce_dparser* p, xtce_dbuf* out,
                                 const uint8_t* q, size_t n, int attr) {
        static const char xml_ns[] = "http://www.w3.org/XML/1998/namespace";
        const uint8_t* colon = (const uint8_t*)memchr(q, ':', n);
        size_t plen = colon ? (size_t)(colon - q) : 0;
        const uint8_t* local = colon ? colon + 1 : q;
        size_t llen = n - (colon ? plen + 1 : 0);
        const uint8_t* uri = NULL;
        size_t ulen = 0;
        if (!colon && attr) {
                xtce_dbuf_put(out, q, n);
                return 0;
        }
        if (colon && plen == 3 && memcmp(q, "xml", 3) == 0) {
                uri = (const uint8_t*)xml_ns;
                ulen = sizeof xml_ns - 1;
        } else {
                const xtce_dbuf* u = xtce_dp_lookup(p, q, plen);
                if (u && u->n) {
                        uri = u->p;
                        ulen = u->n;
                } else if (colon) {
                        return xtce_dp_fail(p, "unbound prefix");
                }
        }
        if (uri) {
                xtce_dbuf_byte(out, '{');
                xtce_dbuf_put(out, uri, ulen);
                xtce_dbuf_byte(out, '}');
        }
        xtce_dbuf_put(out, local, llen);
        return 0;
}

typedef struct xtce_dattr {
        size_t at, len; /* where the name is in the document */
        xtce_dbuf name, value;
} xtce_dattr;

static inline int xtce_dattr_cmp(const void* x, const void* y) {
        return xtce_dbuf_cmp(&((const xtce_dattr*)x)->name,
                             &((const xtce_dattr*)y)->name);
}

/* A run of text ends: its encoding, if anything is left once whitespace is
 * trimmed from both ends. */
static inline void xtce_dp_flush(xtce_dbuf* text, xtce_dbuf** items,
                                 size_t* n_items, size_t* cap) {
        size_t a = 0, b = text->n;
        while (a < b && xtce_dp_space(text->p[a])) ++a;
        while (b > a && xtce_dp_space(text->p[b - 1])) --b;
        if (b > a) {
                xtce_dbuf item = {NULL, 0, 0, 0};
                if (*n_items == *cap) {
                        size_t c = *cap ? *cap * 2 : 8;
                        xtce_dbuf* grown =
                                (xtce_dbuf*)realloc(*items, c * sizeof **items);
                        if (!grown) {
                                text->failed = 1;
                                return;
                        }
                        *items = grown;
                        *cap = c;
                }
                xtce_dbuf_byte(&item, 'T');
                xtce_dbuf_field(&item, text->p + a, b - a);
                if (item.failed) text->failed = 1;
                (*items)[(*n_items)++] = item;
        }
        text->n = 0;
}

static inline int xtce_dp_push_item(xtce_dbuf** items, size_t* n_items,
                                    size_t* cap, xtce_dbuf item) {
        if (*n_items == *cap) {
                size_t c = *cap ? *cap * 2 : 8;
                xtce_dbuf* grown =
                        (xtce_dbuf*)realloc(*items, c * sizeof **items);
                if (!grown) return -1;
                *items = grown;
                *cap = c;
        }
        (*items)[(*n_items)++] = item;
        return 0;
}

static inline int xtce_dp_unordered(const uint8_t* local, size_t n) {
        int i;
        for (i = 0; xtce_digest_unordered[i]; ++i)
                if (strlen(xtce_digest_unordered[i]) == n &&
                    memcmp(xtce_digest_unordered[i], local, n) == 0)
                        return 1;
        return 0;
}

/* An element, from its '<', appended to `out` as its encoding. */
static inline int xtce_dp_element(xtce_dparser* p, xtce_dbuf* out, int root) {
        size_t qs, qn, i, n_attrs = 0, cap_attrs = 0, n_items = 0;
        size_t cap_items = 0, ns_mark = p->n_ns, local_at;
        xtce_dattr* attrs = NULL;
        xtce_dbuf* items = NULL;
        xtce_dbuf tag = {NULL, 0, 0, 0}, a = {NULL, 0, 0, 0};
        xtce_dbuf body = {NULL, 0, 0, 0}, text = {NULL, 0, 0, 0};
        int empty = 0, rc = -1;
        ++p->pos;
        ++p->depth;
        if (xtce_dp_name(p, &qs, &qn)) goto done;
        for (;;) {
                size_t before = p->pos, ns, nn, vs = 0, vn = 0;
                uint8_t q;
                xtce_dbuf value = {NULL, 0, 0, 0};
                xtce_dp_skip_space(p);
                if (xtce_dp_starts(p, "/>")) {
                        p->pos += 2;
                        empty = 1;
                        break;
                }
                if (xtce_dp_starts(p, ">")) {
                        ++p->pos;
                        break;
                }
                if (p->pos == before) {
                        xtce_dp_fail(p, "expected whitespace or '>'");
                        goto done;
                }
                if (xtce_dp_name(p, &ns, &nn)) goto done;
                xtce_dp_skip_space(p);
                if (!xtce_dp_starts(p, "=")) {
                        xtce_dp_fail(p, "expected '='");
                        goto done;
                }
                ++p->pos;
                xtce_dp_skip_space(p);
                if (p->pos >= p->n ||
                    (p->s[p->pos] != '"' && p->s[p->pos] != '\'')) {
                        xtce_dp_fail(p, "expected a quoted value");
                        goto done;
                }
                q = p->s[p->pos++];
                if (xtce_dp_past(p, q == '"' ? "\"" : "'",
                                 "unterminated attribute value", &vs, &vn))
                        goto done;
                if (xtce_dp_unescape(p, &value, p->s + vs, vn, 1)) {
                        xtce_dbuf_free(&value);
                        goto done;
                }
                if ((nn == 5 && memcmp(p->s + ns, "xmlns", 5) == 0) ||
                    (nn > 6 && memcmp(p->s + ns, "xmlns:", 6) == 0)) {
                        xtce_dns d;
                        if (p->n_ns == p->cap_ns) {
                                size_t c = p->cap_ns ? p->cap_ns * 2 : 8;
                                xtce_dns* g = (xtce_dns*)realloc(
                                        p->ns, c * sizeof *p->ns);
                                if (!g) {
                                        xtce_dbuf_free(&value);
                                        xtce_dp_fail(p, "out of memory");
                                        goto done;
                                }
                                p->ns = g;
                                p->cap_ns = c;
                        }
                        d.prefix = nn == 5 ? p->s + ns : p->s + ns + 6;
                        d.prefix_len = nn == 5 ? 0 : nn - 6;
                        d.uri = value;
                        d.depth = p->depth;
                        p->ns[p->n_ns++] = d;
                        continue;
                }
                if (n_attrs == cap_attrs) {
                        size_t c = cap_attrs ? cap_attrs * 2 : 8;
                        xtce_dattr* g =
                                (xtce_dattr*)realloc(attrs, c * sizeof *attrs);
                        if (!g) {
                                xtce_dbuf_free(&value);
                                xtce_dp_fail(p, "out of memory");
                                goto done;
                        }
                        attrs = g;
                        cap_attrs = c;
                }
                /* The name is expanded once the element's namespace
                 * declarations are all known; keep where it is for now. */
                memset(&attrs[n_attrs], 0, sizeof attrs[n_attrs]);
                attrs[n_attrs].at = ns;
                attrs[n_attrs].len = nn;
                attrs[n_attrs].value = value;
                ++n_attrs;
        }
        if (xtce_dp_expand(p, &tag, p->s + qs, qn, 0)) goto done;
        for (local_at = tag.n; local_at > 0 && tag.p[local_at - 1] != '}';
             --local_at) {
        }
        if (root && !(tag.n - local_at == 11 &&
                      memcmp(tag.p + local_at, "SpaceSystem", 11) == 0)) {
                xtce_dp_fail(p, "the root element is not SpaceSystem");
                goto done;
        }
        for (i = 0; i < n_attrs; ++i)
                if (xtce_dp_expand(p, &attrs[i].name, p->s + attrs[i].at,
                                   attrs[i].len, 1))
                        goto done;
        if (n_attrs) qsort(attrs, n_attrs, sizeof *attrs, xtce_dattr_cmp);
        for (i = 1; i < n_attrs; ++i)
                if (xtce_dbuf_cmp(&attrs[i - 1].name, &attrs[i].name) == 0) {
                        xtce_dp_fail(p, "duplicate attribute");
                        goto done;
                }
        while (!empty) {
                size_t lt = p->pos, ns, nn;
                while (lt < p->n && p->s[lt] != '<') ++lt;
                if (lt == p->n) {
                        xtce_dp_fail(p, "unclosed element");
                        goto done;
                }
                if (xtce_dp_unescape(p, &text, p->s + p->pos, lt - p->pos, 0))
                        goto done;
                p->pos = lt;
                if (xtce_dp_starts(p, "<!--")) {
                        p->pos += 4;
                        if (xtce_dp_past(p, "-->", "unclosed comment", NULL,
                                         NULL))
                                goto done;
                } else if (xtce_dp_starts(p, "<![CDATA[")) {
                        size_t s0, sn;
                        p->pos += 9;
                        if (xtce_dp_past(p, "]]>", "unclosed CDATA section",
                                         &s0, &sn))
                                goto done;
                        xtce_dbuf_put(&text, p->s + s0, sn);
                } else if (xtce_dp_starts(p, "<?")) {
                        p->pos += 2;
                        if (xtce_dp_past(p, "?>",
                                         "unclosed processing instruction",
                                         NULL, NULL))
                                goto done;
                } else if (xtce_dp_starts(p, "</")) {
                        p->pos += 2;
                        if (xtce_dp_name(p, &ns, &nn)) goto done;
                        if (nn != qn || memcmp(p->s + ns, p->s + qs, qn)) {
                                xtce_dp_fail(p, "mismatched tag");
                                goto done;
                        }
                        xtce_dp_skip_space(p);
                        if (!xtce_dp_starts(p, ">")) {
                                xtce_dp_fail(p, "expected '>'");
                                goto done;
                        }
                        ++p->pos;
                        xtce_dp_flush(&text, &items, &n_items, &cap_items);
                        break;
                } else if (xtce_dp_starts(p, "<!")) {
                        xtce_dp_fail(p, "unexpected markup declaration");
                        goto done;
                } else {
                        xtce_dbuf child = {NULL, 0, 0, 0};
                        xtce_dp_flush(&text, &items, &n_items, &cap_items);
                        if (xtce_dp_element(p, &child, 0) ||
                            xtce_dp_push_item(&items, &n_items, &cap_items,
                                              child)) {
                                xtce_dbuf_free(&child);
                                if (!p->failed)
                                        xtce_dp_fail(p, "out of memory");
                                goto done;
                        }
                }
        }
        if (n_items && xtce_dp_unordered(tag.p + local_at, tag.n - local_at))
                qsort(items, n_items, sizeof *items, xtce_dbuf_cmp);
        for (i = 0; i < n_attrs; ++i) {
                xtce_dbuf_field(&a, attrs[i].name.p, attrs[i].name.n);
                xtce_dbuf_field(&a, attrs[i].value.p, attrs[i].value.n);
        }
        for (i = 0; i < n_items; ++i)
                xtce_dbuf_put(&body, items[i].p, items[i].n);
        xtce_dbuf_byte(out, 'E');
        xtce_dbuf_field(out, tag.p, tag.n);
        xtce_dbuf_field(out, a.p, a.n);
        xtce_dbuf_field(out, body.p, body.n);
        if (out->failed || a.failed || body.failed || tag.failed ||
            text.failed) {
                xtce_dp_fail(p, "out of memory");
                goto done;
        }
        rc = 0;
done:
        for (i = 0; i < n_attrs; ++i) {
                xtce_dbuf_free(&attrs[i].name);
                xtce_dbuf_free(&attrs[i].value);
        }
        free(attrs);
        for (i = 0; i < n_items; ++i) xtce_dbuf_free(&items[i]);
        free(items);
        while (p->n_ns > ns_mark) xtce_dbuf_free(&p->ns[--p->n_ns].uri);
        xtce_dbuf_free(&tag);
        xtce_dbuf_free(&a);
        xtce_dbuf_free(&body);
        xtce_dbuf_free(&text);
        --p->depth;
        return rc;
}

/* Checks that `s` is UTF-8 holding only characters XML allows. */
static inline int xtce_dp_check_utf8(const uint8_t* s, size_t n,
                                     xtce_digest_error* err) {
        size_t i = 0;
        while (i < n) {
                uint32_t c = s[i];
                size_t k, j;
                if (c < 0x80)
                        k = 0;
                else if ((c & 0xe0) == 0xc0) {
                        k = 1;
                        c &= 0x1f;
                } else if ((c & 0xf0) == 0xe0) {
                        k = 2;
                        c &= 0x0f;
                } else if ((c & 0xf8) == 0xf0) {
                        k = 3;
                        c &= 0x07;
                } else
                        goto bad;
                for (j = 1; j <= k; ++j) {
                        if (i + j >= n || (s[i + j] & 0xc0) != 0x80) goto bad;
                        c = c << 6 | (s[i + j] & 0x3f);
                }
                /* Overlong forms, surrogates and values beyond U+10FFFF. */
                if ((k == 1 && c < 0x80) || (k == 2 && c < 0x800) ||
                    (k == 3 && c < 0x10000) || (c >= 0xD800 && c <= 0xDFFF))
                        goto bad;
                if (!xtce_dp_xml_char(c)) {
                        if (err) {
                                snprintf(err->message, sizeof err->message,
                                         "character U+%04X is not allowed "
                                         "in XML",
                                         (unsigned)c);
                                err->line = 0;
                        }
                        return -1;
                }
                i += k + 1;
        }
        return 0;
bad:
        if (err) {
                snprintf(err->message, sizeof err->message,
                         "the document is not UTF-8");
                err->line = 0;
        }
        return -1;
}

/* The value of name="..." in the XML declaration `d` (`n` bytes), or NULL. */
static inline const uint8_t* xtce_dp_pseudo(const uint8_t* d, size_t n,
                                            const char* name, size_t* len) {
        size_t k = strlen(name), i, j;
        for (i = 0; i + k <= n; ++i) {
                if (memcmp(d + i, name, k) != 0) continue;
                j = i + k;
                while (j < n && xtce_dp_space(d[j])) ++j;
                if (j >= n || d[j] != '=') return NULL;
                ++j;
                while (j < n && xtce_dp_space(d[j])) ++j;
                if (j >= n || (d[j] != '"' && d[j] != '\'')) return NULL;
                {
                        uint8_t q = d[j];
                        size_t s = ++j;
                        while (j < n && d[j] != q) ++j;
                        if (j >= n) return NULL;
                        *len = j - s;
                        return d + s;
                }
        }
        return NULL;
}

/* The canonical encoding of the XTCE document at `data`, whose MD5 digest is
 * its digest, in *out (which the caller frees with xtce_dbuf_free). */
static inline int xtce_digest_encoding(const uint8_t* data, size_t len,
                                       xtce_dbuf* out, xtce_digest_error* err) {
        xtce_dbuf text = {NULL, 0, 0, 0};
        xtce_dparser p;
        size_t i;
        int rc = -1;
        if (len >= 3 && memcmp(data, "\xef\xbb\xbf", 3) == 0) {
                data += 3;
                len -= 3;
        }
        if (xtce_dp_check_utf8(data, len, err)) return -1;
        /* XML normalizes every line end to a line feed first. */
        for (i = 0; i < len; ++i) {
                if (data[i] == '\r') {
                        xtce_dbuf_byte(&text, '\n');
                        if (i + 1 < len && data[i + 1] == '\n') ++i;
                } else {
                        xtce_dbuf_byte(&text, data[i]);
                }
        }
        if (text.failed) {
                if (err) {
                        snprintf(err->message, sizeof err->message,
                                 "out of memory");
                        err->line = 0;
                }
                xtce_dbuf_free(&text);
                return -1;
        }
        memset(&p, 0, sizeof p);
        p.s = text.p ? text.p : (const uint8_t*)"";
        p.n = text.n;
        p.err = err;
        if (xtce_dp_starts(&p, "<?xml") && p.n > 5 && xtce_dp_space(p.s[5])) {
                size_t ds, dn, en;
                const uint8_t* enc;
                p.pos = 5;
                if (xtce_dp_past(&p, "?>", "unclosed XML declaration", &ds,
                                 &dn))
                        goto done;
                enc = xtce_dp_pseudo(p.s + ds, dn, "encoding", &en);
                if (enc) {
                        char e[16] = {0};
                        size_t k;
                        for (k = 0; k < en && k < sizeof e - 1; ++k)
                                e[k] = (char)(enc[k] >= 'A' && enc[k] <= 'Z'
                                                      ? enc[k] + 32
                                                      : enc[k]);
                        if (en >= sizeof e || (strcmp(e, "utf-8") != 0 &&
                                               strcmp(e, "utf8") != 0)) {
                                xtce_dp_fail(&p, "the encoding is not UTF-8");
                                goto done;
                        }
                }
        }
        if (xtce_dp_misc(&p)) goto done;
        if (xtce_dp_starts(&p, "<!DOCTYPE")) {
                xtce_dp_fail(&p, "documents with a DOCTYPE are not supported");
                goto done;
        }
        if (!xtce_dp_starts(&p, "<")) {
                xtce_dp_fail(&p, "no root element");
                goto done;
        }
        if (xtce_dp_element(&p, out, 1)) goto done;
        if (xtce_dp_misc(&p)) goto done;
        if (p.pos != p.n) {
                xtce_dp_fail(&p, "content after the root element");
                goto done;
        }
        rc = 0;
done:
        free(p.ns);
        xtce_dbuf_free(&text);
        if (rc) xtce_dbuf_free(out);
        return rc;
}

/* The MD5 digest of the XTCE document at `data`, as 16 bytes: what programs
 * send each other to compare. */
static inline int xtce_digest_bytes(const uint8_t* data, size_t len,
                                    uint8_t out[16], xtce_digest_error* err) {
        xtce_dbuf enc = {NULL, 0, 0, 0};
        if (xtce_digest_encoding(data, len, &enc, err)) return -1;
        xtce_md5(enc.p, enc.n, out);
        xtce_dbuf_free(&enc);
        return 0;
}

/* The digest of the XTCE document at `data`, as lowercase hex. */
static inline int xtce_digest(const uint8_t* data, size_t len, char hex[33],
                              xtce_digest_error* err) {
        uint8_t d[16];
        if (xtce_digest_bytes(data, len, d, err)) return -1;
        xtce_digest_hex(d, hex);
        return 0;
}

/* The digest of the XTCE document `text` (NUL-terminated), as lowercase
 * hex. */
static inline int xtce_digest_string(const char* text, char hex[33],
                                     xtce_digest_error* err) {
        return xtce_digest((const uint8_t*)text, strlen(text), hex, err);
}

/* The digest of the XTCE document in the file `path`, as 16 bytes. */
static inline int xtce_digest_file_bytes(const char* path, uint8_t out[16],
                                         xtce_digest_error* err) {
        FILE* f = fopen(path, "rb");
        xtce_dbuf data = {NULL, 0, 0, 0};
        uint8_t chunk[4096];
        size_t got;
        int rc;
        if (!f) {
                if (err) {
                        snprintf(err->message, sizeof err->message,
                                 "%s: cannot open", path);
                        err->line = 0;
                }
                return -1;
        }
        while ((got = fread(chunk, 1, sizeof chunk, f)) > 0)
                xtce_dbuf_put(&data, chunk, got);
        rc = ferror(f) || data.failed ? -1 : 0;
        fclose(f);
        if (rc) {
                if (err) {
                        snprintf(err->message, sizeof err->message,
                                 "%s: cannot read", path);
                        err->line = 0;
                }
        } else {
                rc = xtce_digest_bytes(data.p ? data.p : (const uint8_t*)"",
                                       data.n, out, err);
        }
        xtce_dbuf_free(&data);
        return rc;
}

/* The digest of the XTCE document in the file `path`, as lowercase hex. */
static inline int xtce_digest_file(const char* path, char hex[33],
                                   xtce_digest_error* err) {
        uint8_t d[16];
        if (xtce_digest_file_bytes(path, d, err)) return -1;
        xtce_digest_hex(d, hex);
        return 0;
}

#ifdef __cplusplus
namespace xtce {

/* The digest of the XTCE document `text`, as lowercase hex; throws
 * std::runtime_error if it can't be read. */
inline std::string digest(std::string_view text) {
        char hex[33];
        xtce_digest_error err;
        if (::xtce_digest((const uint8_t*)text.data(), text.size(), hex, &err))
                throw std::runtime_error(err.message);
        return hex;
}

/* The digest of the XTCE document in the file `path`, as lowercase hex;
 * throws std::runtime_error if it can't be read. */
inline std::string digest_file(const std::string& path) {
        char hex[33];
        xtce_digest_error err;
        if (::xtce_digest_file(path.c_str(), hex, &err))
                throw std::runtime_error(err.message);
        return hex;
}

}  // namespace xtce
#endif

#endif /* XTCE_DIGEST_H */
