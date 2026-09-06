### Title
Fail-open on `get_tenure_tip` RPC error causes parent-tenure height check to be silently skipped - (File: stacks-signer/src/chainstate/mod.rs)

### Summary
`SortitionData::check_latest_block_in_tenure` (which validates that a proposed block confirms the highest known block of its parent tenure) treats an RPC failure from `client.get_tenure_tip(tenure_id)` as if the proposal is automatically valid, returning `Ok(true)` instead of failing closed. This lets a block proposal with a stale/insufficient `chain_length` bypass the canonical-height comparison against the true parent-tenure tip whenever that RPC call errors.

### Finding Description
The intended safety equality is: a block proposal is only accepted as "confirming the latest block of its parent tenure" if its `chain_length` is verified to exceed the actual chain tip height of that tenure, as reported by the node. The code instead does: [1](#0-0) 

When `client.get_tenure_tip(tenure_id)` returns `Err`, the function logs a warning ("Assuming proposal is higher than the parent tenure for now") and returns `Ok(true)` immediately, entirely skipping the subsequent comparison against `tip.anchored_header` / `nakamoto_tip` height that would otherwise occur.

This preceding local check via `SortitionData::get_tenure_last_block_info` only guards against blocks the signer's own `signerdb` already knows about [2](#0-1) . The `get_tenure_tip` RPC call is the fallback mechanism meant to catch blocks unknown locally by asking the node authoritatively for the tenure's real tip. When this authoritative check errors out (e.g. because the referenced parent tenure was short-lived on a losing fork and the node pruned/never fully retained its tip data — an outcome an attacker can engineer by naming such a `parent_tenure_id`), the code assumes the proposal passes rather than rejecting or deferring judgment. This breaks the fail-closed guarantee: an RPC error is not equivalent to "the proposal is valid," yet it is treated as such.

### Impact Explanation
This breaks the "canonicity" safety property: a block proposal that does not actually confirm the true latest block of its claimed parent tenure can be accepted by this specific check due to an incidental/attacker-induced RPC error, rather than being rejected. Combined with other proposal-validation steps, a signer could contribute an approval toward a non-canonical/conflicting block, which matches the Critical category (signer signing an invalid/non-canonical/conflicting block).

### Likelihood Explanation
An attacker needs only to win one miner slot and craft a `BlockProposal` referencing a `parent_tenure_id` that is a short-lived/losing-fork tenure the node has not fully retained tip data for — no majority signer collusion, no auth token, and no local access are required. This is a plausible, repeatable condition for any tenure that was quickly orphaned, since node retention of tip data for non-canonical/pruned tenures is not guaranteed to succeed on every RPC call.

### Recommendation
Change the error branch to fail closed: on `get_tenure_tip` error, either reject the proposal (`Ok(false)`), retry, or explicitly treat it as "unknown" and defer/reject rather than assuming a pass. At minimum, do not return `Ok(true)` on an RPC error without independent corroboration that the parent tenure has no other/higher block.

### Proof of Concept
Rust test in `stacks-signer/src/chainstate/tests/` mocking/stubbing `StacksClient::get_tenure_tip` to return `Err(ClientError::InvalidResponse(..))` for a given `tenure_id`, then calling `SortitionData::check_latest_block_in_tenure` with a `NakamotoBlock` whose `chain_length` is lower than or equal to the tenure's actual (but unreachable) tip height. Assert the function returns `Ok(true)` (bypass) instead of `Ok(false)`/an error, demonstrating that the safety comparison against `tip.anchored_header`'s chain length at lines 462+ never executes.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L384-419)
```rust
        let last_block_info = SortitionData::get_tenure_last_block_info(
            tenure_id,
            signer_db,
            tenure_last_block_proposal_timeout,
        )?;

        if let Some(info) = last_block_info {
            // N.B. this block might not be the last globally accepted block across the network;
            // it's just the highest one in this tenure that we know about.  If this given block is
            // no higher than it, then it's definitely no higher than the last globally accepted
            // block across the network, so we can do an early rejection here.
            if block.header.chain_length <= info.block.header.chain_length {
                warn!(
                    "Miner's block proposal does not confirm as many blocks as we expect";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "proposed_chain_length" => block.header.chain_length,
                    "expected_at_least" => info.block.header.chain_length + 1,
                );
                if info.signed_group.is_none_or(|signed_time| {
                    signed_time + reorg_attempts_activity_timeout.as_secs() > get_epoch_time_secs()
                }) {
                    // Note if there is no signed_group time, this is a locally accepted block (i.e. tenure_last_block_proposal_timeout has not been exceeded).
                    // Treat any attempt to reorg a locally accepted block as valid miner activity.
                    // If the call returns a globally accepted block, check its globally accepted time against a quarter of the block_proposal_timeout
                    // to give the miner some extra buffer time to wait for its chain tip to advance
                    // The miner may just be slow, so count this invalid block proposal towards valid miner activity.
                    if let Err(e) = signer_db.update_last_activity_time(
                        &block.header.consensus_hash,
                        get_epoch_time_secs(),
                    ) {
                        warn!("Failed to update last activity time: {e}");
                    }
                }
                return Ok(false);
            }
```

**File:** stacks-signer/src/chainstate/mod.rs (L450-461)
```rust
        let tip = match client.get_tenure_tip(tenure_id) {
            Ok(tip) => tip.anchored_header,
            Err(e) => {
                warn!(
                    "Failed to fetch the tenure tip for the parent tenure: {e:?}. Assuming proposal is higher than the parent tenure for now.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "signer_signature_hash" => %block.header.signer_signature_hash(),
                    "parent_tenure" => %tenure_id,
                );
                return Ok(true);
            }
        };
```
