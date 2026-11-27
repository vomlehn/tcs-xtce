//! Streams: FixedFrameStreams, VariableFrameStreams and CustomStreams, and
//! the chains in which one feeds another, as the Python compiler's
//! `stream` and `link_streams` make them.

use num_bigint::BigInt;
use tcs_xtce::generated as x;

use super::{qname, Compiler};
use crate::dump::Mut;
use crate::model::{
    stream_chain, stream_loop, unsupported, Error, Key, Result, Stream,
};

/// What a stream refers to, which `link_streams` resolves once every
/// stream is compiled: the SpaceSystem it is in, its StreamRef, and a
/// CustomStream's decodedStreamRef and encodedStreamRef.
#[derive(Debug, Clone)]
pub struct StreamRefs {
    /// The key of the SpaceSystem it is defined in.
    pub path: Key,
    /// Its StreamRef, if any.
    pub stream_ref: Option<String>,
    /// A CustomStream's decodedStreamRef.
    pub decoded: Option<String>,
    /// A CustomStream's encodedStreamRef.
    pub encoded: Option<String>,
}

/// Reads hexadecimal as Python's `int(s, 16)` does.
///
/// # Arguments
///
/// * `s`      - The text, which may start with `0x`.
/// * `what`   - What it is, for error messages.
/// * `where_` - Where it is, for error messages.
///
/// # Returns
///
/// The integer.
///
/// # Errors
///
/// Unsupported, if it isn't hexadecimal.
fn hex(s: &str, what: &str, where_: &str) -> Result<BigInt> {
    let t = s.trim();
    let body = t
        .strip_prefix("0x")
        .or_else(|| t.strip_prefix("0X"))
        .unwrap_or(t);
    match crate::pyparse::big_int(body, 16) {
        Some(v) => Ok(v),
        None => unsupported(format!(
            "{where_}: {what} {} is not hexadecimal",
            crate::pyfmt::repr(s)
        )),
    }
}

/// Gives the low bits of an integer.
///
/// # Arguments
///
/// * `v` - The integer.
/// * `n` - How many bits.
///
/// # Returns
///
/// `v & ((1 << n) - 1)`.
fn low(v: &BigInt, n: i64) -> BigInt {
    v & ((BigInt::from(1) << (n as usize)) - 1)
}

