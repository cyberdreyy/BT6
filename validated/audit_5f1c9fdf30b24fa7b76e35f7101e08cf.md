### Title
`store_and_process_block_signature` broadcasts a block that has already reached `GloballyRejected` consensus - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`store_and_process_block_signature` (`stacks-signer/src/v0/signer.rs:2443-2538`) tallies replayed `BlockAccepted` messages and unconditionally calls `broadcast_signed_block` once the acceptance-weight threshold is crossed, without ever checking `block_info.has_reached_consensus()`. Its sibling, `store_and_process_block_rejection` (`stacks-signer/src/v0/signer.rs:2290-2293`), explicitly early-returns on that same check, so the two ledgers are not symmetrically gated.

### Finding Description
The intended equality is: a `BlockInfo` may reach at most one of `GloballyAccepted` / `GloballyRejected`, and once either is reached, no further tally on the other axis should be able to act on the block.

`store_and_process_block_rejection` enforces this correctly: [1](#0-0) 
This returns immediately once `has_reached_consensus()` is true, before ever tallying rejection weight or acting further.

`store_and_process_block_signature` has no equivalent gate. The only early-exit check on the acceptance path is: [2](#0-1) 
`signed_group.is_some()` is only set once the block was *already* locally/globally accepted by this same function — it says nothing about a block that reached `GloballyRejected` via `mark_globally_rejected` in `store_and_process_block_rejection`. So if `block_info.state == GloballyRejected` and `signed_group` is still `None` (the normal case for a rejected block, since it was never signed), execution falls through, tallies `compute_signature_signing_weight`, and if the threshold is met: [3](#0-2) 
`mark_locally_accepted(true)` sets `self.signed_group.get_or_insert(...)` *before* attempting the state transition; even if the internal `move_to` fails because the current state is a terminal global state, the error is swallowed whenever `has_reached_consensus()` is true (which it is, since the block is `GloballyRejected`). Regardless of that failure, `insert_block` persists the block (now with a `signed_group` timestamp despite failing the transition), and — critically — `broadcast_signed_block` is called **unconditionally**, with no re-check of the block's state.

Attacker's exact message: a captured, stale, but validly-signed `BlockAccepted` (`SignerMessage::BlockResponse` Accepted) chunk from an honest signer for a `signer_signature_hash` that signer X has not yet recorded a signature for in its own `signerdb` (e.g., an accept issued by that honest signer before enough other signers rejected the same hash). The attacker only needs gossip/StackerDB replay capability — no private key, no majority weight — to re-inject this old, still-validly-signed chunk into signer X's view after X has already independently reached `GloballyRejected` for that hash via `store_and_process_block_rejection`.

Exploit flow: (1) enough honest signers reject a proposal, X calls `mark_globally_rejected` via `store_and_process_block_rejection`. (2) attacker replays enough distinct honest-signer accept chunks for the same hash (chunks X had not yet stored, satisfying `add_block_signature`'s "new to DB" check) via `handle_block_response` → `handle_block_signature` → `store_and_process_block_signature`. (3) Once tallied weight crosses `NakamotoBlockHeader::compute_voting_weight_threshold`, X calls `broadcast_signed_block`, sending the block to its own node as though signing consensus had been reached, despite X's own signerdb having already recorded global rejection for that exact hash.

### Impact Explanation
This breaks the safety property "a rejection recounted as acceptance" (explicitly listed Critical category). The bug does not require majority signer collusion or node-side reorg rules to manifest — it is entirely internal to the signer's own weight-ledger logic: `store_and_process_block_signature` lacks the `has_reached_consensus()` guard that its counterpart enforces. If the same stale-replay race is reproduced against enough distinct honest signers concurrently, each independently proceeds to `broadcast_signed_block` for a block their own signerdb marked `GloballyRejected`, which can finalize a block that the signer set had already collectively rejected. The bug is repeatable per proposal/hash as long as the attacker can find distinct not-yet-recorded honest accept signatures to replay.

### Likelihood Explanation
Preconditions: a reward cycle in progress; a block hash for which enough honest signers issued rejections to reach `GloballyRejected` on signer X, while other (or the same) honest signers' earlier accept messages for that identical hash remain valid and replayable (proposal re-broadcast preserves `signer_signature_hash`, so old cached accept chunks stay cryptographically valid indefinitely). Attacker cost is a single miner slot (to trigger a re-proposal at the same hash, or simply to have gossip access) plus normal StackerDB/gossip relay ability — no private key material or auth token needed since the replayed chunk carries a valid signature from its original honest signer. This is realistic in split-decision scenarios where signers do not converge synchronously on the same verdict, which the protocol must tolerate.

### Recommendation
Add a symmetric guard to `store_and_process_block_signature`, mirroring `store_and_process_block_rejection`'s check, and place it before any tallying:
```rust
if block_info.has_reached_consensus() {
    return;
}
```
Additionally, `mark_locally_accepted`'s side effect of setting `signed_group` should not happen before the state-transition check succeeds (avoid mutating fields ahead of a fallible `move_to`), and `broadcast_signed_block` should never execute unless the transition to an accepted state actually succeeded.

### Proof of Concept
Rust test plan in `stacks-signer/src/v0/signer.rs` test module (or an integration test driving the `Signer` state machine):
1. Construct a `BlockInfo` for a `NakamotoBlock` and insert it into `signer_db` in `Unprocessed`/`PreCommitted` state.
2. Drive enough calls to `store_and_process_block_rejection` (with distinct `signer_address`es whose combined `compute_signature_signing_weight` exceeds `total_weight - min_weight`) so that `block_info.mark_globally_rejected()` succeeds and `block_info.has_reached_consensus()` returns `true`; assert this via `signer_db.block_lookup(&hash).state == BlockState::GloballyRejected`.
3. Re-fetch the (now globally-rejected) `block_info` and drive `store_and_process_block_signature` with enough distinct, previously-unseen `signer_address`/`signature` pairs (simulating replayed stale `BlockAccepted` chunks) to cross `NakamotoBlockHeader::compute_voting_weight_threshold`.
4. Assert (failing on current code, passing after fix) that `broadcast_signed_block` is **not** invoked and `signer_db.block_lookup(&hash).state` remains `GloballyRejected` — i.e., the function must early-return before calling `mark_locally_accepted`/`broadcast_signed_block` once `has_reached_consensus()` is `true`.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L2290-2293)
```rust
        if block_info.has_reached_consensus() {
            // Checking the rejection signatures is pointless. We have already reached consensus on this block.
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2468-2471)
```rust
        if block_info.signed_group.is_some() {
            // We have already processed this block to the accepted state. Adding more signatures will not change anything so nothing to check.
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2528-2537)
```rust
        if let Err(e) = block_info.mark_locally_accepted(true) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}");
            }
        }
        let _ = self.signer_db.insert_block(block_info).map_err(|e| {
            warn!("Failed to set group threshold signature timestamp for {block_hash}: {e:?}");
            panic!("{self} Failed to write block to signerdb: {e}");
        });
        self.broadcast_signed_block(stacks_client, block_info.block.clone(), &addrs_to_sigs);
```
