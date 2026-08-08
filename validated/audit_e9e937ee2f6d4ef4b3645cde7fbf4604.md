### Title
`Some(0.0)` UiTokenAmount collapses to `None` on round-trip through `generated::TokenBalance` - ([File: storage-proto/src/convert.rs])

### Summary
The `From<TransactionTokenBalance> for generated::TokenBalance` and `From<generated::TokenBalance> for TransactionTokenBalance` conversions use `unwrap_or_default()` and an epsilon comparison against `f64::default()` (0.0) to encode/decode the optional `ui_amount` field [1](#0-0) . This makes a legitimate `Some(0.0)` value (produced by a zero-amount SPL-Token transfer with nonzero decimals) indistinguishable from an absent (`None`) value after a round trip through the protobuf-backed `generated::TokenBalance` type used for archival transaction storage.

### Finding Description
`From<TransactionTokenBalance> for generated::TokenBalance::from` encodes `ui_token_amount.ui_amount` as `value.ui_token_amount.ui_amount.unwrap_or_default()`, mapping both `None` and `Some(0.0)` to the wire value `0.0` [2](#0-1) . On decode, `From<generated::TokenBalance> for TransactionTokenBalance::from` reconstructs the optional value via `(ui_token_amount.ui_amount - f64::default()).abs() > f64::EPSILON`, which evaluates false for a wire value of `0.0`, producing `None` [3](#0-2) . Thus any legitimate `Some(0.0)` — e.g., from an SPL-Token transfer instruction moving `0` base units of a mint with nonzero decimals, where `ui_amount` is computed as `0.0` — is silently converted into `None` after being written to and read back from archival transaction storage that uses this `generated::TokenBalance` protobuf type. An attacker only needs to submit a single zero-amount SPL-Token transfer (or similarly zero-valued token instruction) on-chain and then read it back via `getTransaction`/`getConfirmedBlock` against storage that uses this bigtable/protobuf transaction-status encoding.

### Impact Explanation
This is a parse/encode fidelity bug: the RPC client cannot distinguish "ui_amount was computed and is exactly zero" from "ui_amount was never computed" for historical/archival transaction reads, which corresponds to the PARSE_FIDELITY / decoder-misreporting category permitted in scope. The impact is limited to metadata correctness of a token-balance amount field returned by RPC for archived transactions; it does not affect consensus, funds, or live state.

### Likelihood Explanation
Trivial to trigger: any unprivileged user can submit a valid SPL-Token instruction transferring `0` base units on a mint with nonzero decimals (which is a legal, zero-cost operation), and later retrieve that transaction via a standard read-only RPC call. No special privileges are required and it is fully reproducible.

### Recommendation
Change the encoding to use an explicit presence flag (e.g., `Option<f64>` via `oneof`, or encode the `None` case with a distinguishable sentinel/wrapper) instead of relying on the numeric value `0.0` as a stand-in for absence, and replace the round-trip comparison in `From<generated::TokenBalance> for TransactionTokenBalance` with a structural presence check rather than an epsilon float comparison.

### Proof of Concept
```rust
// storage-proto/src/convert.rs (test)
#[test]
fn test_ui_amount_zero_round_trip_loses_fidelity() {
    let original = TransactionTokenBalance {
        account_index: 0,
        mint: "MintPubkey11111111111111111111111111111111".to_string(),
        ui_token_amount: UiTokenAmount {
            ui_amount: Some(0.0), // legitimate zero-amount transfer with nonzero decimals
            decimals: 6,
            amount: "0".to_string(),
            ui_amount_string: "0".to_string(),
        },
        owner: "Owner11111111111111111111111111111111111111".to_string(),
        program_id: "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA".to_string(),
    };

    let encoded: generated::TokenBalance = original.clone().into();
    let decoded: TransactionTokenBalance = encoded.into();

    // Fails: decoded.ui_token_amount.ui_amount is None, not Some(0.0)
    assert_eq!(original, decoded);
}
```
Expected: the assertion fails, demonstrating that `Some(0.0)` is silently converted to `None`, confirming the fidelity loss described.

### Citations

**File:** storage-proto/src/convert.rs (L671-699)
```rust
impl From<TransactionTokenBalance> for generated::TokenBalance {
    fn from(value: TransactionTokenBalance) -> Self {
        Self {
            account_index: value.account_index as u32,
            mint: value.mint,
            ui_token_amount: Some(generated::UiTokenAmount {
                ui_amount: value.ui_token_amount.ui_amount.unwrap_or_default(),
                decimals: value.ui_token_amount.decimals as u32,
                amount: value.ui_token_amount.amount,
                ui_amount_string: value.ui_token_amount.ui_amount_string,
            }),
            owner: value.owner,
            program_id: value.program_id,
        }
    }
}

impl From<generated::TokenBalance> for TransactionTokenBalance {
    fn from(value: generated::TokenBalance) -> Self {
        let ui_token_amount = value.ui_token_amount.unwrap_or_default();
        Self {
            account_index: value.account_index as u8,
            mint: value.mint,
            ui_token_amount: UiTokenAmount {
                ui_amount: if (ui_token_amount.ui_amount - f64::default()).abs() > f64::EPSILON {
                    Some(ui_token_amount.ui_amount)
                } else {
                    None
                },
```
