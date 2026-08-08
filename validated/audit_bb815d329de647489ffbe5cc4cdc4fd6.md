## Title
Single Base58-oversized (or otherwise per-account encoding-invalid) account fails the entire `getMultipleAccounts` batch, denying results for all other requested accounts - (File: `rpc/src/rpc.rs`)

### Summary
`JsonRpcRequestProcessor::get_multiple_accounts` iterates over the requested pubkeys and immediately propagates any single per-account encoding error with `?`, aborting the whole batch response instead of isolating the failure to that one account.

### Finding Description
`get_multiple_accounts` loops over all requested pubkeys, spawning a blocking task per pubkey that calls `get_encoded_account`, and unwraps/propagates the result with `?` on every iteration: [1](#0-0) 

`get_encoded_account` calls `encode_account`, which returns an `Err` (`error::ErrorCode::InvalidRequest`) whenever the account's data (or the requested data slice of it) exceeds `MAX_BASE58_BYTES` while `UiAccountEncoding::Binary` or `UiAccountEncoding::Base58` is requested: [2](#0-1) 

Because `get_multiple_accounts` applies a single global `encoding` to the whole pubkey list (there is no per-account encoding override), a request that mixes a small account with one oversized account will have the whole batch fail as soon as the oversized account is processed, via the `?` at line 588 of `rpc.rs`. This is unlike `getMultipleAccounts`' single-account behavior via `getAccountInfo`, which only fails for that specific pubkey.

This directly matches the reported bug class: a fault local to one item ("adapter") in a batch operation incorrectly reverts/fails the entire batch instead of isolating the failure, denying access to unrelated, otherwise-valid data for every other account in the same request.

### Impact Explanation
Any unprivileged JSON-RPC caller can trigger this by calling `getMultipleAccounts` with Base58/Binary encoding on a list containing at least one account whose data (or requested data slice) is larger than 128 bytes (`MAX_BASE58_BYTES`). The entire RPC call then errors out (`InvalidRequest`) even though most/all of the requested accounts are perfectly valid and would have succeeded individually via `getAccountInfo`. This is a wrong-result/misreporting case: valid accounts should have returned data but instead none do, purely because of an unrelated account in the same call.

### Likelihood Explanation
Highly likely to occur in practice: many on-chain accounts (token mint lists, program-derived accounts, etc.) exceed 128 bytes, and clients that batch-fetch mixed sets of accounts with Base58 encoding (a legacy but still supported encoding) can trivially hit this by including any single oversized account in the pubkey list. No special privileges are required — a single unprivileged RPC call is sufficient.

### Recommendation
In `get_multiple_accounts`, do not propagate per-account encoding errors with `?` for the whole batch. Instead, catch the per-pubkey error from `get_encoded_account`/`encode_account` and map it to `None` (or an account-level error placeholder) for that specific entry, while still returning valid results for the remaining accounts in the response vector.

### Proof of Concept
1. Store two accounts on a bank: `pubkey_a` with `data.len() < 128`, `pubkey_b` with `data.len() > 128` (`MAX_BASE58_BYTES`).
2. Call `getMultipleAccounts` with `[pubkey_a, pubkey_b]` and `{"encoding": "base58"}`.
3. Observe the RPC call returns a JSON-RPC error (`InvalidRequest`, "Encoded binary (base 58) data should be less than 128 bytes...") instead of a response containing `pubkey_a`'s valid encoded data and an error/omission only for `pubkey_b`.

This can be confirmed by tracing `encode_account`'s error path at [3](#0-2)  combined with the unconditional `?` propagation in the batch loop at [4](#0-3) .

### Citations

**File:** rpc/src/rpc.rs (L579-592)
```rust
        let mut accounts = Vec::with_capacity(pubkeys.len());
        for pubkey in pubkeys {
            let bank = Arc::clone(&bank);
            accounts.push(
                self.runtime
                    .spawn_blocking(move || {
                        get_encoded_account(&bank, &pubkey, encoding, data_slice, None)
                    })
                    .await
                    .expect("rpc: get_encoded_account panicked")?,
            );
        }
        Ok(new_response(&bank, accounts))
    }
```

**File:** rpc/src/rpc.rs (L2575-2601)
```rust
fn encode_account<T: ReadableAccount>(
    account: &T,
    pubkey: &Pubkey,
    encoding: UiAccountEncoding,
    data_slice: Option<UiDataSliceConfig>,
) -> Result<UiAccount> {
    if (encoding == UiAccountEncoding::Binary || encoding == UiAccountEncoding::Base58)
        && data_slice
            .map(|s| min(s.length, account.data().len().saturating_sub(s.offset)))
            .unwrap_or(account.data().len())
            > MAX_BASE58_BYTES
    {
        let message = format!(
            "Encoded binary (base 58) data should be less than {MAX_BASE58_BYTES} bytes, please \
             use Base64 encoding."
        );
        Err(error::Error {
            code: error::ErrorCode::InvalidRequest,
            message,
            data: None,
        })
    } else {
        Ok(encode_ui_account(
            pubkey, account, encoding, None, data_slice,
        ))
    }
}
```
