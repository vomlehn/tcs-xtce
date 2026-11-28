//! Alarms and algorithms: the alarm level of each field of a packet with
//! an alarm, on the packet alone or counting earlier samples as an alarm
//! monitor keeps them, and the outputs of the MathAlgorithms and
//! CustomAlgorithms a packet triggers, as the generated code's
//! `alarms()`, `monitor()` and `algorithms()` give them.

use std::cmp::Ordering;
use std::collections::HashMap;

use num_bigint::Sign;
use num_traits::ToPrimitive;
use tcs_xtce::packet::{self as pk, AlarmLevel};
use tcs_xtce_model::model::{
    Alarm, AlarmContext, AlarmCriteria, AlarmDef, Band, CustomAlarm, Dfa,
    Encoding, Local, Node, Num, Term, VType,
};

use crate::interp::{self, Algorithm, Look, Val};

/// What an alarm monitor keeps for one parameter, as the runtime's
/// `AlarmState` keeps it: whether it is in alarm, its runs of samples in
/// and out of alarm, and its last values and their times, for change
/// ranges.
#[derive(Debug, Clone)]
struct State {
    /// The samples in alarm in a row.
    violations: u32,
    /// The samples out of alarm in a row.
    conformances: u32,
    /// The level reported last, 0 for none.
    level: u8,
    /// The last values, as many as the change ranges look back at.
    values: Vec<f64>,
    /// Their times.
    times: Vec<f64>,
    /// How many values are kept.
    len: usize,
    /// Where the next value goes.
    next: usize,
}

impl State {
    /// Makes the state of a parameter not in alarm, with no samples.
    ///
    /// # Arguments
    ///
    /// * `h` - The samples its change ranges look back at.
    ///
    /// # Returns
    ///
    /// The state.
    fn new(h: usize) -> Self {
        State {
            violations: 0,
            conformances: 0,
            level: 0,
            values: vec![0.0; h],
            times: vec![0.0; h],
            len: 0,
            next: 0,
        }
    }

    /// Takes a sample's own level and decides the level to report, as
    /// the runtime's `AlarmState::update` does.
    ///
    /// # Arguments
    ///
    /// * `level`           - The sample's own level, 0 for none.
    /// * `min_violations`  - The samples in alarm in a row that raise it.
    /// * `min_conformance` - The samples out of alarm in a row that clear
    ///                       it.
    ///
    /// # Returns
    ///
    /// The level to report, 0 for none.
    fn update(
        &mut self,
        level: u8,
        min_violations: u32,
        min_conformance: u32,
    ) -> u8 {
        if level > 0 {
            self.violations += 1;
            self.conformances = 0;
            if self.level > 0 || self.violations >= min_violations {
                self.level = level;
            }
        } else {
            self.conformances += 1;
            self.violations = 0;
            if self.level > 0 && self.conformances >= min_conformance {
                self.level = 0;
            }
        }
        self.level
    }

    /// Works out how much a value changed since an earlier sample, as the
    /// runtime's `AlarmState::change` does.
    ///
    /// # Arguments
    ///
    /// * `x`          - The value.
    /// * `t`          - Its time, in seconds.
    /// * `k`          - How many samples back the earlier one is.
    /// * `per_second` - Whether to divide the change by the time between.
    /// * `percent`    - Whether the change is a percentage of the earlier
    ///                  value.
    ///
    /// # Returns
    ///
    /// The change, or `None` when there is no such sample or no change can
    /// be worked out.
    fn change(
        &self,
        x: f64,
        t: f64,
        k: usize,
        per_second: bool,
        percent: bool,
    ) -> Option<f64> {
        let h = self.values.len();
        if k == 0 || self.len < k {
            return None;
        }
        let i = (self.next + h - k) % h;
        let (then, t_then) = (self.values[i], self.times[i]);
        let mut d = x - then;
        if percent {
            if then == 0.0 {
                return None;
            }
            d = d / then.abs() * 100.0;
        }
        if per_second {
            let dt = t - t_then;
            if !(dt > 0.0) {
                return None;
            }
            d /= dt;
        }
        Some(d)
    }

    /// Remembers a sample, forgetting the oldest once the state is full.
    ///
    /// # Arguments
    ///
    /// * `x` - The value.
    /// * `t` - Its time, in seconds.
    fn push(&mut self, x: f64, t: f64) {
        let h = self.values.len();
        if h == 0 {
            return;
        }
        self.values[self.next] = x;
        self.times[self.next] = t;
        self.next = (self.next + 1) % h;
        self.len = (self.len + 1).min(h);
    }
}

