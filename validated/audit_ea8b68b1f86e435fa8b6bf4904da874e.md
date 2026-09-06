### Title
Group-signature threshold path skips the chainstate re-check that guards against signing/broadcasting a conflicting block — (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`store_and_process_block_signature`, which tallies peer `BlockResponse::Accepted` messages toward the 70% signature threshold and triggers `broadcast_signed_block`, does not re-run `check_block_against_signer_db_state` before marking the block `LocallyAccepted` and pushing it to the node. This is the one path in the "sign" state machine that reaches `mark_locally_accepted` / `broadcast_signed_block` without repeating the freshness/conflict re-check that every other path to a signature (`handle_block_validate_ok`, `handle_block_pre_commit`) performs immediately before spending or aggregating a signature.

### Finding Description
The signer has two ways to reach a "sign it" outcome:

1. **Pre-commit → signature** (`handle_block_pre_commit`, `stacks-signer/src/v0/signer.rs:1250-1374`): before signing, it explicitly re-runs `check_block_against_signer_db_state` [1](#0-0)  and then the conflict guard (`get_signed_conflicts`/`conflict_still_blocks`), specifically because "the chain and signer db state may have changed materially since this block passed the proposal-time checks" (comment at lines 1340-1344).

2. **Aggregating peer signatures → broadcast** (`store_and_process_block_signature`, `stacks-signer/src/v0/signer.rs:2442-2538`): when a peer's `Accepted` message arrives and that peer has *already* sent a pre-commit we recorded earlier (`has_committed(...)` is true), the code skips straight past the `if signer_address != &self.stacks_address && !has_committed(...)` branch [2](#0-1) , stores the signature, tallies weight, and — once `total_signature_weight >= min_weight` — calls `block_info.mark_locally_accepted(true)` and `broadcast_signed_block` [3](#0-2) . Nowhere in this function is `check_block_against_signer_db_state` (or the pre-commit conflict guard) invoked.

The precommit that made `has_committed` true was checked against chainstate *at the time it arrived*, not at the time the 70%-signature threshold is crossed. Per the documented design (`docs/signer-flows.md:229-347`), the entire purpose of re-running `check_block_against_signer_db_state` at the moment of signing is that "between validation and threshold, we may have signed a _different_ block at the same height, possibly in another tenure" — i.e., the local view can change between pre-commit and the moment a signature is actually produced/relayed. `store_and_process_block_signature` is exactly that later moment for the group-threshold path, yet it has no re-check.

Concretely: signer S pre-commits to block B (chainstate consistent at that time). Afterward, S signs a conflicting sibling block B′ at the same height (e.g., via the `handle_block_pre_commit` own-tenure/cross-tenure conflict-refresh logic resolving in favor of B′, per `signer.rs:1368-1727` and `docs/signer-flows.md:250-268`). Once B′ is signed, `check_block_against_signer_db_state` would now reject B if re-evaluated (its own-tenure/parent-tenure "latest signed block" check would fail, `docs/signer-flows.md:389-418`). But if enough *other* signers' `Accepted` messages for B arrive after S already recorded their pre-commits earlier, `store_and_process_block_signature` tallies them, crosses 70%, and calls `mark_locally_accepted` + `broadcast_signed_block` for B — without ever re-asking whether B still passes chainstate checks against S's now-updated local state (which includes the just-signed B′).

This breaks the "signed vs validated" equality the guard exists to preserve: a block can be marked `LocallyAccepted` and pushed toward the node by this signer via the signature-aggregation path even though the same signer's own chainstate view (updated since the last check) would reject it via the pre-commit path.

### Impact Explanation
This falls under "a signer signing an invalid, non-canonical, or conflicting block" / contributing to relaying such a block without the safety re-check that every other code path enforces. Since `broadcast_signed_block` feeds `handle_post_block`, which hands the block to the node, a stale-chainstate relay of a block that the signer's own current view would otherwise reject undermines the double-sign/conflict protection the codebase deliberately implements everywhere else (`docs/signer-flows.md:274-341` explicitly frames this re-check as required "because a signature can outlive the block it covers").

### Likelihood Explanation
Requires only:
- The attacker/miner (single "one-slot miner (plus gossip)" actor per scope) to produce two conflicting proposals at the same height in normal fashion (already a supported, tested scenario per `stacks-signer/src/v0/tests.rs` sibling-conflict tests, e.g. `signer_refuses_to_sign_second_sibling_tenure_start`).
- Ordinary gossip timing: pre-commits for B arrive and are recorded before S signs the conflicting B′, and B's `Accepted` signatures from peers (who validated B before ever seeing B′) arrive after. No majority collusion, no other signer's key, and no auth token are needed — the described sequence relies only on normal message ordering that gossip cannot guarantee, which is exactly the class of race the pre-commit path was hardened against but the signature-aggregation path was not.

### Recommendation
Add a `check_block_against_signer_db_state` (and, if warranted, the conflict guard) call in `store_and_process_block_signature` immediately before `mark_locally_accepted`/`broadcast_signed_block`, mirroring the re-check already performed in `handle_block_pre_commit`, so the group-signature-threshold path cannot promote/broadcast a block that the signer's own updated chainstate view would otherwise reject.

### Proof of Concept
Not directly executable from the indexed code alone (no access to a live multi-signer test harness in this session); the sequence to reproduce is:
1. Configure ≥2 signers; have the miner (or an equivalent proposer) propose block B at height h in tenure T, and get a subset of signers to pre-commit to B (recorded via `add_block_pre_commit`).
2. Before threshold is reached, have signer S sign a conflicting sibling B′ at height h (any tenure), following the documented stale-conflict-replacement path in `handle_block_pre_commit` (`signer.rs:1368-1727`), so S's local chainstate (`check_block_against_signer_db_state`) would now reject B.
3. Have the previously-pre-committed peers broadcast `BlockResponse::Accepted` for B (their own local views may not yet reflect S's B′ signature).
4. On S, these accepted messages flow through `store_and_process_block_signature`; since `has_committed` is already true for those peers, the code skips to tallying and, upon reaching threshold, calls `mark_locally_accepted`/`broadcast_signed_block` for B on S — without S re-validating B against its now-conflicting local state.

I was not able to inspect `mark_locally_accepted`/`BlockInfo::check_state`'s exact enforcement logic in `signerdb.rs` in this session (tool budget exhausted before retrieving those definitions), so it remains unconfirmed whether `check_state`/`move_to` independently blocks this transition at the state-machine level; this should be verified directly against `stacks-signer/src/signerdb.rs` before treating the recommendation as sufficient on its own.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1345-1366)
```rust
        if let Some(block_rejection) =
            self.check_block_against_signer_db_state(stacks_client, &block_info.block)
        {
            warn!(
                "{self}: Reached the pre-commit threshold for a block, but it no longer passes the chainstate checks. Rejecting.";
                "signer_signature_hash" => %block_hash,
                "block_height" => block_info.block.header.chain_length,
                "reject_code" => %block_rejection.reason_code,
                "reject_reason" => &block_rejection.reason,
            );
            if let Err(e) = block_info.mark_locally_rejected() {
                if !block_info.has_reached_consensus() {
                    warn!("{self}: Failed to mark block as locally rejected: {e:?}");
                }
            };
            self.signer_db
                .insert_block(&block_info)
                .unwrap_or_else(|e| self.handle_insert_block_error(e));
            self.handle_block_rejection(&block_rejection, sortition_state);
            self.send_block_response(&block_info.block, block_rejection.into());
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

**File:** stacks-signer/src/v0/signer.rs (L2503-2538)
```rust
        if min_weight > total_signature_weight {
            info!("{self}: Received block acceptance, but have not yet reached the acceptance threshold.";
                "signer_signature_hash" => %block_hash,
                "signature_weight" => signature_weight,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "block_height" => block_info.block.header.chain_length,
                "total_weight_approved" => total_signature_weight,
                "total_weight" => total_weight,
                "percent_approved" => (total_signature_weight as f64 / total_weight as f64 * 100.0),
            );
            return;
        }
        info!("{self}: have reached the block acceptance threshold";
            "signer_signature_hash" => %block_hash,
            "signature_weight" => signature_weight,
            "consensus_hash" => %block_info.block.header.consensus_hash,
            "block_height" => block_info.block.header.chain_length,
            "total_weight_approved" => total_signature_weight,
            "total_weight" => total_weight,
            "percent_approved" => (total_signature_weight as f64 / total_weight as f64 * 100.0),
        );

        // have enough signatures to broadcast!
        // move block to LOCALLY accepted state.
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
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
    }
```
