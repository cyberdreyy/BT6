## Title
Late-arriving signatures can push a `GloballyRejected` block over threshold and get it broadcast to the node - ([File: stacks-signer/src/v0/signer.rs])

### Summary
`store_and_process_block_signature` tallies acceptance signatures and decides whether to broadcast a block to the node purely by weight, without first checking whether the block has already reached consensus as **rejected**. By contrast, the sibling rejection path (`handle_block_rejection`) explicitly bails out with `block_info.has_reached_consensus()` before tallying. This asymmetry lets a block this signer already treats as `GloballyRejected` be pushed to the stacks-node once enough (possibly late) acceptance signatures accumulate, because the failure of `mark_locally_accepted` on that block is silently swallowed and does not gate the subsequent broadcast.

### Finding Description
`BlockInfo::check_state` explicitly forbids moving from `GloballyRejected` to `LocallyAccepted`/`GloballyAccepted` — the two global states are terminal against each other [1](#0-0) .

The rejection-counting path enforces this invariant defensively at the top: as soon as consensus has been reached (`GloballyAccepted` or `GloballyRejected`), it returns immediately, before even loading/tallying the rejection weight: [2](#0-1) 

The acceptance-counting path, `store_and_process_block_signature`, has no equivalent guard. It only skips tallying when `block_info.signed_group.is_some()` (i.e., the block was already accepted), which is `None` for a rejected block: [3](#0-2) 

It then unconditionally computes signature weight and, once `total_signature_weight` crosses `min_weight`, calls `mark_locally_accepted(true)`. If that call fails (which it must, since the block is already `GloballyRejected` and `check_state` forbids the transition), the warning is suppressed via `!block_info.has_reached_consensus()`, but execution **falls through** regardless of the `Err` — `insert_block` and `broadcast_signed_block` both run unconditionally: [4](#0-3) 

`add_block_signature`, which stores signatures for tallying, is likewise not gated on `has_reached_consensus()`, so signatures arriving after a rejection is finalized are still recorded and counted toward the 70% threshold: [5](#0-4) 

This is the direct analog of the Rubicon `Position` bug: the contract there let users keep interacting (`increaseMargin`) with a position whose collateral was already liquidated, because the "liquidated" state wasn't checked before performing the follow-up action. Here, the signer keeps interacting with a block (tallying signatures, broadcasting it) whose fate (`GloballyRejected`) was already decided, because that terminal state isn't checked before the follow-up action (`broadcast_signed_block`).

### Impact Explanation
`broadcast_signed_block` hands the assembled signature set to `handle_post_block`, pushing the block to this signer's stacks-node for processing — i.e., the node may adopt a block that a 30%+ rejecting minority (as tracked by this very signer) already caused to be marked `GloballyRejected`. This is exactly the "rejection recounted as an accept" class: a block outcome that should be terminal (rejected) gets flipped into an accepted broadcast because the weight tally and broadcast path never re-checks the terminal rejected state before acting. This risks propagating/pushing a block the signer set (from this signer's local bookkeeping) already rejected, undermining the safety of the two-phase, weight-based consensus the signer protocol relies on.

### Likelihood Explanation
The docs for this codebase explicitly acknowledge that rejection is "a revocable opinion" and a rejecting signer may later send an acceptance — this is a designed, reachable scenario (see `docs/signer-flows.md` commentary on conflicts surviving global rejection) [6](#0-5) . Any set of signers (or a single slow/partitioned signer plus normal gossip of other signers' pre-existing signatures) that causes acceptance signatures to arrive/replay after this signer already computed `GloballyRejected` locally can trigger the unconditional fallthrough — no majority collusion or key compromise is required, only ordinary message reordering/latency that the codebase itself says is expected.

### Recommendation
Add a `has_reached_consensus()` (or specifically `state == BlockState::GloballyRejected`) guard at the top of `store_and_process_block_signature`, mirroring `handle_block_rejection`'s early return, and make the `broadcast_signed_block` call conditional on `mark_locally_accepted` succeeding rather than falling through on `Err`.

### Proof of Concept
1. Signer S observes rejection signatures from >30% weight for block B and calls `handle_block_rejection`, which marks B `GloballyRejected` at [7](#0-6) .
2. Due to normal message delay/replay, acceptance signatures for the *same* block B (signed earlier by other signers before they saw the rejection majority, or replayed) keep arriving at S and are processed by `handle_block_signature` → `store_and_process_block_signature`.
3. Because `signed_group.is_some()` is false (B was rejected, not accepted) at [3](#0-2) , tallying proceeds and `add_block_signature` keeps recording new signatures without ever checking `has_reached_consensus()`.
4. Once accumulated acceptance weight crosses `min_weight`, `mark_locally_accepted(true)` is attempted and fails (state is `GloballyRejected`), but the warning is swallowed because `has_reached_consensus()` is true, and the code proceeds anyway to `insert_block` and `broadcast_signed_block` at [4](#0-3) , pushing the already-rejected block B to the node.

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

**File:** stacks-signer/src/v0/signer.rs (L2286-2293)
```rust
            Ok(false) => return, // We already have this signature, do not process it again.
            Ok(true) => (),
        }

        if block_info.has_reached_consensus() {
            // Checking the rejection signatures is pointless. We have already reached consensus on this block.
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2335-2337)
```rust
        if let Err(e) = block_info.mark_globally_rejected() {
            warn!("{self}: Failed to mark block as globally rejected: {e:?}",);
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2451-2467)
```rust
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

**File:** stacks-signer/src/v0/signer.rs (L2468-2471)
```rust
        if block_info.signed_group.is_some() {
            // We have already processed this block to the accepted state. Adding more signatures will not change anything so nothing to check.
            return;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L2525-2538)
```rust
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

**File:** docs/signer-flows.md (L322-327)
```markdown
A conflict is any block a signature was ever put over — ours, or a group
threshold we observed — whatever its state now. In particular rejection, even
_global_ rejection, does not clear one: a rejection is a revocable opinion,
while a signature is a bearer instrument that can still be aggregated toward
the 70% threshold if rejecting signers change their minds. Only staleness or
node-derived death (the two questions above) clears a conflict.
```
