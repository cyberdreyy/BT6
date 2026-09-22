### Title
Unbounded per-slot writable-account fee cache growth in `PrioritizationFeeCache` can be driven by ordinary transactions, mirroring CVE-2026-66257's unbounded caching DoS - ([File: runtime/src/prioritization_fee.rs])

### Summary
`PrioritizationFeeCache` records, for every non-vote transaction that a leader attempts to include in a block, a `HashMap<Pubkey, u64>` entry per *writable* account referenced by that transaction. This map is not bounded or deduplicated against attacker cost, and is only pruned once, at the end of a slot, when `mark_block_completed()` runs. Because the account keys need not correspond to existing on-chain accounts, an attacker can submit transactions that reference the maximum number of distinct, freshly-generated writable pubkeys allowed per transaction, forcing the leader to grow this map with new heap entries for every transaction it processes, before any pruning ever occurs.

### Finding Description
`PrioritizationFeeCache::update()` iterates every non-vote transaction, filters only by lock validation and a non-zero compute-unit limit, and for each one sends the full list of writable account keys through an unbounded channel to the servicing thread: [1](#0-0) 

The servicing thread accumulates these updates in `unfinalized: BTreeMap<Slot, BTreeMap<BankId, PrioritizationFee>>`, and each `PrioritizationFee::update()` call inserts every writable pubkey from the transaction into `min_writable_account_fees: HashMap<Pubkey, u64>` unconditionally: [2](#0-1) 

Pruning of this map (`prune_irrelevant_writable_accounts`) is only invoked from `mark_block_completed()`, which is only called once, from `finalize_slot`, after the bank for that slot is fully replayed/frozen: [3](#0-2) 

Because `min_writable_account_fees` is a plain `Pubkey -> u64` map with no size cap and no per-transaction deduplication against already-seen keys other than natural hash collisions, and because writable account keys in a transaction do not need to reference real, funded, or even rent-exempt accounts to pass `validate_account_locks` (only account-count/format is checked), an attacker can maximize entries per transaction up to `get_transaction_account_lock_limit()` (the transaction account lock limit) using freshly-generated, never-before-seen `Pubkey`s. Submitting many such transactions during a single slot causes the in-memory map (and the antecedent unbounded MPSC channel, since `unbounded()` is used for `CacheServiceUpdate`) to grow proportionally to `num_transactions_processed_in_slot * account_lock_limit`, entirely under the control of an unprivileged transaction sender, with no cap enforced until the slot is finalized minutes/blocks later (and further bounded upward by `MAX_UNFINALIZED_SLOTS` retained forks/duplicate banks, not a hard entry cap).

This is architecturally analogous to the reported Qpid Proton-J issue: a cache keyed by attacker-supplied "symbol" values (here, Pubkeys) is populated without an upper bound or without validating that the caching cost is proportionate to legitimate work, and is only trimmed after the fact.

### Impact Explanation
If the servicing thread cannot keep pace with the leader's transaction throughput (e.g., under sustained high-TPS spam of maximally-account-heavy transactions), the unbounded channel and the per-slot `min_writable_account_fees` maps can accumulate substantially faster than they are pruned, increasing leader memory pressure. In the worst case this could contribute to leader-side memory exhaustion, which in the availability categories accepted for this analysis maps to a transaction-triggered path toward degraded leader performance / potential process instability (the closest accepted category being a transaction-triggered cluster-availability impact). This does not fund-move, mint, escalate CPI privileges, or corrupt consensus/stake state.

### Likelihood Explanation
Reaching this code path requires only ordinary, unprivileged transactions: any lock-valid transaction with a non-zero compute-unit limit and many distinct writable account keys (which need not exist on-chain) is accepted into the fee-cache pipeline via `Committer`/`Consumer` invoking `PrioritizationFeeCache::update()`. However, actual growth is still constrained in practice by the leader's block-level compute/cost-tracker limits (which cap the number of transactions processable per slot) and by `MAX_UNFINALIZED_SLOTS`, so this is a memory-pressure amplifier rather than a guaranteed unbounded-until-OOM primitive. Exploiting it to the point of causing real operational impact would likely require sustained, high-volume abuse combined with a slow/backlogged servicing thread, which I was not able to fully verify within this investigation (I could not directly confirm production memory limits or observe a specific magnitude that reliably triggers OOM/halt).

### Recommendation
Bound `PrioritizationFee::min_writable_account_fees` (e.g., by capping the number of distinct writable-account entries tracked per slot, or by pruning opportunistically as entries are inserted rather than only at `mark_block_completed()`), and/or bound the `CacheServiceUpdate` channel and drop/backpressure updates when the servicing thread falls behind, so that the memory cost of tracking prioritization fees cannot grow linearly and unboundedly with attacker-chosen, non-existent account keys within a single slot.

### Proof of Concept
Conceptual PoC (not verified end-to-end in this investigation):
1. Generate `N` transactions, each referencing `account_lock_limit` freshly-generated `Pubkey`s as writable accounts (e.g., as no-op instruction accounts to a program that tolerates missing accounts, or accounts intentionally chosen to fail later but still pass initial lock validation).
2. Submit these transactions rapidly to a leader during its slot(s).
3. Each transaction causes `PrioritizationFeeCache::update()` to enqueue its full writable-account list; the servicing thread inserts every new pubkey into that slot/bank's `min_writable_account_fees` map ( [4](#0-3) ), which is not pruned until the slot is finalized ( [5](#0-4) ).
4. Repeating across consecutive slots/forks (bounded by `MAX_UNFINALIZED_SLOTS`) sustains elevated memory usage from this cache while the attacker only pays the exhaustion account-lock rate cost, not a per-cached-entry cost.

### Citations

**File:** runtime/src/prioritization_fee_cache.rs (L219-269)
```rust
                if sanitized_transaction.is_simple_vote_transaction() {
                    continue;
                }

                let transaction_configuration =
                    sanitized_transaction.transaction_configuration(&bank.feature_set);
                let lock_result = validate_account_locks(
                    sanitized_transaction.account_keys(),
                    bank.get_transaction_account_lock_limit(),
                );

                if transaction_configuration.is_err() || lock_result.is_err() {
                    continue;
                }
                let transaction_configuration = transaction_configuration.unwrap();

                // filter out any transaction that requests zero compute_unit_limit
                // since its priority fee amount is not instructive
                if transaction_configuration.compute_unit_limit == 0 {
                    continue;
                }

                let writable_accounts = sanitized_transaction
                    .account_keys()
                    .iter()
                    .enumerate()
                    .filter(|(index, _)| sanitized_transaction.is_writable(*index))
                    .map(|(_, key)| *key)
                    .collect();

                let (prioritization_fee, calculate_prioritization_fee_us) =
                    measure_us!(transaction_configuration.priority_fee_lamports);
                self.metrics
                    .accumulate_total_calculate_prioritization_fee_elapsed_us(
                        calculate_prioritization_fee_us,
                    );

                // See rounding note on `compute_unit_price_in_microlamports`.
                let compute_unit_price =
                    transaction_configuration.compute_unit_price_in_microlamports();
                self.sender
                    .send(CacheServiceUpdate::TransactionUpdate {
                        slot: bank.slot(),
                        bank_id: bank.bank_id(),
                        compute_unit_price,
                        prioritization_fee,
                        writable_accounts,
                    })
                    .unwrap_or_else(|err| {
                        warn!("prioritization fee cache transaction updates failed: {err:?}");
                    });
```

**File:** runtime/src/prioritization_fee_cache.rs (L309-356)
```rust
    fn finalize_slot(
        unfinalized: &mut UnfinalizedPrioritizationFees,
        cache: &RwLock<BTreeMap<Slot, PrioritizationFee>>,
        cache_max_size: usize,
        slot: Slot,
        bank_id: BankId,
        metrics: &PrioritizationFeeCacheMetrics,
    ) {
        if unfinalized.is_empty() {
            return;
        }

        // prune cache by evicting write account entry from prioritization fee if its fee is less
        // or equal to block's minimum transaction fee, because they are irrelevant in calculating
        // block minimum fee.
        let (slot_prioritization_fee, slot_finalize_us) = measure_us!({
            // remove unfinalized slots
            *unfinalized = unfinalized.split_off(&slot.saturating_sub(MAX_UNFINALIZED_SLOTS));

            let Some(mut slot_prioritization_fee) = unfinalized.remove(&slot) else {
                return;
            };

            // Only retain priority fee reported from optimistically confirmed bank
            let pre_purge_bank_count = slot_prioritization_fee.len() as u64;
            let mut prioritization_fee = slot_prioritization_fee.remove(&bank_id);
            let post_purge_bank_count = prioritization_fee.as_ref().map(|_| 1).unwrap_or(0);
            metrics.accumulate_total_purged_duplicated_bank_count(
                pre_purge_bank_count.saturating_sub(post_purge_bank_count),
            );
            // It should be rare that optimistically confirmed bank had no prioritized
            // transactions, but duplicated and unconfirmed bank had.
            if pre_purge_bank_count > 0 && post_purge_bank_count == 0 {
                warn!(
                    "Finalized bank has empty prioritization fee cache. slot {slot} bank id \
                     {bank_id}"
                );
            }

            if let Some(prioritization_fee) = &mut prioritization_fee {
                if let Err(err) = prioritization_fee.mark_block_completed() {
                    error!("Unsuccessful finalizing slot {slot}, bank ID {bank_id}: {err:?}");
                }
                prioritization_fee.report_metrics(slot);
            }
            prioritization_fee
        });
        metrics.accumulate_total_block_finalize_elapsed_us(slot_finalize_us);
```

**File:** runtime/src/prioritization_fee.rs (L175-208)
```rust
impl PrioritizationFee {
    /// Update self for minimum transaction fee in the block and minimum fee for each writable account.
    pub fn update(
        &mut self,
        compute_unit_price: u64,
        prioritization_fee: u64,
        writable_accounts: Vec<Pubkey>,
    ) {
        let (_, update_us) = measure_us!({
            if !self.is_finalized {
                if compute_unit_price < self.min_compute_unit_price {
                    self.min_compute_unit_price = compute_unit_price;
                }

                for write_account in writable_accounts {
                    self.min_writable_account_fees
                        .entry(write_account)
                        .and_modify(|write_lock_fee| {
                            *write_lock_fee = std::cmp::min(*write_lock_fee, compute_unit_price)
                        })
                        .or_insert(compute_unit_price);
                }

                self.metrics
                    .accumulate_total_prioritization_fee(prioritization_fee);
                self.metrics.update_compute_unit_price(compute_unit_price);
            } else {
                self.metrics
                    .increment_attempted_update_on_finalized_fee_count(1);
            }
        });

        self.metrics.accumulate_total_update_elapsed_us(update_us);
    }
```
