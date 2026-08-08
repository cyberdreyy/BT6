### Title
`getMultipleAccounts` Fails Entirely for All Valid Accounts If Any Single Account in the Batch Exceeds the Base58 Encoding Limit - (File: `rpc/src/rpc.rs`)

### Summary
The `getMultipleAccounts` JSON-RPC handler loops over a list of pubkeys and encodes each account individually. If the caller requests `binary`/`base58` encoding and any single account in the batch has data larger than `MAX_BASE58_BYTES` (128 bytes) after slicing, `encode_account` returns an `Err`, which is propagated with `?` and aborts the whole request — discarding the successfully-encoded results for every other (valid) account in the same batch. This mirrors the reported Ion Protocol bug class: one "bad" element in a list (there a paused pool, here an oversized account) blocks access to all other good elements processed in the same call.

### Finding Description
`JsonRpcRequestProcessor::get_multiple_accounts` iterates over the requested pubkeys and, for each one, spawns a blocking task calling `get_encoded_account`, then immediately propagates any error with `?`: [1](#0-0) 

`get_encoded_account` calls `encode_account`, which returns an `Err(Error::InvalidRequest)` whenever the (possibly sliced) account data exceeds `MAX_BASE58_BYTES` while the requested encoding is `Binary` or `Base58`: [2](#0-1) 

Because the loop pushes `.expect(...)?` for each pubkey sequentially, the first pubkey in the list whose account triggers this size check causes the function to return early with an error — even though accounts processed earlier in the loop were already successfully encoded, and accounts later in the list may also be perfectly fine. The overall `getMultipleAccounts` response is lost entirely; the caller receives an `InvalidRequest` error instead of the accounts it validly requested.

This directly parallels the reported pattern: iterating a list where one "problem" entry (there: a paused pool; here: an oversized account combined with a `base58`/`binary` encoding request) prevents legitimate processing of the rest of the list, rather than the implementation skipping/reporting the problem entry and returning the rest.

### Impact Explanation
Any unprivileged RPC caller can construct (or simply happen to include) a pubkey with an account whose data is larger than 128 bytes in a `getMultipleAccounts` request using `base58`/`binary` encoding, alongside other legitimate pubkeys they want data for. The entire batch request fails, denying the caller data for accounts that would otherwise have been served successfully. This is a temporary denial-of-service against a client-facing read RPC, not a validator crash, but it degrades data availability for legitimate `getMultipleAccounts` batch queries whenever any single item in the batch is oversized for base58 encoding.

### Likelihood Explanation
Likelihood is low-to-moderate: it requires the caller to request `base58`/`binary` encoding (not the default `base64`) together with at least one account exceeding 128 bytes in the batch. It is easily triggerable in a single RPC call by any user and requires no special privileges, but the trigger condition (explicit base58 encoding request over sizeable accounts) is a corner case rather than the common path.

### Recommendation
Change the per-pubkey handling in `get_multiple_accounts` to not `?`-propagate individual encoding errors out of the whole batch. Instead, catch `encode_account`'s error for a specific pubkey and either return `null`/`None` for just that entry (similar to how a missing account already returns `None`) or embed an error indicator for that specific slot in the response array, while still returning encoded data for the rest of the accounts in the batch — consistent with how `encode_bs58` already degrades gracefully to an inline `"error: data too large for bs58 encoding"` string in some other code paths (see `account-decoder/src/lib.rs`, `encode_bs58`) rather than failing the whole call.

### Proof of Concept
1. Create two accounts on-chain: `A` (small, e.g. 10 bytes of data) and `B` (data length > 128 bytes).
2. Call `getMultipleAccounts` with `pubkeys = [A, B]` and `config.encoding = "base58"`.
3. Observe: instead of returning `[<A's encoded data>, <error-string-for-B-or-null>]`, the JSON-RPC call fails outright with `InvalidRequest: "Encoded binary (base 58) data should be less than 128 bytes, please use Base64 encoding."`, and the client receives no data at all for account `A`, even though `A` alone would have been served successfully via a single-pubkey `getAccountInfo` request or a batch not containing `B`.

### Citations

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

**File:** rpc/src/rpc.rs (L2552-2601)
```rust
fn get_encoded_account(
    bank: &Bank,
    pubkey: &Pubkey,
    encoding: UiAccountEncoding,
    data_slice: Option<UiDataSliceConfig>,
    // only used for simulation results
    overwrite_accounts: Option<&HashMap<Pubkey, AccountSharedData>>,
) -> Result<Option<UiAccount>> {
    match account_resolver::get_account_from_overwrites_or_bank(pubkey, bank, overwrite_accounts) {
        Some(account) => {
            let response = if is_known_spl_token_id(account.owner())
                && encoding == UiAccountEncoding::JsonParsed
            {
                get_parsed_token_account(bank, pubkey, account, overwrite_accounts)
            } else {
                encode_account(&account, pubkey, encoding, data_slice)?
            };
            Ok(Some(response))
        }
        None => Ok(None),
    }
}

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
