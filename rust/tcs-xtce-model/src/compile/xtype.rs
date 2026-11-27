//! The XTCE parameter and argument types, as the compiler reads them:
//! one enum for the twenty kinds, with what the Python compiler finds on
//! them by name (`getattr`) given by methods that say `None` where a kind
//! has no such field.

use tcs_xtce::generated as x;

/// A parameter type or an argument type, owned, as `inherited` gives it.
#[derive(Debug, Clone)]
pub enum XType {
    /// A parameter type.
    P(x::ParameterTypeSetChoice),
    /// An argument type.
    A(x::ArgumentTypeSetChoice),
}

/// Runs an expression on the struct inside whichever kind a type is.
macro_rules! each {
    ($t:expr, $v:ident => $e:expr) => {
        match $t {
            XType::P(p) => match p {
                x::ParameterTypeSetChoice::StringParameterType($v) => $e,
                x::ParameterTypeSetChoice::EnumeratedParameterType($v) => $e,
                x::ParameterTypeSetChoice::IntegerParameterType($v) => $e,
                x::ParameterTypeSetChoice::BinaryParameterType($v) => $e,
                x::ParameterTypeSetChoice::FloatParameterType($v) => $e,
                x::ParameterTypeSetChoice::BooleanParameterType($v) => $e,
                x::ParameterTypeSetChoice::RelativeTimeParameterType($v) => $e,
                x::ParameterTypeSetChoice::AbsoluteTimeParameterType($v) => $e,
                x::ParameterTypeSetChoice::ArrayParameterType($v) => $e,
                x::ParameterTypeSetChoice::AggregateParameterType($v) => $e,
            },
            XType::A(a) => match a {
                x::ArgumentTypeSetChoice::StringArgumentType($v) => $e,
                x::ArgumentTypeSetChoice::EnumeratedArgumentType($v) => $e,
                x::ArgumentTypeSetChoice::IntegerArgumentType($v) => $e,
                x::ArgumentTypeSetChoice::BinaryArgumentType($v) => $e,
                x::ArgumentTypeSetChoice::FloatArgumentType($v) => $e,
                x::ArgumentTypeSetChoice::BooleanArgumentType($v) => $e,
                x::ArgumentTypeSetChoice::RelativeTimeArgumentType($v) => $e,
                x::ArgumentTypeSetChoice::AbsoluteTimeArgumentType($v) => $e,
                x::ArgumentTypeSetChoice::ArrayArgumentType($v) => $e,
                x::ArgumentTypeSetChoice::AggregateArgumentType($v) => $e,
            },
        }
    };
}

