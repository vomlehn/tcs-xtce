/* The C++ interface to tcs-xtce-dynamic, the tcs-xtce run-time codec: a
 * header-only layer over the C interface, tcs-xtce-dynamic.h, that does
 * nothing the C interface doesn't.  Its classes free what they hold when
 * they are destroyed, failures are thrown as tcs::xd::Error, and the
 * functions an application supplies are any callable.
 *
 * Link with libtcs-xtce-dynamic.  C++20. */

#ifndef TCS_XTCE_DYNAMIC_HPP
#define TCS_XTCE_DYNAMIC_HPP

#include <array>
#include <cstdint>
#include <functional>
#include <memory>
#include <span>
#include <stdexcept>
#include <string>
#include <string_view>
#include <utility>
#include <vector>

#include "tcs-xtce-dynamic.h"

namespace tcs::xd {

/** A failure of a call to the codec: its status and message. */
class Error : public std::runtime_error {
public:
        /** Makes the error of the thread's last failure.
         *
         * @param status  The status the call returned. */
        explicit Error(int status)
                : std::runtime_error(tcs_xd_last_error()), status_(status)
        {
        }

        /** Gives the status the call returned.
         *
         * @return  A negative tcs_xd_status. */
        int status() const { return status_; }

private:
        /** The status the call returned. */
        int status_;
};

/** Throws the error of a call that failed.
 *
 * @param r  What the call returned.
 * @return   It, if it didn't fail. */
inline int checked(int r)
{
        if (r < 0)
                throw Error(r);
        return r;
}

/** Gives text the codec copies out, as snprintf does.
 *
 * @param get  Copies it into a buffer and gives its whole length.
 * @return     The text. */
template <class Get> std::string text(Get get)
{
        std::string s(64, '\0');
        std::size_t n = get(s.data(), s.size());
        if (n >= s.size()) {
                s.assign(n + 1, '\0');
                get(s.data(), s.size());
        }
        s.resize(n);
        return s;
}

/** A SpaceSystem of a mission. */
using Space = tcs_xd_space;
/** A container or meta-command of a mission. */
using Node = tcs_xd_node;
/** One scalar value of a mission. */
using Slot = tcs_xd_slot;
/** The slots of a whole value. */
using Slots = tcs_xd_slots;
/** A value of a slot. */
using Datum = tcs_xd_datum;

/** Makes a datum of an integer.
 *
 * @param i  The integer.
 * @return   The datum. */
inline Datum int_datum(std::int64_t i)
{
        Datum d{};
        d.kind = TCS_XD_KIND_INT;
        d.i = i;
        return d;
}

/** Makes a datum of an enumeration's value.
 *
 * @param i  The value.
 * @return   The datum. */
inline Datum enum_datum(std::int64_t i)
{
        Datum d{};
        d.kind = TCS_XD_KIND_ENUM;
        d.i = i;
        return d;
}

/** Makes a datum of a float.
 *
 * @param f  The float.
 * @return   The datum. */
inline Datum float_datum(double f)
{
        Datum d{};
        d.kind = TCS_XD_KIND_FLOAT;
        d.f = f;
        return d;
}

/** Makes a datum of a string, which it borrows.
 *
 * @param s  The string, of UTF-8.
 * @return   The datum. */
inline Datum str_datum(std::string_view s)
{
        Datum d{};
        d.kind = TCS_XD_KIND_STR;
        d.bytes = reinterpret_cast<const std::uint8_t *>(s.data());
        d.len = s.size();
        return d;
}

/** Gives a datum's string or binary bytes.
 *
 * @param d  The datum.
 * @return   Its bytes, borrowed. */
inline std::string_view bytes_of(const Datum &d)
{
        return {reinterpret_cast<const char *>(d.bytes), d.len};
}

class Store;
class Scratch;
class Monitor;
class Deframer;

/** Frees a callable kept as a context.
 *
 * @param ctx  The callable. */
template <class F> void destroy(void *ctx)
{
        delete static_cast<F *>(ctx);
}

/** A mission, read and resolved. */
class Mission {
public:
        /** Reads and resolves a mission from documents in memory.
         *
         * @param docs  Each document's text, in the order they build on
         *              one another. */
        explicit Mission(const std::vector<std::string_view> &docs)
        {
                std::vector<const std::uint8_t *> p;
                std::vector<std::uintptr_t> n;
                for (auto d : docs) {
                        p.push_back(reinterpret_cast<const std::uint8_t *>(
                                d.data()));
                        n.push_back(d.size());
                }
                tcs_xd_mission *m = nullptr;
                checked(tcs_xd_mission_read(p.data(), n.data(), p.size(),
                                            &m));
                m_.reset(m);
        }

