## Analysis Result

I traced the report's bug class — "the same logical quantity is tracked via two paths that should stay in lockstep but can silently diverge, producing an invalid comparison/count" — onto the node-side vote tally in `stackerdb_listener.rs`. I found a concrete instance where one signer's weight can be counted in **both** the rejected and accepted running totals for the same block, because the two message handlers gate their weight updates on different membership sets.

### Title
Stale rejection weight is never retracted when a signer later accepts the same block, letting one signer's weight double-count across `total_weight_rejected` and `total_weight_approved` - (File: `stacks-node/src/nakamoto_node/stackerdb_listener.rs`)

### Summary
`StackerDBListener` tallies signer votes for a proposed block into two counters, `total_weight_approved` and `total_weight_rejected`, gated by two different tracking structures: the accept path checks membership in `gathered_signatures` (a `slot_id -> signature` map) while the reject path checks membership in `responded_signers` (a plain set). A signer that legitimately rejects a block and then later re-evaluates and accepts the same block hash — a state transition the signer explicitly supports (`LocallyRejected -> LocallyAccepted : re-evaluated`) — has its weight added to `total_weight_rejected` on the first message and, because the accept handler never checks `responded_signers` or decrements `total_weight_rejected`, has its weight added a second time to `total_weight_approved` on the second message. The two totals are supposed to be a partition of signer weight; after this sequence they are not, and `total_weight_rejected` is left holding a stale weight that never leaves.

