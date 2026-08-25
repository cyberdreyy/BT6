## Title
Cost model's `allocated_accounts_data_size` estimation only counts top-level `SystemInstruction` allocations, undercounting CPI-driven account-data growth and weakening `CostTracker`'s per-block data-size guard - (File: cost-model/src/cost_model.rs)

## Summary
`CostModel::calculate_allocated_accounts_data_size` derives the `allocated_accounts_data_size` field used by `CostTracker::would_fit`'s `WouldExceedAccountDataBlockLimit` check solely from `transaction.program_instructions_iter()`, which yields only the transaction's top-level compiled instructions, not instructions invoked via CPI. A program that performs `Allocate`/`CreateAccount` via a cross-program invocation to the system program is therefore invisible to this accounting, both before execution (`calculate_cost`) and after execution (`calculate_cost_for_executed_transaction`), since both paths feed the same top-level-only iterator into `calculate_allocated_accounts_data_size`.

## Finding Description
`CostModel::calculate_cost` (cost-model/src/cost_model.rs:36-52) and `CostModel::calculate_cost_for_executed_transaction` (lines 56-77) both call `calculate_transaction_cost` with `transaction.program_instructions_iter()` as the instruction source [1](#0-0) [2](#0-1) . That iterator is then scanned by `calculate_allocated_accounts_data_size`, which pattern-matches only `SystemInstruction::CreateAccount/CreateAccountWithSeed/Allocate/AllocateWithSeed/CreateAccountAllowPrefund` on instructions whose top-level `program_id` equals `system_program::id()` [3](#0-2) [4](#0-3) . Notably, even `calculate_cost_for_executed_transaction`, which is meant to reflect the *actual* execution costs, does not source `allocated_accounts_data_size` from any post-execution/actual account-growth signal (e.g. `processed_tx.loaded_accounts_data_size()`); it re-derives it from the same static, top-level-only iterator. The function's own doc comment concedes the limitation: "eventually, potentially determine account data size of all writable accounts / at the moment, calculate account data size of account creation" (lines 263-264) [5](#0-4) .

Consequently, an attacker who deploys an SBPF program that CPIs into the system program's `Allocate`/`CreateAccount` (or invokes another program that does so, or grows account data through a program's own `realloc`-based CPI pattern) produces a transaction whose top-level instruction list contains no system-program allocation instruction. `CostModel::calculate_cost` will therefore compute `allocated_accounts_data_size = 0` for that transaction regardless of how much data the CPI actually allocates, and `CostTracker::would_fit` (cost-model/src/cost_tracker.rs:288-293) will never trip `WouldExceedAccountDataBlockLimit` for it [6](#0-5) . `CostTracker::add_transaction_cost` then also adds this same understated (zero) value to `self.allocated_accounts_data_size` (cost-model/src/cost_tracker.rs:314), so the block-level running total never reflects the CPI-driven growth [7](#0-6) .

## Impact Explanation
This falls under the METERING / VALUE_ACCOUNTING invariant: `CostTracker`'s `allocated_accounts_data_size` limit exists specifically to bound the total per-block account-data growth for anti-spam/anti-bloat purposes (see `limits.allocated_data_size`, block_cost_limits). By routing allocations through CPI, an attacker can cause real account-data growth (still hard-capped per-transaction at the VM/runtime level by `MAX_PERMITTED_ACCOUNTS_DATA_ALLOCATIONS_PER_TRANSACTION`, but not capped at all in the leader's per-block heuristic) that is never reflected in the cost-tracking ledger. Packing many such transactions into one block/window lets the actual accounts-data growth in a slot exceed the intended `allocated_data_size` block ceiling, i.e., unmetered account-data growth that silently bypasses the block-level allocation-limit guard. Because this affects only a leader/replay-side soft admission-control heuristic (not a value baked into the bank hash), it does not itself cause consensus divergence, but it defeats the specific anti-bloat control the check exists to enforce.

## Likelihood Explanation
Fully reachable by an unprivileged attacker: deploying an SBPF program and issuing CPI calls to the system program's `Allocate`/`CreateAccount` requires no special privilege, keys, or non-default configuration. The gap is deterministic and 100% reproducible for any transaction that performs account-data growth exclusively via CPI rather than as a top-level instruction, and is trivially repeatable at scale (submit many such transactions per slot).

