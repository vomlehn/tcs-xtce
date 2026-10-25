/* Prints the digest of each XTCE document named on the command line, one per
 * line, or "error" for one that can't be read: what tests/digest_check.sh
 * compares across languages.  Built as C, with the C functions, and as C++,
 * with the C++ ones. */
#include <xtce/digest.h>

#ifdef __cplusplus
#include <iostream>

int main(int argc, char** argv) {
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
int main(int argc, char** argv) {
        int i;
        for (i = 1; i < argc; ++i) {
                char hex[33];
                if (xtce_digest_file(argv[i], hex, NULL) == 0)
                        puts(hex);
                else
                        puts("error");
        }
        return 0;
}
#endif