/// The alarm state of each parameter of a mission whose alarms depend on
/// earlier samples, which [`crate::Mission::monitor_alarms`] reads and
/// updates, as the generated code's `AlarmMonitor` is.
#[derive(Debug, Clone)]
pub struct Monitor {
    /// The number of the mission it belongs to.
    pub(crate) mission: u64,
    /// Each monitored parameter's state.
    states: Vec<State>,
}

/// Which parameters a mission's alarm monitor keeps, and how many
/// samples of each.
#[derive(Debug, Clone, Default)]
pub(crate) struct Monitored {
    /// The place of each parameter's state, by its qualified name.
    index: HashMap<String, usize>,
    /// The samples each one's change ranges look back at.
    history: Vec<usize>,
}

impl Monitored {
    /// Makes the list of a mission's monitored parameters.
    ///
    /// # Arguments
    ///
    /// * `list` - The model's: each parameter's qualified name, its name in
    ///            the generated code and its history.
    ///
    /// # Returns
    ///
    /// The list.
    pub(crate) fn new(list: &[(String, String, i64)]) -> Self {
        let mut m = Monitored::default();
        for (key, _, h) in list {
            m.index.insert(key.clone(), m.history.len());
            m.history.push((*h).max(0) as usize);
        }
        m
    }

    /// Makes a monitor with no samples.
    ///
    /// # Arguments
    ///
    /// * `mission` - The number of the mission.
    ///
    /// # Returns
    ///
    /// The monitor.
    pub(crate) fn monitor(&self, mission: u64) -> Monitor {
        Monitor {
            mission,
            states: self.history.iter().map(|&h| State::new(h)).collect(),
        }
    }
}

/// Says whether an alarm definition's level depends on earlier samples.
///
/// # Arguments
///
/// * `d` - The definition.
///
/// # Returns
///
/// Whether it does.
fn def_stateful(d: &AlarmDef) -> bool {
    d.change.is_some() || d.min_violations > 1 || d.min_conformance > 1
}

/// Says whether any of an alarm's definitions satisfies a test.
///
/// # Arguments
///
/// * `a` - The alarm.
/// * `f` - The test.
///
/// # Returns
///
/// Whether one does.
fn any_def(a: &Alarm, f: impl Fn(&AlarmDef) -> bool) -> bool {
    a.contexts.iter().any(|(_, d)| f(d)) || a.default.as_ref().is_some_and(&f)
}

/// Says whether criteria all hold; one that can't be evaluated doesn't.
///
/// # Arguments
///
/// * `terms` - The criteria.
/// * `vals`  - The values of the packet.
///
/// # Returns
///
/// Whether they do.
fn holds_all(terms: &[Term], vals: &dyn Look) -> bool {
    terms.iter().all(|t| interp::term(t, vals).unwrap_or(false))
}

/// Compares a value with an integer band's bound.
///
/// # Arguments
///
/// * `x` - The value.
/// * `n` - The bound.
///
/// # Returns
///
/// How the value compares with it, or `None` if they don't compare.
fn cmp_bound(x: &Val, n: &Num) -> Option<Ordering> {
    match (x, n) {
        (Val::Int(i), Num::Int(b)) => Some(match b.to_i128() {
            Some(v) => i.cmp(&v),
            // Past every value of the field.
            None if b.sign() == Sign::Minus => Ordering::Greater,
            None => Ordering::Less,
        }),
        (Val::Big(i), Num::Int(b)) => Some(crate::wide::cmp_big(i, b)),
        (x, n) => interp::math_value(x).ok()?.partial_cmp(&n.as_f64()),
    }
}

/// Raises a level to a band's if a float violates the band.
///
/// # Arguments
///
/// * `b`  - The band.
/// * `x`  - The value.
/// * `lv` - The level.
fn band_f64(b: &Band, x: f64, lv: &mut u8) {
    let lo = b.lo.as_ref().map(Num::as_f64);
    let hi = b.hi.as_ref().map(Num::as_f64);
    if pk::in_band(x, lo, b.lo_incl, hi, b.hi_incl) == b.inside {
        *lv = (*lv).max(b.level as u8);
    }
}

/// Raises a level to a band's if a value violates the band.
///
/// # Arguments
///
/// * `b`  - The band.
/// * `x`  - The value.
/// * `vt` - Its type.
/// * `lv` - The level.
fn band(b: &Band, x: &Val, vt: &VType, lv: &mut u8) {
    if vt.kind != "int" {
        if let Ok(v) = interp::math_value(x) {
            band_f64(b, v, lv);
        }
        return;
    }
    // Integers are compared exactly, both bounds in the band.
    let above = |n: &Num| cmp_bound(x, n).is_some_and(|o| o != Ordering::Less);
    let below =
        |n: &Num| cmp_bound(x, n).is_some_and(|o| o != Ordering::Greater);
    let inside =
        b.lo.as_ref().is_none_or(above) && b.hi.as_ref().is_none_or(below);
    if inside == b.inside {
        *lv = (*lv).max(b.level as u8);
    }
}