### Finding Description
In the `Accepted` handler, weight is only added once per `slot_id`, gated on `gathered_signatures`: [1](#0-0) 

In the `Rejected` handler, weight is added once per `slot_id`, gated on a *different* set, `responded_signers`: [2](#0-1) 

The accept handler does insert into `responded_signers` too (line 465 in the earlier read), but it never checks it before adding weight, and it never subtracts from `total_weight_rejected` if the same `slot_id` is already present there. The reject handler is symmetric: it does not check `gathered_signatures` and never subtracts from `total_weight_approved`. So the two totals are updated independently and can overlap in the set of signers they represent.

This is reachable by a single honest signer under normal protocol behavior, not just a malicious one: the signer state machine explicitly allows a block to move `LocallyRejected -> LocallyAccepted` on re-evaluation (e.g., a conflicting sibling this signer initially rejected against later becomes stale/non-canonical, and the signer signs the original proposal after all), as shown by the documented state diagram and covered directly by the test `stale_sibling_replaced_when_canonical_tip_below`, which asserts a block moves from a rejected/pre-committed state to `LocallyAccepted` with `signed_self` set after a conflict times out: [3](#0-2) 

When such a signer broadcasts `BlockResponse::Rejected` first and later `BlockResponse::Accepted` for the same `signer_signature_hash`, the node-side listener records both, leaving `total_weight_rejected` permanently inflated by that signer's weight even though the signer's final, current vote is "accept."

The consumer of these two counters, `signer_coordinator.rs`, evaluates the stale rejected weight *before* the approved weight in its polling loop: [4](#0-3) 

Because the reject-threshold check (`total_weight_rejected.saturating_add(...) > self.total_weight`) is checked first and never decays even after the flip-flopping signer's true vote becomes "accept," a miner can spuriously abort a tenure/block that has, in truth, already crossed the real accept threshold, or can prematurely classify transactions as "blocked by a minority" using stale rejection weight that no longer reflects any signer's live vote.

### Impact Explanation
This does not let an invalid/non-canonical block get signed (the actual gathered signature set, verified per-signature, is what is returned to be embedded and re-verified on-chain via `verify_signer_signatures`), so it is not a Critical block-validity break. It is, however, a state-tracking equality violation: `total_weight_approved + total_weight_rejected` is supposed to reflect signer weight partitioned by current, live decision, but can exceed the total signer weight, and `total_weight_rejected` can remain "true" for a signer whose live decision is now "accept." This can wedge a miner into treating an otherwise-signable block as rejected (falling back to alternate transaction sets or aborting the tenure) based on stale votes that no longer represent any signer's actual position — a liveness degradation of the kind called out under the High-impact category ("a signer wedged into never signing valid blocks" / stalled block production due to stale aggregate state), triggerable without requiring a majority of malicious signers.

### Likelihood Explanation
The precondition — a signer moving from `LocallyRejected` to `LocallyAccepted` for the same block after re-evaluation — is a first-class, tested code path in the signer (`stale_sibling_replaced_when_canonical_tip_below`), not a hypothetical edge case; it is expected to occur during ordinary reorg-recovery scenarios described in `docs/signer-flows.md` section 5. Only one signer needs to exhibit this natural vote flip for the node's tally to become inconsistent; no coordination or malicious majority is required.

### Recommendation
Track a signer's most-recent decision per `slot_id` in a single structure (or make the reject/accept handlers mutually exclusive on the same `responded_signers`/decision map), and when an `Accepted` message arrives for a `slot_id` already counted in `total_weight_rejected`, subtract that weight from `total_weight_rejected` before adding it to `total_weight_approved` (and symmetrically for a later `Rejected` after an earlier `Accepted`). This restores the invariant that the two totals partition signer weight by each signer's current vote.

### Proof of Concept
1. Signer `S` (weight `w`) receives block proposal `B`. Local chainstate re-check fails due to a fresh, live conflicting sibling at the same height; `S` broadcasts `BlockResponse::Rejected(B)`.
2. Node's `stackerdb_listener` records this: `responded_signers.insert(S)`, `total_weight_rejected += w` (lines 515-518 above).
3. Time passes; the conflicting sibling's signature times out and is found non-canonical (as in `stale_sibling_replaced_when_canonical_tip_below`), so `S`'s local view re-evaluates `B` and signs it, broadcasting `BlockResponse::Accepted(B)`.
4. Node's listener processes this: `gathered_signatures.contains_key(S)` is `false` (never set by the reject path), so it adds weight again: `total_weight_approved += w` (lines 443-448 above), while `total_weight_rejected` still contains `w` from step 2, uncorrected.
5. `signer_coordinator.rs`'s polling loop now sees both `total_weight_rejected` and `total_weight_approved` non-zero for the same signer's weight; if `total_weight_rejected` (now stale) still crosses the `> self.total_weight - self.weight_threshold` bound at the moment of the check, the miner returns `Err(NakamotoNodeError::SignersRejected)` even though `S`'s live vote — and possibly enough other signers' votes — would otherwise cross the accept threshold, per the check order shown at lines 508-545 of `signer_coordinator.rs`.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-464)
```rust
                        if !block.gathered_signatures.contains_key(&slot_id) {
                            block.total_weight_approved = block
                                .total_weight_approved
                                .saturating_add(signer_entry.weight);

                            info!("StackerDBListener: Signature Added to block";
                                "signer_signature_hash" => %block_sighash,
                                "signer_pubkey" => signer_pubkey.to_hex(),
                                "signer_slot_id" => slot_id,
                                "signature" => %signature,
                                "signer_weight" => signer_entry.weight,
                                "total_weight_approved" => block.total_weight_approved,
                                "percent_approved" => block.total_weight_approved as f64 / self.total_weight as f64 * 100.0,
                                "total_weight_rejected" => block.total_weight_rejected,
                                "percent_rejected" => block.total_weight_rejected as f64 / self.total_weight as f64 * 100.0,
                                "weight_threshold" => self.weight_threshold,
                                "tenure_extend_timestamp" => tenure_extend_timestamp,
                                "read_count_extend_timestamp" => read_count_extend_timestamp,
                                "server_version" => metadata.server_version,
                            );
                        }
                        block.gathered_signatures.insert(slot_id, signature);
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-518)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-signer/src/v0/tests.rs (L809-826)
```rust
    #[test]
    fn stale_sibling_replaced_when_canonical_tip_below() {
        // A zero timeout makes A's signature stale immediately, and the node's canonical tip
        // is still the parent (height 9): A failed to be confirmed, so the signer must sign
        // the replacement rather than stall the tenure (the reorg-recovery case).
        let (info_a, info_b, _) = run_sibling_scenario(Duration::ZERO, false, None);
        assert_a_signed(&info_a);
        assert_eq!(
            info_b.state,
            BlockState::LocallyAccepted,
            "block B should be signed: the conflicting sibling timed out and is not canonical, got: {}",
            info_b.state
        );
        assert!(
            info_b.signed_self.is_some(),
            "block B should carry our signature after the conflict timed out unconfirmed"
        );
    }
```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L508-545)
```rust

            if block_status
                .total_weight_rejected
                .saturating_add(self.weight_threshold)
                > self.total_weight
            {
                info!(
                    "{}/{} signer weight votes to reject block",
                    block_status.total_weight_rejected, self.total_weight;
                    "signer_signature_hash" => %block_signer_sighash,
                );
                counters.bump_naka_rejected_blocks();

                // Only act on failed txids that a blocking minority (>30% weight) agrees on
                let blocking_minority = self.total_weight.saturating_sub(self.weight_threshold);
                let mut temporarily_excluded_txids = HashSet::new();
                let mut permanently_excluded_txids = HashSet::new();
                for (txid, info) in &block_status.failed_txids {
                    if info.total_weight > blocking_minority {
                        // Do not perma ban txids that only a small minority of signers reported as problematic
                        // But make sure its removed from the next block proposal
                        if info.problematic_weight > blocking_minority {
                            permanently_excluded_txids.insert(txid.clone());
                        } else {
                            temporarily_excluded_txids.insert(txid.clone());
                        }
                    }
                }

                return Err(NakamotoNodeError::SignersRejected {
                    temporarily_excluded_txids,
                    permanently_excluded_txids,
                });
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
