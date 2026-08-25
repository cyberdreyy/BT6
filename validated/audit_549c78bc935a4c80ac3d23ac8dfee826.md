### Title
`getMultipleAccounts` RPC batch call aborts the entire response when a single account fails to encode - (File: `rpc/src/rpc.rs`)

### Summary
`JsonRpcRequestProcessor::get_multiple_accounts` iterates over a caller-supplied list of pubkeys and calls `get_encoded_account` for each one, propagating any per-account error with `?`. This means a single account in the batch that fails to encode (e.g., due to the `Binary`/`Base58` encoding size check) causes the whole multi-account RPC response to fail with an error, discarding all the other accounts that were successfully resolved in the same call, exactly the "one bad item halts the whole batch" atomicity failure class described in the external report (there, one bad `ChainlinkReport` halted an entire on-chain batch and one bad HTTP-validated report halted an entire off-chain tick).

### Finding Description
`get_multiple_accounts` builds a per-pubkey `Vec` by spawning a blocking task per pubkey and immediately unwrapping the result with `?`: [1](#0-0) 

Each blocking task calls `get_encoded_account`, which in turn calls `encode_account` for the found account: [2](#0-1) 

`encode_account` returns an `Err` (JSON-RPC `InvalidRequest`) whenever the encoded size under `Binary`/`Base58` encoding exceeds `MAX_BASE58_BYTES` (128 bytes) after applying the requested data slice: [3](#0-2) 

Because `get_multiple_accounts` calls `.await?` on every account lookup inside the loop (line 588), the first account in the caller-supplied pubkey list that trips this size check aborts the entire request via early return, and none of the other (potentially many, potentially valid) accounts in the batch are returned to the caller — even though each account lookup is logically independent and most succeeded. This mirrors the reported bug class: batching N independent items behind a per-item throw with no isolation, so one bad item (here, one oversized account with `encoding=base58` requested by the same caller) causes total failure of the batch for RPC clients relying on `getMultipleAccounts` to fetch many accounts in one round trip.

### Impact Explanation
This is reachable from any unprivileged RPC client with no special permissions: any caller can request `getMultipleAccounts` with an arbitrary pubkey list and `encoding: "base58"` (or the legacy `"binary"` default) and a data-slice/pubkey combination that yields >128 bytes for at least one account. The result is a full-batch RPC failure for the caller, causing:
- Wasted RPC round-trips and increased retry pressure on RPC nodes as clients cannot fetch the N-1 good accounts.
- Potential downstream failures/timeouts for any bot, indexer, keeper, or liquidator service that batches account lookups via `getMultipleAccounts` and treats a batch error as "no data," since it cannot distinguish "one bad account" from "RPC is down."
This is a service-degradation / availability issue on the RPC surface, not a consensus or fund-safety issue — no unauthorized state mutation, no consensus divergence, and no memory-safety impact. It is a legitimate but narrow reliability bug that scales with the practice of batching lookups for many accounts (the risk grows the more accounts a single call requests, similar to how the reported bug's blast radius scaled with feed count).

### Likelihood Explanation
Likelihood is moderate-to-high for accidental triggering (any client requesting base58/legacy-binary encoding for large token/program accounts in a batch will hit this), and trivially reproducible by any external caller intentionally probing RPC behavior. No privileged access, timing races, or special conditions are required — only a normal `getMultipleAccounts` HTTP RPC call with a base58/binary encoding request against a mixed batch of pubkeys where at least one account exceeds `MAX_BASE58_BYTES`.

### Recommendation
Change `get_multiple_accounts` to collect per-pubkey `Result<Option<UiAccount>>` without short-circuiting the whole loop on the first `Err`. For encoding-related failures (like the base58 size limit), return a per-item error placeholder (or `null`) in the position of the offending pubkey instead of failing the entire response, mirroring the same per-item isolation recommended in the analog report (skip-and-log rather than abort-the-batch). Reserve full-request failure for request-level problems (invalid params, bank/context errors), not per-account encoding limitations.

### Proof of Concept
1. Fund/create two accounts: account A with small data (<128 bytes), account B with data >128 bytes (e.g. a large token or program account).
2. Call the JSON-RPC method:
```json
{"jsonrpc":"2.0","id":1,"method":"getMultipleAccounts","params":[["<A_pubkey>","<B_pubkey>"], {"encoding":"base58"}]}
```
3. Observe: because `encode_account` for account B returns `Err(InvalidRequest)` (per `rpc/src/rpc.rs:2581-2595`), and `get_multiple_accounts` propagates this via `?` at line 588 (`rpc/src/rpc.rs:562-592`), the entire response becomes a JSON-RPC error — account A's valid, successfully-fetched data is never returned to the caller, even though only account B was problematic.

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

**File:** rpc/src/rpc.rs (L2552-2573)
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
