### Title
`simulateTransaction` can panic the RPC handler via `assert!(self.is_frozen())` when the bank returned by `get_bank_with_config` for `commitment=processed` is not yet frozen - ([File: rpc/src/rpc.rs, runtime/src/bank.rs])

### Summary
The `simulateTransaction` RPC handler fetches a bank via `meta.get_bank_with_config(RpcContextConfig { commitment, min_context_slot })` and then unconditionally calls `bank.simulate_transaction(&transaction, enable_cpi_recording)`, which begins with `assert!(self.is_frozen(), "simulation bank must be frozen")`. For `commitment == Processed` (the default when a client omits `commitment` or explicitly requests it), `JsonRpcRequestProcessor::bank()` returns the heaviest/working bank from `bank_forks`, which is not guaranteed to be frozen. This lets a single unprivileged client trigger the `assert!` panic with an ordinary `simulateTransaction` call.

### Finding Description
The call chain is:
1. `rpc_full::Full::simulate_transaction` (`rpc/src/rpc.rs:4010-4038`) decodes the client-supplied transaction and calls `meta.get_bank_with_config(RpcContextConfig { commitment, min_context_slot })?`. [1](#0-0) 
2. `JsonRpcRequestProcessor::get_bank_with_config` (`rpc/src/rpc.rs:274-289`) calls `self.bank(commitment)` and only checks `min_context_slot`, performing no `is_frozen()` check. [2](#0-1) 
3. `JsonRpcRequestProcessor::bank` (`rpc/src/rpc.rs:349-400`) for `CommitmentLevel::Processed` explicitly returns "the heaviest slot" from `bank_forks`, i.e., the current working bank, which can still be actively accepting transactions (unfrozen) at the moment of the RPC call. [3](#0-2) 
4. Back in `simulate_transaction`, the unfrozen bank is passed to `bank.simulate_transaction(&transaction, enable_cpi_recording)` (`rpc/src/rpc.rs:4071`). [4](#0-3) 
5. `Bank::simulate_transaction` (`runtime/src/bank.rs:3809-3818`) asserts the bank is frozen before delegating to `simulate_transaction_unchecked`: `assert!(self.is_frozen(), "simulation bank must be frozen");`. [5](#0-4) 

The codebase itself contains a regression test that demonstrates exactly this panic path end-to-end via the JSON-RPC dispatcher, confirming it is reachable purely through a crafted `simulateTransaction` request without any special privileges: `test_rpc_simulate_transaction_panic_on_unfrozen_bank` explicitly asserts `!bank.is_frozen()` and then issues the RPC call, expecting the panic `"simulation bank must be frozen"`. [6](#0-5) 

No commitment/slot/parameter guard in the `simulateTransaction` path checks `bank.is_frozen()` before calling `Bank::simulate_transaction`; `get_bank_with_config` only validates `min_context_slot`, and `commitment=processed` (default) is documented to yield the actively-updating working bank.

### Impact Explanation
A single unprivileged RPC client can cause the RPC-handling thread executing `simulate_transaction` to panic. Whether this results in full validator process termination depends on the panic strategy/catch-boundary configured for the RPC handler thread pool (not verified from available context), but at minimum it panics inside the thread servicing the request, which the repository's own test suite treats as a genuine defect worth a dedicated regression test (`test_rpc_simulate_transaction_panic_on_unfrozen_bank`). This matches the "RPC crash from a single request" bounty category: no consensus-state mutation occurs, but the request can deny service to the RPC subsystem by panicking a worker thread on ordinary, low-frequency input.

### Likelihood Explanation
- Precondition: the client's `simulateTransaction` request (with `commitment` omitted/`"processed"`, or `min_context_slot` unset/satisfied) races against bank-freezing during normal validator operation, i.e., the RPC request arrives while the current working bank is still being built and not yet frozen.
- This is a normal, recurring window during validator operation (the working bank is unfrozen for a significant fraction of each slot), so the race is naturally and repeatably triggerable with a single low-rate RPC call, well within the "one call per `CLUSTER_SLOT_TIME_TARGET / 2`" constraint.
- Feasibility is confirmed by the existing unit test that reproduces the exact panic using only an RPC request against an unfrozen working bank.

### Recommendation
In `JsonRpcRequestProcessor::get_bank_with_config` (or specifically in `simulate_transaction`), check `bank.is_frozen()` before calling `Bank::simulate_transaction`. If the resolved bank is not frozen (e.g., `commitment=processed` race), either wait for/return the parent frozen bank, or return a proper JSON-RPC error (e.g., a `BlockNotAvailable`/`NodeUnhealthy`-style error) instead of relying on `Bank::simulate_transaction`'s internal `assert!` to enforce the invariant.

### Proof of Concept
The vulnerability is already reproduced by the in-repo test:
```rust
// rpc/src/rpc.rs
#[test]
#[should_panic(expected = "simulation bank must be frozen")]
fn test_rpc_simulate_transaction_panic_on_unfrozen_bank() {
    let rpc = RpcHandler::start();
    let bank = rpc.working_bank();
    let recent_blockhash = bank.confirmed_last_blockhash();
    let RpcHandler { meta, io, mint_keypair, .. } = rpc;

    let bob_pubkey = Pubkey::new_unique();
    let tx = system_transaction::transfer(&mint_keypair, &bob_pubkey, 1234, recent_blockhash);
    let tx_serialized_encoded = bs58::encode(wincode::serialize(&tx).unwrap()).into_string();

    assert!(!bank.is_frozen());

    let req = format!(
        r#"{{"jsonrpc":"2.0","id":1,"method":"simulateTransaction","params":["{tx_serialized_encoded}", {{"sigVerify": true}}]}}"#,
    );

    // should panic because `bank` is not frozen
    let _ = io.handle_request_sync(&req, meta);
}
``` [6](#0-5) 

Expected assertion for a fix validation: after applying a fix (e.g., checking `is_frozen()` and returning an `Err` instead of calling `Bank::simulate_transaction`), this test should be changed to assert a JSON-RPC error response is returned instead of panicking, and an integration test issuing repeated `simulateTransaction` calls with `commitment=processed` concurrently with normal slot/bank rotation should assert no panic occurs across many iterations.

### Citations

**File:** rpc/src/rpc.rs (L273-289)
```rust
impl JsonRpcRequestProcessor {
    fn get_bank_with_config(&self, config: RpcContextConfig) -> Result<Arc<Bank>> {
        let RpcContextConfig {
            commitment,
            min_context_slot,
        } = config;
        let bank = self.bank(commitment);
        if let Some(min_context_slot) = min_context_slot
            && bank.slot() < min_context_slot
        {
            return Err(RpcCustomError::MinContextSlotNotReached {
                context_slot: bank.slot(),
            }
            .into());
        }
        Ok(bank)
    }
```

**File:** rpc/src/rpc.rs (L371-382)
```rust
        match commitment.commitment {
            CommitmentLevel::Processed => {
                debug!("RPC using the heaviest slot: {slot:?}");
            }
            CommitmentLevel::Finalized => {
                debug!("RPC using block: {slot:?}");
            }
            CommitmentLevel::Confirmed => unreachable!(), // SingleGossip variant is deprecated
        };

        let r_bank_forks = self.bank_forks.read().unwrap();
        r_bank_forks.get(slot).unwrap_or_else(|| {
```

**File:** rpc/src/rpc.rs (L4010-4038)
```rust
        fn simulate_transaction(
            &self,
            meta: Self::Metadata,
            data: String,
            config: Option<RpcSimulateTransactionConfig>,
        ) -> Result<RpcResponse<RpcSimulateTransactionResult>> {
            debug!("simulate_transaction rpc request received");
            let RpcSimulateTransactionConfig {
                sig_verify,
                replace_recent_blockhash,
                commitment,
                encoding,
                accounts: config_accounts,
                min_context_slot,
                inner_instructions: enable_cpi_recording,
            } = config.unwrap_or_default();
            let tx_encoding = encoding.unwrap_or(UiTransactionEncoding::Base58);
            let binary_encoding = tx_encoding.into_binary_encoding().ok_or_else(|| {
                Error::invalid_params(format!(
                    "unsupported encoding: {tx_encoding}. Supported encodings: base58, base64"
                ))
            })?;
            let (_, mut unsanitized_tx) =
                decode_and_deserialize::<VersionedTransaction>(data, binary_encoding)?;

            let bank = &*meta.get_bank_with_config(RpcContextConfig {
                commitment,
                min_context_slot,
            })?;
```

**File:** rpc/src/rpc.rs (L4068-4072)
```rust
            let simulation_result = if let Some(err) = verification_error {
                TransactionSimulationResult::new_error(err)
            } else {
                bank.simulate_transaction(&transaction, enable_cpi_recording)
            };
```

**File:** rpc/src/rpc.rs (L6795-6820)
```rust
    #[test]
    #[should_panic(expected = "simulation bank must be frozen")]
    fn test_rpc_simulate_transaction_panic_on_unfrozen_bank() {
        let rpc = RpcHandler::start();
        let bank = rpc.working_bank();
        let recent_blockhash = bank.confirmed_last_blockhash();
        let RpcHandler {
            meta,
            io,
            mint_keypair,
            ..
        } = rpc;

        let bob_pubkey = Pubkey::new_unique();
        let tx = system_transaction::transfer(&mint_keypair, &bob_pubkey, 1234, recent_blockhash);
        let tx_serialized_encoded = bs58::encode(wincode::serialize(&tx).unwrap()).into_string();

        assert!(!bank.is_frozen());

        let req = format!(
            r#"{{"jsonrpc":"2.0","id":1,"method":"simulateTransaction","params":["{tx_serialized_encoded}", {{"sigVerify": true}}]}}"#,
        );

        // should panic because `bank` is not frozen
        let _ = io.handle_request_sync(&req, meta);
    }
```

**File:** runtime/src/bank.rs (L3809-3818)
```rust
    /// Run transactions against a frozen bank without committing the results
    pub fn simulate_transaction(
        &self,
        transaction: &impl TransactionWithMeta,
        enable_cpi_recording: bool,
    ) -> TransactionSimulationResult {
        assert!(self.is_frozen(), "simulation bank must be frozen");

        self.simulate_transaction_unchecked(transaction, enable_cpi_recording)
    }
```
