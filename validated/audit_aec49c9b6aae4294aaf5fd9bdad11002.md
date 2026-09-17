## Title
Txpool L1/operator-fee admission check prices transactions against the current head block's L1 attributes instead of the block they will actually execute in - (File: crates/execution/txpool/src/validator.rs)

## Summary
The Holograph `LayerZeroModule` bug used the *source* chain's `baseGas`/`gasPerByte` to price a message intended for a *different* (destination) chain, causing over/under-pricing and potentially stuck messages. The structurally analogous pattern in this repo is `BaseTransactionValidator`'s mempool admission check: it prices a pending transaction's L1 data fee / operator fee using the `L1BlockInfo` and timestamp captured from the **last seen head block** (`on_new_head_block` → `update_l1_block_info`), while the transaction will actually be charged and executed against the `L1BlockInfo` of **whatever future block it lands in** — a different "chain state" than the one used to price it, just as LZ priced against the wrong chain's config.

## Finding Description
`BaseL1BlockInfo` caches a single `l1_block_info` and `timestamp`, updated only on `on_new_head_block`: [1](#0-0) 

`validate_tx` (used at admission) computes the additional L1 + operator fee cost using this cached, potentially stale, `L1BlockInfo`/`spec` rather than the L1 attributes that will actually be posted in the block where the transaction is eventually included: [2](#0-1) 

The core pricing routine `L1BlockInfo::tx_cost` / `calculate_tx_l1_cost` (and the Isthmus `operator_fee_charge`) is fork- and scalar-sensitive — Bedrock, Ecotone, Fjord and the Isthmus operator fee all use different formulas and different L1 base fee/scalar values: [3](#0-2) 

Because Base's L1 attributes (base fee, blob base fee, scalars, operator-fee scalar/constant) are re-posted by the sequencer in every new L2 block via the `setL1BlockValues`/L1 attributes deposit transaction, these values legitimately change from block to block. The pool's admission check reads a single cached snapshot (`self.block_info`) from the *last known head*, not the values that will be in force at the specific future block the transaction is ultimately sequenced into — mirroring the LZ bug's use of the wrong chain's fee parameters to price a cross-domain operation.

The test suite explicitly documents that under-pricing at admission vs. actual execution charge is a known failure mode the validator is trying to close (for the *known* current-head snapshot), which confirms the validator's fee check is fee-schedule/timestamp dependent and only as accurate as its cached snapshot: [4](#0-3) 

## Impact Explanation
If the L1 base fee, blob base fee, or Isthmus operator-fee scalar/constant increase between the head block used to admit a transaction and the block it is actually included in (both routine and expected on an OP-stack L2, since these values are refreshed every block from L1), a transaction that passed the pool's balance check can become underpriced relative to the fee actually charged at execution. This is the same class of consequence as the audited report: the sender's account may not actually be able to cover `tx_cost + l1_data_fee + operator_fee` by the time of inclusion, causing execution-time failure (`LackOfFundForMaxFee`-style errors) after having been optimistically admitted — funds/nonce state churn and transaction failure that the pool's admission gate was specifically supposed to prevent. Because this is an admission-time economic check rather than a consensus-critical fee derivation (the block executor itself always recomputes the correct in-block L1BlockInfo via `try_fetch`/`extract_l1_info` at execution), this does not corrupt consensus or provable output, but it undermines the very guarantee the check exists to provide (pre-admission solvency), potentially causing sender transactions to be needlessly rejected from blocks or fail on execution despite passing pool admission — a liveness/gas-griefing-adjacent issue for affected senders rather than a protocol-breaking one.

## Likelihood Explanation
L1 base fee and blob base fee move continuously as they track L1; Ecotone/Fjord/Isthmus scalars change less often. Every L2 block re-derives fresh values in `try_fetch`/`extract_l1_info` from the deposit transaction, so any block-to-block delta directly creates a window where the pool's cached snapshot from `on_new_head_block` diverges from the state at actual inclusion. Given block times and mempool dwell time, this window is regularly and unavoidably non-zero, making the divergence a normal occurrence rather than an edge case — though its magnitude is usually small.

## Recommendation
Re-validate (or re-price) the affordability check against the L1 attributes of the block the transaction is about to be included in at build time (not just once at pool admission against the last head), or track and tighten the admission margin to buffer for expected L1 base-fee/scalar drift between admission and inclusion, so a transaction accepted by the pool is not silently underfunded by the time it executes.

## Proof of Concept
Conceptual reproduction (mirrors the existing `rejects_tx_underfunded_for_operator_fee_post_isthmus` test harness):
1. Seed a sender with balance sufficient to cover `tx.cost + l1_data_fee(spec_at_head)` computed from the currently cached `L1BlockInfo` at pool admission (`BaseTransactionValidator::validate_transaction`).
2. Before the transaction is sequenced, advance the L1 attributes (increase `l1_base_fee`/`l1_blob_base_fee`/operator-fee scalar) as would legitimately happen on the next L2 block (simulating normal L1 fee movement), without triggering `on_new_head_block` for the pool (i.e., the transaction sits in the mempool across the change).
3. Include the transaction in the next block; the block executor recomputes `L1BlockInfo::try_fetch` fresh and charges the new, higher L1/operator fee via `L1BlockInfo::tx_cost`.
4. Observe the transaction now costs more than the sender's balance, causing an execution-time insufficient-funds failure despite having passed the pool's earlier admission check — demonstrating that admission-time pricing used stale ("source") fee parameters instead of the fee parameters actually in force ("destination") at inclusion time.

### Citations

**File:** crates/execution/txpool/src/validator.rs (L2148-2154)
```rust
    fn on_new_head_block(&self, new_tip_block: &SealedBlock<Self::Block>) {
        self.inner.on_new_head_block(new_tip_block);
        self.update_l1_block_info(
            new_tip_block.header(),
            new_tip_block.body().transactions().first(),
        );
    }
```

**File:** crates/execution/txpool/src/validator.rs (L3413-3418)
```rust
    ///
    /// Asserts that the txpool affordability check accounts for the post-Isthmus operator fee, so a
    /// sender funded only for `tx.cost + l1_data_fee` (but not the additional operator fee) is
    /// rejected at admission instead of being accepted and later failing during execution with
    /// `LackOfFundForMaxFee`.
    #[tokio::test]
```

**File:** crates/execution/metering/src/transaction.rs (L27-53)
```rust
pub fn validate_tx<T: Transaction + Encodable2718>(
    account: Account,
    txn: &Recovered<T>,
    l1_block_info: &mut L1BlockInfo,
    spec: BaseSpecId,
) -> Result<(), TxValidationError> {
    let data = txn.encoded_2718();

    // For EIP-1559 transactions: `max_fee_per_gas * gas_limit + tx_value`.
    let max_fee = txn.max_fee_per_gas().saturating_mul(txn.gas_limit() as u128);
    let txn_cost = txn.value().saturating_add(U256::from(max_fee));

    // Return error if execution cost exceeds balance
    if txn_cost > account.balance {
        return Err(TxValidationError::InsufficientFundsForTransfer(txn_cost, account.balance));
    }

    // Base-specific checks to see whether the sender can cover the L1 gas cost.
    // Reference: https://github.com/paradigmxyz/reth/blob/6aa73f14808491aae77fc7c6eb4f0aa63bef7e6e/crates/optimism/txpool/src/validator.rs#L219
    let l1_cost_addition = l1_block_info.calculate_tx_l1_cost(&data, spec);
    let l1_cost = txn_cost.saturating_add(l1_cost_addition);
    if l1_cost > account.balance {
        return Err(TxValidationError::InsufficientFundsForL1Gas(l1_cost, account.balance));
    }

    Ok(())
}
```

**File:** crates/common/evm/src/l1block.rs (L279-312)
```rust
    /// Calculate additional transaction cost.
    #[inline]
    pub fn tx_cost(&mut self, enveloped_tx: &[u8], gas_limit: U256, spec: BaseSpecId) -> U256 {
        // compute L1 cost
        let mut additional_cost = self.calculate_tx_l1_cost(enveloped_tx, spec);

        // compute operator fee
        if spec.is_enabled_in(BaseUpgrade::Isthmus) {
            let operator_fee_charge = self.operator_fee_charge(enveloped_tx, gas_limit, spec);
            additional_cost = additional_cost.saturating_add(operator_fee_charge);
        }

        additional_cost
    }

    /// Calculate the gas cost of a transaction based on L1 block data posted on L2, depending on the [`BaseSpecId`] passed.
    pub fn calculate_tx_l1_cost(&mut self, input: &[u8], spec_id: BaseSpecId) -> U256 {
        if let Some(tx_l1_cost) = self.tx_l1_cost {
            return tx_l1_cost;
        }
        // If the input is a deposit transaction or empty, the default value is zero.
        let tx_l1_cost = if input.is_empty() || input.first() == Some(&0x7E) {
            return U256::ZERO;
        } else if spec_id.is_enabled_in(BaseUpgrade::Fjord) {
            self.calculate_tx_l1_cost_fjord(input)
        } else if spec_id.is_enabled_in(BaseUpgrade::Ecotone) {
            self.calculate_tx_l1_cost_ecotone(input, spec_id)
        } else {
            self.calculate_tx_l1_cost_bedrock(input, spec_id)
        };

        self.tx_l1_cost = Some(tx_l1_cost);
        tx_l1_cost
    }
```
