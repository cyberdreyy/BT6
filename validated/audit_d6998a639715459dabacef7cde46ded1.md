### Title
Truncating cast from `u32` to `u8` when converting BigTable-stored `InnerInstructions.index` / `TransactionTokenBalance.account_index` causes silent misreporting of instruction/account indices in `getTransaction` responses - (File: storage-proto/src/convert.rs)

### Summary
The BigTable protobuf storage format encodes `InnerInstructions.index` and `TransactionTokenBalance.account_index` as `u32`, but when converting the stored protobuf representation back to the in-memory `solana-transaction-status` types, the code casts these values down to `u8` with an unchecked `as` cast, silently truncating any value ≥ 256 modulo 256, exactly the truncation-in-casting bug class described in the external report.

### Finding Description
`storage-proto/src/convert.rs` implements the round-trip conversions between the internal transaction-status types and the `generated::*` protobuf types used for long-term (BigTable) storage: [1](#0-0) 

converts `generated::InnerInstructions.index` (a `u32`) into the internal `InnerInstructions.index` via `value.index as u8`, and [2](#0-1) 

converts `generated::TokenBalance.account_index` (a `u32`) into `TransactionTokenBalance.account_index` via `value.account_index as u8`. Both casts truncate the value to its low 8 bits instead of validating that it fits in a `u8` (or keeping it as a wider integer type), matching the pattern in the referenced Nouns Builder finding where a `uint256` was silently truncated to `uint8` before being used to gate downstream logic.

`index` in `InnerInstructions` identifies which top-level instruction in the transaction message produced the inner instructions, and `account_index` identifies which account in the transaction's account-keys list a token-balance entry refers to. Both are naturally bounded by the number of instructions/accounts that fit in a serialized transaction (max 1232 bytes), and a transaction can be packed with more than 255 minimal instructions (each needs as little as ~3 bytes: 1-byte program-id index, 1-byte account-count, 1-byte data-length), so the `u32` source value legitimately can exceed `u8::MAX` before being wrapped by this cast.

### Impact Explanation
When this truncated value is later serialized in JSON-RPC responses (e.g., `getTransaction`/`getConfirmedTransaction` served from BigTable long-term storage), a client requesting historical transaction data can be served token-balance entries incorrectly associated with an unrelated account (`account_index` wrapped to a different valid index in the same transaction), or inner-instruction sets attributed to the wrong top-level instruction (`index` wrapped mod 256). This is a decoder misreporting bug: an unprivileged RPC caller retrieves wrong account/instruction attribution for historical transaction data without any error being raised.

### Likelihood Explanation
Triggering the bug requires a transaction with more than 255 top-level instructions (for the `InnerInstructions.index` path) or more than 255 accounts (for `TransactionTokenBalance.account_index`) to have been confirmed and archived to BigTable at some point; such transactions are packable within the 1232-byte transaction size limit given minimal per-instruction/account encoding, though real-world traffic populated with 255+ distinct SPL-token accounts or 255+ minimal instructions in a single transaction is uncommon. The bug is silent (no panic, no error) and only manifests as wrong-index metadata returned through the RPC read path, so likelihood of accidental occurrence is low but the condition is not access-controlled or otherwise gated — any historical transaction meeting the size/count profile is affected for every reader.

### Recommendation
Do not truncate `index`/`account_index` to `u8` during BigTable protobuf round-tripping. Either widen the internal `InnerInstructions.index` and `TransactionTokenBalance.account_index` fields to `u16`/`u32` to match the protobuf wire type, or add an explicit bounds check (`u8::try_from(value).map_err(...)`) and reject/flag conversion failures instead of using `as u8`.

### Proof of Concept
Not independently runnable within this review (no filesystem/terminal access), but the defect is directly demonstrable by constructing a `generated::InnerInstructions { index: 300, .. }` (or `generated::TokenBalance { account_index: 300, .. }`) and calling `InnerInstructions::from(...)` / `TransactionTokenBalance::from(...)` as defined at: [1](#0-0) [2](#0-1) 
which will yield `index == 44` / `account_index == 44` (300 mod 256) instead of erroring, reproducing the same class of truncation-driven misattribution described in the external report.

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

**File:** storage-proto/src/convert.rs (L688-693)
```rust
impl From<generated::TokenBalance> for TransactionTokenBalance {
    fn from(value: generated::TokenBalance) -> Self {
        let ui_token_amount = value.ui_token_amount.unwrap_or_default();
        Self {
            account_index: value.account_index as u8,
            mint: value.mint,
```
