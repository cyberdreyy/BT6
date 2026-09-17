## Title
Unprivileged L1 depositor can set a deposit's `gas_limit` above the derived L2 block's available gas, turning a protocol‑mandated deposit into a hard execution error instead of a "failed deposit" — analogous to the tBTC SPV/gas-limit bug ([File: crates/common/evm2/src/executor.rs])

### Summary
The tBTC finding is that a protocol which must embed unbounded, user‑controlled data into a gas‑limited verification path can be broken simply because the block gas limit is finite: a legitimate user action becomes unprovable/unexecutable, and funds/finality is lost. Base has a structurally similar boundary: an L1 depositor unilaterally chooses the `gas_limit` field of an `OptimismPortal` deposit, and that value is later replayed verbatim as the `TxDeposit.gas_limit` of a **mandatory** transaction in a derived L2 block. Unlike ordinary mempool transactions, deposits are never filtered for fit against the block gas budget before being placed into `BasePayloadAttributes`; the check only happens at execution time, where it now returns a hard error rather than the OP‑stack‑mandated "failed deposit" outcome.

### Finding Description
Deposits are derived from L1 receipts and pushed unconditionally into the transaction list of the next L2 block: [1](#0-0) [2](#0-1) 

Nothing in this path checks whether an individual deposit's `gas_limit` fits inside the L2 block's gas limit before it is included — contrast this with the mempool transaction path, which explicitly pre-filters via `ExecutionInfo::is_tx_over_limits` before ever calling into execution: [3](#0-2) 

When the derived block is actually executed, `execute_transaction` reserves the declared `gas_limit` against remaining block gas and, for **Regolith‑and‑later** deposits, treats an over-budget deposit as a hard error rather than routing it into `handle_deposit`'s graceful "failed deposit" path: [4](#0-3) 

This is confirmed by an explicit unit test asserting the error (not a settled/failed-deposit `Ok`): [5](#0-4) 

By the OP-stack deposit invariant documented directly in this codebase, deposits are supposed to *never* fail fatally — only a database error is fatal, everything else settles as a "failed deposit" (mint/nonce retained, call reverted): [6](#0-5) 

But `BlockGasLimitExceeded` is raised **before** `handle_deposit` is ever invoked, at the pre-execution gas-reservation check in `execute_transaction`, so a deposit whose `gas_limit` exceeds the block's available gas short-circuits into a hard `HandlerError` instead of reaching the deposit-specific fail-soft logic. Because deposit inclusion is not optional (it is mandated by the derivation pipeline / `no_tx_pool=true` attributes), this is exactly the "Strict derived-attributes / no_tx_pool=true path" halt/stall class the repository's own review guide calls out as **Critical**: [7](#0-6) 

### Impact Explanation
Just as an oversized Bitcoin transaction can never be SPV-proven on Ethereum once it exceeds the effective gas budget for the verification call, a deposit whose `gas_limit` exceeds the L2 block's remaining/available gas can never be executed under the current handler logic — it does not settle as a "failed deposit" (an outcome the design explicitly guarantees), it instead raises a hard error at exactly the moment the block/payload containing it is built or re-executed. Because deposit inclusion in the derived attributes is mandatory and not subject to the resource-fit filtering that ordinary mempool transactions get, this can propagate as a fatal error while building or validating a payload that must contain this deposit, which the codebase's own block-production hazard taxonomy classifies as capable of halting block production or causing sequencer/validator divergence — a node halt / chain-split class impact, not merely a lost individual deposit.

### Likelihood Explanation
Any unprivileged L1 account can call the deposit path (`OptimismPortal.depositTransaction`-equivalent) and freely choose the `gas_limit` field encoded into `TxDeposit`; this is a single, unprivileged, permissionless transaction, matching the report's required reachability bar ("a depositor... single signed transaction"). The condition is triggered whenever a deposit's declared `gas_limit` is not comfortably smaller than the L2 block's available gas at the time of inclusion (e.g., a large but not-obviously-malicious deposit gas request, or a subsequent reduction of the L2 `SystemConfig.gas_limit` relative to what the L1-side resource-metering allowed at deposit time) — a state that legitimate use, not adversarial sophistication, can reach.

### Recommendation
- Route any deposit whose reserved gas exceeds the block's available gas through the existing "failed deposit" settlement path (`Self::failed_deposit(tx)`) rather than surfacing `BlockGasLimitExceeded` as a hard `HandlerError`, consistent with the documented invariant that deposits never fail fatally.
- Alternatively/additionally, cap or clamp an over-budget deposit's effective gas at block-build time in the attributes builder (`stateful.rs`) so a derived block is never constructed with a deposit that cannot execute under the active `SystemConfig.gas_limit`.
- Add boundary/regression tests exercising a deposit `gas_limit` that exceeds the *current* block gas limit specifically through the derivation → payload-attributes → execution pipeline (not just direct executor unit tests), to verify the derived payload still finalizes and the deposit settles as failed instead of aborting block building/validation.

### Proof of Concept
1. An unprivileged L1 account submits a deposit transaction (e.g., via the L1 deposit contract) with `gas_limit` set close to or above the current L2 `SystemConfig.gas_limit` (or a value that was valid at submission time but becomes larger than the L2 block's *available* remaining gas once earlier deposits/upgrade txs in the same block consume some of the budget).
2. `derive_deposits` picks up the `TransactionDeposited` log and the attributes builder unconditionally appends the resulting `TxDeposit` to the derived block's transaction list — see `crates/consensus/derive/src/attributes/stateful.rs:129-146,220-239` — with no pre-check against `SystemConfig.gas_limit`.
3. When the sequencer/builder or a verifying node executes this derived block, `execute_transaction` (`crates/common/evm2/src/executor.rs:252-277`) computes `reserved_gas = tx.gas_limit()` and, because the deposit is post-Regolith, compares it against `block_available_gas`; when it exceeds that budget it returns `Err(HandlerError::external(BlockGasLimitExceeded))` instead of calling `handle_deposit`.
4. This matches the asserted behavior of `rejects_post_regolith_deposit_over_block_gas_limit` in `crates/common/evm2/tests/block_execution.rs:167-181`, which explicitly expects an `Err` for exactly this scenario — confirming the hard-failure path is reachable with nothing more than a single unprivileged, correctly-formed L1 deposit.

### Citations

**File:** crates/consensus/derive/src/attributes/stateful.rs (L129-146)
```rust
            let deposits =
                derive_deposits(epoch.hash, &receipts, self.rollup_cfg.deposit_contract_address)
                    .await
                    .map_err(|e| PipelineError::BadEncoding(e).crit())?;
            let (updates, errors) = sys_config.update_with_receipts(
                &receipts,
                self.rollup_cfg.l1_system_config_address,
                self.rollup_cfg.is_ecotone_active(header.timestamp),
            );
            for kind in &updates {
                info!(target: "attributes", epoch = epoch.number, %kind, "Applied system config update");
            }
            for err in &errors {
                warn!(target: "attributes", error = ?err, epoch = epoch.number, "Malformed system config update (skipped)");
            }
            l1_header = header;
            deposit_transactions = deposits;
            0
```

**File:** crates/consensus/derive/src/attributes/stateful.rs (L220-239)
```rust
        let base_time_active = self.rollup_cfg.is_denim_active(target_l2_time);
        let mut txs = Vec::with_capacity(
            1 + usize::from(base_time_active)
                + deposit_transactions.len()
                + upgrade_transactions.len(),
        );
        txs.push(encoded_l1_info_tx.into());

        if base_time_active {
            let base_time = BaseTimeUpdateTx::new(target_l2_millis).map_err(|e| {
                PipelineError::AttributesBuilder(BuilderError::BaseTimeUpdate(e)).crit()
            })?;
            let envelope = base_time.into_deposit_tx(target_l2_number);
            let mut encoded = Vec::with_capacity(envelope.length());
            envelope.encode_2718(&mut encoded);
            txs.push(encoded.into());
        }

        txs.extend(deposit_transactions);
        txs.extend(upgrade_transactions);
```

**File:** crates/execution/payload/src/builder.rs (L1300-1313)
```rust
            if info.is_tx_over_limits(
                tx_da_size,
                block_gas_limit,
                tx_da_limit,
                block_da_limit,
                tx.gas_limit().saturating_add(tx_payer_auth),
                da_footprint_gas_scalar,
            ) {
                // we can't fit this transaction into the block, so we need to mark it as
                // invalid which also removes all dependent transaction from
                // the iterator before we can continue
                Self::skip_current(&mut best_txs, tx.signer(), tx.nonce(), replay_independent);
                continue;
            }
```

**File:** crates/common/evm2/src/executor.rs (L252-277)
```rust
    pub fn execute_transaction(&mut self, tx: &Recovered<BaseTxEnvelope>) -> HandlerResult<()> {
        let ty = tx.ty();
        let is_deposit = tx.is_deposit();
        let signer = tx.signer();

        // Reject a transaction whose reserved gas exceeds the block's remaining gas, before
        // executing it. For an EIP-8130 transaction the reservation adds the payer's worst-case
        // authentication gas (metered on top of the sender-signed gas limit), matching the
        // reference `reserved_block_gas`. Pre-Regolith deposits are exempt (matching the reference's
        // `is_regolith || !is_deposit` guard); every other transaction is checked.
        let mut reserved_gas = tx.gas_limit();
        if let Some(signed) = tx.as_eip8130() {
            let payer_auth =
                IntrinsicGas::max_payer_auth_cost(signed).map_err(HandlerError::external)?;
            reserved_gas = reserved_gas.saturating_add(payer_auth);
        }
        let block_gas_limit = self.evm.block().gas_limit.saturating_to::<u64>();
        let block_available_gas = block_gas_limit.saturating_sub(self.gas_used);
        let is_regolith =
            (self.evm.config_spec_id().upgrade() as u8) >= (BaseUpgrade::Regolith as u8);
        if reserved_gas > block_available_gas && (is_regolith || !is_deposit) {
            return Err(HandlerError::external(BlockGasLimitExceeded {
                transaction_gas_limit: reserved_gas,
                block_available_gas,
            }));
        }
```

**File:** crates/common/evm2/tests/block_execution.rs (L167-181)
```rust
#[test]
fn rejects_post_regolith_deposit_over_block_gas_limit() {
    // Post-Regolith deposits ARE subject to the block-gas check.
    let mut executor =
        executor_with_block_gas_limit(BaseSpecId::new(BaseUpgrade::Regolith), 50_000);
    let deposit = TxDeposit {
        from: SENDER,
        to: TxKind::Call(TARGET),
        gas_limit: 100_000,
        ..Default::default()
    };
    executor
        .execute_transaction(&Recovered::new_unchecked(BaseTxEnvelope::Deposit(deposit), SENDER))
        .expect_err("post-Regolith deposit over the block gas limit is rejected");
}
```

**File:** crates/common/evm2/src/registry.rs (L96-115)
```rust
        let mut tx_gas =
            GasTracker::new_with_execution_gas_and_reservoir(execution_gas_limit, reservoir);
        // Deposits cannot fail fatally per the OP-stack spec (the revm reference catches every
        // transaction-level error in `catch_error` and returns a failed deposit); only a database
        // error is genuinely fatal. `prepare_initial_frame` only produces `Fatal` errors today,
        // but matching on the variant future-proofs against evm2 introducing typed frame errors:
        // a `Fatal` propagates, anything else settles as a failed deposit.
        let frame = match prepare_initial_frame(
            host,
            tx.from,
            nonce,
            tx.to,
            &tx.input,
            tx.value,
            &mut tx_gas,
        ) {
            Ok(frame) => frame,
            Err(err @ HandlerError::Fatal(_)) => return Err(err),
            Err(_) => return Ok(Self::failed_deposit(tx)),
        };
```

**File:** docs/guides/BLOCK_PRODUCTION_REVIEW.md (L31-31)
```markdown
| Strict derived-attributes / `no_tx_pool=true` path | Derived payload attributes contain invalid transactions, blob transactions, unrecoverable transactions, deposit account load failures, or pre-execution failures. | `execute_pre_steps`, `execute_sequencer_transactions`, payload attributes, deposit nonce loading, blob transaction checks. | `BlobTransactionRejected`, `TransactionEcRecoverFailed`, `AccountLoadFailed`, `EvmExecutionError`. | EL rejects the derived payload; wrong changes can either halt sync/building or diverge from proof-executor behavior. | Do not silently skip consensus-derived invalid input unless proof-executor parity is explicitly preserved. Divergence is critical. | Tests covering `no_tx_pool=true` and `no_tx_pool=false` behavior for the same invalid input; proof-exec ... (truncated)
```
