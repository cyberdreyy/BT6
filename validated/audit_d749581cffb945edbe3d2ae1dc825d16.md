### Title
Byzantine signer's weight can be double-counted in both the reject-tally and accept-tally for the same block, letting a signer conclude "globally rejected" while the real network reaches acceptance — ([File: stacks-signer/src/v0/signer.rs])

### Summary
`SignerDb::add_block_rejection_signer_addr` refuses to record a rejection for a signer that already has a stored signature (acceptance) for the same block, but the symmetric guard does not exist on the acceptance path: `store_and_process_block_signature` calls `add_block_signature` with no check for a pre-existing rejection record from that signer. Because the local per-block tallies `total_reject_weight` (from `block_rejection_signer_addrs`) and `total_signature_weight` (from `block_signatures`) are computed independently, a single signer that first rejects and then accepts the same block gets its weight counted in *both* pools simultaneously.

### Finding Description
`add_block_rejection_signer_addr` explicitly guards one direction: [1](#0-0) 
i.e. once a signature exists for `(block, signer)`, a later rejection from the same signer is dropped.

The reverse transition is not guarded. `store_and_process_block_signature` stores an acceptance via `add_block_signature` unconditionally (aside from de-duplicating an identical prior signature) and never checks `block_rejection_signer_addrs`: [2](#0-1) 

This asymmetry is legitimate in isolation — the local `BlockInfo` state machine explicitly allows `LocallyRejected --> LocallyAccepted` on re-evaluation: [3](#0-2) 
— but a malicious signer can exploit the same code path deliberately: broadcast a `BlockResponse::Rejected` for a block, then broadcast a `BlockResponse::Accepted` for the identical block. Both get durably recorded, and the observer's independent weight computations, [4](#0-3) [5](#0-4) 
each include that signer's full weight `R`.

Because the two thresholds are evaluated against `total_weight` (the sum of *all* signer weights) rather than against weight that has been reserved to one side, an attacker's weight `R` counts toward both the ≥70% acceptance bar and the >30%-blocking rejection bar at once. With disjoint honest votes `X` (reject) and `Y` (accept) satisfying `X ≥ threshold − R` and `Y ≥ threshold − R`, both conditions can be met simultaneously as soon as `R ≥ 2·threshold − total_weight` (≈40% of weight for a 70% threshold) — well under a majority.

### Impact Explanation
An observing signer that independently reaches the rejection threshold calls `mark_globally_rejected` on the block: [6](#0-5) 
This is a *local, terminal* decision (`GloballyRejected` is unreachable from `GloballyAccepted` and vice versa per `BlockInfo::check_state`): [7](#0-6) 
Meanwhile the same block's honest-signer acceptance weight can independently cross 70%, get pushed to the stacks-node, and become the canonical chain tip. The affected signer is now permanently wedged with a locally-rejected verdict on a block that is in fact canonical, breaking the "aggregated-weight vs verified-accepts" invariant this codebase depends on (a rejection is effectively double-spent into a false rejection consensus). This matches the report's core bug class — two internally tracked tallies going out of sync because a value that should be mutually exclusive between two accounting buckets is not — analogous to the EVM precompile issue where an intermediate/`dirty` value diverged from what downstream logic assumed was final. The concrete signer-side consequence falls under "a rejection recounted as an accept"/miscounted-response territory and can wedge the affected signer out of future correct participation on that fork.

### Likelihood Explanation
Requires only a single Byzantine signer (using its own key) controlling roughly ≥40% of signer weight (for a 70%/30% split threshold) — well below a majority — and the ability to send two contradictory, individually-valid `BlockResponse` messages over StackerDB, both of which pass authentication (`is_valid_signer`) and are honored by the existing state machine's legitimate reject→accept re-evaluation path. No forged signatures, no cooperation from other signers, and no timing race with the node's own `verify_signer_signatures` check are needed.

### Recommendation
Make the reject and accept weight ledgers mutually exclusive per signer: when recording an acceptance signature, remove/ignore any prior rejection record for that `(block, signer)` pair (and vice versa is already partially done), and/or compute `total_reject_weight` and `total_signature_weight` from disjoint sets, e.g. only counting a signer's most recent verdict, or refusing to accept a flipped vote once either tally is being finalized (mirroring the existing "cannot reject after a signature exists" guard with an analogous "cannot re-add a signature weight to accept tally if that signer's weight already counted toward a locally-rejected decision" check).

### Proof of Concept
1. Set up a reward-cycle signer set where one Byzantine signer `S` controls ≈40%+ of total weight, and the remainder is split so that the rest can independently reach ~30%+ reject weight and ~30%+ accept weight among *different* honest signers (feasible because thresholds only need `threshold - R` from honest peers).
2. `S` broadcasts `BlockResponse::Rejected` for block `B`. Other honest signers together contribute reject weight `X` such that `X + R ≥ NakamotoBlockHeader::compute_voting_weight_threshold`-derived reject bar; an observing signer's `store_and_process_block_rejection` calls `mark_globally_rejected(B)` (`stacks-signer/src/v0/signer.rs:2335`).
3. `S` then broadcasts `BlockResponse::Accepted` for the same `B`. `add_block_signature` stores it without checking the existing rejection row (`stacks-signer/src/v0/signer.rs:2454-2460`, `stacks-signer/src/signerdb.rs` lacking a symmetric guard vs. `add_block_rejection_signer_addr:1922-1941`). Other honest signers contribute accept weight `Y` such that `Y + R` crosses the acceptance threshold at `store_and_process_block_signature` (`stacks-signer/src/v0/signer.rs:2503-2537`), triggering `broadcast_signed_block` and pushing `B` to the node.
4. The observing signer from step 2 is left with `B` marked `GloballyRejected` in its own `SignerDb` while `B` is simultaneously the real, node-adopted canonical block — demonstrating the desync.

### Citations

**File:** stacks-signer/src/signerdb.rs (L313-329)
```rust
    /// Check if the block state transition is valid
    fn check_state(&self, state: BlockState) -> bool {
        let prev_state = &self.state;
        if *prev_state == state {
            return true;
        }
        match state {
            BlockState::Unprocessed => false,
            BlockState::LocallyAccepted | BlockState::LocallyRejected => !matches!(
                prev_state,
                BlockState::GloballyRejected | BlockState::GloballyAccepted
            ),
            BlockState::GloballyAccepted => !matches!(prev_state, BlockState::GloballyRejected),
            BlockState::GloballyRejected => !matches!(prev_state, BlockState::GloballyAccepted),
            BlockState::PreCommitted => matches!(prev_state, BlockState::Unprocessed),
        }
    }
```

**File:** stacks-signer/src/signerdb.rs (L1922-1941)
```rust
    /// Record an observed block rejection_signature
    pub fn add_block_rejection_signer_addr(
        &self,
        block_sighash: &Sha512Trunc256Sum,
        addr: &StacksAddress,
        reject_reason: RejectReasonPrefix,
    ) -> Result<bool, DBError> {
        // If this signer/block already has a signature, do not allow a rejection
        let sig_qry = "SELECT EXISTS(SELECT 1 FROM block_signatures WHERE signer_signature_hash = ?1 AND signer_addr = ?2)";
        let sig_args = params![block_sighash, addr.to_string()];
        let exists = self.db.query_row(sig_qry, sig_args, |row| row.get(0))?;
        if exists {
            warn!("Cannot add block rejection because a signature already exists.";
                "signer_signature_hash" => %block_sighash,
                "signer_address" => %addr,
                "reject_reason" => ?reject_reason
            );
            return Ok(false);
        }

```

**File:** stacks-signer/src/v0/signer.rs (L2295-2313)
```rust
        // do we have enough signatures to mark a block a globally rejected?
        // i.e. is (set-size) - (threshold) + 1 reached.
        let rejection_addrs = match self.signer_db.get_block_rejection_signer_addrs(block_hash) {
            Ok(addrs) => addrs,
            Err(e) => {
                warn!("{self}: Failed to load block rejection addresses: {e:?}.",);
                return;
            }
        };
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_reject_weight =
            self.compute_signature_signing_weight(rejection_addrs.iter().map(|(addr, _)| addr));
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });
        if total_reject_weight.saturating_add(min_weight) <= total_weight {
```

**File:** stacks-signer/src/v0/signer.rs (L2335-2341)
```rust
        if let Err(e) = block_info.mark_globally_rejected() {
            warn!("{self}: Failed to mark block as globally rejected: {e:?}",);
        }
        if let Err(e) = self.signer_db.insert_block(block_info) {
            error!("{self}: Failed to update block state: {e:?}",);
            panic!("{self} Failed to update block state: {e}");
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2442-2466)
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

        // If this isn't our own signature and we haven't seen a pre-commit from this signer yet, try treating it as a pre-commit in case the caller is running an outdated version
        if signer_address != &self.stacks_address && !self.signer_db.has_committed(block_hash, signer_address).inspect_err(|e| warn!("Failed to check if pre-commit message already considered for {signer_address:?} for {block_hash}: {e}")).unwrap_or(false) {
            self.handle_block_pre_commit(stacks_client, sortition_state, signer_address, block_hash);
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2494-2523)
```rust
        let signature_weight = self.signer_weights.get(signer_address).unwrap_or(&0);
        let total_signature_weight = self.compute_signature_signing_weight(addrs_to_sigs.keys());
        let total_weight = self.compute_signature_total_weight();

        let min_weight = NakamotoBlockHeader::compute_voting_weight_threshold(total_weight)
            .unwrap_or_else(|_| {
                panic!("{self}: Failed to compute threshold weight for {total_weight}")
            });

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
```

**File:** docs/signer-flows.md (L140-150)
```markdown
    Unprocessed --> PreCommitted : mark_pre_committed
    PreCommitted --> LocallyAccepted : mark_locally_accepted = WE SIGN
    Unprocessed --> LocallyRejected : mark_locally_rejected
    PreCommitted --> LocallyRejected : mark_locally_rejected
    LocallyRejected --> LocallyAccepted : re-evaluated
    LocallyAccepted --> LocallyRejected : re-evaluated
    LocallyAccepted --> GloballyAccepted : mark_globally_accepted
    LocallyRejected --> GloballyRejected : mark_globally_rejected
    GloballyAccepted --> [*]
    GloballyRejected --> [*]
```
```
