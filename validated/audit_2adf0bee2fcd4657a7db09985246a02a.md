## Title
Denial-of-service on writable-account transactions via repeated exhaustion of the per-account cost limit - (`cost-model/src/cost_tracker.rs`)

### Summary
Agave's `CostTracker` enforces a per-writable-account compute-unit budget (`MAX_WRITABLE_ACCOUNT_UNITS`, currently 24,000,000 CU) inside every block, in addition to the overall block limit. Once a writable account's cumulative cost within a block hits this ceiling, `CostTracker::would_fit()` rejects every further transaction that writes to that account with `CostTrackerError::WouldExceedAccountMaxLimit` (surfaced as `TransactionError::WouldExceedMaxAccountCostLimit`), regardless of the caller. Because this budget is refreshed every new block/slot, an attacker who can consistently pay enough priority fee to get scheduled first can repeatedly saturate a shared, high-traffic account's budget every single block, indefinitely denying any other user's transaction that must write-lock that same account — the same "repeatedly reset a shared throttle to lock everyone else out" pattern described in the referenced RocketPool `unstake()` DoS report (reset a shared cooldown each period so any competing legitimate call keeps failing).

### Finding Description
The account-level cost limit is defined in [1](#0-0)  and defaulted into `CostTrackerLimits` at [2](#0-1) .

The check that turns "shared account already busy" into a hard rejection of *other* transactions is `would_fit()`: [3](#0-2) 

This check runs both pre-execution (`check_block_cost_limits` in the SVM execution path) and post-execution when actual costs are committed in banking stage: [4](#0-3) [5](#0-4) 

`WouldExceedMaxAccountCostLimit` is a distinct, tracked error path in banking-stage metrics, confirming it is a routine rejection mechanism rather than an edge case: [6](#0-5) 

The budget resets every block because a fresh `CostTracker` is derived from the parent for each new bank: [7](#0-6) . This means the throttle is not "spent once and gone" like a global rate limit — it is a per-block-window quota on a specific account, exactly analogous to RocketPool's per-deposit "reset the timer" delay: any actor who can win/occupy the quota every window can perpetually block everyone else touching that account, since ordinary users have no way to bypass or reserve capacity ahead of a resource-exhausting attacker.

### Impact Explanation
Any writable account that many independent users rely on for a state-changing instruction (a popular AMM pool authority, a heavily used PDA, a program's global config/vault account, etc.) can be monopolized. An attacker with enough SOL to pay competitive priority fees can submit self-transactions that write-lock the target account and consume most/all of the 24M CU account budget every slot. Every other legitimate transaction that also needs to write-lock that same account will be rejected in every subsequent block with `WouldExceedMaxAccountCostLimit`, producing a sustained, targeted denial of service against that account's functionality (deposits, unstakes, settlement, etc.) for as long as the attacker keeps paying to re-saturate the account limit each block.

### Likelihood Explanation
The attack requires only: (1) knowledge of the target hot account's pubkey (public), (2) enough transactions/fees to burn ~24M CU worth of instructions against that account each block, and (3) enough priority fee to be included ahead of legitimate competing transactions. This is entirely within reach of an ordinary user submitting normal transactions through the standard client → banking-stage → cost-tracker path; no privileged access, leaked keys, or node compromise is needed. The economic cost (fees) is the only real barrier, and it scales with block frequency (~2.5 blocks/sec), making sustained targeting of a specific high-value account plausible for a motivated attacker.

### Recommendation
Consider decoupling "quota exhaustion by one payer/program" from "block all other payers": e.g., track per-account cost quota with fairness/priority weighting across distinct fee payers or writers instead of a single first-come-first-served pool, or expose per-account congestion metrics so dependent programs/clients can react (batch elsewhere, use different accounts, or fall back to read-only paths) rather than failing hard. At minimum, document the DoS surface for protocol/program authors relying on shared hot accounts so they can design around per-block, per-account CU ceilings (e.g., sharding a single hot account into multiple accounts to spread the write-lock cost).

### Proof of Concept
1. Identify a shared writable account `A` used by many unrelated users' transactions (e.g., a popular program's vault/config account).
2. Each slot, submit enough self-transactions writing to `A` with sufficiently high priority fee, whose summed cost approaches `MAX_WRITABLE_ACCOUNT_UNITS` (24,000,000 CU) as computed in `CostTracker::would_fit` ( [3](#0-2) ), ensuring they land before other users' transactions targeting `A` in that block's ordering.
3. Any competing transaction from another user that also write-locks `A` in that same block will fail `would_fit()` and be rejected with `TransactionError::WouldExceedMaxAccountCostLimit` (tracked at [8](#0-7) ).
4. Because the tracker resets fresh per new bank ( [7](#0-6) ), repeat step 2 every slot to perpetually deny all other transactions touching account `A`.

### Citations

**File:** cost-model/src/block_cost_limits.rs (L30-33)
```rust
/// Number of compute units that a writable account in a block is allowed. The
/// limit is to prevent too many transactions write to same account, therefore
/// reduce block's parallelism.
pub const MAX_WRITABLE_ACCOUNT_UNITS: u64 = 24_000_000;
```

**File:** cost-model/src/cost_tracker.rs (L87-96)
```rust
impl Default for CostTrackerLimits {
    fn default() -> Self {
        const _: () = assert!(MAX_WRITABLE_ACCOUNT_UNITS <= MAX_BLOCK_UNITS);
        Self {
            account_cost: MAX_WRITABLE_ACCOUNT_UNITS,
            block_cost: MAX_BLOCK_UNITS,
            allocated_data_size: MAX_BLOCK_ACCOUNTS_DATA_SIZE_DELTA,
        }
    }
}
```

**File:** cost-model/src/cost_tracker.rs (L131-136)
```rust
impl CostTracker {
    pub fn new_from_parent_limits(&self) -> Self {
        let mut new = Self::default();
        new.set_limits(self.limits);
        new
    }
```

**File:** cost-model/src/cost_tracker.rs (L295-309)
```rust
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

**File:** runtime/src/transaction_execution.rs (L157-169)
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
```

**File:** core/src/banking_stage/consumer.rs (L542-563)
```rust
        let mut cost_tracker = bank.write_cost_tracker().unwrap();

        for (index, transaction_cost) in transaction_costs.iter_mut().enumerate() {
            let Some(cost) = transaction_cost.as_ref() else {
                continue;
            };

            match cost_tracker.try_add(cost) {
                Ok(_) => {}
                Err(err) => {
                    let transaction_error = TransactionError::from(err);
                    *transaction_cost = None;
                    if all_or_nothing {
                        all_or_nothing_error = Some((index, transaction_error));
                        break;
                    } else {
                        remaining_batch_error = Some((index, transaction_error));
                        break;
                    }
                }
            }
        }
```

**File:** core/src/banking_stage/consumer.rs (L689-707)
```rust
    fn accumulate_cost_limit_error(
        transaction_error: &TransactionError,
        error_counters: &mut TransactionErrorMetrics,
    ) {
        match transaction_error {
            TransactionError::WouldExceedMaxBlockCostLimit => {
                error_counters.would_exceed_max_block_cost_limit += 1;
            }
            TransactionError::WouldExceedMaxVoteCostLimit => {
                error_counters.would_exceed_max_vote_cost_limit += 1;
            }
            TransactionError::WouldExceedMaxAccountCostLimit => {
                error_counters.would_exceed_max_account_cost_limit += 1;
            }
            TransactionError::WouldExceedAccountDataBlockLimit => {
                error_counters.would_exceed_account_data_block_limit += 1;
            }
            _ => {}
        }
```
