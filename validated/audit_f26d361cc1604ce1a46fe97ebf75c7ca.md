Based on the code, this claim does not hold up.

**Analysis:**

`force_flush_accounts_cache` in [1](#0-0)  is purely a storage-tier operation: it moves already-committed account state from the in-memory write cache to persistent storage [2](#0-1) . It has no interaction whatsoever with the `BlockhashQueue` or blockhash expiry logic.

Blockhash validity/expiry is governed entirely by `BlockhashQueue::is_hash_index_valid`, which compares `last_hash_index` (incremented deterministically once per registered hash via `register_hash`, called during tick/block processing) against `max_age` [3](#0-2) . This state is a deterministic function of the processed block history (ticks and hash registrations), which is itself consensus-critical and identical across all validators replaying the same blocks. Since `register_hash` is invoked deterministically as part of block/tick production [4](#0-3) , there is no attacker-controlled degree of freedom that could make a `BlockhashQueue` state diverge between honest validators replaying the same ledger — the queue's index bookkeping is independent of accounts-cache flush timing, which is merely a storage/performance optimization unrelated to transaction validity checks.

The attacker "selecting a recent_blockhash near the expiry boundary" is exactly the intended, well-tested behavior of the blockhash-age window (see `test_reject_old_last_hash`, `test_change_max_age` in [5](#0-4) ), not a bug — every validator computes hash age identically from `last_hash_index`, which is a deterministic replay artifact, not something the accounts-cache flush timing or an attacker's transaction can perturb differently between nodes.

There is no code path connecting `force_flush_accounts_cache` to blockhash expiry, and no mechanism by which an unprivileged attacker's transaction can cause `is_hash_valid_for_age` to disagree between two validators replaying the identical bank/block sequence.

### No Vulnerability found for this question.

### Citations

**File:** runtime/src/bank.rs (L4812-4817)
```rust
    pub fn force_flush_accounts_cache(&self) {
        self.rc
            .accounts
            .accounts_db
            .flush_accounts_cache(true, Some(self.slot()))
    }
```

**File:** accounts-db/src/accounts_db.rs (L4229-4250)
```rust
    // `force_flush` flushes all the cached roots `<= requested_flush_root`. It also then
    // flushes excess remaining rooted slots while 'should_aggressively_flush_cache' is true
    pub fn flush_accounts_cache(&self, force_flush: bool, requested_flush_root: Option<Slot>) {
        #[cfg(not(test))]
        assert!(requested_flush_root.is_some());

        if !force_flush && !self.should_aggressively_flush_cache() {
            return;
        }

        // Flush only the roots <= requested_flush_root, so that snapshotting has all
        // the relevant roots in storage.
        let mut flush_roots_elapsed = Measure::start("flush_roots_elapsed");

        let _guard = self.active_stats.activate(ActiveStatItem::Flush);

        // Note even if force_flush is false, we will still flush all roots <= the
        // given `requested_flush_root`, even if some of the later roots cannot be used for
        // cleaning due to an ongoing scan
        let (total_new_cleaned_roots, num_cleaned_roots_flushed, mut flush_stats) =
            self.flush_rooted_accounts_cache_with_clean(requested_flush_root);
        flush_roots_elapsed.stop();
```

**File:** accounts-db/src/blockhash_queue.rs (L130-156)
```rust
    fn is_hash_index_valid(last_hash_index: u64, max_age: usize, hash_index: u64) -> bool {
        last_hash_index - hash_index <= max_age as u64
    }

    pub fn register_hash(&mut self, hash: &Hash, lamports_per_signature: u64) {
        self.last_hash_index += 1;
        self.purge();
        self.hashes.insert(
            *hash,
            HashInfo {
                fee_calculator: FeeCalculator::new(lamports_per_signature),
                hash_index: self.last_hash_index,
                timestamp: timestamp(),
            },
        );

        self.last_hash = Some(*hash);
        self.refresh_durable_nonce();
    }

    fn purge(&mut self) {
        if self.hashes.len() >= self.max_age {
            self.hashes.retain(|_, info| {
                Self::is_hash_index_valid(self.last_hash_index, self.max_age, info.hash_index)
            });
        }
    }
```

**File:** accounts-db/src/blockhash_queue.rs (L225-241)
```rust
    fn test_reject_old_last_hash() {
        let max_age = 100;
        let mut hash_queue = BlockhashQueue::new(max_age);
        let last_hash = hash(&serialize(&0).unwrap());
        for i in 0..102 {
            let last_hash = hash(&serialize(&i).unwrap());
            hash_queue.register_hash(&last_hash, 0);
        }
        // Assert we're no longer able to use the oldest hash.
        assert!(!hash_queue.is_hash_valid_for_age(&last_hash, max_age));
        assert!(!hash_queue.is_hash_valid_for_age(&last_hash, 0));

        // Assert we are not able to use the oldest remaining hash.
        let last_valid_hash = hash(&serialize(&1).unwrap());
        assert!(hash_queue.is_hash_valid_for_age(&last_valid_hash, max_age));
        assert!(!hash_queue.is_hash_valid_for_age(&last_valid_hash, 0));
    }
```

**File:** runtime/src/bank/check_transactions.rs (L238-256)
```rust
    ) -> TransactionResult<Option<Pubkey>> {
        let recent_blockhash = tx.recent_blockhash();
        if hash_queue
            .get_hash_info_if_valid(recent_blockhash, max_age)
            .is_some()
        {
            Ok(None)
        } else if let Some((nonce_address, _)) = self.check_nonce_transaction_validity(
            tx,
            next_durable_nonce,
            strict_nonce_size_check,
            strict_nonce_authority_check,
        ) {
            Ok(Some(nonce_address))
        } else {
            error_counters.blockhash_not_found += 1;
            Err(TransactionError::BlockhashNotFound)
        }
    }
```
