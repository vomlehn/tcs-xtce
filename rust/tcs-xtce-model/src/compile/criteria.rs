//! Criteria: the comparisons, boolean expressions and custom algorithms of
//! restriction criteria, include conditions and lookups, bound to the
//! values a packet decodes before them, as the Python compiler's `match`,
//! `boolean`, `comparison` and `value_ref` make them.

use std::rc::Rc;

use num_bigint::BigInt;
use tcs_xtce::generated as x;

use super::types::member_path;
use super::{child, qname, Compiler};
use crate::dump::Shared;
use crate::model::{
    ref_text, unsupported, AllOf, AnyOf, Criterion, Custom, Encoding, Index,
    Input, Key, Lit, Local, PathStep, Result, Step, Term, VType,
};
use crate::pyfmt::repr;

/// The most inputs a custom algorithm may have.
pub const MAX_HOOK_INPUTS: usize = 32;

/// Gives the key of an argument from its name, in a command.
pub type ArgKey<'f> = Option<&'f dyn Fn(&str) -> Result<String>>;

/// A reference to a value of the packet, as any of XTCE's reference
/// elements gives it.
#[derive(Debug, Clone)]
pub struct XRef {
    /// Whether it names an argument rather than a parameter.
    pub arg: bool,
    /// The reference as written.
    pub name: String,
    /// Which instance: 0 the last decoded, -1 the one before.
    pub instance: i64,
    /// useCalibratedValue, if given.
    pub calibrated: Option<bool>,
}

impl XRef {
    /// Makes the reference of a ParameterInstanceRef.
    ///
    /// # Arguments
    ///
    /// * `r` - The XTCE reference.
    ///
    /// # Returns
    ///
    /// The reference.
    pub fn param(r: &x::ParameterInstanceRefType) -> Self {
        XRef {
            arg: false,
            name: r.parameter_ref.clone(),
            instance: r.instance.unwrap_or(0),
            calibrated: r.use_calibrated_value,
        }
    }

    /// Makes the reference of a Comparison.
    ///
    /// # Arguments
    ///
    /// * `c` - The XTCE comparison.
    ///
    /// # Returns
    ///
    /// The reference.
    pub fn comparison(c: &x::ComparisonType) -> Self {
        XRef {
            arg: false,
            name: c.parameter_ref.clone(),
            instance: c.instance.unwrap_or(0),
            calibrated: c.use_calibrated_value,
        }
    }

    /// Makes the reference of an ArgumentInstanceRef.
    ///
    /// # Arguments
    ///
    /// * `r` - The XTCE reference.
    ///
    /// # Returns
    ///
    /// The reference.
    pub fn argument(r: &x::ArgumentInstanceRefType) -> Self {
        XRef {
            arg: true,
            name: r.argument_ref.clone(),
            instance: 0,
            calibrated: r.use_calibrated_value,
        }
    }

    /// Makes the reference of an ArgumentComparison.
    ///
    /// # Arguments
    ///
    /// * `c` - The XTCE comparison.
    ///
    /// # Returns
    ///
    /// The reference: to a parameter or an argument.
    pub fn arg_comparison(c: &x::ArgumentComparisonType) -> Self {
        match &c.choice {
            x::ArgumentComparisonChoice::ParameterInstanceRef(p) => {
                XRef::param(p)
            }
            x::ArgumentComparisonChoice::ArgumentInstanceRef(a) => {
                XRef::argument(a)
            }
        }
    }
}

/// An input of a custom algorithm, of either kind of InputSet.
#[derive(Debug, Clone, Copy)]
pub enum InputRef<'b> {
    /// A Constant.
    Const(&'b x::ConstantType),
    /// An InputParameterInstanceRef.
    Param(&'b x::InputParameterInstanceRefType),
    /// An InputArgumentInstanceRef.
    Arg(&'b x::ArgumentInstanceRefType),
}

/// Lists the inputs of a custom algorithm of telemetry.
///
/// # Arguments
///
/// * `alg` - The algorithm.
///
/// # Returns
///
/// Its inputs, in order.
pub fn inputs_of(alg: &x::InputAlgorithmType) -> Vec<InputRef<'_>> {
    set_inputs(alg.input_set.as_ref())
}

/// Lists the inputs of a telemetry InputSet.
///
/// # Arguments
///
/// * `set` - The InputSet, if any.
///
/// # Returns
///
/// Its inputs, in order; none without one.
pub fn set_inputs(set: Option<&x::InputSetType>) -> Vec<InputRef<'_>> {
    set.into_iter()
        .flat_map(|s| s.items.iter())
        .map(|ch| match ch {
            x::InputSetChoice::Constant(c) => InputRef::Const(c),
            x::InputSetChoice::InputParameterInstanceRef(p) => {
                InputRef::Param(p)
            }
        })
        .collect()
}