/// An encoding of a value, as a type's choice of data encoding gives it.
#[derive(Debug, Clone, Copy)]
pub enum EncRef<'a> {
    /// An IntegerDataEncoding.
    Int(&'a x::IntegerDataEncodingType),
    /// A FloatDataEncoding.
    Float(&'a x::FloatDataEncodingType),
    /// A parameter's StringDataEncoding.
    Str(&'a x::StringDataEncodingType),
    /// An argument's StringDataEncoding.
    ArgStr(&'a x::ArgumentStringDataEncodingType),
    /// A parameter's BinaryDataEncoding.
    Bin(&'a x::BinaryDataEncodingType),
    /// An argument's BinaryDataEncoding.
    ArgBin(&'a x::ArgumentBinaryDataEncodingType),
}

impl<'a> EncRef<'a> {
    /// Gives the encoding's element name.
    ///
    /// # Returns
    ///
    /// Such as `IntegerDataEncoding`.
    pub fn name(&self) -> &'static str {
        match self {
            EncRef::Int(_) => "IntegerDataEncoding",
            EncRef::Float(_) => "FloatDataEncoding",
            EncRef::Str(_) | EncRef::ArgStr(_) => "StringDataEncoding",
            EncRef::Bin(_) | EncRef::ArgBin(_) => "BinaryDataEncoding",
        }
    }

    /// Gives the encoding's bitOrder.
    ///
    /// # Returns
    ///
    /// The bit order, if it gives one.
    pub fn bit_order(&self) -> Option<x::BitOrderType> {
        match self {
            EncRef::Int(e) => e.bit_order,
            EncRef::Float(e) => e.bit_order,
            EncRef::Str(e) => e.bit_order,
            EncRef::ArgStr(e) => e.bit_order,
            EncRef::Bin(e) => e.bit_order,
            EncRef::ArgBin(e) => e.bit_order,
        }
    }

    /// Gives the encoding's byteOrder.
    ///
    /// # Returns
    ///
    /// The byte order as written, if it gives one.
    pub fn byte_order(&self) -> Option<&'a str> {
        match self {
            EncRef::Int(e) => e.byte_order.as_deref(),
            EncRef::Float(e) => e.byte_order.as_deref(),
            EncRef::Str(e) => e.byte_order.as_deref(),
            EncRef::ArgStr(e) => e.byte_order.as_deref(),
            EncRef::Bin(e) => e.byte_order.as_deref(),
            EncRef::ArgBin(e) => e.byte_order.as_deref(),
        }
    }

    /// Says whether the encoding has an ErrorDetectCorrect.
    ///
    /// # Returns
    ///
    /// Whether it does.
    pub fn has_edc(&self) -> bool {
        match self {
            EncRef::Int(e) => e.error_detect_correct.is_some(),
            EncRef::Float(e) => e.error_detect_correct.is_some(),
            EncRef::Str(e) => e.error_detect_correct.is_some(),
            EncRef::ArgStr(e) => e.error_detect_correct.is_some(),
            EncRef::Bin(e) => e.error_detect_correct.is_some(),
            EncRef::ArgBin(e) => e.error_detect_correct.is_some(),
        }
    }
}

/// Gives the encoding a parameter type's choice of data encoding names.
macro_rules! enc_of {
    ($c:expr, $m:ident) => {
        $c.as_ref().map(|c| match c {
            x::$m::BinaryDataEncoding(e) => EncRef::Bin(e),
            x::$m::FloatDataEncoding(e) => EncRef::Float(e),
            x::$m::IntegerDataEncoding(e) => EncRef::Int(e),
            x::$m::StringDataEncoding(e) => EncRef::Str(e),
        })
    };
    (arg $c:expr, $m:ident) => {
        $c.as_ref().map(|c| match c {
            x::$m::BinaryDataEncoding(e) => EncRef::ArgBin(e),
            x::$m::FloatDataEncoding(e) => EncRef::Float(e),
            x::$m::IntegerDataEncoding(e) => EncRef::Int(e),
            x::$m::StringDataEncoding(e) => EncRef::ArgStr(e),
        })
    };
}

/// A ValidRange, or an argument type's ValidRangeSet.
#[derive(Debug, Clone, Copy)]
pub enum ValidRanges<'a> {
    /// An integer parameter type's ValidRange.
    Int(&'a x::IntegerDataValidRangeType),
    /// A float parameter type's ValidRange.
    Float(&'a x::FloatDataValidRangeType),
    /// An integer argument type's ValidRangeSet.
    IntSet(&'a x::ValidIntegerRangeSetType),
    /// A float argument type's ValidRangeSet.
    FloatSet(&'a x::ValidFloatRangeSetType),
}

/// One range of a [`ValidRanges`], as the Python reads it.
#[derive(Debug, Clone, Copy)]
pub struct Range {
    /// minInclusive, as an int for an integer range.
    pub min_inclusive: Option<f64>,
    /// minInclusive of an integer range.
    pub min_inclusive_int: Option<i64>,
    /// minExclusive.
    pub min_exclusive: Option<f64>,
    /// maxInclusive, as a float.
    pub max_inclusive: Option<f64>,
    /// maxInclusive of an integer range.
    pub max_inclusive_int: Option<i64>,
    /// maxExclusive.
    pub max_exclusive: Option<f64>,
    /// validRangeAppliesToCalibrated, where the range itself has it.
    pub applies_to_calibrated: Option<bool>,
}

impl ValidRanges<'_> {
    /// Lists the ranges.
    ///
    /// # Returns
    ///
    /// A ValidRange's one range, or a ValidRangeSet's ranges, with what
    /// each has itself (a set's validRangeAppliesToCalibrated is on the
    /// set, not on its ranges, so its ranges have none).
    pub fn ranges(&self) -> Vec<Range> {
        let int = |lo: Option<i64>, hi: Option<i64>, a| Range {
            min_inclusive: lo.map(|v| v as f64),
            min_inclusive_int: lo,
            min_exclusive: None,
            max_inclusive: hi.map(|v| v as f64),
            max_inclusive_int: hi,
            max_exclusive: None,
            applies_to_calibrated: a,
        };
        let float = |lo, lox, hi, hix, a| Range {
            min_inclusive: lo,
            min_inclusive_int: None,
            min_exclusive: lox,
            max_inclusive: hi,
            max_inclusive_int: None,
            max_exclusive: hix,
            applies_to_calibrated: a,
        };
        match self {
            ValidRanges::Int(r) => vec![int(
                r.min_inclusive,
                r.max_inclusive,
                r.valid_range_applies_to_calibrated,
            )],
            ValidRanges::Float(r) => vec![float(
                r.min_inclusive,
                r.min_exclusive,
                r.max_inclusive,
                r.max_exclusive,
                r.valid_range_applies_to_calibrated,
            )],
            ValidRanges::IntSet(s) => s
                .valid_range
                .iter()
                .map(|r| int(r.min_inclusive, r.max_inclusive, None))
                .collect(),
            ValidRanges::FloatSet(s) => s
                .valid_range
                .iter()
                .map(|r| {
                    float(
                        r.min_inclusive,
                        r.min_exclusive,
                        r.max_inclusive,
                        r.max_exclusive,
                        None,
                    )
                })
                .collect(),
        }
    }

    /// Says whether the ranges are integers, which the Python holds as
    /// ints rather than floats.
    ///
    /// # Returns
    ///
    /// Whether they are.
    pub fn integer(&self) -> bool {
        matches!(self, ValidRanges::Int(_) | ValidRanges::IntSet(_))
    }
}

/// The static alarm ranges of an alarm: plain, or a time's, with units.
#[derive(Debug, Clone, Copy)]
pub enum RangesRef<'a> {
    /// AlarmRangesType.
    Plain(&'a x::AlarmRangesType),
    /// TimeAlarmRangesType.
    Time(&'a x::TimeAlarmRangesType),
    /// ChangeAlarmRangesType.
    Change(&'a x::ChangeAlarmRangesType),
}

impl<'a> RangesRef<'a> {
    /// Gives the ranges' rangeForm.
    ///
    /// # Returns
    ///
    /// It, if given.
    pub fn range_form(&self) -> Option<x::RangeFormType> {
        match self {
            RangesRef::Plain(r) => r.range_form,
            RangesRef::Time(r) => r.range_form,
            RangesRef::Change(r) => r.range_form,
        }
    }

    /// Gives the range for each level, from watch to severe.
    ///
    /// # Returns
    ///
    /// Each level's range, if given, in the order of [`crate::model::LEVELS`]
    /// after `normal`.
    pub fn levels(&self) -> [Option<&'a x::FloatRangeType>; 5] {
        match self {
            RangesRef::Plain(r) => [
                r.watch_range.as_ref(),
                r.warning_range.as_ref(),
                r.distress_range.as_ref(),
                r.critical_range.as_ref(),
                r.severe_range.as_ref(),
            ],
            RangesRef::Time(r) => [
                r.watch_range.as_ref(),
                r.warning_range.as_ref(),
                r.distress_range.as_ref(),
                r.critical_range.as_ref(),
                r.severe_range.as_ref(),
            ],
            RangesRef::Change(r) => [
                r.watch_range.as_ref(),
                r.warning_range.as_ref(),
                r.distress_range.as_ref(),
                r.critical_range.as_ref(),
                r.severe_range.as_ref(),
            ],
        }
    }

    /// Gives a time's timeUnits.
    ///
    /// # Returns
    ///
    /// The units, if they are a time's and given.
    pub fn time_units(&self) -> Option<x::TimeUnitsType> {
        match self {
            RangesRef::Time(r) => r.time_units,
            _ => None,
        }
    }
}

/// What an alarm definition has, for any kind of alarm.
#[derive(Debug, Clone, Copy, Default)]
pub struct AlarmView<'a> {
    /// minViolations.
    pub min_violations: Option<i64>,
    /// minConformance.
    pub min_conformance: Option<i64>,
    /// disabled.
    pub disabled: Option<bool>,
    /// The AlarmConditions, if it has them.
    pub conditions: Option<&'a x::AlarmConditionsType>,
    /// The CustomAlarm, if it has one.
    pub custom: Option<&'a x::CustomAlarmType>,
    /// ChangeAlarmRanges.
    pub change: Option<&'a x::ChangeAlarmRangesType>,
    /// ChangePerSecondAlarmRanges, a time's.
    pub change_per_second: Option<&'a x::TimeAlarmRangesType>,
    /// StaticAlarmRanges.
    pub static_ranges: Option<RangesRef<'a>>,
    /// AlarmMultiRanges.
    pub multi: Option<&'a x::AlarmMultiRangesType>,
    /// defaultAlarmLevel.
    pub default_level: Option<x::ConcernLevelsType>,
    /// EnumerationAlarmList.
    pub enum_list: Option<&'a x::EnumerationAlarmListType>,
    /// StringAlarmList.
    pub string_list: Option<&'a x::StringAlarmListType>,
    /// A context alarm's ContextMatch.
    pub context_match: Option<&'a x::ContextMatchType>,
}

/// Fills in the fields every kind of alarm has.
macro_rules! alarm_base {
    ($a:expr, $choice:ident) => {
        AlarmView {
            min_violations: $a.min_violations,
            min_conformance: $a.min_conformance,
            disabled: $a.disabled,
            conditions: match &$a.choice {
                Some(x::$choice::AlarmConditions(c)) => Some(c),
                _ => None,
            },
            custom: match &$a.choice {
                Some(x::$choice::CustomAlarm(c)) => Some(c),
                _ => None,
            },
            ..AlarmView::default()
        }
    };
}

/// Gives the view of a numeric alarm.
macro_rules! numeric_alarm {
    ($a:expr, $choice:ident) => {
        AlarmView {
            static_ranges: $a
                .static_alarm_ranges
                .as_ref()
                .map(RangesRef::Plain),
            change: $a.change_alarm_ranges.as_ref(),
            multi: $a.alarm_multi_ranges.as_ref(),
            ..alarm_base!($a, $choice)
        }
    };
}

/// Gives the view of a time's alarm.
macro_rules! time_alarm {
    ($a:expr, $choice:ident) => {
        AlarmView {
            static_ranges: $a.static_alarm_ranges.as_ref().map(RangesRef::Time),
            change_per_second: $a.change_per_second_alarm_ranges.as_ref(),
            ..alarm_base!($a, $choice)
        }
    };
}

/// Gives the view of a string's alarm.
macro_rules! string_alarm {
    ($a:expr, $choice:ident) => {
        AlarmView {
            default_level: $a.default_alarm_level,
            string_list: $a.string_alarm_list.as_ref(),
            ..alarm_base!($a, $choice)
        }
    };
}

/// Gives the view of an enumeration's alarm.
macro_rules! enum_alarm {
    ($a:expr, $choice:ident) => {
        AlarmView {
            default_level: $a.default_alarm_level,
            enum_list: $a.enumeration_alarm_list.as_ref(),
            ..alarm_base!($a, $choice)
        }
    };
}

/// Gives the views of the alarms of a context alarm list, each with its
/// ContextMatch.
macro_rules! contexts {
    ($l:expr, $view:ident, $choice:ident) => {
        $l.as_ref().map(|l| {
            l.context_alarm
                .iter()
                .map(|a| AlarmView {
                    context_match: Some(&a.context_match),
                    ..$view!(a, $choice)
                })
                .collect::<Vec<_>>()
        })
    };
}

impl XType {
    /// Gives the type's element name.
    ///
    /// # Returns
    ///
    /// Such as `IntegerParameterType`.
    pub fn kind(&self) -> &'static str {
        use x::ArgumentTypeSetChoice as A;
        use x::ParameterTypeSetChoice as P;
        match self {
            XType::P(p) => match p {
                P::StringParameterType(_) => "StringParameterType",
                P::EnumeratedParameterType(_) => "EnumeratedParameterType",
                P::IntegerParameterType(_) => "IntegerParameterType",
                P::BinaryParameterType(_) => "BinaryParameterType",
                P::FloatParameterType(_) => "FloatParameterType",
                P::BooleanParameterType(_) => "BooleanParameterType",
                P::RelativeTimeParameterType(_) => "RelativeTimeParameterType",
                P::AbsoluteTimeParameterType(_) => "AbsoluteTimeParameterType",
                P::ArrayParameterType(_) => "ArrayParameterType",
                P::AggregateParameterType(_) => "AggregateParameterType",
            },
            XType::A(a) => match a {
                A::StringArgumentType(_) => "StringArgumentType",
                A::EnumeratedArgumentType(_) => "EnumeratedArgumentType",
                A::IntegerArgumentType(_) => "IntegerArgumentType",
                A::BinaryArgumentType(_) => "BinaryArgumentType",
                A::FloatArgumentType(_) => "FloatArgumentType",
                A::BooleanArgumentType(_) => "BooleanArgumentType",
                A::RelativeTimeArgumentType(_) => "RelativeTimeArgumentType",
                A::AbsoluteTimeArgumentType(_) => "AbsoluteTimeArgumentType",
                A::ArrayArgumentType(_) => "ArrayArgumentType",
                A::AggregateArgumentType(_) => "AggregateArgumentType",
            },
        }
    }

    /// Gives the type's name.
    ///
    /// # Returns
    ///
    /// Its name attribute.
    pub fn name(&self) -> &str {
        each!(self, t => &t.name)
    }

    /// Gives the type's baseType.
    ///
    /// # Returns
    ///
    /// It, if the type has one, empty or not.
    pub fn base_type(&self) -> Option<&str> {
        use x::ArgumentTypeSetChoice as A;
        use x::ParameterTypeSetChoice as P;
        match self {
            XType::P(P::ArrayParameterType(_))
            | XType::P(P::AggregateParameterType(_))
            | XType::A(A::ArrayArgumentType(_))
            | XType::A(A::AggregateArgumentType(_)) => None,
            XType::P(p) => match p {
                P::StringParameterType(t) => t.base_type.as_deref(),
                P::EnumeratedParameterType(t) => t.base_type.as_deref(),
                P::IntegerParameterType(t) => t.base_type.as_deref(),
                P::BinaryParameterType(t) => t.base_type.as_deref(),
                P::FloatParameterType(t) => t.base_type.as_deref(),
                P::BooleanParameterType(t) => t.base_type.as_deref(),
                P::RelativeTimeParameterType(t) => t.base_type.as_deref(),
                P::AbsoluteTimeParameterType(t) => t.base_type.as_deref(),
                _ => None,
            },
            XType::A(a) => match a {
                A::StringArgumentType(t) => t.base_type.as_deref(),
                A::EnumeratedArgumentType(t) => t.base_type.as_deref(),
                A::IntegerArgumentType(t) => t.base_type.as_deref(),
                A::BinaryArgumentType(t) => t.base_type.as_deref(),
                A::FloatArgumentType(t) => t.base_type.as_deref(),
                A::BooleanArgumentType(t) => t.base_type.as_deref(),
                A::RelativeTimeArgumentType(t) => t.base_type.as_deref(),
                A::AbsoluteTimeArgumentType(t) => t.base_type.as_deref(),
                _ => None,
            },
        }
    }

    /// Fills in what the type leaves out from its base type, which must
    /// be the same kind.
    ///
    /// # Arguments
    ///
    /// * `base` - The base type, with what it inherits filled in.
    ///
    /// # Returns
    ///
    /// The type, with no baseType.
    ///
    /// # Panics
    ///
    /// If the base type is of another kind, which the caller checks first.
    pub fn inherit(&self, base: &XType) -> XType {
        use x::ArgumentTypeSetChoice as A;
        use x::ParameterTypeSetChoice as P;
        /// Inherits within one kind of a choice.
        macro_rules! kinds {
            ($me:expr, $b:expr, $e:ident, [$($k:ident),*]) => {
                match ($me, $b) {
                    $(($e::$k(t), $e::$k(b)) => $e::$k(t.inherit(b)),)*
                    _ => panic!("a base type of another kind"),
                }
            };
        }
        match (self, base) {
            (XType::P(t), XType::P(b)) => XType::P(kinds!(
                t,
                b,
                P,
                [
                    StringParameterType,
                    EnumeratedParameterType,
                    IntegerParameterType,
                    BinaryParameterType,
                    FloatParameterType,
                    BooleanParameterType,
                    RelativeTimeParameterType,
                    AbsoluteTimeParameterType
                ]
            )),
            (XType::A(t), XType::A(b)) => XType::A(kinds!(
                t,
                b,
                A,
                [
                    StringArgumentType,
                    EnumeratedArgumentType,
                    IntegerArgumentType,
                    BinaryArgumentType,
                    FloatArgumentType,
                    BooleanArgumentType,
                    RelativeTimeArgumentType,
                    AbsoluteTimeArgumentType
                ]
            )),
            _ => panic!("a base type of another kind"),
        }
    }

    /// Gives the type's AncillaryDataSet.
    ///
    /// # Returns
    ///
    /// It, if the type has one.
    pub fn ancillary_data_set(&self) -> Option<&x::AncillaryDataSetType> {
        use x::ArgumentTypeSetChoice as A;
        use x::ParameterTypeSetChoice as P;
        match self {
            XType::P(P::ArrayParameterType(_))
            | XType::P(P::AggregateParameterType(_))
            | XType::A(A::ArrayArgumentType(_))
            | XType::A(A::AggregateArgumentType(_)) => None,
            XType::P(p) => match p {
                P::StringParameterType(t) => t.ancillary_data_set.as_ref(),
                P::EnumeratedParameterType(t) => t.ancillary_data_set.as_ref(),
                P::IntegerParameterType(t) => t.ancillary_data_set.as_ref(),
                P::BinaryParameterType(t) => t.ancillary_data_set.as_ref(),
                P::FloatParameterType(t) => t.ancillary_data_set.as_ref(),
                P::BooleanParameterType(t) => t.ancillary_data_set.as_ref(),
                P::RelativeTimeParameterType(t) => {
                    t.ancillary_data_set.as_ref()
                }
                P::AbsoluteTimeParameterType(t) => {
                    t.ancillary_data_set.as_ref()
                }
                _ => None,
            },
            XType::A(a) => match a {
                A::StringArgumentType(t) => t.ancillary_data_set.as_ref(),
                A::EnumeratedArgumentType(t) => t.ancillary_data_set.as_ref(),
                A::IntegerArgumentType(t) => t.ancillary_data_set.as_ref(),
                A::BinaryArgumentType(t) => t.ancillary_data_set.as_ref(),
                A::FloatArgumentType(t) => t.ancillary_data_set.as_ref(),
                A::BooleanArgumentType(t) => t.ancillary_data_set.as_ref(),
                A::RelativeTimeArgumentType(t) => t.ancillary_data_set.as_ref(),
                A::AbsoluteTimeArgumentType(t) => t.ancillary_data_set.as_ref(),
                _ => None,
            },
        }
    }

    /// Gives the type's data encoding, for the kinds that choose one.
    ///
    /// # Returns
    ///
    /// The encoding, or `None` if it has none (or is a time, array or
    /// aggregate).
    pub fn data_encoding(&self) -> Option<EncRef<'_>> {
        use x::ArgumentTypeSetChoice as A;
        use x::ParameterTypeSetChoice as P;
        match self {
            XType::P(p) => match p {
                P::StringParameterType(t) => {
                    enc_of!(t.choice, StringParameterChoice)
                }
                P::EnumeratedParameterType(t) => {
                    enc_of!(t.choice, EnumeratedParameterChoice)
                }
                P::IntegerParameterType(t) => {
                    enc_of!(t.choice, IntegerParameterChoice)
                }
                P::BinaryParameterType(t) => {
                    enc_of!(t.choice, BinaryParameterChoice)
                }
                P::FloatParameterType(t) => {
                    enc_of!(t.choice, FloatParameterChoice)
                }
                P::BooleanParameterType(t) => {
                    enc_of!(t.choice, BooleanParameterChoice)
                }
                _ => None,
            },
            XType::A(a) => match a {
                A::StringArgumentType(t) => {
                    enc_of!(arg t.choice, StringArgumentChoice)
                }
                A::EnumeratedArgumentType(t) => {
                    enc_of!(arg t.choice, EnumeratedArgumentChoice)
                }
                A::IntegerArgumentType(t) => {
                    enc_of!(arg t.choice, IntegerArgumentChoice)
                }
                A::BinaryArgumentType(t) => {
                    enc_of!(arg t.choice, BinaryArgumentChoice)
                }
                A::FloatArgumentType(t) => {
                    enc_of!(arg t.choice, FloatArgumentChoice)
                }
                A::BooleanArgumentType(t) => {
                    enc_of!(arg t.choice, BooleanArgumentChoice)
                }
                _ => None,
            },
        }
    }

    /// Gives an integer type's sizeInBits and signed.
    ///
    /// # Returns
    ///
    /// Each, if given; `None`s for other kinds.
    pub fn int_size(&self) -> (Option<i64>, Option<bool>) {
        match self {
            XType::P(x::ParameterTypeSetChoice::IntegerParameterType(t)) => {
                (t.size_in_bits, t.signed)
            }
            XType::A(x::ArgumentTypeSetChoice::IntegerArgumentType(t)) => {
                (t.size_in_bits, t.signed)
            }
            _ => (None, None),
        }
    }

    /// Gives the type's ValidRange or ValidRangeSet.
    ///
    /// # Returns
    ///
    /// It, for an integer or float type that has one.
    pub fn valid_ranges(&self) -> Option<ValidRanges<'_>> {
        use x::ArgumentTypeSetChoice as A;
        use x::ParameterTypeSetChoice as P;
        match self {
            XType::P(P::IntegerParameterType(t)) => {
                t.valid_range.as_ref().map(ValidRanges::Int)
            }
            XType::P(P::FloatParameterType(t)) => {
                t.valid_range.as_ref().map(ValidRanges::Float)
            }
            XType::A(A::IntegerArgumentType(t)) => {
                t.valid_range_set.as_ref().map(ValidRanges::IntSet)
            }
            XType::A(A::FloatArgumentType(t)) => {
                t.valid_range_set.as_ref().map(ValidRanges::FloatSet)
            }
            _ => None,
        }
    }

    /// Gives an enumerated type's EnumerationList.
    ///
    /// # Returns
    ///
    /// It, for an enumerated type.
    pub fn enumeration_list(&self) -> Option<&x::EnumerationListType> {
        match self {
            XType::P(x::ParameterTypeSetChoice::EnumeratedParameterType(t)) => {
                Some(&t.enumeration_list)
            }
            XType::A(x::ArgumentTypeSetChoice::EnumeratedArgumentType(t)) => {
                Some(&t.enumeration_list)
            }
            _ => None,
        }
    }

    /// Gives a boolean type's oneStringValue and zeroStringValue.
    ///
    /// # Returns
    ///
    /// Each, if given.
    pub fn bool_strings(&self) -> (Option<&str>, Option<&str>) {
        match self {
            XType::P(x::ParameterTypeSetChoice::BooleanParameterType(t)) => (
                t.one_string_value.as_deref(),
                t.zero_string_value.as_deref(),
            ),
            XType::A(x::ArgumentTypeSetChoice::BooleanArgumentType(t)) => (
                t.one_string_value.as_deref(),
                t.zero_string_value.as_deref(),
            ),
            _ => (None, None),
        }
    }

    /// Gives a time type's Encoding and ReferenceTime.
    ///
    /// # Returns
    ///
    /// Each, if given; `None`s for other kinds.
    pub fn time(
        &self,
    ) -> (Option<&x::EncodingType>, Option<&x::ReferenceTimeType>) {
        use x::ArgumentTypeSetChoice as A;
        use x::ParameterTypeSetChoice as P;
        match self {
            XType::P(P::RelativeTimeParameterType(t)) => {
                (t.encoding.as_ref(), t.reference_time.as_ref())
            }
            XType::P(P::AbsoluteTimeParameterType(t)) => {
                (t.encoding.as_ref(), t.reference_time.as_ref())
            }
            XType::A(A::RelativeTimeArgumentType(t)) => {
                (t.encoding.as_ref(), t.reference_time.as_ref())
            }
            XType::A(A::AbsoluteTimeArgumentType(t)) => {
                (t.encoding.as_ref(), t.reference_time.as_ref())
            }
            _ => (None, None),
        }
    }

    /// Gives an aggregate type's MemberList.
    ///
    /// # Returns
    ///
    /// It, for an aggregate type.
    pub fn member_list(&self) -> Option<&x::MemberListType> {
        match self {
            XType::P(x::ParameterTypeSetChoice::AggregateParameterType(t)) => {
                Some(&t.member_list)
            }
            XType::A(x::ArgumentTypeSetChoice::AggregateArgumentType(t)) => {
                Some(&t.member_list)
            }
            _ => None,
        }
    }

    /// Gives the type's DefaultAlarm and ContextAlarmList, as views.
    ///
    /// # Returns
    ///
    /// The default alarm, and the context alarms; `None` for each the
    /// type doesn't give, and for argument types, which have no alarms.
    pub fn alarms(
        &self,
    ) -> (Option<AlarmView<'_>>, Option<Vec<AlarmView<'_>>>) {
        use x::ParameterTypeSetChoice as P;
        let XType::P(p) = self else {
            return (None, None);
        };
        match p {
            P::IntegerParameterType(t) => (
                t.default_alarm
                    .as_ref()
                    .map(|a| numeric_alarm!(a, NumericAlarmChoice)),
                contexts!(
                    t.context_alarm_list,
                    numeric_alarm,
                    NumericContextAlarmChoice
                ),
            ),
            P::FloatParameterType(t) => (
                t.default_alarm
                    .as_ref()
                    .map(|a| numeric_alarm!(a, NumericAlarmChoice)),
                contexts!(
                    t.context_alarm_list,
                    numeric_alarm,
                    NumericContextAlarmChoice
                ),
            ),
            P::StringParameterType(t) => (
                t.default_alarm
                    .as_ref()
                    .map(|a| string_alarm!(a, StringAlarmChoice)),
                contexts!(
                    t.context_alarm_list,
                    string_alarm,
                    StringContextAlarmChoice
                ),
            ),
            P::EnumeratedParameterType(t) => (
                t.default_alarm
                    .as_ref()
                    .map(|a| enum_alarm!(a, EnumerationAlarmChoice)),
                contexts!(
                    t.context_alarm_list,
                    enum_alarm,
                    EnumerationContextAlarmChoice
                ),
            ),
            P::BinaryParameterType(t) => (
                t.default_alarm
                    .as_ref()
                    .map(|a| alarm_base!(a, BinaryAlarmChoice)),
                contexts!(
                    t.context_alarm_list,
                    alarm_base,
                    BinaryContextAlarmChoice
                ),
            ),
            P::BooleanParameterType(t) => (
                t.default_alarm
                    .as_ref()
                    .map(|a| alarm_base!(a, BooleanAlarmChoice)),
                contexts!(
                    t.context_alarm_list,
                    alarm_base,
                    BooleanContextAlarmChoice
                ),
            ),
            P::RelativeTimeParameterType(t) => (
                t.default_alarm
                    .as_ref()
                    .map(|a| time_alarm!(a, TimeAlarmChoice)),
                contexts!(
                    t.context_alarm_list,
                    time_alarm,
                    TimeContextAlarmChoice
                ),
            ),
            _ => (None, None),
        }
    }
}

/// An array type: its element type's reference and its dimensions.
#[derive(Debug, Clone, Copy)]
pub enum ArrayRef<'a> {
    /// An array parameter type.
    P(&'a x::ArrayParameterType),
    /// An array argument type.
    A(&'a x::ArrayArgumentType),
}

impl XType {
    /// Gives an array type.
    ///
    /// # Returns
    ///
    /// It, for an array type.
    pub fn array(&self) -> Option<ArrayRef<'_>> {
        match self {
            XType::P(x::ParameterTypeSetChoice::ArrayParameterType(t)) => {
                Some(ArrayRef::P(t))
            }
            XType::A(x::ArgumentTypeSetChoice::ArrayArgumentType(t)) => {
                Some(ArrayRef::A(t))
            }
            _ => None,
        }
    }
}
