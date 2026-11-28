// Tests the C++ interface to the run-time codec on the demo mission: the
// demo's custom algorithms supplied as lambdas, one keeping a count, every
// vector unpacking and packing again to the bytes the generated codecs
// give, failures thrown, and a stream giving frames to a lambda.
//
// Usage: cpp_test MISSION_DIR

#include <cmath>
#include <cstring>
#include <fstream>
#include <iostream>
#include <sstream>
#include <string>
#include <vector>

#include "tcs-dynxtce.hpp"

namespace xd = tcs::dynxtce;

/** The checks that failed. */
static int failures;

/** Records a check.
 *
 * @param ok    Whether it held.
 * @param what  What it checked. */
static void check(bool ok, const std::string &what)
{
        if (!ok) {
                std::cerr << "FAIL: " << what << '\n';
                failures++;
        }
}

/** Reads a whole file.
 *
 * @param path  Its path.
 * @return      Its text. */
static std::string slurp(const std::string &path)
{
        std::ifstream f(path, std::ios::binary);
        std::ostringstream s;
        s << f.rdbuf();
        return s.str();
}

/** Reads hexadecimal.
 *
 * @param h  The digits.
 * @return   The bytes. */
static std::vector<std::uint8_t> unhex(const std::string &h)
{
        std::vector<std::uint8_t> b;
        for (std::size_t i = 0; i + 1 < h.size(); i += 2)
                b.push_back(std::uint8_t(std::stoi(h.substr(i, 2), nullptr,
                                                   16)));
        return b;
}

/** Gives seconds as whole milliseconds.
 *
 * @param s   The seconds.
 * @param ms  Where the milliseconds go.
 * @return    Whether they lie in 0 to 2^32 - 1. */
static bool to_ms(double s, std::uint32_t &ms)
{
        double v = std::floor(s * 1000.0 + 0.5);
        if (!(v >= 0.0 && v < 4294967296.0))
                return false;
        ms = std::uint32_t(v);
        return true;
}

/** Supplies the demo mission's custom algorithms.
 *
 * @param m      The mission.
 * @param calls  Counts the calls of Unmask. */
