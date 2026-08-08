### Title
Silent fallback to `root_bank()` in `JsonRpcRequestProcessor::bank()` returns wrong-slot data to clients requesting `finalized`/`processed` commitment - ([File: rpc/src/rpc.rs])

### Summary
`JsonRpcRequestProcessor::bank()` looks up a slot from `BlockCommitmentCache::slot_with_commitment()` and then fetches it from `BankForks`. If that slot has already been pruned from `BankForks` (e.g., because `BankForks::set_root()`/`prune_non_rooted` has advanced past it before `BlockCommitmentCache` was updated with the newer `highest_super_majority_root`), the method silently substitutes `r_bank_forks.root_bank()` and logs only a `warn!`, with no error surfaced to the RPC caller. Any single unprivileged `getBalance`/`getAccountInfo`/etc. call issued during this window receives data for a different (older or newer, depending on race direction) slot than the one it requested, while the RPC response's `context.slot` may not reflect this substitution accurately relative to what the client asked for.

### Finding Description
`bank()` in [1](#0-0)  resolves the commitment slot as follows:
1. For `Confirmed`, it takes a fast path through `optimistically_confirmed_bank` (unaffected).
2. For `Processed`/`Finalized`, it reads `slot = block_commitment_cache.read().unwrap().slot_with_commitment(commitment.commitment)`, then does `r_bank_forks.get(slot)`.
3. If `get(slot)` returns `None` — i.e., that bank no longer exists in `BankForks` — the code does **not** return an RPC error. It logs a `warn!` and falls back to `r_bank_forks.root_bank()`, which may be a slot far from (older or, after further root advances, potentially inconsistent with) the slot the client actually asked for.

The code's own inline comment documents this exact defect: [2](#0-1) 
It explicitly states the bank may be missing because "an old bank has been purged from BankForks and a new BlockCommitmentCache has not yet arrived," referencing solana-labs/solana#11078.

This is reachable because `BlockCommitmentCache` and `BankForks` are updated by two separate, asynchronous subsystems:
- `BankForks::set_root()` / `prune_non_rooted()` prunes banks below the `highest_super_majority_root` value passed in at call time [3](#0-2) , invoked directly from replay/votor via `set_bank_forks_root()` [4](#0-3) .
- `BlockCommitmentCache`'s `highest_super_majority_root`/`slot` fields are updated separately by `AggregateCommitmentService::run()` on its own background thread, reacting to a channel of aggregation data [5](#0-4) .

Because these two updates are not atomic with respect to each other, there is a window where `BankForks` has already pruned a slot that the (stale) `BlockCommitmentCache` still names via `slot_with_commitment()`. An unprivileged client sending a single `getBalance`/`getAccountInfo` call with `commitment: "finalized"` (or `"processed"`) exactly in this window will trigger the `None` branch and be silently served `root_bank()` data instead of an error — violating "returned data belongs to the requested key, slot, fork, and commitment level" with no indication to the caller that a substitution occurred.

### Impact Explanation
This falls under the RPC "wrong-slot/fork/account data returned" category explicitly listed as in-scope impact. A wallet or exchange polling `getBalance`/`getAccountInfo` with `finalized` commitment during this race would receive state attributed to the wrong slot without any distinguishing error, sentinel, or warning in the JSON-RPC response — only a server-side log line the client never sees.

### Likelihood Explanation
The race window is narrow but real: it requires the root to advance (pruning old banks) between the moment `BlockCommitmentCache`'s cached slot value was last set and the client's RPC call reading that stale value against the already-pruned `BankForks`. This can occur naturally under normal validator operation whenever root advancement outruns the asynchronous commitment-aggregation thread (busy `AggregateCommitmentService`, GC pauses, thread scheduling delays, or bursts of consecutive root advances). It requires only a single unprivileged RPC call at ordinary polling rates — no elevated privileges, no other clients, and no crafted payloads — satisfying the audit's single-call-per-slot-time attacker model. The window is intermittent/timing-dependent rather than deterministically triggerable on demand, so likelihood is best characterized as low-to-moderate but non-zero and reproducible via targeted unit tests that directly construct the race (as the code's own long-standing comment acknowledges).

### Recommendation
Do not silently substitute `root_bank()` when the target slot is missing. Instead:
- Return an explicit RPC error (e.g., a dedicated `RpcCustomError` such as `SlotNotAvailableForCommitment`), or
- As suggested in the existing comment, have `BlockCommitmentCache` hold `Arc<Bank>` handles instead of bare `Slot`s so the referenced bank cannot be pruned out from under a commitment lookup, eliminating the race entirely.
At minimum, the substituted `root_bank()` slot must be clearly reflected as such in the response context so clients can detect the discrepancy rather than assuming the requested commitment was honored.

### Proof of Concept
Unit test in `rpc/src/rpc.rs` test module, directly exercising `JsonRpcRequestProcessor::bank()`:

```rust
#[test]
fn test_bank_stale_commitment_cache_falls_back_silently() {
    // Build bank_forks with slots 0..=5, then advance the root to 5,
    // pruning slots below the new highest_super_majority_root.
    let (bank_forks, ..) = new_bank_forks();
    // ... build a chain of banks up to slot 5, freeze them ...
    bank_forks.write().unwrap().set_root(5, None, Some(5)); // prunes slots < 5

    // Construct a BlockCommitmentCache that is stale: it still names slot 2
    // (already pruned) as the "finalized" slot.
    let block_commitment_cache = Arc::new(RwLock::new(BlockCommitmentCache::new(
        HashMap::new(),
        42,
        CommitmentSlots {
            slot: 2,
            root: 2,
            highest_confirmed_slot: 2,
            highest_super_majority_root: 2, // stale/pruned slot
        },
    )));

    let meta = /* construct JsonRpcRequestProcessor with bank_forks and block_commitment_cache above */;

    let bank = meta.bank(Some(CommitmentConfig::finalized()));

    // Bug: bank() silently returns root_bank() (slot 5) instead of erroring,
    // even though the client asked for the (now-nonexistent) finalized slot 2.
    // A correct implementation should surface a distinguishable error instead
    // of transparently substituting a different slot.
    assert_ne!(bank.slot(), 2, "requested finalized slot no longer exists");
    // Current (buggy) behavior:
    assert_eq!(bank.slot(), bank_forks.read().unwrap().root_bank().slot());
    // Desired fix: this call should instead return Result::Err(...) or
    // otherwise signal a mismatch to the RPC layer.
}
```

Expected outcome after fix: `bank()` (or the calling `get_bank_with_config`) should propagate an explicit error to the JSON-RPC client instead of silently returning `root_bank()`.

### Citations

**File:** rpc/src/rpc.rs (L365-399)
```rust
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
```

**File:** runtime/src/bank_forks.rs (L697-738)
```rust
    fn prune_non_rooted(
        &mut self,
        root: Slot,
        highest_super_majority_root: Option<Slot>,
    ) -> (Vec<BankWithScheduler>, u64, u64) {
        // We want to collect timing separately, and the 2nd collect requires
        // a unique borrow to self which is already borrowed by self.banks
        let mut prune_slots_time = Measure::start("prune_slots");
        let prune_slots: Vec<_> = self
            .get_non_rooted(root, highest_super_majority_root)
            .collect();
        prune_slots_time.stop();

        let mut prune_remove_time = Measure::start("prune_slots");
        let removed_banks = prune_slots
            .into_iter()
            .filter_map(|slot| self.remove(slot))
            .collect();
        prune_remove_time.stop();

        (
            removed_banks,
            prune_slots_time.as_ms(),
            prune_remove_time.as_ms(),
        )
    }

    pub fn get_non_rooted(
        &self,
        root: Slot,
        highest_super_majority_root: Option<Slot>,
    ) -> impl Iterator<Item = Slot> + '_ {
        let highest_super_majority_root = highest_super_majority_root.unwrap_or(root);
        self.banks.keys().copied().filter(move |slot| {
            let keep = *slot == root
                || self.descendants[&root].contains(slot)
                || (*slot < root
                    && *slot >= highest_super_majority_root
                    && self.descendants[slot].contains(&root));
            !keep
        })
    }
```

**File:** votor/src/root_utils.rs (L207-234)
```rust
pub fn set_bank_forks_root<CB>(
    my_pubkey: &Pubkey,
    new_root: Slot,
    bank_forks: &RwLock<BankForks>,
    snapshot_controller: Option<&SnapshotController>,
    highest_super_majority_root: Option<Slot>,
    drop_bank_sender: &Sender<Vec<BankWithScheduler>>,
    callback: CB,
) where
    CB: FnOnce(&BankForks),
{
    let banks_to_remove: Vec<_> = {
        let bank_forks = bank_forks.read().unwrap();
        bank_forks
            .get_non_rooted(new_root, highest_super_majority_root)
            .filter_map(|slot| bank_forks.get_with_scheduler(slot))
            .collect()
    };
    for bank in banks_to_remove {
        let _ = bank.wait_for_completed_scheduler();
    }

    bank_forks.read().unwrap().prune_program_cache(new_root);
    let removed_banks = bank_forks.write().unwrap().set_root(
        new_root,
        snapshot_controller,
        highest_super_majority_root,
    );
```

**File:** core/src/commitment_service.rs (L120-153)
```rust
    fn run(
        receiver: &Receiver<TowerCommitmentAggregationData>,
        ag_receiver: &Receiver<AlpenglowCommitmentAggregationData>,
        block_commitment_cache: &RwLock<BlockCommitmentCache>,
        rpc_subscriptions: Option<&RpcSubscriptions>,
        exit: &AtomicBool,
    ) -> Result<(), RecvTimeoutError> {
        loop {
            if exit.load(Ordering::Relaxed) {
                return Ok(());
            }

            let mut aggregate_commitment_time = Measure::start("aggregate-commitment-ms");
            let commitment_slots = select! {
                recv(receiver) -> msg => {
                    let data = msg?;
                    let data = receiver.try_iter().last().unwrap_or(data);
                    let ancestors = data.bank.status_cache_ancestors();
                    if ancestors.is_empty() {
                        continue;
                    }
                    Self::update_commitment_cache(block_commitment_cache, data, ancestors)
                }
                recv(ag_receiver) -> msg => {
                    let data = msg?;
                    let data = ag_receiver.try_iter().last().unwrap_or(data);
                    Self::alpenglow_update_commitment_cache(
                        block_commitment_cache,
                        data.commitment_type,
                        data.slot,
                    )
                }
                default(Duration::from_secs(1)) => continue
            };
```
