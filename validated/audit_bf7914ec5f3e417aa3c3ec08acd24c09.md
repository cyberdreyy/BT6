Confirmed. I have enough evidence to establish the vulnerability with exact file/line support.

### Title
Signature threshold check silently swallowed by outdated-peer fallback, allowing enough approvals to accumulate without ever being tallied — liveness wedge - (File: `stacks-signer/src/v0/signer.rs`)

### Summary

### Finding Description
`store_and_process_block_signature` is the only routine that recomputes the cumulative signature weight for a block and triggers `broadcast_signed_block` once 70% is reached [1](#0-0) . Immediately after persisting a newly-received acceptance signature, it contains a compatibility fallback: if the sender is not this signer itself and this signer has never recorded a pre-commit from that peer, the message is entirely rerouted into `handle_block_pre_commit` and the function returns before ever computing `total_signature_weight` for that call [2](#0-1) .

`handle_block_pre_commit`, however, has its own unconditional early exit: if this signer has already produced its own signature for the block (`block_info.signed_self.is_some()`), it logs "nothing to do" and returns immediately, without ever looking at signature weight [3](#0-2) .

The result is a dead end: once a signer has already signed a block itself and is only waiting for enough peer signatures to cross the 70% acceptance threshold and broadcast, every subsequent acceptance that arrives from a peer this signer never saw pre-commit from is stored in the DB (via `add_block_signature`) but the code path that would notice the threshold has been crossed and call `broadcast_signed_block` is never invoked — it is diverted into `handle_block_pre_commit`, which discards it at the `signed_self.is_some()` guard. The `store_and_process_block_signature` docstring explicitly frames this function as "check[ing] if we have reached a consensus decision on the block because of it" [4](#0-3) , but for this class of message that check is skipped entirely, both by the fallback branch and by the destination branch's own veto.

This is directly analogous to the report's LP/Synth accounting bug: a genuine, validly-authenticated contribution (a signature, like a Synth mint's deposited liquidity) is recorded in the underlying store but never counted toward the tally/threshold that governs the pool's collective action (broadcasting the block, like allowing withdrawal), because it was funneled through a different accounting bucket (the pre-commit tally) that has its own exit condition blind to the recorded contribution.

### Impact Explanation
This is a liveness wedge on a single signer: that signer can end up holding a fully or over-threshold set of stored signatures for a block yet never notice it and never broadcast, because the specific recount-and-broadcast logic is bypassed for every "never-precommitted-to-me" peer's message once the local signer has already signed. In a network with any signers running the older (pre-pre-commit) protocol version — the exact case the fallback comment says it exists for ("in case the caller is running an outdated version," [5](#0-4) ) — this can plausibly recur on every block: this signer would depend entirely on other signers' own `store_and_process_block_signature` calls (triggered by their own signature, not by outdated peers') to actually notice threshold crossing and broadcast, weakening the redundancy the protocol relies on and potentially stalling block production if enough participating signers hit this dead end simultaneously.

### Likelihood Explanation
This requires no majority and no compromised keys — it is triggered purely by ordinary message ordering/mix of protocol versions: one signer already having signed (`signed_self` set) plus a mix of even a single valid acceptance from a peer whose pre-commit this signer never received (due to version skew, message loss/reorder, or an adversarial peer that skips broadcasting a pre-commit and jumps straight to a signature). This is a routine gossip pattern, not an attack requiring coordination.

### Recommendation
`store_and_process_block_signature`'s outdated-peer fallback should not be an unconditional `return`. After delegating to `handle_block_pre_commit` for bookkeeping (recording the implied pre-commit), it must still fall through to (or separately trigger) the signature-weight recount and broadcast check — regardless of whether `handle_block_pre_commit` itself decided there was "nothing to do" because `signed_self` was already set. Concretely, the threshold/broadcast logic currently at lines 2468–2537 should run unconditionally whenever a new signature was freshly stored by `add_block_signature`, independent of whether the sender's message also gets treated as an implied pre-commit for bookkeeping purposes.

### Proof of Concept
1. Signer S proposes/validates block B and signs it: `S.block_info.signed_self` becomes `Some`.
2. Signer S has not yet received a `BlockPreCommit` from peer P (e.g., P runs an older protocol version that only ever sends `BlockResponse::Accepted`, never a `BlockPreCommit`), i.e. `signer_db.has_committed(B, P) == false`.
3. P's valid signature over B arrives at S as `BlockAccepted`. `handle_block_signature` → `store_and_process_block_signature` stores it via `add_block_signature` (succeeds, since new), then since `P != S.stacks_address` and `has_committed == false`, calls `handle_block_pre_commit(S, P, B)` and returns [2](#0-1) .
4. Inside `handle_block_pre_commit`, `block_info.signed_self.is_some()` is `true` (from step 1), so it logs "nothing to do" and returns [3](#0-2) .
5. Even if P's weight, combined with S's own already-recorded signature and any other previously stored signatures in `signer_db`, now sums to ≥ the 70% threshold, no code path at S recomputes `total_signature_weight` or calls `broadcast_signed_block` for this event. S will only notice the crossed threshold if it later receives another signature from a signer it *has* pre-committed with (which re-enters the bypassed branch), or if some other signer independently broadcasts the block.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1316-1321)
```rust
        if block_info.signed_self.is_some() {
            debug!(
                "{self}: Received pre-commit for a block that we have already signed. Doing nothing...",
            );
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2442-2461)
```rust
    /// Store the block acceptance signature and check if we have reached a consensus decision on the block because of it. If we have, update the block state accordingly and broadcast the block if accepted.
    fn store_and_process_block_signature(
        &mut self,
        stacks_client: &StacksClient,
        sortition_state: &mut Option<SortitionsView>,
        block_info: &mut BlockInfo,
        signer_address: &StacksAddress,
        signature: &MessageSignature,
    ) {
        let block_hash = &block_info.signer_signature_hash();
        // signature is valid! store it.
        // if this returns false, it means the signature already exists in the DB, so just return.
        if !self
            .signer_db
            .add_block_signature(block_hash, signer_address, signature)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block signature"))
        {
            return;
        }

```

**File:** stacks-signer/src/v0/signer.rs (L2462-2466)
```rust
        // If this isn't our own signature and we haven't seen a pre-commit from this signer yet, try treating it as a pre-commit in case the caller is running an outdated version
        if signer_address != &self.stacks_address && !self.signer_db.has_committed(block_hash, signer_address).inspect_err(|e| warn!("Failed to check if pre-commit message already considered for {signer_address:?} for {block_hash}: {e}")).unwrap_or(false) {
            self.handle_block_pre_commit(stacks_client, sortition_state, signer_address, block_hash);
            return;
        }
```