        /** Gives the C mission.
         *
         * @return  It. */
        tcs_xd_mission *c() const { return m_.get(); }

        /** Gives the top SpaceSystem.
         *
         * @return  Its handle. */
        Space top() const { return tcs_xd_top(c()); }

        /** Looks a SpaceSystem up from a base.
         *
         * @param base  The SpaceSystem the path is relative to.
         * @param path  The path.
         * @return      Its handle. */
        Space space_system(Space base, const std::string &path) const
        {
                Space s{};
                checked(tcs_xd_space_system(c(), base, path.c_str(), &s));
                return s;
        }

        /** Looks a container up from a base.
         *
         * @param base  The SpaceSystem the path is relative to.
         * @param path  The path.
         * @return      Its handle. */
        Node container(Space base, const std::string &path) const
        {
                Node n{};
                checked(tcs_xd_container(c(), base, path.c_str(), &n));
                return n;
        }

        /** Looks a meta-command up from a base.
         *
         * @param base  The SpaceSystem the path is relative to.
         * @param path  The path.
         * @return      Its handle. */
        Node meta_command(Space base, const std::string &path) const
        {
                Node n{};
                checked(tcs_xd_meta_command(c(), base, path.c_str(), &n));
                return n;
        }

        /** Looks a parameter, or a scalar member or element of one, up.
         *
         * @param base  The SpaceSystem the path is relative to.
         * @param path  The path.
         * @return      Its slot. */
        Slot parameter(Space base, const std::string &path) const
        {
                Slot s{};
                checked(tcs_xd_parameter(c(), base, path.c_str(), &s));
                return s;
        }

        /** Looks the slots of a parameter, or of a member or element of
         * one, up.
         *
         * @param base  The SpaceSystem the path is relative to.
         * @param path  The path.
         * @return      Its slots. */
        Slots parameter_slots(Space base, const std::string &path) const
        {
                Slots s{};
                checked(tcs_xd_parameter_slots(c(), base, path.c_str(), &s));
                return s;
        }

        /** Looks an argument of a meta-command up.
         *
         * @param cmd   The meta-command.
         * @param name  The argument's name.
         * @return      Its slot. */
        Slot argument(Node cmd, const std::string &name) const
        {
                Slot s{};
                checked(tcs_xd_argument(c(), cmd, name.c_str(), &s));
                return s;
        }

        /** Looks an entry of a container or meta-command up.
         *
         * @param n     The node.
         * @param name  The entry's name.
         * @return      Its slot. */
        Slot entry(Node n, const std::string &name) const
        {
                Slot s{};
                checked(tcs_xd_entry(c(), n, name.c_str(), &s));
                return s;
        }

        /** Looks the slots of an argument, or of a member or element of
         * one, up.
         *
         * @param cmd   The meta-command.
         * @param name  The argument's name.
         * @return      Its slots. */
        Slots argument_slots(Node cmd, const std::string &name) const
        {
                Slots s{};
                checked(tcs_xd_argument_slots(c(), cmd, name.c_str(), &s));
                return s;
        }

        /** Looks the slots of an entry, or of a member or element of one,
         * up.
         *
         * @param n     The node.
         * @param name  The entry's name.
         * @return      Its slots. */
        Slots entry_slots(Node n, const std::string &name) const
        {
                Slots s{};
                checked(tcs_xd_entry_slots(c(), n, name.c_str(), &s));
                return s;
        }

        /** Gives the mission's digest, as xtce-digest computes it.
         *
         * @return  Its 16 bytes. */
        std::array<std::uint8_t, 16> digest() const
        {
                std::array<std::uint8_t, 16> d{};
                tcs_xd_mission_digest(c(), d.data());
                return d;
        }

