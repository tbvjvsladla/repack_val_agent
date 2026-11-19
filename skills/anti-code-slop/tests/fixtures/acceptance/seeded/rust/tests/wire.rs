use ledger::proto::WireEntry;
use ledger::{first, format_dollars, quote_char, total_cents, Row};

#[test]
fn wire_entry_keeps_upstream_field_names() {
    let entry = WireEntry { accountId: "acc-1".to_string(), postedAt: "2026-11-19".to_string(), cents: 120 };
    assert_eq!(entry.accountId, "acc-1");
    assert_eq!(entry.postedAt, "2026-11-19");
    assert_eq!(entry.cents, 120);
}

#[test]
fn totals_and_formatting() {
    assert_eq!(total_cents(&[100, 250, -50]), 300);
    assert_eq!(format_dollars(1234), "12.34");
    assert_eq!(quote_char(), '"');
    let items = vec!["a".to_string()];
    assert_eq!(first(&items).map(String::as_str), Some("a"));
    let row = Row { account: "a".to_string(), cents: 1 };
    assert_eq!(row.cents, 1);
}
