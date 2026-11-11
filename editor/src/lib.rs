//! The XTCE editor's workings, apart from its windows: a document held so
//! that it is written back as it was read ([`tree`]), each XTCE version's
//! schema ([`schema`]) and the checking of a document against it.

pub mod cli;
pub mod codec;
pub mod content;
pub mod convert;
pub mod edit;
pub mod fetch;
pub mod gui;
pub mod make;
pub mod refs;
pub mod save;
pub mod schema;
pub mod session;
pub mod tools;
pub mod tree;
pub mod validate;