        /** Gives a SpaceSystem's absolute path.
         *
         * @param s  The SpaceSystem.
         * @return   The path. */
        std::string path_of(Space s) const
        {
                return text([&](char *b, std::size_t l) {
                        return tcs_xd_path_of_space(c(), s, b, l);
                });
        }

        /** Gives the kind of a slot's type: int, float, enum, bool,
         * string or binary.
         *
         * @param s  The slot.
         * @return   The kind. */
        std::string kind(Slot s) const
        {
                return text([&](char *b, std::size_t l) {
                        return tcs_xd_slot_kind(c(), s, b, l);
                });
        }

        /** Says whether a slot is a time, and which kind.
         *
         * @param s  The slot.
         * @return   1 for an absolute time, 2 for a relative one, 0 for
         *           anything else. */
        int time(Slot s) const { return tcs_xd_slot_time(c(), s); }

        /** Says whether a node is abstract.
         *
         * @param n  The node.
         * @return   Whether it is. */
        bool is_abstract(Node n) const { return tcs_xd_is_abstract(c(), n); }

        /** Gives the most bytes a packet of a node takes.
         *
         * @param n  The node.
         * @return   The bytes, or 0 if they aren't known. */
        std::size_t max_bytes(Node n) const
        {
                return tcs_xd_max_bytes(c(), n);
        }

        /** Gives what the mission's packets use that the codec doesn't run
         * yet.
         *
         * @return  One line for each such node; empty if none. */
        std::string unsupported() const
        {
                return text([&](char *b, std::size_t l) {
                        return tcs_xd_unsupported(c(), b, l);
                });
        }

        /** Gives the slots of no maximum size of their own.
         *
         * @return  The slots. */
        std::vector<Slot> unbounded() const
        {
                std::vector<Slot> v;
                for (std::size_t i = 0; i < tcs_xd_unbounded_count(c()); i++)
                        v.push_back(tcs_xd_unbounded(c(), i));
                return v;
        }

        /** One of the algorithms the application supplies functions for. */
        struct Algorithm {
                /** Its name. */
                std::string name;
                /** The kind of function it needs, such as binary_from. */
                std::string kind;
                /** Whether its function is set. */
                bool set;
        };

        /** Lists the algorithms the application supplies functions for.
         *
         * @return  Each one. */
        std::vector<Algorithm> algorithms() const
        {
                std::vector<Algorithm> v;
                for (std::size_t i = 0; i < tcs_xd_algorithm_count(c());
                     i++) {
                        char name[512], kind[64];
                        int set = checked(tcs_xd_algorithm(
                                c(), i, name, sizeof name, kind,
                                sizeof kind));
                        v.push_back({name, kind, set == 1});
                }
                return v;
        }

        /** Lists the mission's streams.
         *
         * @return  Each one's qualified name. */
        std::vector<std::string> streams() const
        {
                std::vector<std::string> v;
                for (std::size_t i = 0; i < tcs_xd_stream_count(c()); i++)
                        v.push_back(text([&](char *b, std::size_t l) {
                                return tcs_xd_stream_name(c(), i, b, l);
                        }));
                return v;
        }

        /** Gives the container each frame of a stream is.
         *
         * @param path  The stream's qualified name.
         * @param out   Where the container goes.
         * @return      Whether its frames are a container's: false for a
         *              CustomStream. */
        bool stream_container(const std::string &path, Node &out) const
        {
                return checked(tcs_xd_stream_container(c(), path.c_str(),
                                                       &out)) == 1;
        }

        /** Gives a node's absolute path.
         *
         * @param n  The node.
         * @return   The path. */
        std::string path_of(Node n) const
        {
                return text([&](char *b, std::size_t l) {
                        return tcs_xd_path_of_node(c(), n, b, l);
                });
        }

        /** Gives a slot's absolute path.
         *
         * @param s  The slot.
         * @return   The path. */
        std::string path_of(Slot s) const
        {
                return text([&](char *b, std::size_t l) {
                        return tcs_xd_path_of_slot(c(), s, b, l);
                });
        }

        /** Supplies the function of a custom algorithm in a criterion.
         *
         * @param name  The algorithm's name.
         * @param f     Whether it holds, from its inputs. */
        void set_criterion(const std::string &name,
                           std::function<bool(std::span<const double>)> f)
        {
                using F = decltype(f);
                auto *p = new F(std::move(f));
                checked(tcs_xd_set_criterion(
                        c(), name.c_str(),
                        [](void *x, const double *v, std::uintptr_t n) {
                                return (*static_cast<F *>(x))({v, n});
                        },
                        p, destroy<F>));
        }

