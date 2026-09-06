I looked into this request, but I want to flag a concern before diving in: the external report is about a stored XSS vulnerability in Liferay's web fragment rendering (CWE-79, injecting HTML/script into non-HTML fields). That bug class is inherently about a web application failing to escape untrusted HTML in a browser context. It has no structural analog to a Rust blockchain signer's consensus logic — there's no "HTML rendering," "script injection," or "browser context" concept in `stacks-signer` state machine code. Forcing a mapping between "unescaped HTML field" and "signer signing an invalid block" would require fabricating a causal link that doesn't exist in the code, which I won't do without genuine evidence.

Let me actually check the relevant signer code for the legitimate safety properties mentioned (signed vs validated equality, one-per-height, approved-parent vs canonical, aggregated-weight vs verified-accepts) to see if anything real turns up, rather than force-fitting the XSS analogy.### Title
Stale weight tally in `StackerDBListener` lets a signer's vote-flip inflate/retain the wrong side of the approval/rejection tally - ([File: stacks-node/src/nakamoto_node/stackerdb_listener.rs])

### Summary
The node-side `StackerDBListener` (used by the miner/coordinator to tally signer votes for a proposed block) uses **two different, mutually inconsistent dedup keys** to gate weight accounting for `BlockResponse::Accepted` and `BlockResponse::Rejected` messages from the same signer slot. The accept path gates on `gathered_signatures.contains_key(slot_id)` while the reject path gates on `responded_signers.insert(slot_id)`, and `responded_signers` is inserted on *both* paths. This means once a signer's slot has been counted on one side, a later message of the *other* kind is not correctly reconciled: depending on order, a signer's weight can either be double-counted into both `total_weight_approved` and `total_weight_rejected`, or a signer's later retraction (Accept→Reject) never removes their weight from `total_weight_approved`.

### Finding Description
In `stacks-node/src/nakamoto_node/stackerdb_listener.rs`:

