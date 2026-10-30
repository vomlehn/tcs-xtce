/* Prints the digest of each XTCE document named on the command line, one per
 * line, or "error" for one that can't be read: what tests/digest_check.sh
 * compares across languages.  With --set, prints the one digest of the set of
 * documents instead.  Built as C, with the C functions, and as C++, with the
 * C++ ones. */
#include <xtce-digest.h>

#ifdef __cplusplus
#include <iostream>

/** Prints the digest of each document named, or of the set with --set.
 *
 * @param argc  The number of arguments.
 * @param argv  The arguments: optionally "--set", then the documents'
 *              paths.
 * @return      0; a document that can't be read prints "error". */
int main(int argc, char** argv) {
        if (argc > 1 && std::string(argv[1]) == "--set") {
                std::vector<std::string> paths(argv + 2, argv + argc);
                try {
                        std::cout << xtce::digest_files(paths) << "\n";
                } catch (const std::runtime_error&) {
                        std::cout << "error\n";
                }
                return 0;
        }
        for (int i = 1; i < argc; ++i) {
                try {
                        std::cout << xtce::digest_file(argv[i]) << "\n";
                } catch (const std::runtime_error&) {
                        std::cout << "error\n";
                }
        }
        return 0;
}
#else
/** Prints the digest of each document named, or of the set with --set.
 *
 * @param argc  The number of arguments.
 * @param argv  The arguments: optionally "--set", then the documents'
 *              paths.
 * @return      0; a document that can't be read prints "error". */
int main(int argc, char** argv) {
        int i;
        char hex[33];
        if (argc > 1 && strcmp(argv[1], "--set") == 0) {
                if (xtce_digest_files((const char* const*)argv + 2,
                                      (size_t)argc - 2, hex, NULL) == 0)
                        puts(hex);
                else
                        puts("error");
                return 0;
        }
        for (i = 1; i < argc; ++i) {
                if (xtce_digest_file(argv[i], hex, NULL) == 0)
                        puts(hex);
                else
                        puts("error");
        }
        return 0;
}
#endif