        /** Supplies the function of a CustomAlgorithm.
         *
         * @param name  The algorithm's name.
         * @param f     Its outputs from its inputs; false for none. */
        void set_outputs(
                const std::string &name,
                std::function<bool(std::span<const double>, std::span<double>)>
                        f)
        {
                using F = decltype(f);
                auto *p = new F(std::move(f));
                checked(tcs_xd_set_outputs(
                        c(), name.c_str(),
                        [](void *x, const double *v, std::uintptr_t n,
                           double *o, std::uintptr_t m) {
                                return (*static_cast<F *>(x))({v, n},
                                                              {o, m});
                        },
                        p, destroy<F>));
        }

        /** Supplies the function of a custom alarm.
         *
         * @param name  The algorithm's name.
         * @param f     Its level, 0 to 5, from its inputs. */
        void set_alarm(const std::string &name,
                       std::function<std::uint8_t(std::span<const double>)> f)
        {
                using F = decltype(f);
                auto *p = new F(std::move(f));
                checked(tcs_xd_set_alarm(
                        c(), name.c_str(),
                        [](void *x, const double *v, std::uintptr_t n) {
                                return (*static_cast<F *>(x))({v, n});
                        },
                        p, destroy<F>));
        }

        /** Supplies the function of a custom checksum.
         *
         * @param name  The algorithm's name.
         * @param f     The checksum of the data's bits from start to end. */
        void set_checksum(
                const std::string &name,
                std::function<std::uint64_t(std::span<const std::uint8_t>,
                                            std::size_t, std::size_t)>
                        f)
        {
                using F = decltype(f);
                auto *p = new F(std::move(f));
                checked(tcs_xd_set_checksum(
                        c(), name.c_str(),
                        [](void *x, const std::uint8_t *d, std::uintptr_t l,
                           std::uintptr_t s, std::uintptr_t e) {
                                return (*static_cast<F *>(x))({d, l}, s, e);
                        },
                        p, destroy<F>));
        }

        /** What a transform's or time's decoding gives: the bytes it took
         * and those it made, or nothing if they don't decode. */
        struct Decoded {
                /** Whether they decode. */
                bool ok;
                /** The bytes it took. */
                std::size_t took;
                /** The bytes it made, or the room it needs. */
                std::size_t made;
        };

        /** Supplies the decoding function of a transform.
         *
         * @param name  The algorithm's name.
         * @param f     Decodes bytes into the room given. */
        void set_binary_from(
                const std::string &name,
                std::function<Decoded(std::span<const std::uint8_t>,
                                      std::span<std::uint8_t>)>
                        f)
        {
                using F = decltype(f);
                auto *p = new F(std::move(f));
                checked(tcs_xd_set_binary_from(
                        c(), name.c_str(),
                        [](void *x, const std::uint8_t *b, std::uintptr_t n,
                           std::uint8_t *o, std::uintptr_t cap,
                           std::uintptr_t *took, std::uintptr_t *made) {
                                Decoded d = (*static_cast<F *>(x))({b, n},
                                                                   {o, cap});
                                *took = d.took;
                                *made = d.made;
                                return d.ok;
                        },
                        p, destroy<F>));
        }

        /** Supplies the encoding function of a transform.
         *
         * @param name  The algorithm's name.
         * @param f     Encodes bytes into the room given: the bytes it made
         *              or the room it needs, or a negative number if they
         *              don't encode. */
        void set_binary_to(
                const std::string &name,
                std::function<long long(std::span<const std::uint8_t>,
                                        std::span<std::uint8_t>)>
                        f)
        {
                using F = decltype(f);
                auto *p = new F(std::move(f));
                checked(tcs_xd_set_binary_to(
                        c(), name.c_str(),
                        [](void *x, const std::uint8_t *b, std::uintptr_t n,
                           std::uint8_t *o, std::uintptr_t cap,
                           std::uintptr_t *made) {
                                long long r = (*static_cast<F *>(x))({b, n},
                                                                     {o, cap});
                                *made = r < 0 ? 0 : std::uintptr_t(r);
                                return r >= 0;
                        },
                        p, destroy<F>));
        }