/// Runs a pattern's automaton over a string.
///
/// # Arguments
///
/// * `d` - The automaton; it starts in state 0.
/// * `s` - The string's bytes.
///
/// # Returns
///
/// Whether it accepts all of them.
fn dfa_match(d: &Dfa, s: &[u8]) -> bool {
    let mut q = 0usize;
    for &b in s {
        let row = &d.trans[d.starts[q] as usize..d.starts[q + 1] as usize];
        let b = b as i64;
        match row.iter().find(|&&(lo, hi, _)| lo <= b && b <= hi) {
            Some(&(_, _, next)) => q = next as usize,
            None => return false,
        }
    }
    d.accept[q]
}

/// The state of a parameter an alarm monitor keeps, while one of its
/// samples is evaluated.
struct Live<'s> {
    /// The state.
    s: &'s mut State,
    /// The sample's time, in seconds.
    t: f64,
    /// The samples in alarm in a row that raise it.
    mv: u32,
    /// The samples out of alarm in a row that clear it.
    mc: u32,
}

/// Raises a level to the one an alarm definition gives a value.
///
/// # Arguments
///
/// * `ad`   - The definition.
/// * `x`    - The value.
/// * `vt`   - Its type, an array's element's for an array.
/// * `vals` - The values of the packet, which criteria and custom alarms
///            read.
/// * `live` - The monitored state, to check change ranges and take the
///            definition's minViolations and minConformance.
/// * `lv`   - The level.
fn alarm_def(
    ad: &AlarmDef,
    x: &Val,
    vt: &VType,
    vals: &dyn Look,
    live: Option<&mut Live<'_>>,
    lv: &mut u8,
) {
    if let Some(l) = live {
        if let (Some(c), Ok(v)) = (&ad.change, interp::math_value(x)) {
            let k = c.samples.max(0) as usize;
            if let Some(d) = l.s.change(v, l.t, k, c.per_second, c.percent) {
                for b in &c.bands {
                    band_f64(b, d, lv);
                }
            }
        }
        if (ad.min_violations, ad.min_conformance) != (1, 1) {
            l.mv = ad.min_violations as u32;
            l.mc = ad.min_conformance as u32;
        }
    }
    for b in &ad.bands {
        band(b, x, vt, lv);
    }
    if vt.kind == "enum" && (!ad.labels.is_empty() || ad.default != 0) {
        let v = match x {
            Val::Enum(i) => Some(*i),
            Val::Big(b) => crate::wide::to_i64(b),
            _ => None,
        };
        let level = v
            .and_then(|v| ad.labels.iter().find(|l| l.0 <= v && v <= l.1))
            .map_or(ad.default, |l| l.2);
        *lv = (*lv).max(level as u8);
    }
    if vt.kind == "string" && (!ad.patterns.is_empty() || ad.default != 0) {
        if let Val::Str(s) = x {
            let level = ad
                .patterns
                .iter()
                .find(|(d, _)| dfa_match(d, s.as_bytes()))
                .map_or(ad.default, |p| p.1);
            *lv = (*lv).max(level as u8);
        }
    }
    if let Some(CustomAlarm::Bound(h, inputs)) = &ad.custom {
        // None while its function isn't set.
        let level = match vals.hooks().get(&h.get()) {
            Ok(Algorithm::Alarm(f)) => {
                interp::with_inputs(inputs, vals, |x| f(x))
                    .unwrap_or(0)
                    .min(5)
            }
            _ => 0,
        };
        *lv = (*lv).max(level);
    }
    for (level, c) in &ad.conditions {
        if let AlarmCriteria::Bound(terms) = c {
            if holds_all(terms, vals) {
                *lv = (*lv).max(*level as u8);
            }
        }
    }
}

/// Gives the level an alarm gives one value: that of the definition of
/// the first context whose criteria hold, or of its default.
///
/// # Arguments
///
/// * `a`    - The alarm.
/// * `x`    - The value.
/// * `vt`   - Its type, an array's element's for an array.
/// * `vals` - The values of the packet.
/// * `live` - The monitored state, if the alarm is monitored.
///
/// # Returns
///
/// The level, 0 for none.
fn level_of(
    a: &Alarm,
    x: &Val,
    vt: &VType,
    vals: &dyn Look,
    live: Option<&mut Live<'_>>,
) -> u8 {
    let def = a
        .contexts
        .iter()
        .find(|(c, _)| match c {
            AlarmContext::Bound(t) => holds_all(t, vals),
            AlarmContext::Unbound(_) => false,
        })
        .map(|(_, d)| d)
        .or(a.default.as_ref());
    let mut lv = 0;
    if let Some(d) = def {
        alarm_def(d, x, vt, vals, live, &mut lv);
    }
    lv
}

