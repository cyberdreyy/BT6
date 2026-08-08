### Title
Silent `u32`→`u8` truncation of `account_index`/`index` when decoding BigTable-stored `TokenBalance`/`InnerInstructions` protobuf data - (File: `storage-proto/src/convert.rs`)

### Summary
`storage-proto/src/convert.rs` contains `From` conversions that decode protobuf `generated::TokenBalance` and `generated::InnerInstructions` messages (fields typed `uint32`) into the internal `TransactionTokenBalance` / `InnerInstructions` types, which store the equivalent fields as `u8`. Both conversions perform an unchecked `as u8` downcast with no bounds validation, mirroring the exact bug class from the referenced report (unsafe downcast without a `_safeUint88`-style guard).

### Finding Description
The relevant code: [1](#0-0) [2](#0-1) 

Both `From<generated::InnerInstructions> for InnerInstructions` and `From<generated::TokenBalance> for TransactionTokenBalance` take a `u32` field (`index`, `account_index`) coming from a deserialized protobuf message and cast it directly `as u8` with no `TryFrom`/range check, silently wrapping any value ≥ 256 (e.g. `300 as u8 == 44`).

This proto data is produced/consumed on the path used by `solana-storage-bigtable` to store and retrieve confirmed blocks/transaction metadata that RPC nodes serve for methods like `getTransaction`, `getBlock`, and `getConfirmedBlock` — i.e., unprivileged, request-driven JSON-RPC handlers that decode externally-stored (BigTable) protobuf blobs into `TransactionStatusMeta`/`TransactionTokenBalance` structures returned to RPC callers.

### Impact Explanation
If the stored `account_index` (or inner-instruction `index`) protobuf value exceeds `u8::MAX`, the downcast silently wraps to an incorrect small index instead of erroring. This causes the RPC response to attribute a token balance change to the wrong account index, or an inner instruction to the wrong top-level instruction index — a case of "wrong ... account data returned" via a query, matching the accepted impact categories (decoder misreporting). It does not cause a crash but does cause silently incorrect data being served to RPC clients, which could mislead consumers of `getTransaction`/`getBlock` (e.g., wrong token balance deltas being reported for the wrong account).

### Likelihood Explanation
Likelihood is limited: current legacy/v0 message formats bound account counts and instruction counts well below 256 in practice (transaction packet size limits effectively cap account/instruction counts far under `u8::MAX`), so under normal transaction construction this code path is unlikely to be triggered today. However, the protobuf schema explicitly uses `uint32` for these fields (see `storage-proto/proto/confirmed_block.proto`), suggesting the format was deliberately designed to allow larger values than `u8` in the future/for compatibility, and the conversion silently discards that headroom instead of failing loudly — a latent correctness bug that could surface if account/instruction limits are ever raised, or if a malformed/corrupted BigTable record is read back.

### Recommendation
Replace the unchecked `as u8` casts with a checked conversion (e.g., `u8::try_from(value.index)` / `u8::try_from(value.account_index)`), returning a decode error (or saturating with an explicit `TryFrom`-based error) instead of silently wrapping, analogous to the `_safeUint88` mitigation recommended in the referenced report.

### Proof of Concept
1. Construct a `generated::TokenBalance { account_index: 300, .. }` protobuf message (or `generated::InnerInstructions { index: 300, .. }`) — e.g. as could occur from a corrupted/adversarially-crafted BigTable-stored blob, or a future protocol version with more than 255 accounts/instructions.
2. Convert it with `TransactionTokenBalance::from(generated_token_balance)` / `InnerInstructions::from(generated_inner_instructions)`.
3. Observe `account_index`/`index` becomes `300 as u8 == 44`, silently pointing to the wrong account/instruction, which is then what an RPC client sees returned from `getTransaction`/`getBlock`.

### Citations

**File:** storage-proto/src/convert.rs (L662-669)
```rust
impl From<generated::InnerInstructions> for InnerInstructions {
    fn from(value: generated::InnerInstructions) -> Self {
        Self {
            index: value.index as u8,
            instructions: value.instructions.into_iter().map(|i| i.into()).collect(),
        }
    }
}
```

**File:** storage-proto/src/convert.rs (L688-714)
```rust
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
                decimals: ui_token_amount.decimals as u8,
                amount: ui_token_amount.amount.clone(),
                ui_amount_string: if !ui_token_amount.ui_amount_string.is_empty() {
                    ui_token_amount.ui_amount_string
                } else {
                    real_number_string_trimmed(
                        u64::from_str(&ui_token_amount.amount).unwrap_or_default(),
                        ui_token_amount.decimals as u8,
                    )
                },
            },
            owner: value.owner,
            program_id: value.program_id,
        }
    }
```
