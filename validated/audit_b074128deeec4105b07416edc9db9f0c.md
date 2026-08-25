### Title
Unbounded RPC response allocation via `getMultipleAccounts` with maximally-sized accounts and no `dataSlice` - ([File: rpc/src/rpc.rs])

### Summary
`getMultipleAccounts` only limits the *number* of pubkeys per request (`MAX_MULTIPLE_ACCOUNTS`, default 100) but places no limit on the *aggregate byte size* of the resulting response. An attacker who pre-funds and grows accounts up to `MAX_PERMITTED_DATA_LENGTH` (10MiB) can request up to the configured pubkey limit in one call with `base64` encoding and no `dataSlice`, forcing the RPC node to allocate and base64-encode the full data of every account into a single in-memory response.

### Finding Description
`AccountsDataImpl::get_multiple_accounts` enforces only a count check against `max_multiple_accounts` (default `MAX_MULTIPLE_ACCOUNTS = 100`) before forwarding the pubkey list to `JsonRpcRequestProcessor::get_multiple_accounts`: [1](#0-0) 

That processor function then loops over every pubkey, spawning a blocking task per pubkey that calls `get_encoded_account` with the caller-supplied `encoding` (default `Base64`) and `data_slice` (default `None`, i.e. full account data), collecting every result into one `Vec<Option<UiAccount>>` before returning it as a single RPC response: [2](#0-1) 

There is no byte-size cap tied to this path — `max_request_body_size`/`MAX_REQUEST_BODY_SIZE` in `rpc/src/rpc_service.rs` only bounds the size of the *incoming* HTTP request body, not the outgoing response, and `scan_results_limit_bytes` only applies to `getProgramAccounts`-style scans, not `getMultipleAccounts`. Because each account can independently be grown up to `MAX_PERMITTED_DATA_LENGTH` (10MiB) via normal `system_processor`/loader allocation, and there is no per-response byte budget, a single request with `max_multiple_accounts` pubkeys each near the max size forces the node to hold and base64-encode the full concatenated data set (up to ~100 × 10MiB ≈ 1GB raw, plus ~33% base64 inflation, plus JSON serialization overhead) in memory for a single response.

### Impact Explanation
This is a resource-exhaustion / DoS vector against the RPC service process (memory pressure, GC/allocator stalls, and increased latency for concurrently served clients), matching the "RPC crashes" / "non-RPC remote resource exhaustion of the RPC serving path" bounty category. It does not affect consensus, funds, or the replay path — impact is scoped to the JSON-RPC serving process (`rpc/src/rpc.rs`, `rpc/src/rpc_service.rs`) for validators that expose the full RPC API.

### Likelihood Explanation
Preconditions are attacker-affordable but non-trivial: the attacker must self-fund and grow N accounts (default up to 100) to `MAX_PERMITTED_DATA_LENGTH`, which costs substantial rent-exempt lamports and requires prior `Allocate`/`Extend`-type transactions (subject to existing per-transaction size/CU limits, but achievable over multiple transactions since account growth is not restricted to a single call). Once such accounts exist, a single `getMultipleAccounts` call reliably reproduces the worst-case allocation — no repeated calls or multiple clients are needed, satisfying the single-request-liveness condition in the question. Node operators can partially mitigate by lowering `--rpc-max-multiple-accounts`, but the default configuration and the base RPC API surface impose no response-size cap.

### Recommendation
Add a response-size budget to `get_multiple_accounts` (and similarly to `get_account_info`/`get_program_accounts`) analogous to `scan_results_limit_bytes`, rejecting or truncating requests whose total encoded account bytes (accounting for the chosen encoding and `data_slice`) would exceed a configurable cap, independent of the pubkey-count cap.

### Proof of Concept
```rust
// rpc/tests/rpc_multiple_accounts_size.rs (conceptual)
#[test]
fn test_get_multiple_accounts_unbounded_response_size() {
    let bank = create_test_bank();
    let mut pubkeys = vec![];
    for _ in 0..MAX_MULTIPLE_ACCOUNTS {
        let pubkey = Pubkey::new_unique();
        let data = vec![0u8; MAX_PERMITTED_DATA_LENGTH as usize]; // 10 MiB
        bank.store_account(&pubkey, &AccountSharedData::new(1_000_000_000, data.len(), &system_program::id()));
        pubkeys.push(pubkey);
    }
    let processor = JsonRpcRequestProcessor::new_from_bank(bank);
    let config = Some(RpcAccountInfoConfig {
        encoding: Some(UiAccountEncoding::Base64),
        data_slice: None, // full data requested
        ..Default::default()
    });
    let start_mem = current_process_rss();
    let result = block_on(processor.get_multiple_accounts(pubkeys, config)).unwrap();
    let peak_mem = current_process_rss();
    // Expect: peak_mem - start_mem stays bounded by a configured cap.
    // Actual: grows ~linearly with MAX_MULTIPLE_ACCOUNTS * MAX_PERMITTED_DATA_LENGTH (~1GB+).
    assert!(peak_mem - start_mem < RESPONSE_SIZE_CAP, "unbounded allocation for getMultipleAccounts response");
}
```

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

**File:** rpc/src/rpc.rs (L3305-3313)
```rust
                let max_multiple_accounts = meta
                    .config
                    .max_multiple_accounts
                    .unwrap_or(MAX_MULTIPLE_ACCOUNTS);
                if pubkey_strs.len() > max_multiple_accounts {
                    return Err(Error::invalid_params(format!(
                        "Too many inputs provided; max {max_multiple_accounts}"
                    )));
                }
```
