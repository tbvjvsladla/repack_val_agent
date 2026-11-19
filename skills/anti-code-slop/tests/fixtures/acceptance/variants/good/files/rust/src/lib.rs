// SPDX-License-Identifier: MIT
// Copyright (c) 2026 Example Ledger Authors

#![deny(dead_code)]

pub mod proto;

/// Returns the sum of all entry amounts in cents.
///
/// ```
/// let total = ledger::total_cents(&[100, 250]);
/// assert_eq!(total, 350);
/// ```
pub fn total_cents(entries: &[i64]) -> i64 {
    entries.iter().sum()
}

// IMPORTANT: do not change the field order; the nightly export reads columns by position.
pub struct Row {
    pub account: String,
    pub cents: i64,
}

/// Formats cents as a dollar string with two decimals.
pub fn format_dollars(cents: i64) -> String {
    format!("{}.{:02}", cents / 100, cents % 100)
}

pub fn quote_char() -> char {
    '"'
}

pub fn first<'a>(items: &'a [String]) -> Option<&'a String> {
    items.first()
}
