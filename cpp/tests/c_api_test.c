/* Exercises the C interface: load, inspect, modify, serialize, errors. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "xtce.h"

#define CHECK(cond)                                                            \
        do {                                                                   \
                if (!(cond)) {                                                 \
                        fprintf(stderr, "%s:%d: check failed: %s\n", __FILE__, \
                                __LINE__, #cond);                              \
                        exit(1);                                               \
                }                                                              \
        } while (0)

/** Runs the checks, exiting with status 1 at the first that fails.
 *
 * @return  0 when every check passes. */
int main(void) {
        static const char xml[] =
                "<?xml version=\"1.0\"?>\n"
                "<SpaceSystem xmlns=\"http://www.omg.org/spec/XTCE/20250214\" "
                "name=\"Sat\">\n"
                "  <TelemetryMetaData/>\n"
                "</SpaceSystem>\n";
        const uint8_t* out;
        size_t len, cap;
        xtce_document* doc = xtce_document_new(0, 0);
        CHECK(doc != NULL);
        CHECK(strcmp(xtce_schema_version(), "1.3") == 0);

        CHECK(xtce_document_load(doc, xml, sizeof xml - 1) == XTCE_OK);
        CHECK(strcmp(xtce_document_name(doc), "Sat") == 0);

        CHECK(xtce_document_set_name(doc, "Renamed & <escaped>") == XTCE_OK);
        CHECK(xtce_document_serialize(doc, &out, &len) == XTCE_OK);
        CHECK(len > 0);
        CHECK(memmem(out, len, "name=\"Renamed &amp; &lt;escaped&gt;\"", 36) !=
              NULL);

        /* The output buffer is reused: same capacity across serializations. */
        cap = xtce_document_output_capacity(doc);
        CHECK(xtce_document_serialize(doc, &out, &len) == XTCE_OK);
        CHECK(xtce_document_output_capacity(doc) == cap);

        CHECK(xtce_document_load(
                      doc, "<SpaceSystem name='x'>\n<Bogus/></SpaceSystem>",
                      46) == XTCE_ERROR);
        CHECK(strstr(xtce_document_error(doc), "Bogus") != NULL);
        CHECK(xtce_document_error_line(doc) == 2);

        xtce_document_free(doc);
        puts("c_api_test: ok");
        return 0;
}