## Recommendation
Extend `calculate_allocated_accounts_data_size`'s pre-execution estimate to conservatively account for CPI-capable programs (e.g., treat any transaction invoking a non-system, non-precompile program as potentially allocating up to the remaining per-transaction budget for estimation purposes, similar to how `loaded_accounts_data_size_cost` uses a limit-based approach), and/or make `calculate_cost_for_executed_transaction` source `allocated_accounts_data_size` from the actual post-execution accounts-data-size delta captured by the transaction context (e.g., `TransactionContext`'s accounts-resize-delta bookkeeping already used to enforce `MAX_PERMITTED_ACCOUNTS_DATA_ALLOCATIONS_PER_TRANSACTION` inside the VM) rather than re-deriving a static top-level-only estimate.

## Proof of Concept
```rust
// cost-model/src/cost_model.rs (conceptual test)
// 1. Build a transaction whose single top-level instruction invokes
//    a custom SBPF program `cpi_realloc_program` (program_id != system_program::id()).
// 2. Inside cpi_realloc_program, CPI-invoke system_instruction::allocate(account, MAX_PERMITTED_DATA_LENGTH)
//    (or CreateAccount) on a PDA/owned account.
// 3. Assert:
let sanitized_tx = RuntimeTransaction::from_transaction_for_tests(top_level_tx_calling_only_custom_program);
assert_eq!(
    CostModel::calculate_allocated_accounts_data_size(
        sanitized_tx.program_instructions_iter(),
        &FeatureSet::all_enabled()
    ),
    0 // top-level iterator sees no SystemInstruction, despite CPI allocating real bytes
);
// Meanwhile, actual execution grows the account by up to MAX_PERMITTED_DATA_LENGTH bytes,
// which is never added to CostTracker::allocated_accounts_data_size, so
// CostTracker::would_fit never returns WouldExceedAccountDataBlockLimit for this
// transaction no matter how many are packed into the block.
```
Note: I could not fully trace, within the available index, the exact runtime/VM-level enforcement path (`transaction-context`/`invoke_context`) that hard-caps `MAX_PERMITTED_ACCOUNTS_DATA_ALLOCATIONS_PER_TRANSACTION` per-transaction during actual CPI execution; that mechanism appears to exist (per `transaction-context/src/transaction_accounts.rs` and `program-runtime/src/invoke_context.rs`) and limits worst-case per-transaction impact, but confirming its exact bound would require deeper inspection than the index provided, possibly warranting a full Devin session for exhaustive verification.

### Citations

**File:** cost-model/src/cost_model.rs (L43-51)
```rust
        Self::calculate_transaction_cost(
            transaction,
            transaction.program_instructions_iter(),
            transaction.num_write_locks(),
            programs_execution_cost,
            loaded_accounts_data_size_cost,
            data_bytes_cost,
            feature_set,
        )
```

**File:** cost-model/src/cost_model.rs (L68-76)
```rust
        Self::calculate_transaction_cost(
            transaction,
            transaction.program_instructions_iter(),
            transaction.num_write_locks(),
            actual_programs_execution_cost,
            loaded_accounts_data_size_cost,
            instructions_data_cost,
            feature_set,
        )
```

**File:** cost-model/src/cost_model.rs (L242-261)
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
```

**File:** cost-model/src/cost_model.rs (L263-264)
```rust
    /// eventually, potentially determine account data size of all writable accounts
    /// at the moment, calculate account data size of account creation
```

**File:** cost-model/src/cost_model.rs (L265-301)
```rust
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

**File:** cost-model/src/cost_tracker.rs (L288-293)
```rust
        let allocated_accounts_data_size =
            self.allocated_accounts_data_size + Saturating(tx_cost.allocated_accounts_data_size());

        if allocated_accounts_data_size.0 > self.limits.allocated_data_size {
            return Err(CostTrackerError::WouldExceedAccountDataBlockLimit);
        }
```

**File:** cost-model/src/cost_tracker.rs (L312-315)
```rust
    // Returns the highest account cost for all write-lock accounts `TransactionCost` updated
    fn add_transaction_cost(&mut self, tx_cost: &TransactionCost<impl TransactionWithMeta>) -> u64 {
        self.allocated_accounts_data_size += tx_cost.allocated_accounts_data_size();
        self.transaction_count += 1;
```