static void set_algorithms(xd::Mission &m, int &calls)
{
        m.set_criterion("/Demo/IsEven", [](std::span<const double> v) {
                return std::fmod(v[0], 2.0) == 0.0;
        });
        m.set_outputs("/Demo/MinMax",
                      [](std::span<const double> v, std::span<double> o) {
                              o[0] = v[0];
                              o[1] = v[0];
                              for (double x : v) {
                                      o[0] = std::min(o[0], x);
                                      o[1] = std::max(o[1], x);
                              }
                              return true;
                      });
        m.set_alarm("/Demo/Bands", [](std::span<const double> v) {
                return std::uint8_t(v[0] / 50.0);
        });
        m.set_checksum("/Demo/Twice", [](std::span<const std::uint8_t> d,
                                         std::size_t s, std::size_t e) {
                std::uint64_t t = 0;
                for (std::size_t i = s / 8; i < e / 8; i++)
                        t += d[i];
                return 2 * t;
        });
        m.set_binary_to("Expand", [](std::span<const std::uint8_t> b,
                                     std::span<std::uint8_t> o) {
                if (2 * b.size() <= o.size())
                        for (std::size_t i = 0; i < b.size(); i++)
                                o[2 * i] = o[2 * i + 1] = b[i];
                return (long long)(2 * b.size());
        });
        m.set_binary_from("Shrink", [](std::span<const std::uint8_t> b,
                                       std::span<std::uint8_t> o) {
                xd::Mission::Decoded d{false, b.size(), b.size() / 2};
                if (b.size() % 2 != 0)
                        return d;
                for (std::size_t i = 0; i < b.size(); i += 2)
                        if (b[i] != b[i + 1])
                                return d;
                if (b.size() / 2 <= o.size())
                        for (std::size_t i = 0; i < b.size() / 2; i++)
                                o[i] = b[2 * i];
                d.ok = true;
                return d;
        });
        m.set_binary_to("Mask", [](std::span<const std::uint8_t> b,
                                   std::span<std::uint8_t> o) {
                for (std::size_t i = 0; i < b.size() && i < o.size(); i++)
                        o[i] = b[i] ^ 0x5A;
                return (long long)b.size();
        });
        m.set_binary_from("Unmask", [&calls](std::span<const std::uint8_t> b,
                                             std::span<std::uint8_t> o) {
                calls++;
                for (std::size_t i = 0; i < b.size() && i < o.size(); i++)
                        o[i] = b[i] ^ 0x5A;
                return xd::Mission::Decoded{true, b.size(), b.size()};
        });
        m.set_binary_to("Stuff", [](std::span<const std::uint8_t> b,
                                    std::span<std::uint8_t> o) {
                if (b.size() > 255)
                        return -1LL;
                if (b.size() + 1 <= o.size()) {
                        o[0] = std::uint8_t(b.size());
                        std::memcpy(o.data() + 1, b.data(), b.size());
                }
                return (long long)(b.size() + 1);
        });
        m.set_binary_from("Unstuff", [](std::span<const std::uint8_t> b,
                                        std::span<std::uint8_t> o) {
                if (b.empty() || b.size() <= b[0])
                        return xd::Mission::Decoded{false, 0, 0};
                std::size_t n = b[0];
                if (n <= o.size())
                        std::memcpy(o.data(), b.data() + 1, n);
                return xd::Mission::Decoded{true, n + 1, n};
        });
        m.set_time_from("MsFrom", [](std::span<const std::uint8_t> b,
                                     std::size_t &took, double &s) {
                if (b.size() < 4)
                        return false;
                took = 4;
                s = double(std::uint32_t(b[0]) << 24 | std::uint32_t(b[1]) << 16
                           | std::uint32_t(b[2]) << 8 | b[3])
                    / 1000.0;
                return true;
        });
        m.set_time_to("MsTo", [](double s, std::span<std::uint8_t> o) {
                std::uint32_t v;
                if (!to_ms(s, v))
                        return -1LL;
                if (o.size() >= 4)
                        for (int i = 0; i < 4; i++)
                                o[i] = std::uint8_t(v >> (24 - 8 * i));
                return 4LL;
        });
        m.set_time_from("VarFrom", [](std::span<const std::uint8_t> b,
                                      std::size_t &took, double &s) {
                std::uint64_t v = 0;
                for (std::size_t i = 0; i < b.size() && i < 5; i++) {
                        v |= std::uint64_t(b[i] & 0x7F) << (7 * i);
                        if ((b[i] & 0x80) == 0) {
                                took = i + 1;
                                s = double(v) / 1000.0;
                                return true;
                        }
                }
                return false;
        });
        m.set_time_to("VarTo", [](double s, std::span<std::uint8_t> o) {
                std::uint32_t v;
                std::uint8_t buf[5];
                std::size_t n = 0;
                if (!to_ms(s, v))
                        return -1LL;
                for (; v >= 0x80; v >>= 7)
                        buf[n++] = std::uint8_t((v & 0x7F) | 0x80);
                buf[n++] = std::uint8_t(v);
                if (n <= o.size())
                        std::memcpy(o.data(), buf, n);
                return (long long)n;
        });
        m.set_stream_decode(
                "Descramble",
                [](std::span<const std::uint8_t> b,
                   const std::function<void(std::span<const std::uint8_t>)>
                           &emit) {
                        std::vector<std::uint8_t> out(b.begin(), b.end());
                        for (auto &x : out)
                                x ^= 0x55;
                        emit(out);
                });
        m.set_bit_invert("Invert", [](std::uint8_t b) {
                return std::uint8_t(b ^ 1);
        });
}

/** Runs every vector: unpacks it, and packs it again to the bytes the
 * generated codecs give.
 *
 * @param m    The mission.
 * @param dir  The directory of the demo mission's files.
 * @return     How many vectors gave what they should. */
