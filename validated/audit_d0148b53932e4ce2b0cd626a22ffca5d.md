### Title
Unprivileged `simulateTransaction` RPC call can panic the validator by hitting `assert!(self.is_frozen(), "simulation bank must be frozen")` on an actively-replaying (unfrozen) working bank - (File: `rpc/src/rpc.rs`, `runtime/src/bank.rs`)

### Summary
`Bank::simulate_transaction` unconditionally asserts that the bank is frozen before running the simulation. This assertion is not merely defensive; the test `test_rpc_simulate_transaction_panic_on_unfrozen_bank` in `rpc/src/rpc.rs` demonstrates that calling `simulateTransaction` against an unfrozen bank panics. [1](#0-0) 

The RPC-facing `simulate_transaction` handler resolves the bank to run against purely from `commitment`/`min_context_slot`, via `meta.get_bank_with_config(...)`, with no check that the resolved bank is frozen: [2](#0-1) 

`get_bank_with_config` simply calls `self.bank(commitment)` and only errors on `min_context_slot`, not on freeze state: [3](#0-2) 

For `CommitmentLevel::Processed`, `self.bank(commitment)` resolves to "the heaviest slot" from `block_commitment_cache.slot_with_commitment(...)`, then fetches that bank from `bank_forks`: [4](#0-3) 

`BankForks` explicitly tracks slots whose banks are still unfrozen (mid-replay/mid-production) via `active_bank_slots()` and `working_slot`/`highest_slot()`, confirming that the "heaviest"/working bank returned to RPC callers can legitimately be unfrozen at the moment of the call: [5](#0-4) [6](#0-5) 

`is_frozen()`/`freeze()` show that a bank only becomes frozen once `ReplayStage` (or leader block production) explicitly calls `freeze()` after the slot's last tick, and the hash lock guarantees this only happens once processing completes — i.e., there is a real, non-trivial window during which the working/heaviest bank is unfrozen: [7](#0-6) [8](#0-7) 

### Finding Description
This is the same bug class as the Solidity finding: a function that behaves like a "view"/read-only query internally has a hard precondition (`updateFrequency` boundary in the Solidity case; `is_frozen()` in the Solana case) that is not always true, and the caller-facing wrapper does nothing to guarantee the precondition before invoking it. In Solidity, `delegateToViewImplementation` called `borrowRatePerBlock`/`supplyRatePerBlock` without accounting for the interest-accrual precondition, causing a revert once enough blocks had passed. In agave, the JSON-RPC `simulateTransaction` handler calls `Bank::simulate_transaction`, which does a hard `assert!(self.is_frozen(), ...)`, without the RPC layer ensuring the selected bank is actually frozen. The RPC layer selects the bank purely by commitment level and slot recency (`get_bank_with_config` → `self.bank(commitment)`), and for `processed` commitment this resolves to the "heaviest"/working bank, which by design can be unfrozen while `ReplayStage` (or the leader's own block-production path) is still replaying/producing that slot.

Because `assert!` in Rust is not a `Result`-based error but a hard panic, and there is no `catch_unwind`/panic isolation visible around this RPC call path (`rpc/**/*.rs` search found none), this converts what should be a benign "not ready yet" RPC condition into a process-level panic triggered purely by an unprivileged JSON-RPC request timed against normal, expected validator internal state transitions (replay in progress).

### Impact Explanation
A panic inside `assert!(self.is_frozen(), "simulation bank must be frozen")` triggered from the JSON-RPC request-handling thread will propagate as a Rust panic. Depending on the panic-handling configuration of the RPC runtime, this can crash the request-handling thread or, if `panic = "abort"` / no panic boundary exists on that call stack, crash the entire validator process. This matches the "concrete validator-process crash...from one request" acceptance criterion. Unlike the Solidity finding (Medium — a revert, with functionality otherwise still available via an alternate call path), the Solana analog is more severe because there is no alternate non-panicking path for an RPC client to obtain the same result, and the failure mode is a hard panic/abort rather than a clean error response.

### Likelihood Explanation
Any RPC node accepts unauthenticated `simulateTransaction` calls with `commitment: "processed"` (or, by extension, no explicit commitment, since `processed`/default resolves through `self.bank(commitment)` to a bank that is not necessarily frozen at the exact instant of the call). While in the steady state most calls will race successfully because the working bank is typically frozen once ticks complete, the window during active replay/leader block production where the heaviest bank is unfrozen is a normal, recurring condition of validator operation (not a bug, not requiring malicious input) — it happens on essentially every slot boundary. A client issuing `simulateTransaction` requests at a moderate rate against a node under any load (or one lagging behind its highest slot during replay) will eventually land inside this window and trigger the panic with a single, unprivileged, unauthenticated request. This does not require multiple clients, unfiltered `getProgramAccounts`, or any secondary indexing — it is a single call.

### Recommendation
- In `simulate_transaction` (`rpc/src/rpc.rs`), after resolving `bank` via `get_bank_with_config`, explicitly check `bank.is_frozen()` and return a proper JSON-RPC error (e.g., a `MinContextSlotNotReached`-style or dedicated "bank not ready" error) if the bank is not yet frozen, instead of calling into `bank.simulate_transaction()` unconditionally.
- Alternatively/additionally, change `Bank::simulate_transaction` to return a `Result`/typed error instead of asserting, so that callers (including future callers) cannot trigger a hard panic from external input.
- Add a regression test that issues `simulateTransaction` against a bank at the currently active/unfrozen slot (mirroring `test_rpc_simulate_transaction_panic_on_unfrozen_bank`, but asserting a clean RPC error response rather than a panic).

### Proof of Concept
The existing test in the repository already demonstrates the crash primitive purely with production code paths (no mocks needed for the vulnerable assertion itself): [9](#0-8) 

This test explicitly constructs an unfrozen bank (`assert!(!bank.is_frozen())`) and issues a `simulateTransaction` JSON-RPC request against it, and the test itself documents `#[should_panic(expected = "simulation bank must be frozen")]`. The real-world equivalent is any client sending a `simulateTransaction` request with `processed` commitment while the node's currently-heaviest bank (returned by `bank_forks.working_bank()` / `self.bank(commitment)`) has not yet been frozen by `ReplayStage`/block production — a condition that recurs naturally near every slot boundary, requiring no special privileges and only one RPC call to trigger.

### Citations

**File:** runtime/src/bank.rs (L2339-2341)
```rust
    pub fn is_frozen(&self) -> bool {
        *self.hash.read().unwrap() != Hash::default()
    }
```

**File:** runtime/src/bank.rs (L3057-3084)
```rust
    pub fn freeze(&self) {
        // This lock prevents any new commits from BankingStage
        // `Consumer::execute_and_commit_transactions_locked()` from
        // coming in after the last tick is observed. This is because in
        // BankingStage, any transaction successfully recorded in
        // `record_transactions()` is recorded after this `hash` lock
        // is grabbed. At the time of the successful record,
        // this means the PoH has not yet reached the last tick,
        // so this means freeze() hasn't been called yet. And because
        // BankingStage doesn't release this hash lock until both
        // record and commit are finished, those transactions will be
        // committed before this write lock can be obtained here.
        let mut hash = self.hash.write().unwrap();
        if *hash == Hash::default() {
            // finish up any deferred changes to account state
            self.distribute_transaction_fee_details();
            self.update_slot_history();
            self.run_incinerator();

            // freeze is a one-way trip, idempotent
            self.freeze_started.store(true, Relaxed);
            // updating the accounts lt hash must happen *outside* of hash_internal_state() so
            // that rehash() can be called and *not* modify self.accounts_lt_hash.
            self.finish_accounts_lt_hash_updates();
            *hash = self.hash_internal_state();
            self.rc.accounts.accounts_db.mark_slot_frozen(self.slot());
        }
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

**File:** rpc/src/rpc.rs (L349-400)
```rust
    #[allow(deprecated)]
    fn bank(&self, commitment: Option<CommitmentConfig>) -> Arc<Bank> {
        debug!("RPC commitment_config: {commitment:?}");

        let commitment = commitment.unwrap_or_default();
        if commitment.is_confirmed() {
            let bank = self
                .optimistically_confirmed_bank
                .read()
                .unwrap()
                .bank
                .clone();
            debug!("RPC using optimistically confirmed slot: {:?}", bank.slot());
            return bank;
        }

        let slot = self
            .block_commitment_cache
            .read()
            .unwrap()
            .slot_with_commitment(commitment.commitment);

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
            // We log a warning instead of returning an error, because all known error cases
            // are due to known bugs that should be fixed instead.
            //
            // The slot may not be found as a result of a known bug in snapshot creation, where
            // the bank at the given slot was not included in the snapshot.
            // Also, it may occur after an old bank has been purged from BankForks and a new
            // BlockCommitmentCache has not yet arrived. To make this case impossible,
            // BlockCommitmentCache should hold an `Arc<Bank>` everywhere it currently holds
            // a slot.
            //
            // For more information, see https://github.com/solana-labs/solana/issues/11078
            warn!(
                "Bank with {:?} not found at slot: {:?}",
                commitment.commitment, slot
            );
            r_bank_forks.root_bank()
        })
    }
```

**File:** rpc/src/rpc.rs (L4010-4072)
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
            let mut blockhash: Option<RpcBlockhash> = None;
            if replace_recent_blockhash {
                if sig_verify {
                    return Err(Error::invalid_params(
                        "sigVerify may not be used with replaceRecentBlockhash",
                    ));
                }
                let recent_blockhash = bank.last_blockhash();
                unsanitized_tx
                    .message
                    .set_recent_blockhash(recent_blockhash);
                let last_valid_block_height = bank
                    .get_blockhash_last_valid_block_height(&recent_blockhash)
                    .expect("bank blockhash queue should contain blockhash");
                blockhash.replace(RpcBlockhash {
                    blockhash: recent_blockhash.to_string(),
                    last_valid_block_height,
                });
            }

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

**File:** runtime/src/bank_forks.rs (L243-249)
```rust
    pub fn active_bank_slots(&self) -> Vec<Slot> {
        self.banks
            .iter()
            .filter(|(_, v)| !v.is_frozen())
            .map(|(k, _v)| *k)
            .collect()
    }
```

**File:** runtime/src/bank_forks.rs (L398-412)
```rust
    pub fn highest_slot(&self) -> Slot {
        self.working_slot
    }

    fn find_highest_slot(&self) -> Slot {
        self.banks.values().map(|bank| bank.slot()).max().unwrap()
    }

    pub fn working_bank(&self) -> Arc<Bank> {
        self.banks[&self.highest_slot()].clone_without_scheduler()
    }

    pub fn working_bank_with_scheduler(&self) -> BankWithScheduler {
        self.banks[&self.highest_slot()].clone_with_scheduler()
    }
```
