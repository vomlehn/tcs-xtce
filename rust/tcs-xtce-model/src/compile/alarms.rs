//! A parameter type's alarms: its DefaultAlarm and ContextAlarmList, as
//! the Python compiler's `alarm` and `alarm_def` make them.

use tcs_xtce::generated as x;

use super::types::{int_band, time_unit};
use super::xtype::{AlarmView, RangesRef, XType};
use super::{child, qname, Compiler};
use crate::dump::Elem;
use crate::model::{
    unsupported, Alarm, AlarmContext, AlarmCriteria, AlarmDef, Band, Change,
    CustomAlarm, Error, Key, Num, Result, VType, LEVELS,
};
use crate::pyfmt::repr;
use crate::regex::compile_pattern;

/// Gives the index of a concern level.
///
/// # Arguments
///
/// * `level` - The level.
///
/// # Returns
///
/// Its index in [`LEVELS`].
fn level_of(level: x::ConcernLevelsType) -> i64 {
    LEVELS
        .iter()
        .position(|l| *l == level.as_str())
        .expect("an XTCE concern level") as i64
}

/// The bounds of an alarm range, as the Python reads them.
struct Bounds {
    /// minInclusive.
    min_inclusive: Option<f64>,
    /// minExclusive.
    min_exclusive: Option<f64>,
    /// maxInclusive.
    max_inclusive: Option<f64>,
    /// maxExclusive.
    max_exclusive: Option<f64>,
}

/// Converts one alarm range to a band.
///
/// # Arguments
///
/// * `r`      - The range's bounds.
/// * `level`  - The alarm level, an index into [`LEVELS`].
/// * `inside` - Whether a value inside the range is in alarm.
/// * `unit`   - The seconds in its units, for a time; 1 otherwise.
///
/// # Returns
///
/// The band.
fn band(r: &Bounds, level: i64, inside: bool, unit: f64) -> Band {
    let lo = r.min_inclusive.or(r.min_exclusive);
    let hi = r.max_inclusive.or(r.max_exclusive);
    Band {
        level,
        lo: lo.map(|v| Num::Float(v * unit)),
        lo_incl: r.min_exclusive.is_none(),
        hi: hi.map(|v| Num::Float(v * unit)),
        hi_incl: r.max_exclusive.is_none(),
        inside,
    }
}

/// Converts an AlarmRanges to bands.
///
/// # Arguments
///
/// * `ranges` - The XTCE ranges.
/// * `unit`   - The seconds in its units, for a time; 1 otherwise.
///
/// # Returns
///
/// A band for each level it gives a range for.
fn range_bands(ranges: RangesRef<'_>, unit: f64) -> Vec<Band> {
    let inside = ranges.range_form() == Some(x::RangeFormType::Inside);
    ranges
        .levels()
        .iter()
        .enumerate()
        .filter_map(|(i, r)| {
            r.map(|r| {
                band(
                    &Bounds {
                        min_inclusive: r.min_inclusive,
                        min_exclusive: r.min_exclusive,
                        max_inclusive: r.max_inclusive,
                        max_exclusive: r.max_exclusive,
                    },
                    i as i64 + 1,
                    inside,
                    unit,
                )
            })
        })
        .collect()
}