impl Compiler<'_> {
    /// Compiles a FixedFrameStream, VariableFrameStream or CustomStream;
    /// the streams it refers to are linked later, by `link_streams`.
    ///
    /// # Arguments
    ///
    /// * `key` - The stream's key.
    ///
    /// # Returns
    ///
    /// The stream, and what it refers to.
    ///
    /// # Errors
    ///
    /// Unsupported, if its pcmType, flag or sync pattern isn't supported,
    /// a variable stream has an InvertAlgorithm, or a reference doesn't
    /// resolve.
    pub fn stream(&self, key: &Key) -> Result<(Mut<Stream>, StreamRefs)> {
        use x::StreamSetChoice as S;
        let (path, st) = self.streams.get(key).unwrap().clone();
        let where_ = format!("stream {}", qname(key));
        let (pcm, inverted) = match st {
            S::FixedFrameStream(s) => (s.pcm_type, s.inverted),
            S::VariableFrameStream(s) => (s.pcm_type, s.inverted),
            S::CustomStream(s) => (s.pcm_type, s.inverted),
        };
        let pcm = pcm.map_or("NRZL", |p| p.as_str()).to_string();
        let inverted = inverted.unwrap_or(false);
        if let S::CustomStream(s) = st {
            // Its input is bytes of the encoded stream, which the
            // application's DecodingAlgorithm decodes; it has no frames.
            let out = Stream {
                key: qname(key),
                pcm,
                inverted,
                custom: true,
                decoder: s.decoding_algorithm.name.clone(),
                ..stream_defaults()
            };
            let refs = StreamRefs {
                path: path.clone(),
                stream_ref: None,
                decoded: Some(s.decoded_stream_ref.clone()),
                encoded: Some(s.encoded_stream_ref.clone()),
            };
            return Ok((Mut::new(out), refs));
        }
        if !matches!(
            pcm.as_str(),
            "NRZL" | "NRZM" | "NRZS" | "BiPhaseL" | "BiPhaseM" | "BiPhaseS"
        ) {
            return unsupported(format!("{where_}: pcmType {pcm}"));
        }
        let (choice_service, choice_container, stream_ref) = match st {
            S::FixedFrameStream(s) => match &s.choice {
                x::FixedFrameStreamChoice::ServiceRef(r) => {
                    (Some(&r.service_ref), None, &s.stream_ref)
                }
                x::FixedFrameStreamChoice::ContainerRef(r) => {
                    (None, Some(&r.container_ref), &s.stream_ref)
                }
            },
            S::VariableFrameStream(s) => match &s.choice {
                x::VariableFrameStreamChoice::ServiceRef(r) => {
                    (Some(&r.service_ref), None, &s.stream_ref)
                }
                x::VariableFrameStreamChoice::ContainerRef(r) => {
                    (None, Some(&r.container_ref), &s.stream_ref)
                }
            },
            S::CustomStream(_) => unreachable!("a CustomStream returned"),
        };
        let mut service = Vec::new();
        let ckey = if let Some(sref) = choice_service {
            let has = |k: &[String]| self.services.contains(k);
            let skey = Self::resolve(sref, &path, &has, "service")?;
            let (spath, sv) = self.services.get(&skey).unwrap().clone();
            let mut refs: Vec<(Key, String)> = Vec::new();
            match &sv.choice {
                x::ServiceChoice::ContainerRefSet(s) => {
                    for c in &s.container_ref {
                        refs.push((spath.clone(), c.container_ref.clone()));
                    }
                }
                x::ServiceChoice::MessageRefSet(s) => {
                    let has = |k: &[String]| self.messages.contains(k);
                    for m in &s.message_ref {
                        let mkey = Self::resolve(
                            &m.message_ref,
                            &spath,
                            &has,
                            "message",
                        )?;
                        let (mpath, msg) =
                            self.messages.get(&mkey).unwrap().clone();
                        refs.push((
                            mpath,
                            msg.container_ref.container_ref.clone(),
                        ));
                    }
                }
            }
            for (rpath, r) in refs {
                service.push(qname(&self.container_key(&r, &rpath)?));
            }
            qname(&skey)
        } else {
            qname(&self.container_key(choice_container.unwrap(), &path)?)
        };
        let fixed = matches!(st, S::FixedFrameStream(_));
        let mut out = Stream {
            key: qname(key),
            container: ckey,
            fixed,
            pcm,
            inverted,
            service,
            ..stream_defaults()
        };
        let refs = StreamRefs {
            path: path.clone(),
            stream_ref: stream_ref.as_ref().map(|r| r.stream_ref.clone()),
            decoded: None,
            encoded: None,
        };
        let (auto_invert, verify, check, max_errors) = match st {
            S::FixedFrameStream(s) => (
                &s.sync_strategy.auto_invert,
                s.sync_strategy.verify_to_lock_good_frames,
                s.sync_strategy.check_to_lock_good_frames,
                s.sync_strategy.max_bit_errors_in_sync_pattern,
            ),
            S::VariableFrameStream(s) => (
                &s.sync_strategy.auto_invert,
                s.sync_strategy.verify_to_lock_good_frames,
                s.sync_strategy.check_to_lock_good_frames,
                s.sync_strategy.max_bit_errors_in_sync_pattern,
            ),
            S::CustomStream(_) => unreachable!("a CustomStream returned"),
        };
        if let Some(alg) = auto_invert
            .as_ref()
            .and_then(|a| a.invert_algorithm.as_ref())
        {
            if !out.fixed {
                return unsupported(format!(
                    "{where_}: an InvertAlgorithm on a variable stream, which \
                     doesn't invert"
                ));
            }
            out.inverter = alg.name.clone();
        }
        let s = match st {
            S::VariableFrameStream(v) => {
                let flag = &v.sync_strategy.flag;
                out.flag_bits =
                    flag.flag_size_in_bits.filter(|b| *b != 0).unwrap_or(6);
                if out.flag_bits < 2 {
                    return unsupported(format!(
                        "{where_}: a flag of {} bit",
                        out.flag_bits
                    ));
                }
                out.flag_ones =
                    flag.flag_bit_type.is_none_or(|t| t.as_str() == "ones");
                return Ok((Mut::new(out), refs));
            }
            S::FixedFrameStream(s) => s,
            S::CustomStream(_) => unreachable!("a CustomStream returned"),
        };
        out.frame_bits = s.frame_length_in_bits;
        let sp = &s.sync_strategy.sync_pattern;
        let n = sp.pattern_length_in_bits;
        if n < 1 {
            return unsupported(format!("{where_}: a {n}-bit sync pattern"));
        }
        // The pattern and mask are truncated from the left.
        let text = sp.pattern.trim();
        let pattern =
            hex(if text.is_empty() { "0" } else { text }, "pattern", &where_)?;
        out.pattern = low(&pattern, n);
        let full = low(&BigInt::from(-1), n);
        let mut mask = full.clone();
        if let Some(m) = &sp.mask {
            let ml = sp.mask_length_in_bits.filter(|b| *b != 0).unwrap_or(n);
            let mt = m.trim();
            let mv =
                hex(if mt.is_empty() { "0" } else { mt }, "mask", &where_)?;
            let ones = low(&BigInt::from(-1), ml);
            mask = low(&mv, ml) | (&full & !ones);
        }
        out.mask = low(&mask, n);
        out.pattern_bits = n;
        out.location = sp.bit_location_from_start_of_container.unwrap_or(0);
        if out.location < 0
            || out.frame_bits < 1
            || out.location + n > out.frame_bits
        {
            return unsupported(format!(
                "{where_}: a sync pattern outside the frame"
            ));
        }
        out.max_errors = max_errors.unwrap_or(0);
        out.aperture = s.sync_aperture_in_bits.unwrap_or(0);
        if let Some(v) = verify {
            out.verify = v;
        }
        if let Some(c) = check {
            out.check = c;
        }
        if let Some(a) = auto_invert {
            out.invert_after = a
                .bad_frames_to_auto_invert
                .filter(|b| *b != 0)
                .unwrap_or(1024)
                * out.frame_bits;
        }
        Ok((Mut::new(out), refs))
    }

    /// Finds a compiled stream by reference.
    ///
    /// # Arguments
    ///
    /// * `streams` - The streams compiled.
    /// * `path`    - The key of the SpaceSystem the reference is in.
    /// * `ref_`    - The reference.
    /// * `where_`  - What refers to it, for error messages.
    ///
    /// # Returns
    ///
    /// The stream.
    ///
    /// # Errors
    ///
    /// Unsupported, if it names no stream, or one not compiled.
    fn find_stream(
        &self,
        streams: &[(Mut<Stream>, StreamRefs)],
        path: &Key,
        ref_: &str,
        where_: &str,
    ) -> Result<Mut<Stream>> {
        let has = |k: &[String]| self.streams.contains(k);
        let key = qname(&Self::resolve(ref_, path, &has, "stream")?);
        match streams.iter().find(|s| s.0.get().key == key) {
            Some(s) => Ok(s.0.clone()),
            None => {
                unsupported(format!("{where_}: stream {key} is not supported"))
            }
        }
    }

    /// Connects each stream to the one that feeds it, and leaves out
    /// those whose chain doesn't work.
    ///
    /// # Arguments
    ///
    /// * `streams` - The streams compiled, with what they refer to.
    /// * `errors`  - Where to add why each one left out is.
    ///
    /// # Returns
    ///
    /// The streams kept, each after those that feed it.
    ///
    /// # Errors
    ///
    /// Cycle, if streams feed one another in a loop, or a CustomStream
    /// decodes into itself.
    pub fn link_streams(
        &self,
        streams: &[(Mut<Stream>, StreamRefs)],
        errors: &mut Vec<String>,
    ) -> Result<Vec<Mut<Stream>>> {
        let mut bad: Vec<(String, String)> = Vec::new();
        for (st, refs) in streams {
            let key = st.get().key.clone();
            let where_ = format!("stream {key}");
            let linked: Result<()> = (|| {
                if let Some(r) = &refs.stream_ref {
                    let f =
                        self.find_stream(streams, &refs.path, r, &where_)?;
                    st.get_mut().feeder = Some(f);
                }
                if st.get().custom {
                    let d = refs.decoded.as_deref().unwrap();
                    let target =
                        self.find_stream(streams, &refs.path, d, &where_)?;
                    let tf = target.get().feeder.clone();
                    if let Some(tf) = tf.filter(|f| !f.same(st)) {
                        return unsupported(format!(
                            "{where_}: decodes into {}, which {} feeds",
                            target.get().key,
                            tf.get().key
                        ));
                    }
                    if target.same(st) {
                        return Err(Error::Cycle(format!(
                            "{where_}: decodes into itself, a loop"
                        )));
                    }
                    target.get_mut().feeder = Some(st.clone());
                    let e = refs.encoded.as_deref().unwrap();
                    let source =
                        self.find_stream(streams, &refs.path, e, &where_)?;
                    if !source.same(st) {
                        st.get_mut().feeder = Some(source);
                    }
                }
                Ok(())
            })();
            match linked {
                Ok(()) => {}
                Err(Error::Unsupported(m)) => bad.push((key, m)),
                Err(e) => return Err(e),
            }
        }
        let is_bad =
            |bad: &[(String, String)], k: &str| bad.iter().any(|b| b.0 == k);
        for (st, _) in streams {
            let mut seen: Vec<Mut<Stream>> = Vec::new();
            let mut s = Some(st.clone());
            while let Some(x) = s {
                if let Some(i) = seen.iter().position(|o| o.same(&x)) {
                    return Err(Error::Cycle(stream_loop(&seen[i..])));
                }
                let xk = x.get().key.clone();
                if is_bad(&bad, &xk) && !x.same(st) {
                    let sk = st.get().key.clone();
                    if !is_bad(&bad, &sk) {
                        bad.push((
                            sk.clone(),
                            format!(
                                "stream {sk}: fed by {xk}, which is not \
                                 supported"
                            ),
                        ));
                    }
                    break;
                }
                s = x.get().feeder.clone();
                seen.push(x);
            }
        }
        errors.extend(bad.iter().map(|b| b.1.clone()));
        // Each stream after those that feed it, whose types it holds.
        let mut out: Vec<Mut<Stream>> = Vec::new();
        for (st, _) in streams {
            if is_bad(&bad, &st.get().key) {
                continue;
            }
            for s in stream_chain(st)?.into_iter().rev() {
                if !out.iter().any(|o| o.same(&s)) {
                    out.push(s);
                }
            }
        }
        Ok(out)
    }
}

/// Gives a stream with the Python's defaults.
///
/// # Returns
///
/// The stream: no name, NRZL, 4 frames to verify and 1 to check, 6-bit
/// flags of ones.
pub fn stream_defaults() -> Stream {
    Stream {
        pcm: "NRZL".into(),
        verify: 4,
        check: 1,
        flag_bits: 6,
        flag_ones: true,
        ..Stream::default()
    }
}
