### Title
JsonRpcRequestProcessor::bank() silently falls back to root bank when the commitment-derived slot lags bank_forks, returning stale slot/account data to any RPC caller - (File: rpc/src/rpc.rs)

### Summary
The Yieldoor bug uses a coarse, potentially-stale derived value (`slot0.tick`) to select a resource (the tick range) instead of validating it against the pool's true state, silently producing an inconsistent/asymmetric result. The analogous agave pattern is `JsonRpcRequestProcessor::bank()`, which selects the bank to serve an RPC request purely from a separately-maintained, asynchronously-updated cache (`BlockCommitmentCache::slot_with_commitment`) rather than the authoritative `BankForks` state, and when the two disagree it does not error out but silently substitutes the root bank.

### Finding Description
`bank()` computes `slot` from `block_commitment_cache.slot_with_commitment(commitment)` [1](#0-0) , then looks that slot up in `bank_forks`. If the bank for that slot is missing, instead of returning an RPC error, it logs a warning and returns `r_bank_forks.root_bank()` — a different, older bank than the one requested — while the caller receives a response as if it were successful: [2](#0-1) 

The code's own comment acknowledges this is a known, unresolved race: `BlockCommitmentCache` and `BankForks` are updated by different services at different times, so the cached `slot` can point to a bank that has already been purged from `BankForks` (or was never inserted, e.g. due to a snapshot-creation bug), analogous to slot0's tick lagging the pool's true price at a boundary crossing. In both cases a fast-changing piece of authoritative state (Uniswap pool tick / `BankForks` root and bank set) is approximated by a separately updated, coarser value (`slot0.tick` / `BlockCommitmentCache.commitment_slots`) and consumed without cross-validation.

Because `bank()` is invoked by essentially every RPC handler that accepts a `commitment` parameter (`getAccountInfo`, `getBalance`, `getMultipleAccounts`, `getProgramAccounts`, `getSignatureStatuses`'s `self.bank(Some(CommitmentConfig::processed()))`, `get_transaction_status`, etc.), this fallback path is reachable by an ordinary unprivileged RPC client with a single request — no special timing manipulation beyond the natural race between root advancement/pruning and commitment-cache updates.

### Impact Explanation
When triggered, an RPC client's account/balance/program-account/signature-status query is silently served from the wrong bank (the root bank, which can be many slots behind the one addressed by the client's requested/implied commitment), rather than erroring. This is a "wrong-slot/fork/account data returned" condition per the accepted impact classes: a client can be given stale account balances, stale program account sets, or stale signature-status data while believing it queried the intended slot. This is not a crash, but it is a concrete, silent state-integrity defect reachable by ordinary read-only RPC calls.

### Likelihood Explanation
The condition requires the natural (and documented) race between `BlockCommitmentCache` updates (`AggregateCommitmentService`) and `BankForks` bank insertion/pruning; the code comment states this has occurred in practice (referencing solana-labs/solana#11078) after a bank is purged from `BankForks` before `BlockCommitmentCache` catches up, or when a snapshot omits an expected bank. It requires no attacker privilege and can be observed by any RPC caller under normal operational conditions (bank pruning / snapshot restore), though it is timing/race dependent rather than deterministically triggerable on demand.

### Recommendation
Do not silently substitute `root_bank()` when the commitment-derived slot is absent from `BankForks`. Instead:
- Return an explicit RPC error (e.g. reuse/extend `RpcCustomError` such as `BlockNotAvailable`/a new `SlotNotFound` variant) so callers can retry rather than receive misleadingly-labeled data, or
- Store an `Arc<Bank>` directly in `BlockCommitmentCache` (as the existing comment suggests) instead of a bare `Slot`, eliminating the possibility of the referenced bank being pruned out from under the cache.

### Proof of Concept
Not independently reproduced in this analysis (would require orchestrating a race between bank pruning/`set_root` and `AggregateCommitmentService`'s commitment cache update, or a snapshot missing the expected bank, then issuing a commitment-scoped RPC call such as `getAccountInfo`). The vulnerable fallback path and its acknowledged known-bug status are demonstrated directly in code and comments: [2](#0-1) .

### Citations

**File:** rpc/src/rpc.rs (L365-369)
```rust
        let slot = self
            .block_commitment_cache
            .read()
            .unwrap()
            .slot_with_commitment(commitment.commitment);
```

**File:** rpc/src/rpc.rs (L381-399)
```rust
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