impl Compiler<'_> {
    /// Compiles the DefaultAlarm and ContextAlarmList of a parameter type.
    /// One that can't be evaluated is kept with a note saying why, so that
    /// the packets still compile.
    ///
    /// # Arguments
    ///
    /// * `t`      - The XTCE parameter type, with what it inherits.
    /// * `vt`     - Its compiled type.
    /// * `path`   - The key of the SpaceSystem it is defined in.
    /// * `where_` - What it is, for messages.
    ///
    /// # Returns
    ///
    /// The alarm, or `None` if the type has none.
    pub fn alarm(
        &mut self,
        t: &XType,
        vt: &VType,
        path: &Key,
        where_: &str,
    ) -> Option<Alarm> {
        let (da, cl) = t.alarms();
        if da.is_none() && cl.is_none() {
            return None;
        }
        let mut build = || -> Result<Alarm> {
            let default = match &da {
                Some(a) => Some(self.alarm_def(a, vt, path, where_)?),
                None => None,
            };
            let mut contexts = Vec::new();
            for ca in cl.iter().flatten() {
                let m = ca.context_match.expect("a context alarm's match");
                contexts.push((
                    AlarmContext::Unbound(Elem {
                        value: m.clone(),
                        class: "ContextMatchType",
                    }),
                    self.alarm_def(ca, vt, path, where_)?,
                ));
            }
            Ok(Alarm {
                path: path.clone(),
                default,
                contexts,
                note: String::new(),
                key: String::new(),
            })
        };
        Some(match build() {
            Ok(a) => a,
            Err(Error::Unsupported(m)) | Err(Error::Cycle(m)) => Alarm {
                path: path.clone(),
                default: None,
                contexts: Vec::new(),
                note: m,
                key: String::new(),
            },
        })
    }

    /// Compiles one DefaultAlarm or ContextAlarm.
    ///
    /// # Arguments
    ///
    /// * `a`      - The XTCE alarm.
    /// * `vt`     - The compiled type of the value it watches.
    /// * `path`   - The key of the SpaceSystem the type is defined in.
    /// * `where_` - What the type is, for error messages.
    ///
    /// # Returns
    ///
    /// The alarm's conditions, ranges, change limits, label and pattern
    /// levels; empty if it is disabled.
    ///
    /// # Errors
    ///
    /// Unsupported, if it uses something not supported, or doesn't fit
    /// the value's kind.
    fn alarm_def(
        &mut self,
        a: &AlarmView<'_>,
        vt: &VType,
        path: &Key,
        where_: &str,
    ) -> Result<AlarmDef> {
        let mut d = AlarmDef {
            bands: Vec::new(),
            labels: Vec::new(),
            patterns: Vec::new(),
            default: 0,
            conditions: Vec::new(),
            min_violations: a.min_violations.filter(|n| *n != 0).unwrap_or(1),
            min_conformance: a.min_conformance.filter(|n| *n != 0).unwrap_or(1),
            change: None,
            custom: None,
        };
        if a.disabled == Some(true) {
            return Ok(d);
        }
        if let Some(c) = a.custom {
            let alg = &c.input_algorithm;
            let h = self.hook("alarm", &qname(&child(path, &alg.name)), 0);
            d.custom = Some(CustomAlarm::Unbound(
                h,
                Elem {
                    value: alg.clone(),
                    class: "InputAlgorithmType",
                },
            ));
        } else if let Some(ac) = a.conditions {
            let levels = [
                &ac.watch_alarm,
                &ac.warning_alarm,
                &ac.distress_alarm,
                &ac.critical_alarm,
                &ac.severe_alarm,
            ];
            for (i, mc) in levels.iter().enumerate() {
                if let Some(mc) = mc {
                    d.conditions.push((
                        i as i64 + 1,
                        AlarmCriteria::Unbound(Elem {
                            value: mc.clone(),
                            class: "MatchCriteriaType",
                        }),
                    ));
                }
            }
        }
        if let Some(cr) = a.change {
            if vt.kind != "int" && vt.kind != "float" {
                return unsupported(format!(
                    "{where_}: ChangeAlarmRanges on a {} value",
                    vt.kind
                ));
            }
            if cr.span_of_interest_in_seconds.is_some_and(|s| s != 0.0) {
                return unsupported(format!(
                    "{where_}: ChangeAlarmRanges with a \
                     spanOfInterestInSeconds"
                ));
            }
            d.change = Some(Change {
                bands: range_bands(RangesRef::Change(cr), 1.0),
                per_second: cr
                    .change_type
                    .map_or(true, |c| c == x::ChangeSpanType::ChangePerSecond),
                percent: cr.change_basis
                    == Some(x::ChangeBasisType::PercentageChange),
                samples: cr
                    .span_of_interest_in_samples
                    .filter(|n| *n != 0)
                    .unwrap_or(1),
            });
        }
        if let Some(cr) = a.change_per_second {
            if vt.kind != "float" {
                return unsupported(format!(
                    "{where_}: ChangePerSecondAlarmRanges on a time held as \
                     text or bytes"
                ));
            }
            let tu = cr.time_units.map(|u| u.as_str()).unwrap_or("seconds");
            d.change = Some(Change {
                bands: range_bands(RangesRef::Time(cr), time_unit(tu)),
                per_second: true,
                percent: false,
                samples: 1,
            });
        }
        if let Some(ranges) = a.static_ranges {
            let tu =
                ranges.time_units().map(|u| u.as_str()).unwrap_or("seconds");
            d.bands.extend(range_bands(ranges, time_unit(tu)));
        }
        for r in a.multi.iter().flat_map(|m| m.range.iter()) {
            let level = r.level.map(level_of).unwrap_or(0);
            let inside = r.range_form == Some(x::RangeFormType::Inside);
            d.bands.push(band(
                &Bounds {
                    min_inclusive: r.min_inclusive,
                    min_exclusive: r.min_exclusive,
                    max_inclusive: r.max_inclusive,
                    max_exclusive: r.max_exclusive,
                },
                level,
                inside,
                1.0,
            ));
        }
        if let Some(l) = a.default_level {
            d.default = level_of(l);
        }
        for ea in a.enum_list.iter().flat_map(|l| l.enumeration_alarm.iter()) {
            let Some(lab) = vt
                .labels
                .iter()
                .find(|lab| lab.label == ea.enumeration_label)
            else {
                return unsupported(format!(
                    "{where_}: an alarm on unknown label {}",
                    repr(&ea.enumeration_label)
                ));
            };
            d.labels
                .push((lab.value, lab.max_value, level_of(ea.alarm_level)));
        }
        for sa in a.string_list.iter().flat_map(|l| l.string_alarm.iter()) {
            d.patterns.push((
                compile_pattern(&sa.match_pattern)?,
                level_of(sa.alarm_level),
            ));
        }
        if !d.bands.is_empty() && !matches!(vt.kind.as_str(), "int" | "float") {
            return unsupported(format!(
                "{where_}: alarm ranges on a {} value",
                vt.kind
            ));
        }
        if vt.kind == "int" {
            d.bands = d.bands.iter().map(|b| int_band(b, vt)).collect();
        }
        Ok(d)
    }
}
