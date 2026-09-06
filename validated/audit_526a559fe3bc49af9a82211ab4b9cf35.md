### Title
Peer signature tally bypasses local validity check, letting a signer locally accept and broadcast a block it never validated (or already rejected) — ([File: stacks-signer/src/v0/signer.rs])

### Summary
`store_and_process_block_signature` tallies peer `BlockAccepted` signatures and, once the aggregate weight crosses the 70% threshold, calls `block_info.mark_locally_accepted(true)` and `broadcast_signed_block(...)` — without ever checking `block_info.valid`. Its sibling function, `handle_block_pre_commit`, which performs the analogous "have we crossed the threshold" tally, explicitly guards this with `if !block_info.valid.unwrap_or(false) { return; }` before acting. The missing guard in `store_and_process_block_signature` is the direct analog of the Sablier `refundableAmountOf` bug: a value/action is produced by checking some flags (`signed_group.is_some()`, weight threshold) while omitting the one flag (`valid`) that determines whether the action is actually warranted.

### Finding Description
`handle_block_signature` (stacks-signer/src/v0/signer.rs, ~2372-2440) receives an observed `BlockAccepted` signature from a peer, authenticates the signer and recovers the block info via `block_lookup_by_reward_cycle`, then unconditionally calls: [1](#0-0) 

Inside `store_and_process_block_signature` (~2442-2538), after storing the signature, the function:
1. Redirects to `handle_block_pre_commit` only if the sender never sent a pre-commit (outdated-peer fallback): [2](#0-1) 
2. Otherwise it returns early only if `signed_group.is_some()`: [3](#0-2) 
3. Computes total signature weight and the threshold, and if the threshold is met, marks the block locally accepted and broadcasts it: [4](#0-3) 

At no point does this path check `block_info.valid`. Contrast this with `handle_block_pre_commit`, which reaches the exact same kind of threshold decision but explicitly refuses to act unless the signer's own validation verdict is `Some(true)`: [5](#0-4) 

Because `has_committed` gates the fallback re-route into `handle_block_pre_commit`, any peer who *did* send a pre-commit first (the expected common case) causes their later `BlockAccepted` signature to flow straight through `store_and_process_block_signature`'s weight tally, with no re-check of this signer's own `valid` field. This means:
- A block this signer never validated (`valid == None`, e.g. recovered purely from `block_lookup_by_reward_cycle` before validation completed) can be marked `mark_locally_accepted` purely because other signers' weight crossed 70%.
- A block this signer already rejected (`valid == Some(false)`, via `handle_block_validate_reject`/`mark_locally_rejected`) can later be silently flipped to `LocallyAccepted` by peer signature weight alone, with no re-validation step — i.e., a locally-rejected block gets recounted as accepted.

`mark_locally_accepted` stamps `signed_group`/`approved_time` in `BlockInfo`, which feeds `get_last_signed_block` / `get_tenure_last_block_info`, the exact helpers other chainstate checks (`check_latest_block_in_tenure`, described in docs/signer-flows.md §7) use to decide what this signer has "signed." Poisoning this state with a block the signer never actually validated corrupts its own view of tenure tips and conflict detection for all subsequent proposals, and additionally causes `broadcast_signed_block` to push the block to the node.

### Impact Explanation
This is a "rejection recounted as an accept" — the exact Critical-impact class called out in the rules. A signer's own negative (or absent) validation verdict is overridden purely by counting peer signature weight, with no re-validation gate that the sibling pre-commit path enforces. Beyond the immediate broadcast, the corrupted `BlockInfo`/`signed_group` state feeds tenure-tip and conflict-detection logic used by later proposal/signing decisions, so it can also skew this signer's future pre-commit/signing behavior for subsequent blocks in the same tenure.

### Likelihood Explanation
Triggerable by ordinary gossip from any signer(s) whose combined weight reaches the 70% threshold and who send pre-commits before their acceptance signatures (the expected common flow, which is exactly what avoids the `handle_block_pre_commit` fallback re-route). No majority collusion, forged keys, or auth token access is required by the attacker beyond already having signer/gossip presence — the vulnerable signer's own logic path is what's missing the check.

### Recommendation
Add the same guard used in `handle_block_pre_commit` to `store_and_process_block_signature` before honoring the threshold: only proceed to `mark_locally_accepted`/`broadcast_signed_block` if `block_info.valid == Some(true)`; otherwise hold/park the signature (or run the same chainstate re-check path used elsewhere) until local validation confirms the block.

### Proof of Concept
1. Signer S receives a block proposal and either has not yet received its own validation response (`valid == None`) or has received a `Reject` and called `mark_locally_rejected` (`valid == Some(false)`).
2. Other signers, having validated the block as fine on their end, send pre-commits (recorded via `add_block_pre_commit`/`has_committed`) and subsequently `BlockAccepted` signatures reaching ≥70% weight.
3. S's `handle_block_signature` recovers `block_info` (already tracked, `valid` unfavorable) and calls `store_and_process_block_signature`.
4. Since `has_committed` is true for the senders, the fallback to `handle_block_pre_commit` (which would have checked `valid`) is skipped; S's `signed_group` is unset, so the early return at line 2468-2471 does not fire.
5. Weight tally passes the threshold check at line 2503; S calls `mark_locally_accepted(true)` and `broadcast_signed_block`, even though S's own `valid` field was `None`/`Some(false)` and was never re-checked.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1316-1338)
```rust
        if block_info.signed_self.is_some() {
            debug!(
                "{self}: Received pre-commit for a block that we have already signed. Doing nothing...",
            );
            return;
        }

        if !block_info.valid.unwrap_or(false) {
            // We received a pre-commit for a block that we have not validated or we have already marked this block as invalid.
            // We should not do anything further as we do not know what our response should be and we do not change our votes on rejected
            // blocks unless we receive a new block proposal for it and the reject reason allows us to reconsider.
            debug!(
                "{self}: Received a pre-commit for a block that we have not determined to be valid: {:?}. Doing nothing...", block_info.valid
            );
            return;
        }

        if min_weight > commit_weight {
            debug!(
                "{self}: Not enough pre-committed to block {block_hash} (have {commit_weight}, need at least {min_weight}/{total_weight})"
            );
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2433-2439)
```rust
        self.store_and_process_block_signature(
            stacks_client,
            sortition_state,
            &mut block_info,
            &signer_address,
            signature,
        );
```

**File:** stacks-signer/src/v0/signer.rs (L2462-2466)
```rust
        // If this isn't our own signature and we haven't seen a pre-commit from this signer yet, try treating it as a pre-commit in case the caller is running an outdated version
        if signer_address != &self.stacks_address && !self.signer_db.has_committed(block_hash, signer_address).inspect_err(|e| warn!("Failed to check if pre-commit message already considered for {signer_address:?} for {block_hash}: {e}")).unwrap_or(false) {
            self.handle_block_pre_commit(stacks_client, sortition_state, signer_address, block_hash);
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

**File:** stacks-signer/src/v0/signer.rs (L2503-2537)
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
```
