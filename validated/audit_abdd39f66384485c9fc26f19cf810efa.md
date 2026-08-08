### Title
`JsonRpcRequestProcessor::bank` silently substitutes root-bank (finalized) state for `processed`/`confirmed` commitment requests when the resolved slot has been pruned from `BankForks` - ([File: rpc/src/rpc.rs])

### Summary
`JsonRpcRequestProcessor::bank` resolves a target slot from `BlockCommitmentCache::slot_with_commitment` and then looks it up in `BankForks`. If that slot has already been pruned (a normal occurrence during fork progression/root advancement), the code falls back to `r_bank_forks.root_bank()` without informing the caller, so a `processed`-commitment request can silently return data belonging to the finalized root bank of a different slot.

### Finding Description
In `JsonRpcRequestProcessor::bank`, for non-`confirmed` commitment levels the code does: [1](#0-0) 

Specifically:
- `slot` is computed via `block_commitment_cache.read().unwrap().slot_with_commitment(commitment.commitment)`. For `CommitmentLevel::Processed` this returns `BlockCommitmentCache::slot()` — the "heaviest" bank slot tracked by the separate `commitment_service` thread [2](#0-1) .
- The code then does `r_bank_forks.get(slot).unwrap_or_else(|| { ...; r_bank_forks.root_bank() })`.

The `unwrap_or_else` branch's own comment acknowledges the root cause: `BlockCommitmentCache` and `BankForks` are updated by different components/threads, and a bank can be pruned from `BankForks` before `BlockCommitmentCache` is updated to reflect the new state. This is an existing, documented, unfixed race referenced in the code itself via `https://github.com/solana-labs/solana/issues/11078` [3](#0-2) .

When this race triggers, the function returns `root_bank()` — the finalized root — while the caller explicitly requested `Processed` (or, through the confirmed-bank path indirectly relying on this bank for other calls) commitment. There is no error surfaced to the RPC client; only a `warn!` log on the validator side. Downstream callers such as `get_bank_with_config` (used by `getAccountInfo`, `getBalance`, etc.) receive this substituted bank and never learn the substitution occurred: [4](#0-3) 

Since `min_context_slot` checks compare against `bank.slot()` of the *returned* bank (which is now the root bank's slot, consistent with itself), that guard does not catch the substitution either.

### Impact Explanation
A client that requested `processed` (or otherwise expected non-finalized) state receives, without any error, account/balance data at the finalized root slot instead of the intended processed/confirmed slot. This matches the stated impact: "caller believes it read 'processed' state but actually got root_bank data of a different slot" — wallets/exchanges/dApps relying on `getAccountInfo`/`getBalance` at `processed` commitment could act on stale/finalized data believing it reflects the most recent (possibly reorg-able) fork state, or vice versa misjudge finality. This falls under wrong-slot/fork data returned from a single unprivileged RPC call.

### Likelihood Explanation
This is a genuine, pre-existing race between `commitment_service`'s update of `BlockCommitmentCache` and `BankForks` pruning during normal root advancement — it requires no malicious input, just natural timing during fork progression, and the validator code comment itself documents it as a known, unresolved bug (issue #11078). However, exploitability by an external, unprivileged client is probabilistic rather than deterministic: the attacker cannot directly control internal thread scheduling between the commitment-service and bank-pruning paths; they can only poll at up to the permitted rate and be more likely to observe the substitution during periods of fast root advancement (e.g., right after a `Processed`-slot bank is pruned but before `BlockCommitmentCache` catches up). Repeated low-rate polling with `CommitmentConfig::processed()` while the cluster's root advances increases the chance of hitting the window, but it's not reliably triggerable within a single deterministic call by the attacker's own actions alone.

### Recommendation
Make `BlockCommitmentCache` hold `Arc<Bank>` (or at least validate slot presence atomically with the commitment read) instead of a bare `Slot`, so it cannot reference a slot already pruned from `BankForks`. Alternatively, when `r_bank_forks.get(slot)` misses, return an explicit RPC error (e.g., a "slot skipped/unavailable" error) rather than silently substituting `root_bank()`, and never substitute a finalized-root bank for a `Processed`/`Confirmed` request.

### Proof of Concept
Integration test plan (Rust, using existing test scaffolding from `rpc/src/rpc.rs` tests, e.g. `RpcHandler`/`new_bank_forks`):
```rust
#[test]
fn test_bank_forks_prune_races_block_commitment_cache_processed() {
    // 1. Build bank_forks and block_commitment_cache as in test_rpc_processor_get_block_commitment.
    // 2. Advance bank_forks to slot N, set block_commitment_cache's `slot` (Processed target) to N
    //    via CommitmentSlots { slot: N, .. }.
    // 3. Prune bank_forks so that slot N is removed (simulate BankForks::set_root/prune advancing
    //    root past N) WITHOUT updating block_commitment_cache (simulating the lag between
    //    commitment_service and bank pruning).
    // 4. Call request_processor.bank(Some(CommitmentConfig::processed())).
    // 5. Assert failure of the invariant: returned_bank.slot() != N (it silently equals root_bank().slot()),
    //    demonstrating the JsonRpcRequestProcessor::bank fallback substitutes finalized-root data
    //    for a Processed-commitment request instead of erroring.
    assert_ne!(returned_bank.slot(), N);
    assert_eq!(returned_bank.slot(), bank_forks.read().unwrap().root_bank().slot());
}
```
Expected assertion under the fix: `bank()` should either return the bank at slot `N` (if not actually pruned) or a distinguishable error, never silently returning `root_bank()` while claiming to satisfy `Processed`/`Confirmed` commitment.

### Citations

**File:** rpc/src/rpc.rs (L274-289)
```rust
    fn get_bank_with_config(&self, config: RpcContextConfig) -> Result<Arc<Bank>> {
        let RpcContextConfig {
            commitment,
            min_context_slot,
        } = config;
        let bank = self.bank(commitment);
        if let Some(min_context_slot) = min_context_slot
            && bank.slot() < min_context_slot
        {
            return Err(RpcCustomError::MinContextSlotNotReached {
                context_slot: bank.slot(),
            }
            .into());
        }
        Ok(bank)
    }
```

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

**File:** runtime/src/commitment.rs (L116-122)
```rust
    pub fn slot_with_commitment(&self, commitment_level: CommitmentLevel) -> Slot {
        match commitment_level {
            CommitmentLevel::Processed => self.slot(),
            CommitmentLevel::Confirmed => self.highest_gossip_confirmed_slot(),
            CommitmentLevel::Finalized => self.highest_super_majority_root(),
        }
    }
```