        /** Supplies the decoding function of a time.
         *
         * @param name  The algorithm's name.
         * @param f     Decodes a time: whether it does, setting the bytes
         *              it took and the seconds. */
        void set_time_from(
                const std::string &name,
                std::function<bool(std::span<const std::uint8_t>,
                                   std::size_t &, double &)>
                        f)
        {
                using F = decltype(f);
                auto *p = new F(std::move(f));
                checked(tcs_xd_set_time_from(
                        c(), name.c_str(),
                        [](void *x, const std::uint8_t *b, std::uintptr_t n,
                           std::uintptr_t *took, double *secs) {
                                std::size_t t = 0;
                                bool ok = (*static_cast<F *>(x))({b, n}, t,
                                                                 *secs);
                                *took = t;
                                return ok;
                        },
                        p, destroy<F>));
        }

        /** Supplies the encoding function of a time.
         *
         * @param name  The algorithm's name.
         * @param f     Encodes seconds into the room given: the bytes it
         *              made or the room it needs, or a negative number if
         *              they don't encode. */
        void set_time_to(
                const std::string &name,
                std::function<long long(double, std::span<std::uint8_t>)> f)
        {
                using F = decltype(f);
                auto *p = new F(std::move(f));
                checked(tcs_xd_set_time_to(
                        c(), name.c_str(),
                        [](void *x, double secs, std::uint8_t *o,
                           std::uintptr_t cap, std::uintptr_t *made) {
                                long long r = (*static_cast<F *>(x))(
                                        secs, {o, cap});
                                *made = r < 0 ? 0 : std::uintptr_t(r);
                                return r >= 0;
                        },
                        p, destroy<F>));
        }

        /** Supplies a CustomStream's DecodingAlgorithm.
         *
         * @param name  The algorithm's name.
         * @param f     Decodes bytes, calling its second argument with each
         *              piece it makes. */
        void set_stream_decode(
                const std::string &name,
                std::function<void(
                        std::span<const std::uint8_t>,
                        const std::function<void(
                                std::span<const std::uint8_t>)> &)>
                        f)
        {
                using F = decltype(f);
                auto *p = new F(std::move(f));
                checked(tcs_xd_set_stream_decode(
                        c(), name.c_str(),
                        [](void *x, const std::uint8_t *b, std::uintptr_t n,
                           tcs_xd_emit_fn emit, void *ectx) {
                                (*static_cast<F *>(x))(
                                        {b, n},
                                        [&](std::span<const std::uint8_t> s) {
                                                emit(ectx, s.data(),
                                                     s.size());
                                        });
                        },
                        p, destroy<F>));
        }

        /** Supplies a stream's InvertAlgorithm.
         *
         * @param name  The algorithm's name.
         * @param f     What a bit is once the stream is inverted. */
        void set_bit_invert(const std::string &name,
                            std::function<std::uint8_t(std::uint8_t)> f)
        {
                using F = decltype(f);
                auto *p = new F(std::move(f));
                checked(tcs_xd_set_bit_invert(
                        c(), name.c_str(),
                        [](void *x, std::uint8_t b) {
                                return (*static_cast<F *>(x))(b);
                        },
                        p, destroy<F>));
        }

        /** Unpacks a packet into a scratch, classifying it for an abstract
         * node.
         *
         * @param n        The container or meta-command.
         * @param data     The packet.
         * @param scratch  Where its values go.
         * @return         The concrete node it is. */
        Node unpack(Node n, std::span<const std::uint8_t> data,
                    Scratch &scratch) const;

        /** Packs a packet of a concrete node from a store's values.
         *
         * @param n  The container or meta-command.
         * @param s  The store, which keeps the bytes.
         * @return   The bytes, valid until the store packs again. */
        std::span<const std::uint8_t> pack(Node n, Store &s) const;

        /** Gives the alarm level of each field of the packet last unpacked
         * into a scratch.
         *
         * @param scratch  What unpacking gave.
         * @param f        Called with each field's name and level, -1 when
         *                 the field is absent. */
        void alarms(const Scratch &scratch,
                    const std::function<void(std::string_view, int)> &f)
                const;

