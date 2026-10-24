// Reads an XTCE document, deserializes it, and serializes it again.
// Usage: xtce_roundtrip INPUT [OUTPUT]   (writes to stdout without OUTPUT)
#include <fstream>
#include <iostream>

#include "xtce/document.hpp"

int main(int argc, char** argv) {
        if (argc < 2 || argc > 3) {
                std::cerr << "usage: xtce_roundtrip INPUT [OUTPUT]\n";
                return 2;
        }
        try {
                std::ifstream in(argv[1], std::ios::binary);
                if (!in)
                        throw xtce::Error(std::string("cannot open ") +
                                          argv[1]);
                xtce::Document doc;
                doc.read_from(in);
                if (argc == 3) {
                        std::ofstream out(argv[2], std::ios::binary);
                        doc.write_to(out);
                } else {
                        doc.write_to(std::cout);
                }
        } catch (const xtce::Error& e) {
                std::cerr << argv[1] << ": " << e.what() << "\n";
                return 1;
        }
        return 0;
}
