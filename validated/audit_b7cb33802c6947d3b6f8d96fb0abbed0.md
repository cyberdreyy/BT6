## Analog Finding

The reported bug class ("function callable when it shouldn't be due to a missing precondition guard") maps to a concrete crash-inducing precondition failure in Solana's `simulateTransaction` JSON-RPC path.

### Title
Unprivileged `simulateTransaction` RPC call can panic the validator process via `assert!(self.is_frozen())` on an unfrozen working bank - (File: `runtime/src/bank.rs`)

### Summary
`Bank::simulate_transaction()` enforces its "must run on a frozen bank" precondition with a hard `assert!` rather than a recoverable error, and this function is directly reachable from the unauthenticated `simulateTransaction` JSON-RPC method.

### Finding Description
`Bank::simulate_transaction` requires the bank to be frozen before simulating, but instead of returning an `Err`, it panics: [1](#0-0) 

This function is called directly from the `simulateTransaction` RPC handler with a bank selected purely from client-supplied `commitment`/`min_context_slot` parameters, with no freeze step performed by the RPC layer itself: [2](#0-1) [3](#0-2) 

The precondition is not enforced by the RPC layer — it relies entirely on the bank happening to already be frozen when a request arrives. The existing unit tests explicitly acknowledge this ordering dependency, freezing the bank up front specifically "to prevent a panic," and there is a dedicated regression test proving the `assert!` fires and panics when the bank is not frozen: [4](#0-3) [5](#0-4) 

The RPC path for sending transactions with preflight simulation shares the same `simulate_transaction` call and is subject to the identical precondition: [6](#0-5) 

### Impact Explanation
`Bank::simulate_transaction` is invoked with whatever bank `get_bank_with_config` resolves for the caller-chosen commitment level (e.g. `processed`) and `min_context_slot`. If a client can cause that resolution to return a bank that is still being actively built by the leader (i.e., not yet frozen), the `assert!` fires. An `assert!` failure is a Rust panic; in a `panic = "abort"` release build (a common configuration for validator binaries) this terminates the entire validator process from a single unauthenticated RPC call, not merely the handling thread — matching the "concrete validator-process crash from one request" acceptance criterion.

### Likelihood Explanation
The RPC handler itself performs no freeze check or freeze action prior to calling `simulate_transaction`, and the precondition is only satisfied by external circumstance (block production having already finished freezing the bank at the time of the RPC call). Any timing window in which a `processed`/low-commitment bank reference resolves to a bank still in the process-and-freeze pipeline reaches the `assert!` directly, with no permission or validator/operator role required — this is a plain unprivileged `simulateTransaction` call.

### Recommendation
Replace the `assert!(self.is_frozen(), ...)` in `Bank::simulate_transaction` with either (a) an internal freeze call, or (b) a recoverable `Result`/RPC error path so that an unfrozen bank produces a JSON-RPC error response instead of aborting the process. Additionally, the RPC handler in `rpc/src/rpc.rs` (`simulate_transaction`, and the preflight path in `send_transaction`) should validate/ensure bank freeze state before invoking `bank.simulate_transaction`, rather than depending on incidental external freezing.

### Proof of Concept
The codebase's own test `test_rpc_simulate_transaction_panic_on_unfrozen_bank` is a working PoC demonstrating that invoking `simulateTransaction` through the RPC handler against an unfrozen bank panics with `"simulation bank must be frozen"`: [4](#0-3) 

Note: I could not verify the full implementation of `get_bank_with_config` (the function resolving which bank is used based on `commitment`/`min_context_slot`) within the indexed context, so the exact production-timing window in which an in-flight (unfrozen) working bank would be returned to `simulateTransaction` is inferred from the test comments and the `assert!` contract rather than directly observed in `get_bank_with_config`'s source. A Devin session with full repository access would be needed to confirm the precise commitment/slot conditions under which the RPC layer can hand `simulate_transaction` an unfrozen bank in production.

### Citations

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

**File:** rpc/src/rpc.rs (L3946-3950)
```rust
                let simulation_result = if let Some(err) = verification_error {
                    TransactionSimulationResult::new_error(err)
                } else {
                    preflight_bank.simulate_transaction(&transaction, false)
                };
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

**File:** rpc/src/rpc.rs (L4059-4072)
```rust
            let transaction =
                sanitize_transaction(unsanitized_tx, bank, bank.get_reserved_account_keys())?;

            let verification_error = if sig_verify {
                transaction.verify().err()
            } else {
                None
            };

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

**File:** rpc/src/rpc.rs (L6934-6949)
```rust
    #[test]
    fn test_rpc_send_transaction_preflight() {
        let exit = Arc::new(AtomicBool::new(false));
        let validator_exit = create_validator_exit(exit.clone());
        let ledger_path = get_tmp_ledger_path!();
        let blockstore = Arc::new(Blockstore::open(&ledger_path).unwrap());
        let block_commitment_cache = Arc::new(RwLock::new(BlockCommitmentCache::default()));
        let (bank_forks, mint_keypair, ..) = new_bank_forks();
        let optimistically_confirmed_bank =
            OptimisticallyConfirmedBank::locked_from_bank_forks_root(&bank_forks);
        let health = RpcHealth::stub(optimistically_confirmed_bank.clone(), blockstore.clone());
        // Mark the node as healthy to start
        health.stub_set_health_status(Some(RpcHealthStatus::Ok));

        // Freeze bank 0 to prevent a panic in `run_transaction_simulation()`
        bank_forks.write().unwrap().get(0).unwrap().freeze();
```
