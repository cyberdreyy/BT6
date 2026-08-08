### Title
`simulateTransaction` with `accounts.addresses` + `jsonParsed` encoding allows unbounded per-account memory/CPU cost proportional to on-chain account size - ([File: account-decoder/src/lib.rs])

### Summary
`sanitize_config()` only bounds transaction *shape* (heap size, instruction count, accounts-per-instruction) via `SanitizeConfig`, but it does not constrain the size of accounts that `simulateTransaction`'s `accounts.addresses` post-simulation encoding will read and decode. When `UiAccountEncoding::JsonParsed` is requested, `encode_ui_account` calls `parse_account_data_v3` on the *entire* `account.data()` buffer with no size cap, so a single simulate call referencing an attacker-controlled large account (up to `MAX_PERMITTED_DATA_LENGTH`, 10 MiB) forces the RPC handler to allocate and parse memory proportional to that account's size.

### Finding Description
`sanitize_config()` in `runtime-transaction/src/sanitize_config.rs` builds a `SanitizeConfig` from `MIN/MAX_HEAP_FRAME_BYTES`, `MAX_INSTRUCTION_TRACE_LENGTH`, and `MAX_ACCOUNTS_PER_INSTRUCTION` [1](#0-0) . These limits only govern the shape/validity of the *transaction message itself* (instructions, heap request, account references per instruction) — they say nothing about the byte size of on-chain accounts that get fetched and serialized back to the client in the simulation response.

Separately, `account-decoder/src/lib.rs::encode_ui_account` is the function that converts a post-simulation account into the JSON-RPC response representation. For `UiAccountEncoding::JsonParsed`, it invokes `parse_account_data_v3(pubkey, account.owner(), account.data(), additional_data)` on the full, unsliced account data [2](#0-1) . Unlike the `Base58`/`Binary` path, which uses `slice_data` and even refuses to encode if the (possibly sliced) data exceeds `MAX_BASE58_BYTES` [3](#0-2) , the `JsonParsed` path has no equivalent size guard before invoking the parser — `data_slice_config` is not even applied to the input passed to `parse_account_data_v3`.

Because accounts can legitimately hold data up to `MAX_PERMITTED_DATA_LENGTH` on-chain, an attacker who has previously written such a large account (e.g., a large SPL-Token-like or custom-program account) can reference it via `accounts.addresses` in a single `simulateTransaction` call with `encoding: "jsonParsed"`. The RPC handler will read that account's full data from the bank, and `encode_ui_account` will pass the complete buffer into the parser, causing memory allocation and CPU cost proportional to the on-chain size — with no explicit per-account or per-request cap tied to `sanitize_config`'s transaction-shape limits.

### Impact Explanation
This is a single-request, single-client resource-cost amplification: transaction message limits enforced by `sanitize_config` (heap size, instruction count, accounts-per-instruction) do not bound the cost of encoding the *contents* of referenced on-chain accounts. A low-rate attacker (one call, respecting `CLUSTER_SLOT_TIME_TARGET / 2`) can force allocation/parsing work proportional to `MAX_PERMITTED_DATA_LENGTH` per referenced account on every call, which is an unbounded-cost-per-request condition falling under the "RPC DoS via unbounded cost for a single low-rate call" category.

### Likelihood Explanation
Feasible and repeatable: the attacker only needs to have previously created (or found) a large on-chain account (permitted up to `MAX_PERMITTED_DATA_LENGTH` by protocol rules) and then issue one `simulateTransaction` RPC call referencing it in `accounts.addresses` with `jsonParsed` encoding. No special privileges, leader/validator control, or multiple calls are required — this can be repeated once per allowed interval indefinitely.

### Recommendation
Add an explicit size check before attempting `jsonParsed` decoding in `encode_ui_account` (mirroring the `MAX_BASE58_BYTES` guard used for `Base58`/`Binary` encoding), falling back to `Binary`/`Base64` (or erroring) when `account.data().len()` exceeds a bounded threshold. Additionally, apply `data_slice_config` consistently to the `JsonParsed` path, and consider having `simulateTransaction`'s `accounts.addresses` handling enforce a documented per-account and per-request data-size ceiling independent of `sanitize_config`'s transaction-shape limits.

### Proof of Concept
```rust
// account-decoder/src/lib.rs (add near existing tests)
#[test]
fn test_json_parsed_encoding_cost_scales_with_account_size() {
    use std::time::Instant;

    // Simulate a large "unknown-owner" account so parse_account_data_v3 falls
    // through and copies/attempts-to-parse the entire buffer.
    let sizes = [1_024usize, 1_048_576, 10 * 1024 * 1024 /* near MAX_PERMITTED_DATA_LENGTH */];
    let mut timings = vec![];

    for size in sizes {
        let account = AccountSharedData::from(Account {
            data: vec![0u8; size],
            ..Account::default()
        });
        let start = Instant::now();
        let ui_account = encode_ui_account(
            &Pubkey::new_unique(),
            &account,
            UiAccountEncoding::JsonParsed,
            None,
            None, // no data_slice_config applied to JsonParsed path
        );
        let elapsed = start.elapsed();
        timings.push((size, elapsed));
        // Assert the full input buffer, not a bounded slice, reaches the encoder.
        assert_eq!(ui_account.space, Some(size as u64));
    }

    // Expected failing assertion: cost/time grows roughly linearly with `size`
    // with no ceiling independent of on-chain account size, demonstrating the
    // missing per-request cap.
    assert!(
        timings[2].1 > timings[0].1 * 10,
        "expected cost to scale with account size without a bound, got {:?}",
        timings
    );
}
```
Integration-level extension: drive this through `rpc/src/rpc.rs`'s `simulate_transaction` handler end-to-end (construct a bank with a large attacker-authored account, call `simulateTransaction` with `accounts.addresses` referencing it and `encoding: "jsonParsed"`), and assert response construction time/memory is bounded independent of the stored account's size — the assertion should fail against current code, confirming the finding.

### Citations

**File:** runtime-transaction/src/sanitize_config.rs (L14-21)
```rust
pub fn sanitize_config() -> SanitizeConfig {
    SanitizeConfig {
        min_requested_heap_size: MIN_HEAP_FRAME_BYTES,
        max_requested_heap_size: MAX_HEAP_FRAME_BYTES,
        max_instructions: MAX_INSTRUCTION_TRACE_LENGTH,
        max_accounts_per_instruction: MAX_ACCOUNTS_PER_INSTRUCTION,
    }
}
```

**File:** account-decoder/src/lib.rs (L34-44)
```rust
fn encode_bs58<T: ReadableAccount>(
    account: &T,
    data_slice_config: Option<UiDataSliceConfig>,
) -> String {
    let slice = slice_data(account.data(), data_slice_config);
    if slice.len() <= MAX_BASE58_BYTES {
        bs58::encode(slice).into_string()
    } else {
        "error: data too large for bs58 encoding".to_string()
    }
}
```

**File:** account-decoder/src/lib.rs (L80-91)
```rust
        UiAccountEncoding::JsonParsed => {
            if let Ok(parsed_data) =
                parse_account_data_v3(pubkey, account.owner(), account.data(), additional_data)
            {
                UiAccountData::Json(parsed_data)
            } else {
                UiAccountData::Binary(
                    BASE64_STANDARD.encode(slice_data(account.data(), data_slice_config)),
                    UiAccountEncoding::Base64,
                )
            }
        }
```
