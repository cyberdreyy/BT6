### Title
`JsonRpcRequestProcessor::bank()` can silently fall back to the wrong bank/slot when the commitment-derived slot is missing from `BankForks`, returning stale/incorrect account and balance data - ([File: rpc/src/rpc.rs])

### Summary
The `bank()` helper used by virtually every JSON-RPC read handler (`getAccountInfo`, `getBalance`, `getProgramAccounts`, `getTransactionCount`, etc.) resolves a target `Bank` from the requested `CommitmentLevel` by looking up a slot in `BlockCommitmentCache` and then fetching that slot from `BankForks`. If the slot is not found — which the code's own comment documents as a known, reachable race — it silently substitutes `root_bank()` instead of surfacing an error, so an unprivileged RPC caller can be served data from a different slot than the one implied by the requested commitment, with no indication that a substitution occurred.

### Finding Description
`bank()` computes `slot` from `block_commitment_cache.slot_with_commitment(commitment.commitment)` and then does `r_bank_forks.get(slot).unwrap_or_else(|| { warn!(...); r_bank_forks.root_bank() })`. [1](#0-0) 

The code comment explicitly documents the root cause: `BlockCommitmentCache` and `BankForks` are updated independently, so a bank can be purged from `BankForks` before `BlockCommitmentCache` is updated to reflect a newer commitment slot, and vice versa — the exact class of “stale reference used instead of the current one” bug described in the external report (there, a stale oracle price was used because no code path forced a refresh before use; here, a stale/mismatched slot reference from one cache is used to index into another cache without verifying consistency). [2](#0-1) 

Notably, a directly analogous race for the accounts-storage layer was previously identified and fixed with a regression test that references RPC symptoms almost identical to this one ("causing RPC to return data from slot N+1 while reporting context.slot = N"), confirming this bug class is real and has manifested in this codebase before: [3](#0-2) 

However, that fix only hardened `AccountsDb::do_load` against loading data from a non-ancestor rooted slot; it did not address the `bank()` fallback path in `rpc.rs`, which still unconditionally substitutes `root_bank()` on a lookup miss rather than returning an error to the caller.

### Impact Explanation
When the fallback triggers, any RPC caller requesting `confirmed` or `finalized` commitment can receive account balances, program accounts, or transaction counts computed against `root_bank()` (a slot potentially far from, and inconsistent with, the slot implied by their requested commitment level), while the RPC response's `context.slot` field may still reflect the originally intended (but stale) commitment slot value from `BlockCommitmentCache`. This is a "wrong-slot/fork/account data returned" outcome from a single unprivileged query, matching the accepted vulnerability class, and can mislead any consumer of the RPC API (wallets, exchanges, bots) that relies on the advertised commitment guarantee to make decisions about finality of balances/transfers.

### Likelihood Explanation
The comment in the source states this occurs "after an old bank has been purged from BankForks and a new BlockCommitmentCache has not yet arrived" — i.e., during normal validator operation whenever bank pruning and commitment-cache updates are not perfectly synchronized, not merely under adversarial or contrived conditions. Because `bank()` is invoked by nearly every full-API RPC handler, the exposure surface is broad, though the race window itself is narrow and timing-dependent, making likelihood moderate rather than high.

### Recommendation
Replace the silent `unwrap_or_else(|| root_bank())` fallback with an explicit error (e.g., a retryable `RpcCustomError`) when the commitment-resolved slot cannot be found in `BankForks`, so callers are not silently served bank data inconsistent with their requested commitment. Alternatively, implement the fix already suggested in the existing code comment: have `BlockCommitmentCache` hold `Arc<Bank>` references directly (instead of bare `Slot`s) so that the referenced bank cannot be purged out from under a commitment lookup.

### Proof of Concept
1. A validator processes new banks and calls `bank_forks.write().unwrap().prune()`/root advancement concurrently with `AggregateCommitmentService` updating `BlockCommitmentCache`.
2. A window exists where `BlockCommitmentCache::slot_with_commitment(Finalized)` still returns a slot `S` that has just been purged from `BankForks` (or not yet inserted), causing `r_bank_forks.get(S)` to return `None`.
3. Any concurrent `getAccountInfo`/`getBalance`/`getProgramAccounts` call with `commitment: "finalized"` hits this path in `bank()` at [2](#0-1)  and is transparently served data from `root_bank()` instead of slot `S`, with only a `warn!` log (invisible to the RPC client) marking the substitution.

### Citations

**File:** rpc/src/rpc.rs (L349-400)
```rust
    #[allow(deprecated)]
    fn bank(&self, commitment: Option<CommitmentConfig>) -> Arc<Bank> {
        debug!("RPC commitment_config: {commitment:?}");

        let commitment = commitment.unwrap_or_default();
        if commitment.is_confirmed() {
            let bank = self
                .optimistically_confirmed_bank
                .read()
                .unwrap()
                .bank
                .clone();
            debug!("RPC using optimistically confirmed slot: {:?}", bank.slot());
            return bank;
        }

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
    }
```

**File:** accounts-db/src/accounts_db/tests/impl.rs (L7225-7237)
```rust
/// loading an account through an older bank must not return
/// data from a rooted slot that is not an ancestor of the querying bank.
///
/// Scenario
///   - Bank at slot 19 has ancestors {17, 19}
///   - Account exists at slot 18 (rooted but NOT an ancestor — different fork)
///   - Account exists at slot 16 (rooted)
///   - min_slot of ancestors = 17
///   - Slot 18 > 17 so it must be excluded; slot 16 <= 17 so it is returned.
///
/// This also covers the original race where `set_root(N+1)` adds a root to
/// the accounts DB before the commitment cache is updated, causing RPC to
/// return data from slot N+1 while reporting `context.slot = N`.
```
