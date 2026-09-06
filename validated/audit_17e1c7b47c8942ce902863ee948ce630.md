Based on my investigation, I found a plausible analog worth reporting: the "outdated-peer fallback" reroute in `store_and_process_block_signature`.

### Title
Peer BlockAccepted signature spoofed to bypass pre-commit validity/threshold gating via the outdated-peer fallback - (File: `stacks-signer/src/v0/signer.rs`)

### Summary
`store_and_process_block_signature` treats a properly-signed `BlockAccepted` from a peer that has not yet sent a `BlockPreCommit` as if it were that peer's pre-commit message, redirecting into `handle_block_pre_commit` [1](#0-0) . This is the same class of issue as the external report: a message intended for one channel (`initiateWithdrawal`/generic message passing) is repurposed to invoke logic gated for a different, more sensitive channel (`ethYieldManager`'s permissioned methods), producing consequences the target logic did not anticipate from that caller.

### Finding Description
`handle_block_pre_commit` gates a peer's pre-commit weight on `block_info.valid.unwrap_or(false)` — i.e. that the *receiving* signer has locally validated the block [2](#0-1) , and increments `commit_weight` only from `get_block_pre_committers`, tallied via `add_block_pre_commit` [3](#0-2) . However, the code path that recorded the signature (`add_block_signature`) already happened unconditionally before the fallback check, meaning the peer's signature is durably persisted as a full "Accepted" vote in `block_signatures` regardless of what this local node's `check_state`/pre-commit gating would have permitted for a genuine pre-commit message [4](#0-3) . Because a legitimate `BlockAccepted` is only ever produced after a peer itself passed the pre-commit threshold and chainstate re-check (see `handle_block_pre_commit`'s SIGN branch) [5](#0-4) , a single misbehaving/outdated-labeled peer can broadcast an `Accepted` response for a block it never actually reached signature-threshold consensus on. The receiving signer's fallback will (a) count this fabricated signature toward `get_block_signatures`/threshold weight later once its own pre-commit vote is separately re-triggered, and (b) permanently store the "signature" in the DB even though it was never validated as a pre-commit by the tallying signer through the normal weight-gated path — this breaks the equality that "a stored block signature implies the signer's own chainstate re-check passed at signature time" (documented invariant in `docs/signer-flows.md` section 6) [6](#0-5) .

### Impact Explanation
This does not require a majority — a single signer (or gossiped/duplicated message from one signer) can trigger the reroute for any node that has not yet seen that signer's pre-commit, letting a signature the local node never independently reconciled through the pre-commit weight-and-recheck gate get durably added to `block_signatures`, contributing to `total_signature_weight` in `store_and_process_block_signature`'s later threshold computation [7](#0-6) . If enough such rerouted signatures accumulate (each individually not majority-controlled), the receiving signer could compute a stale/incorrect acceptance weight and broadcast a block signature set that does not correspond to the current chainstate view, echoing the report's "count as a permissioned operation when the sender lacked the standing to invoke it directly."

### Likelihood Explanation
The fallback is intentionally reachable by any single peer's `BlockAccepted` message (the doc explicitly calls it a deliberate compatibility path for "mixed-version fleets") [8](#0-7) , so the trigger condition (peer has no recorded pre-commit yet) is trivially reachable by a single signer choosing not to send a `BlockPreCommit` before its `BlockAccepted`.

### Recommendation
Before persisting a peer's `BlockAccepted` signature via `add_block_signature`, or before allowing it to later count toward `total_signature_weight`, require that a matching pre-commit is validated through the normal weight/recheck gate at accept-time — i.e. run the same `valid`/threshold/chainstate-recheck gating from `handle_block_pre_commit` synchronously before considering the signature "stored", rather than storing the signature first and re-routing after the fact.

### Proof of Concept
Could not be fully constructed from the index alone — the reroute path is confirmed by static code reading of `store_and_process_block_signature`/`handle_block_pre_commit` [9](#0-8) , and is exercised by the existing test `signers_treat_signatures_as_precommits` in `stacks-node/src/tests/signer/v0/mod.rs`, which specifically validates the intended (benign) use of this fallback for legacy compatibility [10](#0-9) . Whether an actual weight-inflation or wedge condition can be forced end-to-end (i.e., whether later re-validation always self-corrects) was not confirmed within the available context, so this should be treated as a plausible-but-unconfirmed analog requiring further live testing before being escalated as a proven vulnerability.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L1276-1296)
```rust
        // are misbehaving, offline, etc.
        // commit message is from a valid sender! store it
        self.signer_db
            .add_block_pre_commit(block_hash, stacker_address)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block pre-commit"));

        let block_hash = block_info.block.header.signer_signature_hash();
        // do we have enough pre-commits to reach consensus?
        // i.e. is the threshold reached?
        //
        // Tally this up front, before the early returns below, so that every pre-commit we
        // receive can be logged with the running weight. Crossing this threshold is what
        // triggers our block response, so without it the wait for the threshold, which can
        // be minutes and is the bulk of a stalled block's latency, leaves no trace at all.
        let committers = self
            .signer_db
            .get_block_pre_committers(&block_hash)
            .unwrap_or_else(|_| panic!("{self}: Failed to load block commits"));

        let commit_weight = self.compute_signature_signing_weight(committers.iter());
        let total_weight = self.compute_signature_total_weight();
```

**File:** stacks-signer/src/v0/signer.rs (L1323-1331)
```rust
        if !block_info.valid.unwrap_or(false) {
            // We received a pre-commit for a block that we have not validated or we have already marked this block as invalid.
            // We should not do anything further as we do not know what our response should be and we do not change our votes on rejected
            // blocks unless we receive a new block proposal for it and the reject reason allows us to reconsider.
            debug!(
                "{self}: Received a pre-commit for a block that we have not determined to be valid: {:?}. Doing nothing...", block_info.valid
            );
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1345-1345)
```rust
        if let Some(block_rejection) =
```

**File:** stacks-signer/src/v0/signer.rs (L2452-2466)
```rust
        // signature is valid! store it.
        // if this returns false, it means the signature already exists in the DB, so just return.
        if !self
            .signer_db
            .add_block_signature(block_hash, signer_address, signature)
            .unwrap_or_else(|_| panic!("{self}: Failed to save block signature"))
        {
            return;
        }

        // If this isn't our own signature and we haven't seen a pre-commit from this signer yet, try treating it as a pre-commit in case the caller is running an outdated version
        if signer_address != &self.stacks_address && !self.signer_db.has_committed(block_hash, signer_address).inspect_err(|e| warn!("Failed to check if pre-commit message already considered for {signer_address:?} for {block_hash}: {e}")).unwrap_or(false) {
            self.handle_block_pre_commit(stacks_client, sortition_state, signer_address, block_hash);
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2494-2496)
```rust
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_signature_weight = self.compute_signature_signing_weight(addrs_to_sigs.keys());
        let total_weight = self.compute_signature_total_weight();
```

**File:** docs/signer-flows.md (L377-383)
```markdown
The outdated-peer fallback keeps mixed-version fleets live: an acceptance from a
peer that never sent a pre-commit is routed into the pre-commit path instead, so
that peer's weight still counts toward the threshold that produces _our_
signature. Note that reaching 70% signatures still only marks the block
_locally_ accepted with the group timestamp; global acceptance waits for the node
to adopt it. Marking the miner invalid on a 30% `ReorgNotAllowed` rejection is
skipped once the active protocol version uses global signer state.
```

**File:** stacks-node/src/tests/signer/v0/mod.rs (L8977-8983)
```rust
// Test to ensure a signer operating a two phase commit signer will treat
// signatures from other signers as pre-commits if it has yet to see their pre-commits
// for that block. This enables upgraded pre-commit signers to operate as they should
// with unupgraded signers or if the pre-commit message was somehow dropped.
#[test]
#[ignore]
fn signers_treat_signatures_as_precommits() {
```
