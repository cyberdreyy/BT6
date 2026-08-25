### Title
Integer truncation in `get_instructions_data_cost` allows transactions to under-report cost-model units, weakening block/account cost-limit enforcement - (File: cost-model/src/cost_model.rs)

### Summary
`CostModel::get_instructions_data_cost` computes the "data bytes" cost component of a transaction's cost-model score using plain integer division: `instruction_data_len() / INSTRUCTION_DATA_BYTES_COST`. This mirrors the exact bug class from the external report: an integer division that silently truncates to zero (or to a smaller-than-true value) whenever the numerator is smaller than the denominator, feeding into a downstream aggregate (`TransactionCost::sum()`) that is compared against hard limits (`CostTracker::would_fit`) — analogous to Beedle's truncated `totalDebt` being compared against `maxLoanRatio`.

### Finding Description
`get_instructions_data_cost` is defined as: [1](#0-0) 

It divides the raw instruction data length by `INSTRUCTION_DATA_BYTES_COST` using truncating integer division (no `div_ceil`/round-up), unlike the sibling helper `calculate_pages_for_bytes`, which explicitly rounds up: [2](#0-1) 

This value (`data_bytes_cost`) becomes one component of `TransactionCost`, computed by `calculate_transaction_cost` (called from `calculate_cost`, `calculate_cost_for_executed_transaction`, and `estimate_cost`): [3](#0-2) 

The resulting `TransactionCost::sum()` is what `CostTracker::would_fit` checks against `block_cost`/`account_cost` limits before admitting a transaction into a block: [4](#0-3) 

Because any instruction whose total data length is less than `INSTRUCTION_DATA_BYTES_COST` bytes contributes exactly `0` to the cost sum for this component (truncated down, never rounded up), a user can construct transactions using many small-instruction-data transactions to consume real ledger processing time/bandwidth while being charged less "data bytes" cost than they truly should for the granularity the constant implies. This is the same root cause pattern as the Beedle report: a floor-division feeding a limit-check comparison, silently under-counting instead of reverting or rounding up.

### Impact Explanation
The impact here is bounded and different in severity from the original DeFi bug. In Beedle, precision loss let an attacker bypass a hard collateralization invariant and directly extract value (loan created with insufficient collateral). Here, `data_bytes_cost` is only one minor, ancillary term within `TransactionCost` (alongside `signature_cost`, `write_lock_cost`, `programs_execution_cost`, `loaded_accounts_data_size_cost` — the latter of which correctly rounds up via `calculate_pages_for_bytes`). The truncation at most slightly under-counts the block/account cost budget consumed by small-instruction-data transactions, marginally weakening the accuracy of TPU ingest/block-cost metering rather than causing unauthorized fund movement, consensus divergence, or a hard security-invariant bypass. No fee amount or lamport balance is affected — this is purely a resource-accounting/metering fidelity issue in the cost model.

### Likelihood Explanation
High likelihood of the underlying truncation occurring (any transaction with instruction data smaller than `INSTRUCTION_DATA_BYTES_COST` bytes, which is common), but low likelihood of any measurable exploit value: the magnitude per-transaction is small (a few cost units at most, bounded by `INSTRUCTION_DATA_BYTES_COST`), and the dominant cost terms (`programs_execution_cost`, `write_lock_cost`, `signature_cost`) are unaffected and already gate block capacity. This does not create a consensus-divergence risk (all validators compute the same, deterministic — albeit slightly low — value) nor a crash/DoS vector.

### Recommendation
Change `get_instructions_data_cost` to round up (mirroring `calculate_pages_for_bytes`'s `div_ceil` pattern) so that any non-zero instruction data always contributes at least 1 unit of cost, ensuring the cost model never under-counts:
```rust
fn get_instructions_data_cost(transaction: &impl TransactionMeta) -> u16 {
    let len = transaction.instruction_data_len();
    len.div_ceil(INSTRUCTION_DATA_BYTES_COST as u16)
}
```
This aligns the behavior with the existing rounding convention already used for `loaded_accounts_data_size_cost` in the same file.

### Proof of Concept
Not applicable as an exploitable PoC — this is a metering-accuracy defect, not a fund-safety or consensus-safety bug. Demonstration is simply: for any transaction whose total `instruction_data_len()` is less than `INSTRUCTION_DATA_BYTES_COST` (verifiable via the constant in `cost-model/src/block_cost_limits.rs`, not fully inspected due to index scope), `get_instructions_data_cost` returns `0`, contributing nothing to `TransactionCost::sum()` used by `CostTracker::would_fit`, even though the instruction did carry (small) data that should be metered.

**Note on completeness:** I was unable to fully inspect `cost-model/src/block_cost_limits.rs` (specifically the exact value of `INSTRUCTION_DATA_BYTES_COST`) and `cost-model/src/transaction_cost.rs`'s `TransactionCost::sum()` implementation within the available index results. Given the low severity conclusion already reached, this does not change the assessment, but a background Devin session with full repository access could confirm the exact constant value and weighting of `data_bytes_cost` within `sum()` if further precision is required.

### Citations

**File:** cost-model/src/cost_model.rs (L103-127)
```rust
    fn calculate_transaction_cost<'a, Tx: TransactionMeta>(
        transaction: &'a Tx,
        instructions: impl Iterator<Item = (&'a Pubkey, SVMInstruction<'a>)>,
        num_write_locks: u64,
        programs_execution_cost: u64,
        loaded_accounts_data_size_cost: u64,
        data_bytes_cost: u16,
        feature_set: &FeatureSet,
    ) -> TransactionCost<'a, Tx> {
        let signature_cost = Self::get_signature_cost(transaction);
        let write_lock_cost = Self::get_write_lock_cost(num_write_locks);

        let allocated_accounts_data_size =
            Self::calculate_allocated_accounts_data_size(instructions, feature_set);

        TransactionCost {
            transaction,
            signature_cost,
            write_lock_cost,
            data_bytes_cost,
            programs_execution_cost,
            loaded_accounts_data_size_cost,
            allocated_accounts_data_size,
        }
    }
```

**File:** cost-model/src/cost_model.rs (L180-183)
```rust
    /// Return the instruction data bytes cost.
    fn get_instructions_data_cost(transaction: &impl TransactionMeta) -> u16 {
        transaction.instruction_data_len() / (INSTRUCTION_DATA_BYTES_COST as u16)
    }
```

**File:** cost-model/src/cost_model.rs (L185-190)
```rust
    /// Compute the number of pages needed to contain provided number of bytes.
    fn calculate_pages_for_bytes(bytes: u32) -> u64 {
        u64::from(bytes)
            .saturating_add(ACCOUNT_DATA_COST_PAGE_SIZE.saturating_sub(1))
            .saturating_div(ACCOUNT_DATA_COST_PAGE_SIZE)
    }
```

**File:** cost-model/src/cost_tracker.rs (L272-309)
```rust
    fn would_fit(
        &self,
        tx_cost: &TransactionCost<impl TransactionWithMeta>,
    ) -> Result<(), CostTrackerError> {
        let cost: u64 = tx_cost.sum();

        if self.block_cost().saturating_add(cost) > self.limits.block_cost {
            // check against the total package cost
            return Err(CostTrackerError::WouldExceedBlockMaxLimit);
        }

        // check if the transaction itself is more costly than the account_cost_limit
        if cost > self.limits.account_cost {
            return Err(CostTrackerError::WouldExceedAccountMaxLimit);
        }

        let allocated_accounts_data_size =
            self.allocated_accounts_data_size + Saturating(tx_cost.allocated_accounts_data_size());

        if allocated_accounts_data_size.0 > self.limits.allocated_data_size {
            return Err(CostTrackerError::WouldExceedAccountDataBlockLimit);
        }

        // check each account against account_cost_limit,
        for account_key in tx_cost.writable_accounts() {
            match self.cost_by_writable_accounts.get(account_key) {
                Some(chained_cost) => {
                    if chained_cost.saturating_add(cost) > self.limits.account_cost {
                        return Err(CostTrackerError::WouldExceedAccountMaxLimit);
                    } else {
                        continue;
                    }
                }
                None => continue,
            }
        }

        Ok(())
```
