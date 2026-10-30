// Reads an XTCE document, deserializes it, and serializes it again.
// Usage: xtce_roundtrip INPUT [OUTPUT]   (writes to stdout without OUTPUT)
#include <fstream>
#include <iostream>

#include "xtce-document.hpp"

/** Reads an XTCE document and writes it out again.
 *
 * @param argc  The number of arguments, 2 or 3.
 * @param argv  The arguments: the input's path and, optionally, the
 *              output's; without it the output goes to standard output.
 * @return      0; 1 if the input can't be read or decoded; 2 for a usage
 *              error. */
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