- Accept path (lines 443-465): weight is added to `total_weight_approved` only if `!block.gathered_signatures.contains_key(&slot_id)`, then the code unconditionally inserts into both `gathered_signatures` and `responded_signers`. [1](#0-0) 

- Reject path (line 515): weight is added to `total_weight_rejected` only if `block.responded_signers.insert(slot_id)` returns `true` (i.e., first time this slot is seen at all, across *either* message type). [2](#0-1) 

Two orderings break the intended invariant that `total_weight_approved` and `total_weight_rejected` are disjoint partitions of `total_weight` reflecting each signer's *current* verdict:

1. **Reject, then Accept** (a signer that legitimately re-evaluates from `LocallyRejected` to `LocallyAccepted`, a transition explicitly documented as valid — `docs/signer-flows.md:144`): the first Reject sets `responded_signers.insert(slot_id) == true`, adding weight to `total_weight_rejected`. The later Accept checks `gathered_signatures.contains_key(slot_id)`, which is still `false` (only the reject branch ran), so the same weight is *also* added to `total_weight_approved`. The signer's weight is now double-counted across both tallies, and the stale `total_weight_rejected` contribution is never cleared. [3](#0-2) 

2. **Accept, then Reject** (the symmetric, equally documented transition `LocallyAccepted --> LocallyRejected: re-evaluated`, e.g. after the signer detects a same-height conflict at the pre-commit re-check in `stacks-signer/src/v0/signer.rs:1345-1366`): the Accept adds weight to `total_weight_approved` and inserts into `responded_signers`. The later Reject checks `responded_signers.insert(slot_id)`, which now returns `false` (already present), so the reject branch's `if` body — the only place that would ever *add* rejection weight — never runs, and, critically, nothing anywhere in this file ever *subtracts* from `total_weight_approved`. The node's tally therefore keeps counting this signer as an approver even though their most recent (and only currently valid) verdict is a rejection. [4](#0-3) 

The threshold checks that decide whether the coordinator returns the gathered signatures (`total_weight_approved >= self.weight_threshold`) or declares the block dead (`total_weight_rejected.saturating_add(self.weight_threshold) > self.total_weight`) both live in `signer_coordinator.rs` and rely purely on these two counters, not on a live re-derivation of each signer's current vote. [5](#0-4) 

This breaks the "aggregated-weight vs verified-accepts" equality called out in scope: `total_weight_approved` is supposed to equal the sum of weights of signers whose *current* vote is Accept, but the code allows it to retain weight from signers who have since retracted to Reject.

### Impact Explanation
Scenario 2 (Accept, then Reject) is the more severe direction: a single honest signer that signs a block and then legitimately re-evaluates to reject it (per the documented `LocallyAccepted → LocallyRejected` transition, e.g. because a fresher conflicting sibling block became canonical, `docs/signer-flows.md:250-268`) leaves its weight permanently stuck in `total_weight_approved` on the node side. If enough such signers flip after initially accepting, the coordinator can conclude `total_weight_approved >= weight_threshold` and finalize/push the block using `gathered_signatures` (line 545) even though the *live* set of signers still standing behind the block is below the 70% weight threshold. This does not forge a new signature — each signature in `gathered_signatures` was genuinely produced — but it lets the node treat a block as having met the live 70%-weight safety threshold when the true, current approving weight has fallen below it, i.e. a rejection (retraction) is effectively never subtracted, functionally "recounted" as a standing acceptance. This matches the Critical impact category "aggregated-weight vs verified-accepts" mismatch / a rejection not being reflected in the final tally.

### Likelihood Explanation
The triggering condition (a signer's local state legitimately moving between `LocallyAccepted` and `LocallyRejected` after re-evaluation) is not a hypothetical edge case — it is an explicitly designed and tested state transition in the signer state machine (`BlockInfo::check_state`, `docs/signer-flows.md:130-154`, `signer.rs:1345-1366`'s own-tenure/cross-tenure conflict re-check at the pre-commit threshold). It requires no majority of signers and no key compromise — a single signer naturally hitting this re-evaluation path during normal chain-fork/conflict handling is sufficient to desynchronize the node's tally from the true vote state.

### Recommendation
Track each signer slot's *current* verdict in a single map (e.g., `HashMap<slot_id, Verdict>`) instead of two independently-gated counters (`gathered_signatures` vs `responded_signers`). On any new message, if the slot already has a recorded verdict that differs from the incoming one, subtract the old weight from its counter before adding the new weight to the new counter, so `total_weight_approved` and `total_weight_rejected` always reflect the sum of *current* per-slot verdicts and remain disjoint.

### Proof of Concept
1. Signer S (weight w) sends `BlockResponse::Accepted` for block B → `gathered_signatures[S]` set, `responded_signers` contains S, `total_weight_approved += w`.
2. Signer S's local signer process later re-evaluates B (e.g. a conflicting sibling becomes canonical) and transitions `LocallyAccepted → LocallyRejected`, broadcasting `BlockResponse::Rejected` for B.
3. On the node, `handle` for `Rejected` checks `responded_signers.insert(S)` → returns `false` (already present) → the weight-adding branch is skipped, and `total_weight_approved` is never decremented.
4. `total_weight_approved` therefore continues to include `w` from a signer that no longer endorses B; if the sum of currently-standing approvals is below threshold but this stale `w` pushes `total_weight_approved` at/above `weight_threshold`, `SignerCoordinator::wait_for_block_signatures` (or equivalent) returns `Ok(gathered_signatures)` and the block is pushed, even though live signer weight behind it is insufficient. [3](#0-2) [6](#0-5)

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-519)
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
                        block.responded_signers.insert(slot_id);

                        if block.total_weight_approved >= self.weight_threshold {
                            // Signal to anyone waiting on this block that we have enough signatures
                            cvar.notify_all();
                        }

                        // Update the idle timestamp for this signer
                        self.update_idle_timestamp(
                            signer_pubkey.clone(),
                            tenure_extend_timestamp,
                            signer_entry.weight,
                        );

                        // Update the read-count timestamp for this signer
                        self.update_read_count_timestamp(
                            signer_pubkey,
                            read_count_extend_timestamp,
                            signer_entry.weight,
                        );
                    }
                    SignerMessageV0::BlockResponse(BlockResponse::Rejected(rejected_data)) => {
                        let (lock, cvar) = &*self.blocks;
                        let mut blocks = lock.lock().expect("FATAL: failed to lock block status");

                        let Some(block) = blocks.get_mut(&rejected_data.signer_signature_hash)
                        else {
                            info!(
                                "StackerDBListener: Received rejection for block that we did not request. Ignoring.";
                                "signer_signature_hash" => %rejected_data.signer_signature_hash,
                                "slot_id" => slot_id,
                                "signer_set" => self.signer_set,
                            );
                            continue;
                        };

                        let rejected_pubkey = match rejected_data.recover_public_key() {
                            Ok(rejected_pubkey) => {
                                if rejected_pubkey != signer_pubkey {
                                    warn!("StackerDBListener: Recovered public key from rejected data does not match signer's public key. Ignoring.");
                                    continue;
                                }
                                rejected_pubkey
                            }
                            Err(e) => {
                                warn!("StackerDBListener: Failed to recover public key from rejected data: {e:?}. Ignoring.");
                                continue;
                            }
                        };

                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);

```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L512-545)
```rust
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
