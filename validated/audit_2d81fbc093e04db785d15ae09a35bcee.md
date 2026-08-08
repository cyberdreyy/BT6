### Title
Unbounded per-request CPU/memory cost in `getMultipleAccounts` due to missing aggregate byte-limit across batched `get_encoded_account` calls - ([File: rpc/src/rpc.rs])

### Summary
`JsonRpcRequestProcessor::get_multiple_accounts` only enforces a count limit (`MAX_MULTIPLE_ACCOUNTS`) on the number of pubkeys in a single request, but performs no aggregate byte-size check across the accounts being fetched and encoded. Each pubkey is independently dispatched to `spawn_blocking(get_encoded_account)`, so a single request naming `MAX_MULTIPLE_ACCOUNTS` pubkeys that each resolve to a maximal-size (10MiB) account with `encoding=base64+zstd` forces the runtime to serially clone, encode, and zstd-compress up to `MAX_MULTIPLE_ACCOUNTS × 10MiB` of account data within one client call.

### Finding Description
The RPC entrypoint `rpc_accounts::AccountsData::get_multiple_accounts` validates only the pubkey count against `max_multiple_accounts`/`MAX_MULTIPLE_ACCOUNTS`: [1](#0-0) 

It performs no check on the total or per-account data size before dispatching to `JsonRpcRequestProcessor::get_multiple_accounts`, which loops over every pubkey and, for each one, spawns a blocking task calling `get_encoded_account` with the caller-supplied `encoding` (which may be `Base64Zstd`, forcing zstd compression of the full account data): [2](#0-1) 

Each iteration `.await`s the previous `spawn_blocking` before issuing the next one, so the work is not concurrent, but it is also not bounded by any explicit total-byte cap — only by the count limit (`MAX_MULTIPLE_ACCOUNTS`) and by the protocol-level per-account data cap (`MAX_PERMITTED_DATA_LENGTH`, 10MiB), which an attacker can already satisfy by writing sufficiently large accounts on-chain (or referencing existing large accounts such as large program/data accounts). Since account contents are attacker-controlled data returned via a read-only RPC query, no privileged access is required — the attacker only needs to know/reference `MAX_MULTIPLE_ACCOUNTS` pubkeys that each resolve to near-maximal account sizes and request `encoding=base64+zstd`.

There is no guard anywhere in this path (`get_multiple_accounts` in `rpc.rs`, or the `rpc_accounts` trait impl) that sums up account sizes or rejects the request based on total encoded/compressed payload size — the only existing checks are the pubkey-count limit and the per-account maximum size enforced by the runtime's account-size rules, neither of which bounds the *aggregate* cost of one `getMultipleAccounts` call.

### Impact Explanation
A single unprivileged client can issue one `getMultipleAccounts` JSON-RPC call carrying `MAX_MULTIPLE_ACCOUNTS` pubkeys, each pointing to a ~10MiB account, with `encoding=base64+zstd`. This forces the validator's RPC processing thread pool to clone and zstd-compress up to `MAX_MULTIPLE_ACCOUNTS × 10MiB` of account data serially within the handling of one request, well beyond what a single "per-request" cost bound implies (a request nominally sized for `MAX_MULTIPLE_ACCOUNTS` small accounts). This matches the "unbounded cost for a single low-rate call" bounty category — it does not require multiple clients or exceeding the `CLUSTER_SLOT_TIME_TARGET / 2` rate limit, and does not require `getProgramAccounts` without secondary indexes (explicitly excluded). It can meaningfully degrade RPC responsiveness/availability for the node servicing the request, but does not corrupt consensus state or cross-fork data.

### Likelihood Explanation
Feasible and repeatable at will: an attacker only needs (a) knowledge of `MAX_MULTIPLE_ACCOUNTS` pubkeys pointing to large (near 10MiB) accounts — achievable by writing such accounts on-chain themselves ahead of time (no special privileges needed, just funding rent for large accounts), and (b) the ability to send one `getMultipleAccounts` JSON-RPC request with `encoding=base64+zstd`. This is a single low-rate RPC call, satisfying the attacker constraints in scope. No validator/leader/gossip control or config access is needed.

### Recommendation
Add an aggregate byte-size budget check in `JsonRpcRequestProcessor::get_multiple_accounts` (and/or in the `rpc_accounts` trait handler) that sums the (pre-fetch, if available, or actual) account data sizes across the batch and rejects/truncates the request if the total exceeds a documented bound (e.g., a configurable `max_multiple_accounts_total_bytes`), independent of the existing pubkey-count limit. Consider also capping/disallowing `Base64Zstd` compression cost for very large accounts in bulk multi-account requests, or applying the same size-based rejection used elsewhere (e.g., `MAX_BASE58_BYTES`-style checks) to the aggregate case.

### Proof of Concept
Integration test plan (Rust, using the existing `RpcHandler::start()` test harness seen in `rpc/src/rpc.rs`'s test module):
```rust
#[test]
fn test_get_multiple_accounts_unbounded_cost() {
    let rpc = RpcHandler::start();
    let bank = rpc.working_bank();

    // Store MAX_MULTIPLE_ACCOUNTS accounts each near MAX_PERMITTED_DATA_LENGTH (10MiB)
    let pubkeys: Vec<_> = (0..MAX_MULTIPLE_ACCOUNTS)
        .map(|_| {
            let pubkey = Pubkey::new_unique();
            let data = vec![0xAB; 10 * 1024 * 1024]; // 10MiB, low-entropy for worst-case zstd cost/time tradeoff can be varied
            let account = AccountSharedData::create_from_existing_shared_data(
                1, Arc::new(data), Pubkey::default(), false, 0,
            );
            bank.store_account(&pubkey, &account);
            pubkey
        })
        .collect();

    let request = create_test_request(
        "getMultipleAccounts",
        Some(json!([
            pubkeys.iter().map(|p| p.to_string()).collect::<Vec<_>>(),
            { "encoding": "base64+zstd" }
        ])),
    );

    let start = std::time::Instant::now();
    let peak_mem_before = /* sample RSS */;
    let _result = rpc.handle_request_sync(request);
    let elapsed = start.elapsed();
    let peak_mem_after = /* sample RSS */;

    // Assert this stays under a documented per-request bound, e.g. < 200ms CPU / < 50MB peak allocation
    assert!(elapsed < Duration::from_millis(200), "single getMultipleAccounts call took {:?}", elapsed);
    assert!(peak_mem_after - peak_mem_before < 50 * 1024 * 1024);
}
```
Expected result today: the test fails because CPU time and peak memory scale with `MAX_MULTIPLE_ACCOUNTS × 10MiB` compression work, demonstrating no aggregate limit is enforced.

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
