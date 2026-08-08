Based on my research, I found a strong analog in the Agave RPC layer. The GMX bug is fundamentally about a **request/response temporal mismatch**: a client asks for data as-of one point in time, but due to a delay, the value actually used is silently substituted with a *different* (stale/wrong) state — with financial/correctness consequences for the caller.

### Title
RPC silently falls back to the root bank on a commitment-slot lookup miss, returning data from the wrong slot/fork - ([File: rpc/src/rpc.rs])

### Summary
`JsonRpcRequestProcessor::bank()` resolves a client's requested `CommitmentConfig` to a specific slot via `BlockCommitmentCache::slot_with_commitment()`, then fetches that slot's `Bank` from `BankForks`. If the bank for that slot is no longer present in `BankForks` (e.g. it was pruned/purged), the code does not surface an error — it logs a warning and silently substitutes `r_bank_forks.root_bank()`, an entirely different bank/slot than what commitment resolution selected.

### Finding Description [1](#0-0) 

The function explicitly acknowledges this is a defect in its own comments: [2](#0-1) 

This `bank()` helper backs essentially every unprivileged, unauthenticated JSON-RPC read path that takes a `commitment`/`RpcContextConfig` parameter — `getAccountInfo`, `getMultipleAccounts`, `getProgramAccounts`, `getBalance`, `getTokenAccountsByOwner`, etc. — via `get_bank_with_config`: [3](#0-2) 

The comment identifies the trigger condition precisely: a race between `BankForks` pruning an old bank and `BlockCommitmentCache` being updated to reflect the new state. This is exactly the GMX analog — the caller requested data "as of" a specific commitment/slot (the equivalent of "price at order submission time T"), but a timing gap between two independently-updated pieces of state (`BlockCommitmentCache` vs `BankForks`) causes the actual value served to come from an unrelated bank (the root bank, which could be many slots behind or on a materially different point of the ledger than what was requested).

A related, but now-fixed, instance of the same underlying class of bug is documented in the accounts-db test suite, confirming this exact race has manifested in this codebase before: [4](#0-3) 

That the accounts-db layer was hardened (ancestor-verifying `do_load`) does not fix the RPC-layer `bank()` fallback path — the two are independent surfaces. `get_encoded_account`/`new_response` will report `context.slot` as the *substituted* root bank's slot, not the slot the caller's commitment level actually resolved to, so a caller who is not tracking slots server-side (e.g. simply diffing against a previous "processed" or "confirmed" response) has no straightforward way to detect that they silently received root-bank data instead.

### Impact Explanation
This falls squarely into "wrong-slot/fork/account data returned" from a query. An RPC consumer requesting `processed` or `finalized`-commitment account state can receive account data from the wrong bank/slot without any error being surfaced, purely due to a race between two caches maintained on different threads. This can lead to stale-vs-current confusion analogous to the GMX report: consumers (wallets, indexers, bots reacting to balances/state) may act on data that does not correspond to the slot/commitment level they explicitly requested, producing incorrect economic decisions (e.g., balance checks, nonce/blockhash-adjacent decisions) built on unexpectedly-substituted state.

### Likelihood Explanation
The trigger requires no special privilege — it's reachable by any unprivileged JSON-RPC caller under normal validator operation, purely from natural timing/pruning races between `BankForks` and `BlockCommitmentCache` updates, which the code's own comments say can occur ("it may occur after an old bank has been purged from BankForks and a new BlockCommitmentCache has not yet arrived"). No malicious crafting or multiple calls are required; a single call during an unlucky window is sufficient.

### Recommendation
Instead of falling back to `root_bank()` on a lookup miss, `bank()` should either retry against the current `BlockCommitmentCache` state to re-resolve a valid slot, or return an explicit RPC error (e.g. a `MinContextSlotNotReached`/`BlockCommitmentCache`-style error) so that callers are not silently served substituted data. At minimum, the substitution should not happen silently — the returned `context.slot` already reflects the actual bank, but that alone is not adequate mitigation for callers that don't cross-check the returned slot against their expected commitment resolution.

### Proof of Concept
1. Send `getAccountInfo`/`getMultipleAccounts` with `commitment: "processed"` (or `"finalized"`) concurrently with normal validator operation.
2. Under load, trigger the timing window where `BankForks` has pruned the bank at the slot indicated by `BlockCommitmentCache::slot_with_commitment()` but `BlockCommitmentCache` has not yet been updated to the new state (this is the exact race the code comment at rpc/src/rpc.rs:381-399 describes).
3. Observe that `bank()` returns `root_bank()` instead of erroring, and the RPC response is built from that substituted bank via `new_response(&bank, ...)`, silently returning account/balance data from a different slot/fork than the commitment level nominally selected.

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

**File:** accounts-db/src/accounts_db/tests/impl.rs (L7235-7238)
```rust
/// This also covers the original race where `set_root(N+1)` adds a root to
/// the accounts DB before the commitment cache is updated, causing RPC to
/// return data from slot N+1 while reporting `context.slot = N`.
#[test]
```