        /** Gives the alarm level of each field of the packet last unpacked
         * into a scratch, counting the earlier samples a monitor keeps.
         *
         * @param scratch  What unpacking gave.
         * @param mon      The alarm monitor.
         * @param t        The packet's time, in seconds.
         * @param f        Called with each field's name and level, -1 when
         *                 the field is absent. */
        void monitor_alarms(
                const Scratch &scratch, Monitor &mon, double t,
                const std::function<void(std::string_view, int)> &f) const;

        /** Gives the outputs of the algorithms the packet last unpacked
         * into a scratch triggers.
         *
         * @param scratch  What unpacking gave.
         * @param f        Called with each output's name, whether it has a
         *                 value, and the value. */
        void derived(
                const Scratch &scratch,
                const std::function<void(std::string_view, bool, double)> &f)
                const;

        /** Makes a stream's frame synchronizer.
         *
         * @param path  The stream's qualified name.
         * @return      The stream. */
        Deframer stream(const std::string &path) const;

private:
        /** Frees a C mission. */
        struct Free {
                /** Frees it.
                 *
                 * @param m  The mission. */
                void operator()(tcs_xd_mission *m) const
                {
                        tcs_xd_mission_free(m);
                }
        };

        /** The C mission. */
        std::unique_ptr<tcs_xd_mission, Free> m_;
};

/** The current value of every slot of a mission. */
class Store {
public:
        /** Makes a store, none of its slots with a value.
         *
         * @param m  The mission. */
        explicit Store(const Mission &m)
                : m_(&m), s_(tcs_xd_store_new(m.c()))
        {
        }

        /** Gives the C store.
         *
         * @return  It. */
        tcs_xd_store *c() const { return s_.get(); }

        /** Gives a slot's current value.
         *
         * @param s    The slot.
         * @param out  Where the value goes; its bytes are the store's.
         * @return     Whether it has a value. */
        bool get(Slot s, Datum &out) const
        {
                return checked(tcs_xd_store_get(m_->c(), c(), s, &out)) == 1;
        }

        /** Gives a slot a value, checked against its type.
         *
         * @param s  The slot.
         * @param d  The value. */
        void set(Slot s, const Datum &d)
        {
                checked(tcs_xd_store_set(m_->c(), c(), s, &d));
        }

        /** Gives a slot's raw value: before its calibrator.
         *
         * @param s    The slot.
         * @param out  Where the value goes.
         * @return     Whether it has a value. */
        bool get_raw(Slot s, Datum &out) const
        {
                return checked(tcs_xd_store_get_raw(m_->c(), c(), s, &out))
                       == 1;
        }

        /** Gives a slot a raw value, which packing writes as it is.
         *
         * @param s  The slot.
         * @param d  The raw value. */
        void set_raw(Slot s, const Datum &d)
        {
                checked(tcs_xd_store_set_raw(m_->c(), c(), s, &d));
        }

        /** Takes a slot's value away.
         *
         * @param s  The slot. */
        void clear(Slot s) { tcs_xd_store_clear(m_->c(), c(), s); }

        /** Makes the values a scratch holds the current ones.
         *
         * @param scratch  What unpacking gave. */
        void commit(const Scratch &scratch);

private:
        /** Frees a C store. */
        struct Free {
                /** Frees it.
                 *
                 * @param s  The store. */
                void operator()(tcs_xd_store *s) const
                {
                        tcs_xd_store_free(s);
                }
        };

        /** The mission. */
        const Mission *m_;
        /** The C store. */
        std::unique_ptr<tcs_xd_store, Free> s_;
};

/** A packet decoded and not yet committed. */
class Scratch {
public:
        /** Makes a scratch.
         *
         * @param m  The mission. */
        explicit Scratch(const Mission &m) : s_(tcs_xd_scratch_new(m.c())) {}

        /** Gives the C scratch.
         *
         * @return  It. */
        tcs_xd_scratch *c() const { return s_.get(); }

private:
        /** Frees a C scratch. */
        struct Free {
                /** Frees it.
                 *
                 * @param s  The scratch. */
                void operator()(tcs_xd_scratch *s) const
                {
                        tcs_xd_scratch_free(s);
                }
        };

