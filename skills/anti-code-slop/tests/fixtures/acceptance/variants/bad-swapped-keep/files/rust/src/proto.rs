//! Wire records exchanged with the upstream billing service.

/// A ledger entry in the upstream service's field naming.
pub struct WireEntry {
    #[allow(non_snake_case)]
    pub accountId: String,
    pub postedAt: String,
    pub cents: i64,
}