static int run_vectors(xd::Mission &m, const std::string &dir)
{
        auto top = m.top();
        xd::Node roots[] = {m.container(top, "/Demo/CCSDSPacket"),
                            m.meta_command(top, "/Demo/CCSDSCommand"),
                            m.container(top, "/Demo/Housekeeping")};
        std::istringstream vs(slurp(dir + "/vectors.txt"));
        std::istringstream es(slurp(dir + "/vectors.expected"));
        std::string v, e;
        xd::Scratch scratch(m);
        int matched = 0;

        while (std::getline(vs, v) && std::getline(es, e)) {
                std::istringstream w(v.substr(0, v.find('#')));
                std::string kind, h;
                if (!(w >> kind >> h)) {
                        continue;
                }
                auto root = kind == "tm" ? roots[0]
                            : kind == "tc" ? roots[1] : roots[2];
                auto data = unhex(h);
                auto bar = e.find(" | ");
                std::string again = bar == std::string::npos
                                    ? "" : e.substr(bar + 3);
                again = again.substr(0, again.find(' '));
                xd::Node got;
                try {
                        got = m.unpack(root, data, scratch);
                } catch (const xd::Error &) {
                        check(e.rfind("error:", 0) == 0, v);
                        matched += e.rfind("error:", 0) == 0;
                        continue;
                }
                xd::Store store(m);
                store.commit(scratch);
                try {
                        auto out = m.pack(got, store);
                        bool ok = std::vector<std::uint8_t>(out.begin(),
                                                            out.end())
                                  == unhex(again);
                        check(ok, v);
                        matched += ok;
                } catch (const xd::Error &err) {
                        bool ok = again.rfind("error:", 0) == 0;
                        check(ok, v + ": " + err.what());
                        matched += ok;
                }
        }
        return matched;
}

/** Runs the tests.
 *
 * @param argc  The arguments' count.
 * @param argv  The arguments: the demo mission's directory.
 * @return      0 if every check held, 1 if not. */
int main(int argc, char **argv)
{
        if (argc != 2) {
                std::cerr << "usage: cpp_test MISSION_DIR\n";
                return 2;
        }
        std::string dir = argv[1];
        std::string doc = slurp(dir + "/demo.xtce");
        int calls = 0;
        xd::Deframer dl(nullptr);
        {
                xd::Mission m({doc});
                set_algorithms(m, calls);
                int matched = run_vectors(m, dir);
                std::cout << "cpp_test: " << matched
                          << " vectors unpack and pack as expected\n";
                check(matched > 290, "the vectors");
                check(calls > 0, "Unmask, a lambda with state, is called");
                for (const auto &a : m.algorithms())
                        check(a.set, a.name + " is set");
                bool thrown = false;
                try {
                        m.parameter(m.top(), "/Demo/NoSuch");
                } catch (const xd::Error &e) {
                        thrown = e.status() == TCS_DYNXTCE_STATUS_LOOKUP;
                }
                check(thrown, "a missing parameter throws");
                auto seq = m.parameter(m.top(), "/Demo/SeqCount");
                check(m.path_of(seq) == "/Demo/SeqCount", "a slot's path");
                check(m.kind(seq) == "int", "a slot's kind");
                dl = m.stream("/Demo/Downlink");
        }
        // The stream keeps its mission alive.
        static const std::uint8_t frame[] = {
                0x1a, 0xcf, 0xfc, 0x1d, 0x01, 0x11, 0x11, 0x11,
                0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x11,
        };
        int frames = 0;
        for (int i = 0; i < 3; i++)
                dl.push(frame, [&](std::span<const std::uint8_t>) {
                        frames++;
                });
        check(frames > 0, "frames from Downlink");
        if (failures != 0) {
                std::cerr << "cpp_test: " << failures << " checks failed\n";
                return 1;
        }
        std::cout << "cpp_test: all checks hold\n";
        return 0;
}
