Based on the investigation, the strongest analog I found is a weight-accounting asymmetry in the node-side signer response aggregator, not a signer-side signature-forging bug. I want to be explicit that I was not able to fully confirm (within the remaining budget) the exact signer-side call path that emits a `Rejected` response for a block the same signer previously `Accepted` for — I can show the state machine *permits* `LocallyAccepted → LocallyRejected` re-evaluation (`stacks-signer/src/signerdb.rs` `check_state`), and the docs describe this as a real supported transition, but I did not trace the concrete triggering call site before running out of iterations.

### Title
Stale/Retracted Signer Vote Weight Persists in Node-Side Signature Aggregation - (File: stacks-node/src/nakamoto_node/stackerdb_listener.rs)

### Summary
`StackerDBListener` accumulates `total_weight_approved` and `total_weight_rejected` for a block using two different, non-symmetric dedupe checks (`gathered_signatures.contains_key(&slot_id)` for accepts, `responded_signers.insert(&slot_id)` for rejects), both writing into the same shared `responded_signers` set. If a signer sends an `Accepted` response and later a `Rejected` response for the same block (a transition the signer's own local state machine explicitly allows: `BlockState::check_state` permits `LocallyAccepted -> LocallyRejected`), the aggregator never removes the earlier accepted weight/signature, and the later rejection is silently dropped because `responded_signers` already contains the slot id.

### Finding Description [1](#0-0) 
On `BlockResponse::Accepted`, weight is added to `total_weight_approved` only if the slot id is not already in `gathered_signatures`, then the signature is inserted into `gathered_signatures` and `responded_signers`. [2](#0-1) 
On `BlockResponse::Rejected`, weight is added to `total_weight_rejected` only if `responded_signers.insert(slot_id)` returns `true` (i.e., this slot id has never responded before, in *either* direction).

Because both branches gate on membership in the *shared* `responded_signers` set but only the accept path actually persists the signature into a separate map, the two directions are not symmetric:

- Accept → later Reject: `responded_signers` already contains the slot id (set during the earlier Accept), so the `if` guard for `Rejected` is `false` and `total_weight_rejected` is never incremented. The stale accepted signature remains in `gathered_signatures` and its weight remains in `total_weight_approved` forever.
- Reject → later Accept: `gathered_signatures` does not yet contain the slot id (only accepts populate it), so `total_weight_approved` is incremented — but the previously counted `total_weight_rejected` weight from that signer is never decremented.

In both directions, a single signer's weight can be counted toward *both* `total_weight_approved` and `total_weight_rejected` simultaneously, breaking the implicit invariant that each signer's weight should count once, toward whichever vote is current. The signer-side `SignerDb` explicitly implements the correct behavior for the analogous local bookkeeping — `add_block_signature` deletes any prior rejection row when a signature arrives ( [3](#0-2) ) — showing the project's own design intent is "a signature supersedes a rejection," which the node-side aggregator in `stackerdb_listener.rs` fails to enforce, and which has no reciprocal handling for reject-supersedes-accept at all.

### Impact Explanation
`SignerCoordinator::wait_for_p2p_block_response` (or equivalent) uses `block_status.total_weight_approved` and `block_status.gathered_signatures` to decide when 70% has signed and to assemble the final aggregate signature set pushed to the network: [4](#0-3) 
If a signer accepted a block and then legitimately reversed its decision (rejected it) once, e.g., chain state changed enough to disqualify the block per the signer's own re-evaluation logic, the coordinator can still include that signer's stale, retracted signature in the assembled set and count its weight toward the acceptance threshold — potentially pushing a block that the live signer set no longer actually endorses at the required 70% weight. This degrades the "signed vs validated" equality the whole pre-commit/threshold design is built to guarantee (see `docs/signer-flows.md` sections 5–6), since the aggregate threshold decision does not reflect signers' current votes.

### Likelihood Explanation
Unconfirmed/low-to-moderate. I verified the code-level asymmetry directly, but I did not confirm within the available time whether the current signer implementation (`stacks-signer/src/v0/signer.rs`) actually emits a fresh `BlockResponse::Rejected` message for a block it previously sent `BlockResponse::Accepted` for (versus only ever re-evaluating before its first signature). The state-transition table in `signerdb.rs` structurally permits it, and the project's docs describe `LocallyAccepted → LocallyRejected` as a supported re-evaluation path, which suggests the transition is reachable, but the exact triggering event (burnchain reorg, competing pre-commit, chainstate recheck failure after signing) needs confirmation against the real call sites before treating this as fully proven exploitable.

### Recommendation
In `stackerdb_listener.rs`, make the two branches symmetric and mutually exclusive per signer: when processing `Accepted`, if the slot id is already recorded as rejected, subtract/clear the previously counted `total_weight_rejected` (and vice versa for `Rejected` after `Accepted`), or maintain a single "current vote" map per slot id (rather than a monotonic `responded_signers` set plus a separate `gathered_signatures` map) so a flip in either direction correctly moves weight from one bucket to the other instead of accumulating in both.

### Proof of Concept
Conceptual (not exercised against a live node/signer set):
1. Miner proposes block B; signer S validates it, pre-commits, then signs — sends `BlockResponse::Accepted(B)`. `StackerDBListener` records slot(S) in `gathered_signatures`, `responded_signers`, and adds S's weight to `total_weight_approved`.
2. Before B reaches 70% weight, the chain state changes such that S's local re-evaluation (`check_block_against_signer_db_state`/pre-commit re-check) now judges B invalid/conflicting, and S's local state machine transitions `LocallyAccepted -> LocallyRejected`, so S broadcasts `BlockResponse::Rejected(B)`.
3. In `stackerdb_listener.rs`, the `Rejected` handler's `if block.responded_signers.insert(slot_id)` is `false` (already inserted in step 1), so `total_weight_rejected` is never incremented and S's stale acceptance signature is never purged from `gathered_signatures`.
4. If other signers' votes bring `total_weight_approved` (still including S's stale weight) to ≥ 70%, the coordinator assembles and pushes the block using S's retracted signature as if it were current.

### Citations

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L443-465)
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
```

**File:** stacks-node/src/nakamoto_node/stackerdb_listener.rs (L515-518)
```rust
                        if block.responded_signers.insert(slot_id) {
                            block.total_weight_rejected = block
                                .total_weight_rejected
                                .saturating_add(signer_entry.weight);
```

**File:** stacks-signer/src/signerdb.rs (L1877-1881)
```rust
        // Remove any block rejection entry for this signer and block hash
        let del_qry = "DELETE FROM block_rejection_signer_addrs WHERE signer_signature_hash = ?1 AND signer_addr = ?2";
        let del_args = params![block_sighash, signer_addr.to_string()];
        self.db.execute(del_qry, del_args)?;

```

**File:** stacks-node/src/nakamoto_node/signer_coordinator.rs (L541-545)
```rust
            } else if block_status.total_weight_approved >= self.weight_threshold {
                info!("Received enough signatures, block accepted";
                    "signer_signature_hash" => %block_signer_sighash,
                );
                return Ok(block_status.gathered_signatures.values().cloned().collect());
```