        /** The C scratch. */
        std::unique_ptr<tcs_xd_scratch, Free> s_;
};

/** The alarm state that change ranges and minimum violations count. */
class Monitor {
public:
        /** Makes a monitor with no samples.
         *
         * @param m  The mission. */
        explicit Monitor(const Mission &m) : m_(tcs_xd_monitor_new(m.c())) {}

        /** Gives the C monitor.
         *
         * @return  It. */
        tcs_xd_monitor *c() const { return m_.get(); }

private:
        /** Frees a C monitor. */
        struct Free {
                /** Frees it.
                 *
                 * @param m  The monitor. */
                void operator()(tcs_xd_monitor *m) const
                {
                        tcs_xd_monitor_free(m);
                }
        };

        /** The C monitor. */
        std::unique_ptr<tcs_xd_monitor, Free> m_;
};

/** A stream's frame synchronizer, which keeps its mission alive. */
class Deframer {
public:
        /** Takes a C stream.
         *
         * @param d  The stream. */
        explicit Deframer(tcs_xd_deframer *d) : d_(d) {}

        /** Takes the stream's next bytes, calling a function with each
         * frame they complete.
         *
         * @param data  The bytes.
         * @param f     Called with each frame. */
        void push(std::span<const std::uint8_t> data,
                  const std::function<void(std::span<const std::uint8_t>)> &f)
        {
                using F = std::function<void(std::span<const std::uint8_t>)>;
                tcs_xd_deframer_push(
                        d_.get(), data.data(), data.size(),
                        [](void *x, const std::uint8_t *fr, std::uintptr_t n) {
                                (*static_cast<const F *>(x))({fr, n});
                        },
                        const_cast<F *>(&f));
        }

private:
        /** Frees a C stream. */
        struct Free {
                /** Frees it.
                 *
                 * @param d  The stream. */
                void operator()(tcs_xd_deframer *d) const
                {
                        tcs_xd_deframer_free(d);
                }
        };

        /** The C stream. */
        std::unique_ptr<tcs_xd_deframer, Free> d_;
};

inline Node Mission::unpack(Node n, std::span<const std::uint8_t> data,
                            Scratch &scratch) const
{
        Node got{};
        checked(tcs_xd_unpack(c(), n, data.data(), data.size(), scratch.c(),
                              &got));
        return got;
}

inline std::span<const std::uint8_t> Mission::pack(Node n, Store &s) const
{
        const std::uint8_t *out = nullptr;
        std::uintptr_t len = 0;
        checked(tcs_xd_pack(c(), n, s.c(), &out, &len));
        return {out, len};
}

inline void Mission::alarms(
        const Scratch &scratch,
        const std::function<void(std::string_view, int)> &f) const
{
        using F = std::function<void(std::string_view, int)>;
        checked(tcs_xd_alarms(
                c(), scratch.c(),
                [](void *x, const char *name, std::uintptr_t len, int level) {
                        (*static_cast<const F *>(x))({name, len}, level);
                },
                const_cast<F *>(&f)));
}

inline void Mission::monitor_alarms(
        const Scratch &scratch, Monitor &mon, double t,
        const std::function<void(std::string_view, int)> &f) const
{
        using F = std::function<void(std::string_view, int)>;
        checked(tcs_xd_monitor_alarms(
                c(), scratch.c(), mon.c(), t,
                [](void *x, const char *name, std::uintptr_t len, int level) {
                        (*static_cast<const F *>(x))({name, len}, level);
                },
                const_cast<F *>(&f)));
}

inline void Mission::derived(
        const Scratch &scratch,
        const std::function<void(std::string_view, bool, double)> &f) const
{
        using F = std::function<void(std::string_view, bool, double)>;
        checked(tcs_xd_derived(
                c(), scratch.c(),
                [](void *x, const char *name, std::uintptr_t len, bool here,
                   double v) {
                        (*static_cast<const F *>(x))({name, len}, here, v);
                },
                const_cast<F *>(&f)));
}

inline Deframer Mission::stream(const std::string &path) const
{
        tcs_xd_deframer *d = nullptr;
        checked(tcs_xd_stream(c(), path.c_str(), &d));
        return Deframer(d);
}

inline void Store::commit(const Scratch &scratch)
{
        checked(tcs_xd_commit(c(), scratch.c()));
}

} // namespace tcs::xd

#endif