/// Lists the inputs of a custom algorithm of a command.
///
/// # Arguments
///
/// * `alg` - The algorithm.
///
/// # Returns
///
/// Its inputs, in order.
pub fn arg_inputs_of(alg: &x::ArgumentInputAlgorithmType) -> Vec<InputRef<'_>> {
    use x::ArgumentInputSetChoice as I;
    alg.input_set
        .iter()
        .flat_map(|s| s.items.iter())
        .map(|ch| match ch {
            I::Constant(c) => InputRef::Const(c),
            I::InputParameterInstanceRef(p) => InputRef::Param(p),
            I::InputArgumentInstanceRef(a) => InputRef::Arg(a),
        })
        .collect()
}

/// Match criteria of any of the forms XTCE gives them.
#[derive(Debug, Clone, Copy)]
pub enum CritRef<'b> {
    /// A Comparison.
    Comparison(&'b x::ComparisonType),
    /// A ComparisonList.
    List(&'b x::ComparisonListType),
    /// A BooleanExpression.
    Boolean(&'b x::BooleanExpressionType),
    /// A CustomAlgorithm.
    Custom(&'b x::InputAlgorithmType),
    /// An ArgumentComparison.
    AComparison(&'b x::ArgumentComparisonType),
    /// An ArgumentComparisonList.
    AList(&'b x::ArgumentComparisonListType),
    /// An ArgumentBooleanExpression.
    ABoolean(&'b x::ArgumentBooleanExpressionType),
    /// A command's CustomAlgorithm.
    ACustom(&'b x::ArgumentInputAlgorithmType),
}

impl<'b> CritRef<'b> {
    /// Views a MatchCriteria.
    ///
    /// # Arguments
    ///
    /// * `m` - The criteria.
    ///
    /// # Returns
    ///
    /// The view.
    pub fn match_criteria(m: &'b x::MatchCriteriaType) -> Self {
        match &m.choice {
            x::MatchCriteriaChoice::Comparison(c) => CritRef::Comparison(c),
            x::MatchCriteriaChoice::ComparisonList(c) => CritRef::List(c),
            x::MatchCriteriaChoice::BooleanExpression(c) => CritRef::Boolean(c),
            x::MatchCriteriaChoice::CustomAlgorithm(c) => CritRef::Custom(c),
        }
    }

    /// Views a RestrictionCriteria.
    ///
    /// # Arguments
    ///
    /// * `m` - The criteria.
    ///
    /// # Returns
    ///
    /// The view.
    pub fn restriction(m: &'b x::RestrictionCriteriaType) -> Self {
        use x::RestrictionCriteriaChoice as R;
        match &m.choice {
            R::Comparison(c) => CritRef::Comparison(c),
            R::ComparisonList(c) => CritRef::List(c),
            R::BooleanExpression(c) => CritRef::Boolean(c),
            R::CustomAlgorithm(c) => CritRef::Custom(c),
        }
    }

    /// Views a ContextMatch.
    ///
    /// # Arguments
    ///
    /// * `m` - The match.
    ///
    /// # Returns
    ///
    /// The view.
    pub fn context(m: &'b x::ContextMatchType) -> Self {
        use x::ContextMatchChoice as C;
        match &m.choice {
            C::Comparison(c) => CritRef::Comparison(c),
            C::ComparisonList(c) => CritRef::List(c),
            C::BooleanExpression(c) => CritRef::Boolean(c),
            C::CustomAlgorithm(c) => CritRef::Custom(c),
        }
    }

    /// Views a DiscreteLookup's criteria.
    ///
    /// # Arguments
    ///
    /// * `m` - The lookup.
    ///
    /// # Returns
    ///
    /// The view.
    pub fn lookup(m: &'b x::DiscreteLookupType) -> Self {
        use x::DiscreteLookupChoice as D;
        match &m.choice {
            D::Comparison(c) => CritRef::Comparison(c),
            D::ComparisonList(c) => CritRef::List(c),
            D::BooleanExpression(c) => CritRef::Boolean(c),
            D::CustomAlgorithm(c) => CritRef::Custom(c),
        }
    }

    /// Views a command's MatchCriteria.
    ///
    /// # Arguments
    ///
    /// * `m` - The criteria.
    ///
    /// # Returns
    ///
    /// The view.
    pub fn arg_match(m: &'b x::ArgumentMatchCriteriaType) -> Self {
        use x::ArgumentMatchCriteriaChoice as A;
        match &m.choice {
            A::Comparison(c) => CritRef::AComparison(c),
            A::ComparisonList(c) => CritRef::AList(c),
            A::BooleanExpression(c) => CritRef::ABoolean(c),
            A::CustomAlgorithm(c) => CritRef::ACustom(c),
        }
    }

    /// Views an argument's DiscreteLookup's criteria.
    ///
    /// # Arguments
    ///
    /// * `m` - The lookup.
    ///
    /// # Returns
    ///
    /// The view.
    pub fn arg_lookup(m: &'b x::ArgumentDiscreteLookupType) -> Self {
        use x::ArgumentDiscreteLookupChoice as A;
        match &m.choice {
            A::Comparison(c) => CritRef::AComparison(c),
            A::ComparisonList(c) => CritRef::AList(c),
            A::BooleanExpression(c) => CritRef::ABoolean(c),
            A::CustomAlgorithm(c) => CritRef::ACustom(c),
        }
    }
}

/// One alternative of a BooleanExpression, or of its ANDedConditions or
/// ORedConditions.
#[derive(Debug, Clone, Copy)]
pub enum BoolRef<'b> {
    /// A Condition.
    Condition(&'b x::ComparisonCheckType),
    /// ANDedConditions.
    And(&'b x::ANDedConditionsType),
    /// ORedConditions.
    Or(&'b x::ORedConditionsType),
    /// A command's Condition.
    ACondition(&'b x::ArgumentComparisonCheckType),
    /// A command's ANDedConditions.
    AAnd(&'b x::ArgumentANDedConditionsType),
    /// A command's ORedConditions.
    AOr(&'b x::ArgumentORedConditionsType),
}

/// Follows member and index steps through a type.
///
/// # Arguments
///
/// * `vt`      - The type the steps start in.
/// * `members` - The steps: member names, and indexes, one per array dimension.
/// * `what`    - What is being resolved, for error messages.
///
/// # Returns
///
/// The path (field identifiers, and indexes) of the steps inside `vt`,
/// and the type they reach.
///
/// # Errors
///
/// Unsupported, if a step names no member or element of the type it is
/// applied to, or an index is out of range.
pub fn walk_members(
    vt: &Rc<VType>,
    members: &[Step],
    what: &str,
) -> Result<(Vec<PathStep>, Rc<VType>)> {
    let mut path = Vec::new();
    let mut vt = vt.clone();
    let mut steps: std::collections::VecDeque<Step> =
        members.iter().cloned().collect();
    while let Some(step) = steps.pop_front() {
        match step {
            Step::Index(first) => {
                let Encoding::Array(e) = &vt.enc else {
                    return unsupported(format!(
                        "{what}: {} type {} is not an array",
                        vt.kind, vt.key
                    ));
                };
                // One index per dimension, flattened with the last fastest.
                let mut index = vec![first];
                while (index.len() as i64) < e.dims {
                    match steps.front() {
                        Some(Step::Index(i)) => {
                            index.push(*i);
                            steps.pop_front();
                        }
                        _ => break,
                    }
                }
                if (index.len() as i64) < e.dims {
                    return unsupported(format!(
                        "{what}: {} indexes for a {}-dimensional array",
                        index.len(),
                        e.dims
                    ));
                }
                let varies = matches!(e.count, crate::model::Count::Dyn(_));
                let (mut flat, mut stride, mut need, mut per) = (0, 0, 0, 1);
                for ((i, start), size) in
                    index.iter().zip(&e.starts).zip(&e.sizes)
                {
                    let (i, start) = (*i, *start);
                    match size {
                        None => {
                            if i < start {
                                return unsupported(format!(
                                    "{what}: index {i} is before {start}"
                                ));
                            }
                            need = i - start + 1;
                            stride = flat;
                            flat = i - start;
                        }
                        Some(size) => {
                            if !(start <= i && i < start + size) {
                                return unsupported(format!(
                                    "{what}: index {i} is outside {start}..{}",
                                    start + size - 1
                                ));
                            }
                            flat = flat * size + i - start;
                            stride *= size;
                            per *= size;
                        }
                    }
                }
                let elem = e.elem.clone();
                if varies && stride != 0 {
                    path.push(PathStep::Index(Index {
                        i: flat,
                        count: None,
                        stride,
                        per,
                        need,
                    }));
                } else {
                    path.push(PathStep::Index(Index {
                        i: flat,
                        count: match e.count {
                            crate::model::Count::Int(n) if !varies => Some(n),
                            _ => None,
                        },
                        stride: 0,
                        per: 1,
                        need: 0,
                    }));
                }
                vt = elem;
            }
            Step::Name(name) => {
                let Encoding::Aggregate(e) = &vt.enc else {
                    return unsupported(format!(
                        "{what}: {} type {} has no member {}",
                        vt.kind,
                        vt.key,
                        repr(&name)
                    ));
                };
                let Some(m) = e.members.iter().find(|m| m.name == name) else {
                    return unsupported(format!(
                        "{what}: aggregate type {} has no member {}",
                        vt.key,
                        repr(&name)
                    ));
                };
                path.push(PathStep::Field(m.ident.clone()));
                let next = m.vtype.clone();
                vt = next;
            }
        }
    }
    Ok((path, vt))
}

/// Finds the value of a scope that a reference's instance names: 0 is
/// the one decoded last, -1 the one before it, and so on.
///
/// # Arguments
///
/// * `scope`    - The values decoded so far.
/// * `key`      - The qualified name of the parameter or argument.
/// * `instance` - The instance, 0 or less.
/// * `where_`   - What refers to it, for error messages.
///
/// # Returns
///
/// The Local, or `None` if the scope holds no value of `key`.
///
/// # Errors
///
/// Unsupported, if the instance is positive, or before the first.
pub fn instance_of(
    scope: &[Shared<Local>],
    key: &str,
    instance: i64,
    where_: &str,
) -> Result<Option<Shared<Local>>> {
    if instance > 0 {
        return unsupported(format!(
            "{where_}: instance {instance} of {key}, a value yet to come"
        ));
    }
    let found: Vec<&Shared<Local>> =
        scope.iter().rev().filter(|l| l.get().key == key).collect();
    if found.is_empty() {
        return Ok(None);
    }
    if -instance >= found.len() as i64 {
        let n = found.len();
        return unsupported(format!(
            "{where_}: instance {instance} of {key}, which the packet holds \
             {n} time{} before it",
            if n > 1 { "s" } else { "" }
        ));
    }
    Ok(Some(found[(-instance) as usize].clone()))
}

/// Reads bytes from hexadecimal as Python's `bytes.fromhex` does: pairs of
/// digits, with space allowed between them.
///
/// # Arguments
///
/// * `s` - The text.
///
/// # Returns
///
/// The bytes, or `None` if the text isn't hexadecimal.
fn from_hex(s: &str) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    let cs: Vec<char> = s.chars().collect();
    let mut i = 0;
    while i < cs.len() {
        if cs[i].is_whitespace() {
            i += 1;
            continue;
        }
        let hi = cs.get(i)?.to_digit(16)?;
        let lo = cs.get(i + 1)?.to_digit(16)?;
        out.push((hi * 16 + lo) as u8);
        i += 2;
    }
    Some(out)
}

/// Converts a value written in the document to one of a type.
///
/// # Arguments
///
/// * `vt`     - The type.
/// * `text`   - The value as written.
/// * `raw`    - Whether it is a raw value, which is an integer.
/// * `where_` - Where it is written, for error messages.
///
/// # Returns
///
/// An int, float, bool, string or bytes: an enumeration's label as its
/// value, a boolean's strings as true and false, and binary data from
/// hexadecimal.
///
/// # Errors
///
/// Unsupported, if the value doesn't fit the type, or the type is an
/// array or aggregate.
pub fn literal(vt: &VType, text: &str, raw: bool, where_: &str) -> Result<Lit> {
    let s = text.trim();
    if vt.kind == "array" || vt.kind == "aggregate" {
        return unsupported(format!(
            "{where_}: value {} for {} type {}",
            repr(text),
            vt.kind,
            vt.key
        ));
    }
    let int = |s: &str| -> Option<BigInt> {
        let l = s.to_ascii_lowercase();
        if l.starts_with("0x") || l.starts_with("0b") || l.starts_with("0o") {
            crate::pyparse::big_int(s, 0)
        } else {
            crate::pyparse::big_int(s, 10)
        }
    };
    let got: Option<Lit> = if raw || vt.kind == "int" {
        int(s).map(|v| {
            if vt.kind == "bool" {
                Lit::Bool(v != BigInt::from(0))
            } else {
                Lit::Int(v)
            }
        })
    } else {
        match vt.kind.as_str() {
            "float" => crate::pyparse::float(s).map(Lit::Float),
            "enum" => match vt.labels.iter().find(|l| l.label == s) {
                Some(l) => Some(Lit::Int(l.value.into())),
                None => crate::pyparse::big_int(s, 10).map(Lit::Int),
            },
            "bool" => {
                if s == vt.one_string || s == "true" || s == "1" {
                    Some(Lit::Bool(true))
                } else if s == vt.zero_string || s == "false" || s == "0" {
                    Some(Lit::Bool(false))
                } else {
                    None
                }
            }
            "string" => Some(Lit::Str(text.to_string())),
            _ => from_hex(s).map(Lit::Bytes),
        }
    };
    got.map_or_else(
        || {
            unsupported(format!(
                "{where_}: value {} does not fit {} type {}",
                repr(text),
                vt.kind,
                vt.key
            ))
        },
        Ok,
    )
}

/// The value a reference names: the Local that holds it, the path to it
/// there, its type and its name for messages.
pub type Found = (Shared<Local>, Vec<PathStep>, Rc<VType>, String);

impl Compiler<'_> {
    /// Compiles a RestrictionCriteria, IncludeCondition or DiscreteLookup.
    ///
    /// # Arguments
    ///
    /// * `mc`      - The XTCE match criteria.
    /// * `path`    - The key of the SpaceSystem it is written in.
    /// * `scope`   - The values decoded before it, which it may use.
    /// * `where_`  - What it is in, for error messages.
    /// * `arg_key` - In a command, gives the key of an argument from its name;
    ///               `None` elsewhere.
    ///
    /// # Returns
    ///
    /// Its terms, all of which must hold.
    ///
    /// # Errors
    ///
    /// Unsupported, if it is of an unsupported form, or uses a value that
    /// isn't decoded before it.
    pub fn match_(
        &mut self,
        mc: CritRef<'_>,
        path: &Key,
        scope: &[Shared<Local>],
        where_: &str,
        arg_key: ArgKey<'_>,
    ) -> Result<Vec<Term>> {
        use x::ArgumentBooleanExpressionChoice as AB;
        use x::BooleanExpressionChoice as B;
        /// A comparison: what it compares, its operator and the value.
        type Cmp<'c> = (XRef, Option<x::ComparisonOperatorsType>, &'c str);
        let (name, inputs) = match mc {
            CritRef::Custom(alg) => (&alg.name, inputs_of(alg)),
            CritRef::ACustom(alg) => (&alg.name, arg_inputs_of(alg)),
            _ => (&String::new(), Vec::new()),
        };
        let comps: Vec<Cmp<'_>> = match mc {
            CritRef::Boolean(b) => {
                let c = match &b.choice {
                    B::Condition(c) => BoolRef::Condition(c),
                    B::ANDedConditions(c) => BoolRef::And(c),
                    B::ORedConditions(c) => BoolRef::Or(c),
                };
                return Ok(
                    vec![self.boolean(c, path, scope, where_, arg_key)?],
                );
            }
            CritRef::ABoolean(b) => {
                let c = match &b.choice {
                    AB::Condition(c) => BoolRef::ACondition(c),
                    AB::ANDedConditions(c) => BoolRef::AAnd(c),
                    AB::ORedConditions(c) => BoolRef::AOr(c),
                };
                return Ok(
                    vec![self.boolean(c, path, scope, where_, arg_key)?],
                );
            }
            CritRef::Custom(_) | CritRef::ACustom(_) => {
                let hook =
                    self.hook("criterion", &qname(&child(path, name)), 0);
                let inputs =
                    self.hook_inputs(&inputs, path, scope, where_, arg_key)?;
                return Ok(vec![Term::Custom(Shared::new(Custom {
                    hook,
                    inputs,
                    text: format!("{name}(...)"),
                }))]);
            }
            CritRef::Comparison(c) => {
                vec![(XRef::comparison(c), c.comparison_operator, &c.value)]
            }
            CritRef::List(l) => l
                .comparison
                .iter()
                .map(|c| {
                    (XRef::comparison(c), c.comparison_operator, &c.value[..])
                })
                .collect(),
            CritRef::AComparison(c) => {
                vec![(XRef::arg_comparison(c), c.comparison_operator, &c.value)]
            }
            CritRef::AList(l) => l
                .comparison
                .iter()
                .map(|c| {
                    (
                        XRef::arg_comparison(c),
                        c.comparison_operator,
                        &c.value[..],
                    )
                })
                .collect(),
        };
        let mut out = Vec::new();
        for (r, op, value) in comps {
            let op = op.map(|o| o.as_str()).unwrap_or("==");
            out.push(self.comparison(
                &r,
                op,
                Some(value),
                path,
                scope,
                where_,
                arg_key,
                None,
            )?);
        }
        Ok(out)
    }

    /// Compiles one alternative of a BooleanExpression.
    ///
    /// # Arguments
    ///
    /// * `c`       - The alternative.
    /// * `path`    - The key of the SpaceSystem it is written in.
    /// * `scope`   - The values decoded before it, which it may use.
    /// * `where_`  - What it is in, for error messages.
    /// * `arg_key` - In a command, gives the key of an argument from its name;
    ///               `None` elsewhere.
    ///
    /// # Returns
    ///
    /// The term: a Criterion, AllOf or AnyOf.
    ///
    /// # Errors
    ///
    /// Unsupported, if it is of an unsupported form.
    pub fn boolean(
        &mut self,
        c: BoolRef<'_>,
        path: &Key,
        scope: &[Shared<Local>],
        where_: &str,
        arg_key: ArgKey<'_>,
    ) -> Result<Term> {
        let (and, alts): (bool, Vec<BoolRef<'_>>) = match c {
            BoolRef::Condition(cc) => {
                let lhs = XRef::param(&cc.parameter_instance_ref);
                let op = cc.comparison_operator.as_str();
                return match &cc.choice {
                    x::ComparisonCheckChoice::Value(v) => self.comparison(
                        &lhs,
                        op,
                        Some(v),
                        path,
                        scope,
                        where_,
                        arg_key,
                        None,
                    ),
                    x::ComparisonCheckChoice::ParameterInstanceRef(r) => self
                        .comparison(
                            &lhs,
                            op,
                            None,
                            path,
                            scope,
                            where_,
                            arg_key,
                            Some(&XRef::param(r)),
                        ),
                };
            }
            BoolRef::And(a) => (
                true,
                a.items
                    .iter()
                    .map(|i| match i {
                        x::ANDedConditionsChoice::Condition(c) => {
                            BoolRef::Condition(c)
                        }
                        x::ANDedConditionsChoice::ORedConditions(o) => {
                            BoolRef::Or(o)
                        }
                    })
                    .collect(),
            ),
            BoolRef::Or(o) => (
                false,
                o.items
                    .iter()
                    .map(|i| match i {
                        x::ORedConditionsChoice::Condition(c) => {
                            BoolRef::Condition(c)
                        }
                        x::ORedConditionsChoice::ANDedConditions(a) => {
                            BoolRef::And(a)
                        }
                    })
                    .collect(),
            ),
            BoolRef::ACondition(cc) => {
                use x::ArgumentComparisonCheckChoice1 as L;
                use x::ArgumentComparisonCheckChoice2 as R;
                let lhs = match &cc.choice1 {
                    L::ParameterInstanceRef(p) => XRef::param(p),
                    L::ArgumentInstanceRef(a) => XRef::argument(a),
                };
                let op = cc.comparison_operator.as_str();
                let (text, other) = match &cc.choice2 {
                    R::Value(v) => (Some(&v[..]), None),
                    R::ParameterInstanceRef(p) => (None, Some(XRef::param(p))),
                    R::ArgumentInstanceRef(a) => {
                        (None, Some(XRef::argument(a)))
                    }
                };
                return self.comparison(
                    &lhs,
                    op,
                    text,
                    path,
                    scope,
                    where_,
                    arg_key,
                    other.as_ref(),
                );
            }
            BoolRef::AAnd(a) => (
                true,
                a.items
                    .iter()
                    .map(|i| match i {
                        x::ArgumentANDedConditionsChoice::Condition(c) => {
                            BoolRef::ACondition(c)
                        }
                        x::ArgumentANDedConditionsChoice::ORedConditions(o) => {
                            BoolRef::AOr(o)
                        }
                    })
                    .collect(),
            ),
            BoolRef::AOr(o) => (
                false,
                o.items
                    .iter()
                    .map(|i| match i {
                        x::ArgumentORedConditionsChoice::Condition(c) => {
                            BoolRef::ACondition(c)
                        }
                        x::ArgumentORedConditionsChoice::ANDedConditions(a) => {
                            BoolRef::AAnd(a)
                        }
                    })
                    .collect(),
            ),
        };
        let mut terms = Vec::new();
        for a in alts {
            terms.push(self.boolean(a, path, scope, where_, arg_key)?);
        }
        let joiner = if and { " and " } else { " or " };
        let texts: Vec<String> = terms.iter().map(|t| t.text()).collect();
        let text = format!("({})", texts.join(joiner));
        Ok(if and {
            Term::AllOf(Shared::new(AllOf { terms, text }))
        } else {
            Term::AnyOf(Shared::new(AnyOf { terms, text }))
        })
    }

    /// Finds the value that a reference names.
    ///
    /// # Arguments
    ///
    /// * `r`       - The reference.
    /// * `path`    - The key of the SpaceSystem it is written in.
    /// * `scope`   - The values decoded before it.
    /// * `where_`  - What it is in, for error messages.
    /// * `arg_key` - In a command, gives the key of an argument from its name;
    ///               `None` elsewhere.
    ///
    /// # Returns
    ///
    /// The value's Local, the path to it there, its type and its name.
    ///
    /// # Errors
    ///
    /// Unsupported, if it isn't decoded before, or is an array or
    /// aggregate.
    pub fn value_ref(
        &self,
        r: &XRef,
        path: &Key,
        scope: &[Shared<Local>],
        where_: &str,
        arg_key: ArgKey<'_>,
    ) -> Result<Found> {
        let (pkey, members) = if r.arg {
            let Some(ak) = arg_key else {
                return unsupported(format!(
                    "{where_}: comparison of argument {} outside a command",
                    repr(&r.name)
                ));
            };
            let (base, members) = member_path(&r.name)?;
            (ak(&base)?, members)
        } else {
            let (k, m) = self.param_ref(&r.name, path)?;
            (qname(&k), m)
        };
        let Some(local) = instance_of(scope, &pkey, r.instance, where_)? else {
            return unsupported(format!(
                "{where_}: criterion on {pkey}, which is not decoded before it"
            ));
        };
        let lvt = local.get().vt();
        let (member, vt) = walk_members(
            &lvt,
            &members,
            &format!("{where_}: criterion on {pkey}"),
        )?;
        let pkey = ref_text(&pkey, &members);
        if vt.kind == "array" || vt.kind == "aggregate" {
            return unsupported(format!(
                "{where_}: criterion on {pkey}, which is an {}",
                vt.kind
            ));
        }
        Ok((local, member, vt, pkey))
    }

    /// Compiles a comparison: `ref <op> text`, or with `other`, `ref <op>
    /// other`.
    ///
    /// # Arguments
    ///
    /// * `r`       - The reference to the value compared.
    /// * `op`      - The comparison operator, such as `==`.
    /// * `text`    - The value compared with, as written, or `None`.
    /// * `path`    - The key of the SpaceSystem it is written in.
    /// * `scope`   - The values decoded before it.
    /// * `where_`  - What it is in, for error messages.
    /// * `arg_key` - In a command, gives the key of an argument from its name;
    ///               `None` elsewhere.
    /// * `other`   - A second value to compare with, or `None`.
    ///
    /// # Returns
    ///
    /// The Criterion.
    ///
    /// # Errors
    ///
    /// Unsupported, if the operator doesn't fit the values, a value
    /// compared with another has an IncludeCondition, or the comparison
    /// is raw where it can't be.
    #[allow(clippy::too_many_arguments)]
    pub fn comparison(
        &self,
        r: &XRef,
        op: &str,
        text: Option<&str>,
        path: &Key,
        scope: &[Shared<Local>],
        where_: &str,
        arg_key: ArgKey<'_>,
        other: Option<&XRef>,
    ) -> Result<Term> {
        let (local, member, vt, pkey) =
            self.value_ref(r, path, scope, where_, arg_key)?;
        let raw = r.calibrated == Some(false);
        if op != "=="
            && op != "!="
            && matches!(vt.kind.as_str(), "bool" | "string" | "binary")
        {
            return unsupported(format!(
                "{where_}: {op} on {} parameter {pkey}",
                vt.kind
            ));
        }
        let target = if member.is_empty() {
            None
        } else {
            Some(vt.clone())
        };
        if let Some(o) = other {
            if local.get().optional {
                return unsupported(format!(
                    "{where_}: comparison of {pkey}, which has an \
                     IncludeCondition, with another value"
                ));
            }
            let (olocal, opath, ovt, okey) =
                self.value_ref(o, path, scope, where_, arg_key)?;
            if olocal.get().optional {
                return unsupported(format!(
                    "{where_}: comparison with {okey}, which has an \
                     IncludeCondition"
                ));
            }
            if raw || o.calibrated == Some(false) {
                return unsupported(format!(
                    "{where_}: raw comparison of {pkey} and {okey}"
                ));
            }
            if vt.kind != ovt.kind
                || !matches!(
                    vt.kind.as_str(),
                    "int" | "enum" | "float" | "bool" | "string"
                )
            {
                return unsupported(format!(
                    "{where_}: comparison of {} {pkey} and {} {okey}",
                    vt.kind, ovt.kind
                ));
            }
            return Ok(Term::Criterion(Shared::new(Criterion {
                local,
                op: op.into(),
                value: None,
                text: format!("{pkey} {op} {okey}"),
                path: member,
                target: Some(vt),
                other: Some(olocal),
                other_path: opath,
                other_target: Some(ovt),
            })));
        }
        if raw
            && (!matches!(vt.kind.as_str(), "int" | "enum" | "bool")
                || vt.calibrator.is_some()
                || matches!(vt.enc, Encoding::String(_)))
        {
            return unsupported(format!(
                "{where_}: raw comparison on {} parameter {pkey}",
                vt.kind
            ));
        }
        let text = text.expect("a value to compare with");
        let value = literal(&vt, text, raw, where_)?;
        Ok(Term::Criterion(Shared::new(Criterion {
            local,
            op: op.into(),
            value: Some(value),
            text: format!("{pkey} {op} {text}"),
            path: member,
            target,
            other: None,
            other_path: Vec::new(),
            other_target: None,
        })))
    }

    /// Compiles the inputs of a custom algorithm.
    ///
    /// # Arguments
    ///
    /// * `inputs`  - The XTCE algorithm's inputs, from its InputSet.
    /// * `path`    - The key of the SpaceSystem it is defined in.
    /// * `scope`   - The values of the packet decoded before it is used.
    /// * `where_`  - What uses it, for error messages.
    /// * `arg_key` - In a command, gives the key of an argument from its name;
    ///               `None` elsewhere.
    ///
    /// # Returns
    ///
    /// The inputs, in order.
    ///
    /// # Errors
    ///
    /// Unsupported, if there are too many, a constant isn't a number, or a
    /// value is optional, not a number, or raw but calibrated.
    pub fn hook_inputs(
        &self,
        inputs: &[InputRef<'_>],
        path: &Key,
        scope: &[Shared<Local>],
        where_: &str,
        arg_key: ArgKey<'_>,
    ) -> Result<Vec<Input>> {
        if inputs.len() > MAX_HOOK_INPUTS {
            return unsupported(format!(
                "{where_}: a custom algorithm with more than \
                 {MAX_HOOK_INPUTS} inputs"
            ));
        }
        let mut out = Vec::new();
        for ch in inputs {
            let r = match ch {
                InputRef::Const(v) => {
                    match crate::pyparse::float(&v.value) {
                        Some(f) => out.push(Input::Const(Lit::Float(f))),
                        None => {
                            return unsupported(format!(
                                "{where_}: constant {} {} is not a number",
                                v.constant_name,
                                repr(&v.value)
                            ))
                        }
                    }
                    continue;
                }
                InputRef::Param(v) => XRef {
                    arg: false,
                    name: v.parameter_ref.clone(),
                    instance: v.instance.unwrap_or(0),
                    calibrated: v.use_calibrated_value,
                },
                InputRef::Arg(a) => XRef::argument(a),
            };
            let (local, member, vt, name) =
                self.value_ref(&r, path, scope, where_, arg_key)?;
            if local.get().optional {
                return unsupported(format!(
                    "{where_}: custom algorithm input {name} has an \
                     IncludeCondition"
                ));
            }
            if !matches!(vt.kind.as_str(), "int" | "float" | "enum" | "bool") {
                return unsupported(format!(
                    "{where_}: custom algorithm input {name} is a {} value",
                    vt.kind
                ));
            }
            if r.calibrated == Some(false) && vt.calibrator.is_some() {
                return unsupported(format!(
                    "{where_}: the raw value of calibrated {name}"
                ));
            }
            out.push(Input::Value(local, member, vt));
        }
        Ok(out)
    }
}
