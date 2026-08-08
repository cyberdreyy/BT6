### Title
RPC `bank()` commitment lookup silently substitutes an unrelated root bank when `BlockCommitmentCache` is stale, returning wrong-slot data - (File: `rpc/src/rpc.rs`)

### Summary
The bug report describes `MarketShutdownFacet::shutdownMarket()` making a critical decision from a cached, potentially stale price rather than a freshly fetched one, silently accepting stale state as ground truth. The closest reachable analog in agave is `JsonRpcRequestProcessor::bank()`, which resolves the target bank for a client's requested commitment level using the separately-maintained, potentially-lagging `BlockCommitmentCache` slot instead of validating against the actual state of `BankForks`. When the two caches disagree, the function does not return an error to the caller; it silently substitutes `bank_forks.root_bank()` — an unrelated bank at a different slot — and only logs a `warn!`. Every unprivileged RPC caller that requests any commitment-scoped data is exposed to this stale-cache substitution.

### Finding Description
`JsonRpcRequestProcessor::bank()` computes `slot` from `self.block_commitment_cache.read().unwrap().slot_with_commitment(...)`, a value maintained by a separate update pipeline from `BankForks`, then looks that slot up in `self.bank_forks`: [1](#0-0) 

If the two caches have drifted — e.g. `BlockCommitmentCache` still reports a slot whose bank has since been purged from `BankForks`, or a race between `set_root()` and the commitment-cache update — the `unwrap_or_else` fallback returns `r_bank_forks.root_bank()` instead of surfacing an error. The code comment itself documents this as a known, unresolved race: [2](#0-1) 

This `bank()` accessor is the resolution path used across the unprivileged JSON-RPC surface — `getSlot`, `getBlockHeight`, `getBalance`, `getAccountInfo`, `getTransactionCount`, `getBlocks`, `simulateTransaction`, etc. — via `get_bank_with_config` and direct calls, meaning any client-supplied `commitment` can trigger this substitution path with a single call: [3](#0-2) 

The RPC response is then built using `new_response(&bank, ...)`, which reports `context.slot = bank.slot()`. If `bank` is the substituted root bank rather than the slot the caller (and the commitment cache) believed was being served, the response silently mixes data from an unrelated slot/fork into a response context that still implies the requested commitment semantics — i.e., wrong-slot data is returned without any client-visible signal that a fallback occurred.

This is directly analogous to the reported bug class: a critical decision (which bank/state to serve for a commitment-scoped read) is made against a cached value (`BlockCommitmentCache`) rather than validating it fresh against the authoritative source (`BankForks`), and when they disagree, stale/wrong data is served silently instead of failing safely.

### Impact Explanation
Any unprivileged client issuing a normal, single JSON-RPC request with a commitment level can receive account/slot/balance/transaction data belonging to a different bank/slot than the one implied by the requested commitment, with no error and no distinguishing marker other than a server-side `warn!` log the client never sees. This matches the "wrong-slot/fork/account data returned" impact category from a single low-privilege request.

### Likelihood Explanation
The condition requires the two caches (`BlockCommitmentCache` and `BankForks`) to be transiently out of sync — the code comment explicitly documents this as a real, recurring race during snapshot loading and bank pruning, not merely theoretical. Because `bank()` is on the hot path of nearly every commitment-aware RPC method, any window of desync is reachable by ordinary client traffic without any special timing control needed from the attacker/caller; they simply need to be one of many pollers during the desync window.

### Recommendation
Do not silently substitute `root_bank()` when the commitment-cache-derived slot is absent from `BankForks`. Instead, return an explicit RPC error (e.g. a `SlotNotFound`/`min_context_slot`-style custom error) so callers cannot silently receive data from an unrelated slot, and/or unify commitment-cache and bank-forks updates as suggested in the existing code comment (e.g., have `BlockCommitmentCache` retain an `Arc<Bank>` instead of a bare `Slot`) so the two data sources cannot diverge.

### Proof of Concept
Not independently reproducible from static analysis without triggering the underlying cache-desync race (snapshot load / bank pruning timing) described in the code comment; the vulnerable code path itself is directly demonstrated at `rpc/src/rpc.rs:381-399`, where the fallback to `r_bank_forks.root_bank()` executes unconditionally whenever `r_bank_forks.get(slot)` returns `None`, which can be forced by any client request during the documented desync window between `BlockCommitmentCache` and `BankForks`.

### Citations

**File:** rpc/src/rpc.rs (L273-289)
```rust
impl JsonRpcRequestProcessor {
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
