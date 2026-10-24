// Round-trips the schema-covering samples and checks buffer reuse and errors.
// Usage: roundtrip_test SAMPLE...
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>

#include "xtce/document.hpp"

namespace {

int failures = 0;

#define CHECK(cond)                                                 \
        do {                                                        \
                if (!(cond)) {                                      \
                        std::cerr << __FILE__ << ":" << __LINE__    \
                                  << ": check failed: " #cond "\n"; \
                        ++failures;                                 \
                }                                                   \
        } while (0)

std::string slurp(const char* path) {
        std::ifstream in(path, std::ios::binary);
        std::ostringstream s;
        s << in.rdbuf();
        return s.str();
}

void round_trip(const char* path) {
        std::string bytes = slurp(path);
        try {
                xtce::Document doc = xtce::Document::from_bytes(bytes);
                std::string first(doc.serialize());
                xtce::Document again = xtce::Document::from_bytes(first);
                CHECK(again.root == doc.root);
                CHECK(again.serialize() == first);
        } catch (const xtce::Error& e) {
                std::cerr << path << ": " << e.what() << "\n";
                ++failures;
        }
}

void buffers_are_retained(const char* path) {
        std::string bytes = slurp(path);
        xtce::Document doc = xtce::Document::from_bytes(bytes);
        std::size_t len = doc.serialize().size();
        std::size_t cap = doc.output().capacity();
        const std::uint8_t* ptr = doc.output().data();
        for (int i = 0; i < 3; ++i) {
                CHECK(doc.serialize().size() == len);
                CHECK(doc.output().capacity() == cap);
                CHECK(doc.output().data() == ptr);
        }
        std::size_t in_cap = doc.input().capacity();
        doc.load(bytes);
        CHECK(doc.input().capacity() == in_cap);
}

void errors_have_positions() {
        try {
                xtce::Document::from_bytes(
                        "<SpaceSystem name='a'>\n<Bogus/></SpaceSystem>");
                CHECK(false);
        } catch (const xtce::Error& e) {
                CHECK(e.line() == 2);
                CHECK(e.message().find("Bogus") != std::string::npos);
        }
        try {
                xtce::Document::from_bytes("<SpaceSystem/>");
                CHECK(false);
        } catch (const xtce::Error& e) {
                CHECK(e.message().find("name") != std::string::npos);
        }
}

void builds_documents() {
        xtce::SpaceSystemType ss;
        ss.name = "Built";
        xtce::Document doc(ss);
        std::string out(doc.serialize());
        CHECK(xtce::Document::from_bytes(out).root == ss);
}

}  // namespace

int main(int argc, char** argv) {
        if (argc < 2) {
                std::cerr << "usage: roundtrip_test SAMPLE...\n";
                return 2;
        }
        for (int i = 1; i < argc; ++i) round_trip(argv[i]);
        buffers_are_retained(argv[1]);
        errors_have_positions();
        builds_documents();
        if (failures) {
                std::cerr << failures << " failure(s)\n";
                return 1;
        }
        std::cout << "roundtrip_test: " << (argc - 1) << " samples ok\n";
        return 0;
}
