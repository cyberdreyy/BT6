I have enough evidence now to finalize the analog.

The node configures `TransactionValidationTaskExecutor::eth_builder(...).with_minimum_priority_fee(ctx.config().txpool.minimum_priority_fee)` on the inner Eth validator to enforce a policy-level minimum priority fee for standard transactions admitted to the pool.### Title
EIP-8130 (account-abstraction) transactions bypass the configured `minimum_priority_fee` mempool policy - ([File: crates/execution/txpool/src/validator.rs])

### Summary
Base's node configures a pool-wide minimum priority fee policy via `TransactionValidationTaskExecutor::eth_builder(...).with_minimum_priority_fee(ctx.config().txpool.minimum_priority_fee)` on the inner Eth transaction validator [1](#0-0) . `BaseTransactionValidator::validate_one_with_state_inner` only forwards **standard** (non-EIP-8130) transactions to `self.inner`, which enforces that minimum. EIP-8130 (account-abstraction) transactions take an entirely separate path — `validate_eip8130_structural` + `validate_eip8130_full` — that never calls `self.inner` and never checks the configured minimum priority fee, mirroring the Kairos `repay()` pattern where an admin-set minimum constraint (`MinOfferCost`) is defined but never enforced on one of the code paths that should respect it.

### Finding Description
`BasePoolBuilder::build_pool` wires up the mempool minimum-priority-fee policy exclusively on the inner `EthTransactionValidator` [2](#0-1) . In `validate_one_with_state_inner`, the dispatch is:
- If the tx `is_eip8130()`, run `validate_eip8130_structural` then `validate_eip8130_full`, and return directly — `self.inner` (which holds the `minimum_priority_fee` setting) is never invoked [3](#0-2) .
- Only the non-EIP-8130 branch calls `self.inner.validate_one_with_state(...)`, which is where `minimum_priority_fee` is actually enforced [4](#0-3) .

The doc comment on `validate_eip8130_full` explicitly states the design intent: "This deliberately bypasses the inner Eth validator for EIP-8130 because configured senders may be smart contracts and sponsored transactions charge a payer instead of the sender." [5](#0-4) . That's a legitimate reason to skip balance/sender-specific checks, but it also silently drops the unrelated `minimum_priority_fee` admission policy along with it.

The only fee-related checks actually performed for EIP-8130 transactions are the static/structural check (`tip <= max_fee`, non-zero gas/fee) in `Eip8130Signed::validate_admission_static` [6](#0-5)  and the stateful `FeeCheck::validate_fees` (tip vs cap, cap vs base fee) plus payer-balance checks in `validate_eip8130_full` [7](#0-6) . None of these compare against the node operator's configured `minimum_priority_fee`; `FeeCheck::validate_fees` in `crates/execution/eip8130/src/fee.rs` only checks `tip <= max_fee` and `max_fee >= base_fee` [8](#0-7) .

### Impact Explanation
An operator-configured mempool admission policy (`txpool.minimum_priority_fee`) intended to filter out unprofitable or spam transactions (zero/near-zero tip transactions) is silently unenforced for the entire EIP-8130 transaction class. This lets EIP-8130 (account-abstraction) senders submit and have propagated transactions with a priority fee below the node's configured floor, defeating a deliberate node-operator anti-spam/economic policy across the whole mempool for a growing transaction type. This is analogous in class (a documented, admin-configured minimum-fee constraint silently skipped on one code path) to the reported Kairos issue, though the practical severity here is bounded to mempool admission policy rather than direct fund loss — it does not cause theft or freezing of funds, node halt, or a wrong state transition, since execution-time fee accounting (`FeeCheck::validate_balance`, L1/operator fee checks) is still performed correctly.

### Likelihood Explanation
This will happen on every node that (a) sets a non-zero `txpool.minimum_priority_fee` and (b) has EIP-8130/Zenith active, without requiring any privileged access — any external EIP-8130 sender can trivially submit a zero (or below-minimum) priority-fee transaction and have it admitted and propagated, since it is unconditionally reachable via the standard tx-pool `validate_transaction` entrypoint.

### Recommendation
Enforce the configured `minimum_priority_fee` explicitly inside `validate_eip8130_full`/`validate_eip8130_structural` (e.g., pass the minimum through to `BaseTransactionValidator` and compare `signed.tx().max_priority_fee_per_gas` against it), rather than relying solely on `self.inner`, which the EIP-8130 path deliberately bypasses.

### Proof of Concept
1. Configure a Base node with `--txpool.minimum-priority-fee <N>` (N > 0).
2. Wait for the Zenith fork (enabling EIP-8130) to be active.
3. Submit a valid EIP-8130 transaction with `max_priority_fee_per_gas = 0` (or any value below `N`), satisfying `validate_admission_static` (`tip <= max_fee`, non-zero gas/fee) and `validate_eip8130_full`'s balance checks.
4. Observe the transaction is accepted into the pool and propagated (`validate_one_with_state_inner` returns `TransactionValidationOutcome::Valid` via the EIP-8130 branch at `crates/execution/txpool/src/validator.rs:918-951`, never touching `self.inner`'s `minimum_priority_fee` gate), whereas an equivalent legacy/EIP-1559 transaction with the same sub-minimum tip is rejected by `self.inner.validate_one_with_state`.

### Citations

**File:** crates/execution/node/src/node.rs (L977-999)
```rust
        let validator =
            TransactionValidationTaskExecutor::eth_builder(ctx.provider().clone(), evm_config)
                .no_eip4844()
                .with_max_tx_input_bytes(ctx.config().txpool.max_tx_input_bytes)
                .kzg_settings(ctx.kzg_settings()?)
                .set_tx_fee_cap(ctx.config().rpc.rpc_tx_fee_cap)
                .with_max_tx_gas_limit(ctx.config().txpool.max_tx_gas_limit)
                .with_minimum_priority_fee(ctx.config().txpool.minimum_priority_fee)
                .with_additional_tasks(
                    pool_config_overrides
                        .additional_validation_tasks
                        .unwrap_or_else(|| ctx.config().txpool.additional_validation_tasks),
                )
                .build_with_tasks(ctx.task_executor().clone(), blob_store.clone())
                .map(|validator| {
                    BaseTransactionValidator::new(validator)
                        // In --dev mode we can't require gas fees because we're unable to decode
                        // the L1 block info
                        .require_l1_data_gas_fee(!ctx.config().dev.dev)
                        .with_additional_trusted_delegation_targets(
                            additional_trusted_delegation_targets.clone(),
                        )
                });
```

**File:** crates/execution/txpool/src/validator.rs (L918-951)
```rust
        if transaction.as_eip8130().is_some() {
            let validation = {
                let signed = transaction.as_eip8130().expect("checked above");
                self.validate_eip8130_structural(signed)
                    .and_then(|()| self.validate_eip8130_full(signed))
            };
            let state = match validation {
                Ok(state) => state,
                Err(err) => return TransactionValidationOutcome::Invalid(transaction, err),
            };
            let propagate =
                matches!(origin, TransactionOrigin::External | TransactionOrigin::Local);
            transaction.set_watch_set(state.watch_set.clone());
            transaction.set_watch_manifest(state.manifest.clone());
            transaction.set_limit_class(LimitClass {
                sender: state.sender,
                payer: state.payer,
                classification_generation: state.classification_generation,
                sender_locked: state.sender_locked,
                payer_locked: state.payer_locked,
                payer_trusted: state.payer_trusted,
                payer_balance: state.payer_balance,
                max_cost: state.payer_max_cost,
            });
            let outcome = TransactionValidationOutcome::Valid {
                balance: state.payer_balance_after_auth,
                state_nonce: state.sender_nonce,
                transaction: ValidTransaction::new(transaction, None),
                propagate,
                bytecode_hash: state.sender_bytecode_hash,
                authorities: (state.payer != state.sender).then_some(vec![state.payer]),
            };
            return self.apply_base_checks(outcome, state.payer_auth);
        }
```

**File:** crates/execution/txpool/src/validator.rs (L952-953)
```rust
        let outcome = self.inner.validate_one_with_state(origin, transaction, state);
        self.apply_base_checks(outcome, 0)
```

**File:** crates/execution/txpool/src/validator.rs (L988-996)
```rust
    /// Runs full EIP-8130 admission checks that require account/precompile state:
    /// actor authorization, nonce/replay state, intrinsic gas, create-entry safety,
    /// and payer balance. This deliberately bypasses the inner Eth validator for
    /// EIP-8130 because configured senders may be smart contracts and sponsored
    /// transactions charge a payer instead of the sender.
    ///
    /// The `validate_one_with_state` snapshot is only an `AccountInfoReader`; EIP-8130 needs
    /// storage/code reads for account config, nonce channels, and delegation checks, so this path
    /// takes its own full state snapshot.
```

**File:** crates/execution/txpool/src/validator.rs (L1117-1143)
```rust
        if intrinsic.execution_gas_available(signed.tx().gas_limit).is_none() {
            return Err(InvalidTransactionError::GasTooLow.into());
        }

        let payer_account = state
            .basic_account(&payer)
            .map_err(|error| Self::state_read_error(error, "payer account read failed"))?
            .unwrap_or_default();
        FeeCheck::validate_balance(
            payer_account.balance,
            signed.tx().gas_limit,
            intrinsic.payer_auth,
            signed.tx().max_fee_per_gas,
        )
        .map_err(|_| {
            InvalidPoolTransactionError::from(InvalidTransactionError::InsufficientFunds(
                GotExpected {
                    got: payer_account.balance,
                    expected: FeeCheck::max_fee_charge(
                        signed.tx().gas_limit,
                        intrinsic.payer_auth,
                        signed.tx().max_fee_per_gas,
                    ),
                }
                .into(),
            ))
        })?;
```

**File:** crates/common/consensus/src/transaction/eip8130/signed.rs (L200-216)
```rust
    /// Validates static admission rules without node-specific dependencies.
    pub const fn validate_admission_static(
        &self,
        local_chain_id: u64,
    ) -> Result<(), Eip8130StaticError> {
        let tx = self.tx();
        if tx.chain_id != local_chain_id {
            return Err(Eip8130StaticError::ChainIdMismatch);
        }
        if tx.max_fee_per_gas < tx.max_priority_fee_per_gas {
            return Err(Eip8130StaticError::TipAboveFeeCap);
        }
        if tx.gas_limit == 0 || tx.max_fee_per_gas == 0 {
            return Err(Eip8130StaticError::ZeroGasOrFee);
        }
        Ok(())
    }
```

**File:** crates/execution/eip8130/src/fee.rs (L80-92)
```rust
    pub const fn validate_fees(
        max_fee: u128,
        max_priority_fee: u128,
        base_fee: u128,
    ) -> Result<(), FeeError> {
        if max_priority_fee > max_fee {
            return Err(FeeError::TipAboveFeeCap { tip: max_priority_fee, max_fee });
        }
        if max_fee < base_fee {
            return Err(FeeError::FeeCapBelowBaseFee { base_fee, max_fee });
        }
        Ok(())
    }
```
