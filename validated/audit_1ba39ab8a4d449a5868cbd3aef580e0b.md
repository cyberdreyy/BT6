## Assessment

Confirmed via code: `MAX_ACCOUNT_DATA_GROWTH_PER_TRANSACTION` is `20 MiB` per transaction [1](#0-0) , and this is what a single transaction can actually grow account data by regardless of whether growth happens via a top-level `system_instruction` or via a BPF program's CPI into `system_program::allocate`/`create_account` with a runtime-computed size — the enforcement point `can_data_be_resized`/`update_accounts_resize_delta` operates uniformly on all account writes, top-level or CPI. [2](#0-1) 

However, the block-level cap `MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA` (100,000,000 bytes) is enforced exclusively through `CostTracker::would_fit`/`try_add`, using `tx_cost.allocated_accounts_data_size()`. [3](#0-2) [4](#0-3) 

That value comes from `CostModel::calculate_allocated_accounts_data_size`, which only inspects the **top-level** `program_instructions_iter()` of the transaction and statically deserializes `SystemInstruction::{CreateAccount, Allocate, AllocateWithSeed, CreateAccountWithSeed}` calls made **directly** by the transaction to `system_program::id()`. [5](#0-4)  Any allocation performed via CPI from a user's own BPF program (`invoke()`'d `system_instruction::allocate`, whether with a static or runtime-computed size) is invisible to this function, because it never inspects inner-instruction data — only the outer transaction's compiled instructions.

Critically, this same static estimate is also used for the "actual" post-execution cost fed to `check_block_cost_limits` / `try_add_processed_transaction_costs`, both in the banking-stage leader path and in the replay path (feature `apply_cost_tracker_during_replay`) [6](#0-5) : `calculate_cost_for_executed_transaction` still routes through `calculate_transaction_cost` → `calculate_allocated_accounts_data_size(transaction.program_instructions_iter(), ...)` [7](#0-6)  — it does **not** use the real `accounts_resize_delta` captured in `AccountsDeltas` from actual execution. [8](#0-7) [9](#0-8) 

Meanwhile the real, execution-derived `accounts_resize_delta` is only accumulated into `bank.accounts_data_size_delta_on_chain`, a separate unbounded-by-block-cap counter used for rent/total-size bookkeeping [10](#0-9)  — it is never checked against `MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA`.

Because `can_data_be_resized` still bounds *each transaction* to 20 MiB of growth, and this bound applies deterministically the same way for every validator during replay, there is no consensus divergence (no bank-hash mismatch risk) — every validator computes the same (wrong, i.e. zero) "allocated_accounts_data_size" for CPI-driven growth, so the check remains deterministic even though it's ineffective. The vulnerability is therefore a resource-exhaustion / metering-bypass issue rather than a consensus-safety one.

### Title
Cost model's block accounts-data-size cap (MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA) is bypassed for account growth performed via CPI to system_program - (File: cost-model/src/cost_model.rs, cost-model/src/cost_tracker.rs)

### Summary
`CostModel::calculate_allocated_accounts_data_size` only parses the transaction's top-level instructions for `system_program` account-creation/allocation calls; it never sees allocations performed via CPI from a BPF program. `CostTracker::would_fit`/`try_add` uses this value to enforce `MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA`, so any account-data growth driven through a CPI to `system_instruction::allocate`/`create_account` (static or runtime-computed size) is counted as zero against the per-block data-growth budget, both at block-production time and at replay time (with `apply_cost_tracker_during_replay`).

### Finding Description
`calculate_allocated_accounts_data_size` iterates `transaction.program_instructions_iter()` (top-level instructions only) and for each instruction whose `program_id == system_program::id()` deserializes it as a `SystemInstruction` to extract a `space` field. [5](#0-4)  A BPF program that itself CPIs into `system_program` (e.g. via `invoke()` calling `system_instruction::allocate`) never appears as a top-level instruction with `system_program::id()`; the outer instruction's `program_id` is the attacker's own program. Consequently the function returns `0` contribution for that allocation regardless of how large the CPI-driven allocation is or whether its size argument is static or computed at runtime from account state — the "runtime-computed size" detail in the question is not actually required for the bypass; any CPI-based allocation already evades the estimator.

`CostTracker::would_fit` uses this estimate to decide admission against `MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA` (100 MB/block). [3](#0-2) [4](#0-3)  The same estimator is reused for the "actual" post-execution cost recorded via `calculate_cost_for_executed_transaction`, invoked from both the banking-stage commit path (`check_block_cost_limits`) and the replay path. [11](#0-10) [7](#0-6)  Neither call site substitutes the real `accounts_resize_delta` recorded in `AccountsDeltas` from execution. [8](#0-7) 

Each individual transaction is still capped at 20 MiB of real growth by `can_data_be_resized`/`update_accounts_resize_delta` in `TransactionAccounts`. [2](#0-1) [1](#0-0)  But that per-transaction limit is unrelated to, and much larger than, the intended per-block budget from the cost model, and since the cost-model contribution for CPI allocations is always 0, an attacker can submit many transactions (bounded only by compute-unit/account-lock/write-lock block limits, not by the accounts-data-size limit) each growing up to ~20 MiB via CPI, in aggregate vastly exceeding the intended 100 MB/block cap while the `CostTracker::allocated_accounts_data_size` counter stays at (or near) zero.

### Impact Explanation
This is an unmetered-execution / block-level resource-exhaustion bug (METERING + REPLAY_LIVENESS categories): the deliberate block-level cap meant to bound accounts-db growth per block (`MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA`) can be circumvented entirely for CPI-triggered growth, letting a leader (or an unprivileged sender whose transactions a leader includes) pack a block whose real on-chain data growth is far above the intended 100 MB/slot design limit, increasing accounts-db disk/memory growth per block across all replaying validators. Because the same deterministic (and wrong) estimate is used on all validators, there is no bank-hash divergence — this is a resource-exhaustion/DoS-adjacent issue, not a consensus-safety break.

### Likelihood Explanation
Fully reachable by an unprivileged attacker: deploy any BPF program that calls `invoke(system_instruction::allocate(...))` (size can be a compile-time constant or runtime value — no special precondition needed), fund a keypair, and submit many such transactions. No feature gate, no privileged actor, and no dependency bug is required — this is a structural gap in `calculate_allocated_accounts_data_size`'s static top-level-only inspection design (the code's own comment "eventually, potentially determine account data size of all writable accounts / at the moment, calculate account data size of account creation" acknowledges the estimate is intentionally incomplete). [12](#0-11)  Repeatable indefinitely by submitting further transactions each block.

### Recommendation
Stop relying on the static top-level-instruction estimate for the "actual" post-execution cost. For `calculate_cost_for_executed_transaction`/`check_block_cost_limits`, use the real `AccountsDeltas::accounts_resize_delta` (already captured from execution) instead of re-deriving `allocated_accounts_data_size` from `program_instructions_iter()`, so that CPI-driven growth is correctly counted against `MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA` post-execution (the pre-execution estimate used for scheduling/admission can remain a heuristic, but the value used to enforce the hard block cap after execution must reflect real growth).

### Proof of Concept
1. Deploy program `X` containing: `invoke(&system_instruction::allocate(&target, SIZE), accounts)` where `SIZE` can be any value (e.g., `MAX_ACCOUNT_DATA_GROWTH_PER_TRANSACTION` minus overhead, ~20MB, or a runtime-computed value read from an attacker account).
2. Submit N such transactions (each creating a fresh `target` account) in one bank/slot.
3. Rust integration test sketch (using `svm/tests/integration_test.rs` harness style):
```rust
// build N transactions each invoking program X's allocate-via-CPI instruction
let tx_cost = CostModel::calculate_cost(&tx, &feature_set);
assert_eq!(tx_cost.allocated_accounts_data_size(), 0); // static estimate sees nothing

let commit_results = bank.process_transaction_batch(&transactions);
let accounts_data_size_delta_before = bank.load_accounts_data_size_delta_on_chain();
// ... after committing all N transactions ...
let accounts_data_size_delta_after = bank.load_accounts_data_size_delta_on_chain();

// real growth vastly exceeds MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA
assert!(accounts_data_size_delta_after - accounts_data_size_delta_before > MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA as i64);
// yet CostTracker never rejected any transaction on WouldExceedAccountDataBlockLimit
assert_eq!(bank.read_cost_tracker().unwrap().get_allocated_data_size_limit(), MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA); // unchanged/never hit
```
Expected assertion failure demonstrating the bypass: the cost tracker's `allocated_accounts_data_size` accumulator stays near 0 across all N transactions (never triggers `CostTrackerError::WouldExceedAccountDataBlockLimit`), while `bank.load_accounts_data_size_delta_on_chain()` shows real growth exceeding `MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA`.

### Citations

**File:** transaction-context/src/lib.rs (L19-24)
```rust
pub const MAX_ACCOUNT_DATA_LEN: u64 = 10 * 1024 * 1024;
// Note: With virtual_address_space_adjustments programs can grow accounts
// faster than they intend to, because the AccessViolationHandler might grow
// an account up to MAX_ACCOUNT_DATA_GROWTH_PER_INSTRUCTION at once.
pub const MAX_ACCOUNT_DATA_GROWTH_PER_TRANSACTION: i64 = MAX_ACCOUNT_DATA_LEN as i64 * 2;
pub const MAX_ACCOUNT_DATA_GROWTH_PER_INSTRUCTION: usize = 10 * 1_024;
```

**File:** transaction-context/src/transaction_accounts.rs (L297-326)
```rust
    pub(crate) fn update_accounts_resize_delta(
        &self,
        old_len: usize,
        new_len: usize,
    ) -> Result<(), InstructionError> {
        let accounts_resize_delta = self.resize_delta.get();
        self.resize_delta.set(
            accounts_resize_delta.saturating_add((new_len as i64).saturating_sub(old_len as i64)),
        );
        Ok(())
    }

    pub(crate) fn can_data_be_resized(
        &self,
        old_len: usize,
        new_len: usize,
    ) -> Result<(), InstructionError> {
        // The new length can not exceed the maximum permitted length
        if new_len > MAX_ACCOUNT_DATA_LEN as usize {
            return Err(InstructionError::InvalidRealloc);
        }
        // The resize can not exceed the per-transaction maximum
        let length_delta = (new_len as i64).saturating_sub(old_len as i64);
        if self.resize_delta.get().saturating_add(length_delta)
            > MAX_ACCOUNT_DATA_GROWTH_PER_TRANSACTION
        {
            return Err(InstructionError::MaxAccountsDataAllocationsExceeded);
        }
        Ok(())
    }
```

**File:** cost-model/src/cost_tracker.rs (L272-293)
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
```

**File:** cost-model/src/block_cost_limits.rs (L35-37)
```rust
/// The maximum allowed size, in bytes, that accounts data can grow, per block.
/// This can also be thought of as the maximum size of new allocations per block.
pub const MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA: u64 = 100_000_000;
```

**File:** cost-model/src/cost_model.rs (L54-77)
```rust
    // Calculate executed transaction CU cost, with actual execution and loaded accounts size
    // costs.
    pub fn calculate_cost_for_executed_transaction<'a, Tx: TransactionMeta + SVMStaticMessage>(
        transaction: &'a Tx,
        actual_programs_execution_cost: u64,
        actual_loaded_accounts_data_size_bytes: u32,
        feature_set: &FeatureSet,
    ) -> TransactionCost<'a, Tx> {
        let loaded_accounts_data_size_cost = Self::calculate_loaded_accounts_data_size_cost(
            actual_loaded_accounts_data_size_bytes,
            feature_set,
        );
        let instructions_data_cost = Self::get_instructions_data_cost(transaction);

        Self::calculate_transaction_cost(
            transaction,
            transaction.program_instructions_iter(),
            transaction.num_write_locks(),
            actual_programs_execution_cost,
            loaded_accounts_data_size_cost,
            instructions_data_cost,
            feature_set,
        )
    }
```

**File:** cost-model/src/cost_model.rs (L242-301)
```rust
    fn calculate_account_data_size_on_instruction(
        program_id: &Pubkey,
        instruction: SVMInstruction,
        feature_set: &FeatureSet,
    ) -> SystemProgramAccountAllocation {
        if program_id == &system_program::id() {
            if let Ok(instruction) =
                limited_deserialize(instruction.data, solana_packet::PACKET_DATA_SIZE as u64)
            {
                Self::calculate_account_data_size_on_deserialized_system_instruction(
                    instruction,
                    feature_set,
                )
            } else {
                SystemProgramAccountAllocation::Failed
            }
        } else {
            SystemProgramAccountAllocation::None
        }
    }

    /// eventually, potentially determine account data size of all writable accounts
    /// at the moment, calculate account data size of account creation
    fn calculate_allocated_accounts_data_size<'a>(
        instructions: impl Iterator<Item = (&'a Pubkey, SVMInstruction<'a>)>,
        feature_set: &FeatureSet,
    ) -> u64 {
        let mut tx_attempted_allocation_size = Saturating(0u64);
        for (program_id, instruction) in instructions {
            match Self::calculate_account_data_size_on_instruction(
                program_id,
                instruction,
                feature_set,
            ) {
                SystemProgramAccountAllocation::Failed => {
                    // If any system program instructions can be statically
                    // determined to fail, no allocations will actually be
                    // persisted by the transaction. So return 0 here so that no
                    // account allocation budget is used for this failed
                    // transaction.
                    return 0;
                }
                SystemProgramAccountAllocation::None => continue,
                SystemProgramAccountAllocation::Some(ix_attempted_allocation_size) => {
                    tx_attempted_allocation_size += ix_attempted_allocation_size;
                }
            }
        }

        // The runtime prevents transactions from allocating too much account
        // data so clamp the attempted allocation size to the max amount.
        //
        // Note that if there are any custom bpf instructions in the transaction
        // it's tricky to know whether a newly allocated account will be freed
        // or not during an intermediate instruction in the transaction so we
        // shouldn't assume that a large sum of allocations will necessarily
        // lead to transaction failure.
        (MAX_PERMITTED_ACCOUNTS_DATA_ALLOCATIONS_PER_TRANSACTION as u64)
            .min(tx_attempted_allocation_size.0)
    }
```

**File:** feature-set/src/lib.rs (L2051-2054)
```rust
        (
            apply_cost_tracker_during_replay::id(),
            "apply cost tracker to blocks during replay #29595",
        ),
```

**File:** svm/src/transaction_execution_result.rs (L48-54)
```rust
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct AccountsDeltas {
    /// aggregate resize delta across all accounts touched by the transaction
    pub accounts_resize_delta: i64,
    /// aggregate size of all accounts that were uninitialized by this transaction
    pub accounts_uninitialized_size: u64,
}
```

**File:** runtime/src/transaction_execution.rs (L157-195)
```rust
fn check_block_cost_limits<Tx: TransactionWithMeta>(
    bank: &Bank,
    tx_costs: &[Option<TransactionCost<'_, Tx>>],
) -> TransactionResult<()> {
    let mut cost_tracker = bank.write_cost_tracker().unwrap();
    for tx_cost in tx_costs.iter().flatten() {
        cost_tracker
            .try_add(tx_cost)
            .map_err(TransactionError::from)?;
    }

    Ok(())
}

// Get actual transaction execution costs from transaction commit results
fn get_transaction_costs<'a, Tx: TransactionWithMeta>(
    bank: &Bank,
    commit_results: &[TransactionCommitResult],
    sanitized_transactions: &'a [Tx],
) -> Vec<Option<TransactionCost<'a, Tx>>> {
    assert_eq!(sanitized_transactions.len(), commit_results.len());

    commit_results
        .iter()
        .zip(sanitized_transactions)
        .map(|(commit_result, tx)| {
            if let Ok(committed_tx) = commit_result {
                Some(CostModel::calculate_cost_for_executed_transaction(
                    tx,
                    committed_tx.executed_units,
                    committed_tx.loaded_account_stats.loaded_accounts_data_size,
                    &bank.feature_set,
                ))
            } else {
                None
            }
        })
        .collect()
}
```

**File:** runtime/src/bank.rs (L4419-4430)
```rust
        let accounts_data_len_delta = processing_results
            .iter()
            .filter_map(|processing_result| processing_result.processed_transaction())
            .filter_map(|processed_tx| processed_tx.execution_details())
            .filter_map(|details| details.accounts_deltas.as_ref())
            .map(|deltas| {
                deltas
                    .accounts_resize_delta
                    .saturating_sub_unsigned(deltas.accounts_uninitialized_size)
            })
            .sum();
        self.update_accounts_data_size_delta_on_chain(accounts_data_len_delta);
```
