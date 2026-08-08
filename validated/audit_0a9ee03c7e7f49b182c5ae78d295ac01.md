### Title
`getMultipleAccounts` RPC batch fails entirely when a single account in the batch cannot be encoded — ([File: rpc/src/rpc.rs])

### Summary
The external report describes a "single bad component blocks the whole batch" bug class: one paused strategy in a multi-strategy vault makes withdrawals from *all* strategies impossible, because the code has no way to skip/exclude the problematic member of the batch. The Agave `getMultipleAccounts` JSON-RPC handler exhibits the same structural flaw: a single account in the requested list that cannot be encoded under the requested encoding aborts the entire batch response, denying the caller data for every other (perfectly fine) account in the same request.

### Finding Description
`JsonRpcRequestProcessor::get_multiple_accounts` iterates over the caller-supplied list of pubkeys and calls `get_encoded_account` for each one, propagating any error with `?`: [1](#0-0) 

`get_encoded_account` in turn calls `encode_account`, which — for `Binary`/`Base58` encodings — returns a hard `Err` (not a graceful fallback) whenever the account's data (after slicing) exceeds `MAX_BASE58_BYTES` (128 bytes): [2](#0-1) 

Because the loop in `get_multiple_accounts` uses `?` on every per-pubkey result, the very first oversized/undecodable account in the list causes the whole `Vec<Option<UiAccount>>` computation to be abandoned and the entire RPC response to be an error — even though the surrounding accounts in the same request are perfectly encodable and would have succeeded on their own (as shown by `get_account_info`, which is only ever called with a single pubkey and thus never exhibits this "poison the batch" behavior): [3](#0-2) 

Note that `account-decoder`'s own primitive (`encode_bs58`) already has a graceful per-account fallback (embedding an `"error: data too large for bs58 encoding"` string instead of failing): [4](#0-3) 

but the RPC-layer `encode_account` deliberately overrides this graceful behavior with a hard error (confirmed intentional by the existing test `test_encode_account_throws_when_data_too_large_to_base58_encode`): [5](#0-4) 

This is reasonable for a single-account `getAccountInfo` call (the caller controls the one pubkey and can retry with Base64), but it is not reasonable for `getMultipleAccounts`, where the caller has no a-priori way of knowing which of dozens/hundreds of unrelated pubkeys happens to hold >128 bytes of on-chain data, and one bad entry silently voids the response for every other requested account — mirroring exactly the "one paused/unavailable member blocks operations on all members" pattern from the referenced report.

### Impact Explanation
Any unprivileged RPC client can construct a legitimate `getMultipleAccounts` request (e.g., mixing several well-known large program-owned accounts, which are extremely common on mainnet, with `encoding: "base58"` or the legacy default `"binary"`) and receive a full request failure instead of the accounts that would have encoded fine. This denies useful, valid data to the caller from a single unprivileged JSON-RPC call and forces all callers of this method to defensively avoid Base58/Binary encoding, which is an easy trap since it is the default encoding.

### Likelihood Explanation
High likelihood of accidental triggering: Base58/Binary is the default encoding for `getMultipleAccounts` when no encoding is specified, and any account with data length over 128 bytes (very common for token, stake, program, and most application accounts) in a batch will trigger the failure. No special privileges are required — this is reachable by any client issuing a single, low-cost `getMultipleAccounts` call.

### Recommendation
In `get_multiple_accounts`, do not propagate a per-pubkey encoding error with `?` for the whole batch. Instead, catch the per-account encoding error and substitute either `None`/`null` or an account-level error indicator (similar to `encode_bs58`'s embedded-error-string behavior) for just that entry, allowing the rest of the batch to be returned successfully.

### Proof of Concept
1. Store an account whose data is, e.g., 200 bytes.
2. Call `getMultipleAccounts` with a list containing that pubkey plus several unrelated, small (<128-byte) accounts, using `encoding: "base58"` (or omitting encoding, since `"binary"` behaves the same way through `encode_account`).
3. Observe the JSON-RPC response is a single top-level error ("Encoded binary (base 58) data should be less than 128 bytes...") for the entire call — the caller receives *no* data at all for the other valid pubkeys in the same request, exactly as demonstrated by the existing unit test `test_encode_account_throws_when_data_too_large_to_base58_encode` for the single-account case combined with the batch loop's `?`-propagation in `get_multiple_accounts` at [6](#0-5) .

### Citations

**File:** rpc/src/rpc.rs (L534-560)
```rust
    pub async fn get_account_info(
        &self,
        pubkey: Pubkey,
        config: Option<RpcAccountInfoConfig>,
    ) -> Result<RpcResponse<Option<UiAccount>>> {
        let RpcAccountInfoConfig {
            encoding,
            data_slice,
            commitment,
            min_context_slot,
        } = config.unwrap_or_default();
        let bank = self.get_bank_with_config(RpcContextConfig {
            commitment,
            min_context_slot,
        })?;
        let encoding = encoding.unwrap_or(UiAccountEncoding::Binary);

        let response = self
            .runtime
            .spawn_blocking({
                let bank = Arc::clone(&bank);
                move || get_encoded_account(&bank, &pubkey, encoding, data_slice, None)
            })
            .await
            .expect("rpc: get_encoded_account panicked")?;
        Ok(new_response(&bank, response))
    }
```

**File:** rpc/src/rpc.rs (L562-592)
```rust
    pub async fn get_multiple_accounts(
        &self,
        pubkeys: Vec<Pubkey>,
        config: Option<RpcAccountInfoConfig>,
    ) -> Result<RpcResponse<Vec<Option<UiAccount>>>> {
        let RpcAccountInfoConfig {
            encoding,
            data_slice,
            commitment,
            min_context_slot,
        } = config.unwrap_or_default();
        let bank = self.get_bank_with_config(RpcContextConfig {
            commitment,
            min_context_slot,
        })?;
        let encoding = encoding.unwrap_or(UiAccountEncoding::Base64);

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

**File:** rpc/src/rpc.rs (L5728-5741)
```rust
    #[test]
    #[should_panic(expected = "should be less than 128 bytes")] // If ever `MAX_BASE58_BYTES` changes, the expected error message will need to be updated.
    fn test_encode_account_throws_when_data_too_large_to_base58_encode() {
        let data = vec![42; MAX_BASE58_BYTES + 1];
        let pubkey = Pubkey::new_unique();
        let account = AccountSharedData::create_from_existing_shared_data(
            42,
            Arc::new(data),
            pubkey,
            false,
            0,
        );
        let _ = encode_account(&account, &pubkey, UiAccountEncoding::Base58, None).unwrap();
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