/// Gives a field's alarm level: an array's is the worst of its
/// elements'.
///
/// # Arguments
///
/// * `l`     - The field's Local.
/// * `a`     - Its alarm, bound to the packet.
/// * `vals`  - The values of the packet.
/// * `state` - Its parameter's monitored state and the sample's time, to
///             count earlier samples.
///
/// # Returns
///
/// The level, or `None` when the field is absent.
fn field_level(
    l: &Local,
    a: &Alarm,
    vals: &dyn Look,
    state: Option<(&mut State, f64)>,
) -> Option<AlarmLevel> {
    let vt = l.vt();
    let v = vals.val(l.id)?;
    let mut live = state.map(|(s, t)| Live { s, t, mv: 1, mc: 1 });
    let lv = match (&vt.enc, v) {
        (Encoding::Array(e), Val::Arr(es, used)) => es[..*used]
            .iter()
            .map(|x| level_of(a, x, &e.elem, vals, live.as_mut()))
            .max()
            .unwrap_or(0),
        _ => level_of(a, v, &vt, vals, live.as_mut()),
    };
    let Some(l) = live else {
        return Some(AlarmLevel::from_index(lv));
    };
    let history =
        any_def(a, |d| d.change.as_ref().is_some_and(|c| c.samples > 0));
    if history {
        if let Ok(x) = interp::math_value(v) {
            l.s.push(x, l.t);
        }
    }
    Some(AlarmLevel::from_index(l.s.update(lv, l.mv, l.mc)))
}

/// Gives the alarm level of each field of a packet with an alarm.
///
/// # Arguments
///
/// * `n`       - The packet's node.
/// * `vals`    - Its values.
/// * `monitor` - The alarm monitor, its plan and the sample's time, to
///               count earlier samples for alarms that depend on them and
///               record this one; `None` to count only this one.
/// * `f`       - Called with each field's name and level, `None` when the
///               field is absent.
pub(crate) fn levels(
    n: &Node,
    vals: &dyn Look,
    mut monitor: Option<(&mut Monitor, &Monitored, f64)>,
    f: &mut dyn FnMut(&str, Option<AlarmLevel>),
) {
    for (name, l, a) in &n.alarms {
        let state = match &mut monitor {
            Some((m, plan, t)) if any_def(a, def_stateful) => plan
                .index
                .get(a.key.as_str())
                .map(|&i| (&mut m.states[i], *t)),
            _ => None,
        };
        f(name, field_level(&l.get(), a, vals, state));
    }
}

/// The most outputs of a CustomAlgorithm the codec gathers on the stack;
/// one with more gathers them on the heap.
const OUTPUTS: usize = 32;

/// Gives the output of each MathAlgorithm and CustomAlgorithm a packet
/// triggers.
///
/// # Arguments
///
/// * `n`    - The packet's node.
/// * `vals` - Its values.
/// * `f`    - Called with each output's name and value, `None` when an
///            input is absent or a custom algorithm's function isn't set or
///            gives none.
pub(crate) fn outputs(
    n: &Node,
    vals: &dyn Look,
    f: &mut dyn FnMut(&str, Option<f64>),
) {
    for alg in &n.algorithms {
        let v = match (&alg.hook, &alg.expr) {
            (Some(h), _) => {
                let h = h.get();
                let k = h.outputs.max(0) as usize;
                let i = alg.index.max(0) as usize;
                match vals.hooks().get(&h) {
                    Ok(Algorithm::Outputs(g)) if k <= OUTPUTS => {
                        let mut out = [0.0; OUTPUTS];
                        interp::with_inputs(&alg.inputs, vals, |x| {
                            g(x, &mut out[..k])
                        })
                        .ok()
                        .filter(|ok| *ok)
                        .and_then(|_| out.get(i).copied())
                    }
                    Ok(Algorithm::Outputs(g)) => {
                        let mut out = vec![0.0; k];
                        interp::with_inputs(&alg.inputs, vals, |x| {
                            g(x, &mut out)
                        })
                        .ok()
                        .filter(|ok| *ok)
                        .and_then(|_| out.get(i).copied())
                    }
                    _ => None,
                }
            }
            (None, Some(e)) => interp::math(e, 0.0, vals).ok(),
            (None, None) => None,
        };
        f(&alg.name, v);
    }
}
