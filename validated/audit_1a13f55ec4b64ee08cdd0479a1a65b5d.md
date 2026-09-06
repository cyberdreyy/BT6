Based on the code I was able to trace, this appears to be a **valid finding**, though I was unable to fully retrieve the bodies of `should_reevaluate_reject_reason` and `determine_response` before running out of tool budget — I flag that gap explicitly below.

### Title
GloballyRejected block re-proposal bypasses `check_state`'s terminal-state invariant via fresh `BlockInfo::from` reconstruction - (File: stacks-signer/src/v0/signer.rs)

### Summary
`should_reevaluate_block`'s only "already decided, ignore" short-circuit is `BlockInfo::globally_approved_and_responded()`, which is defined solely in terms of `BlockState::GloballyAccepted` and has no symmetric counterpart for `GloballyRejected` [1](#0-0) . When a miner re-broadcasts a byte-identical `BlockProposal` for a block a signer has already marked `GloballyRejected`, `handle_block_proposal` falls through to constructing a brand-new `BlockInfo` from the proposal with `state: BlockState::Unprocessed`, discarding the prior terminal state entirely rather than attempting (and being blocked by) a `move_to` transition [2](#0-1) [3](#0-2) .

### Finding Description
The intended invariant (per `docs/signer-flows.md` and `BlockInfo::check_state`) is that `GloballyAccepted` and `GloballyRejected` are each terminal and mutually unreachable [4](#0-3) , verified by the unit test `state_machine` which shows `move_to(GloballyAccepted)` is rejected once a block is `GloballyRejected` [5](#0-4) .

However, `should_reevaluate_block`'s "already answered, do nothing" gate (`DONE1` in the docs) is:
```rust
if block_info.globally_approved_and_responded() { ... return false; }
``` [6](#0-5) 

`globally_approved_and_responded` only matches `BlockState::GloballyAccepted` [1](#0-0) . There is no equivalent `globally_rejected_and_responded()` guard. For a `GloballyRejected` block, execution proceeds into the `should_reevaluate_reject_reason` / `determine_response` branches; if those do not recognize and re-send the terminal `GloballyRejected` verdict (I could not confirm their exact bodies before exhausting my tool budget), control falls through to `should_reevaluate_block` returning `true` at its final line [7](#0-6) .

Critically, `handle_block_proposal` does **not** reuse or mutate the existing terminal `BlockInfo` via `move_to` (which would be safely rejected by `check_state`). Instead it discards it and builds a fresh one directly from the proposal:
```rust
let mut block_info = BlockInfo::from(block_proposal.clone());
``` [8](#0-7) 

`BlockInfo::from(BlockProposal)` always initializes `state: BlockState::Unprocessed` [3](#0-2) , so the terminal `GloballyRejected` record is overwritten in the signer's DB rather than protected by `check_state`. This block then legitimately flows through `check_block_against_state` → validation → `mark_pre_committed` → pre-commit threshold → `handle_block_pre_commit`.

I additionally noted that `handle_block_pre_commit`'s final signing step does not gate on the success of `mark_locally_accepted`:
```rust
if let Err(e) = block_info.mark_locally_accepted(false) {
    if !block_info.has_reached_consensus() { warn!(...); }
}
self.signer_db.insert_block(&block_info)...;
let accepted = self.create_block_acceptance(&block_info.block);
self.handle_block_signature(stacks_client, sortition_state, &accepted);
self.send_block_response(&block_info.block, accepted.into());
``` [9](#0-8) 
There is no `return` on the `Err` branch, so a signature/acceptance is broadcast unconditionally once this code path is reached — the only real protection is *not reaching this line*, which depends entirely on the upstream `should_reevaluate_block`/reconstruction logic discussed above.

### Impact Explanation
If confirmed, this breaks the "rejection recounted as acceptance" safety property (Critical). Because every honest signer runs the same code, a single miner re-broadcasting the identical, previously `GloballyRejected` `BlockProposal` could cause every signer independently to reset that block's terminal state to `Unprocessed`, re-validate it, and re-enter the normal pre-commit/sign flow. If the block is otherwise still chain-valid (e.g., it was rejected for a transient reason such as a reorg race rather than static invalidity), the network could collectively re-sign and finalize a block previously rejected by consensus — a chain-safety violation, not merely a single wedged signer.

### Likelihood Explanation
The precondition is simply: a block reaches `GloballyRejected` on a signer's local DB, and the attacker (who won a miner slot) re-gossips the byte-identical `BlockProposal`. This requires no signer collusion, no auth token, and no majority weight — only miner-slot proposal crafting and StackerDB gossip, which is within the stated attacker capability. It is repeatable per rejected proposal.

However, I was **not able to verify** the bodies of `should_reevaluate_reject_reason` and `determine_response` for the `GloballyRejected` case within my remaining tool budget. It is possible (and plausible, given the deliberate symmetry elsewhere in this codebase, e.g., the `globally_approved_and_responded` test `signers_do_not_reconsider_globally_accepted_and_responded_blocks`) that one of these two functions independently short-circuits `GloballyRejected` blocks by re-sending the stored rejection via `determine_response`, in which case the asymmetric `DONE1` naming in the docs would be cosmetic rather than an actual gap. This uncertainty should be resolved before treating this as confirmed.

### Recommendation
1. Add a symmetric guard, e.g. `globally_rejected_and_responded()` (mirroring `globally_approved_and_responded`) in `signerdb.rs`, and check it in `should_reevaluate_block` alongside the existing `GloballyAccepted` check, so both terminal+responded states short-circuit identically.
2. In `handle_block_proposal`, when a prior `block_info` already has `has_reached_consensus() == true`, do not overwrite it with a fresh `BlockInfo::from(block_proposal)`; always resend the stored terminal response instead.
3. In `handle_block_pre_commit`'s signing step, `return` (do not broadcast a signature/acceptance) when `mark_locally_accepted` fails due to `has_reached_consensus()` being true, instead of only suppressing the warning.

### Proof of Concept
```rust
// stacks-signer/src/signerdb.rs (or v0/tests.rs)
#[test]
fn globally_rejected_block_proposal_is_not_reevaluated() {
    let (mut block_info, block_proposal) = create_block();
    // Simulate a full reject → globally rejected lifecycle
    block_info.mark_locally_rejected().unwrap();
    block_info.mark_globally_rejected().unwrap();
    db.insert_block(&block_info).unwrap();

    // Re-deliver the identical BlockProposal
    let should_reevaluate = signer.should_reevaluate_block(
        &stacks_client, &mut sortition_state, &block_info, &block_proposal,
    );
    assert!(
        !should_reevaluate,
        "A GloballyRejected+responded block must not re-enter FRESH evaluation"
    );

    // Negative control mirroring existing GloballyAccepted test
    // (signers_do_not_reconsider_globally_accepted_and_responded_blocks)
    // should have an equivalent for GloballyRejected asserting no signature is
    // ever produced via mark_locally_accepted for this signer_signature_hash.
}
```

### Citations

**File:** stacks-signer/src/signerdb.rs (L233-250)
```rust
impl From<BlockProposal> for BlockInfo {
    fn from(value: BlockProposal) -> Self {
        Self {
            block: value.block,
            burn_block_height: value.burn_height,
            reward_cycle: value.reward_cycle,
            vote: None,
            valid: None,
            proposed_time: get_epoch_time_secs(),
            approved_time: None,
            signed_self: None,
            signed_group: None,
            ext: ExtraBlockInfo::default(),
            state: BlockState::Unprocessed,
            validation_time_ms: None,
            reject_reason: None,
        }
    }
```

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

**File:** stacks-signer/src/signerdb.rs (L359-363)
```rust
    /// Check if the block is globally accepted and this signer has responded to it
    pub fn globally_approved_and_responded(&self) -> bool {
        matches!(self.state, BlockState::GloballyAccepted)
            && (self.signed_self.is_some() || self.valid == Some(false))
    }
```

**File:** stacks-signer/src/signerdb.rs (L3450-3457)
```rust
        // Must manually override as will not be able to move from GloballyAccepted to GloballyRejected
        block.state = BlockState::GloballyRejected;
        assert!(!block.check_state(BlockState::Unprocessed));
        assert!(!block.check_state(BlockState::LocallyAccepted));
        assert!(!block.check_state(BlockState::LocallyRejected));
        assert!(!block.check_state(BlockState::GloballyAccepted));
        assert!(block.check_state(BlockState::GloballyRejected));
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1467-1478)
```rust
        if let Err(e) = block_info.mark_locally_accepted(false) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}",);
            }
        }
        self.signer_db
            .insert_block(&block_info)
            .unwrap_or_else(|e| self.handle_insert_block_error(e));
        let accepted = self.create_block_acceptance(&block_info.block);
        // have to save the signature _after_ the block info
        self.handle_block_signature(stacks_client, sortition_state, &accepted);
        self.send_block_response(&block_info.block, accepted.into());
```

**File:** stacks-signer/src/v0/signer.rs (L1491-1504)
```rust
        if block_info.globally_approved_and_responded() {
            info!("{self}: received a block proposal for a globally accepted block to which we have already responded. Ignoring.";
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_info.block.block_id(),
                "block_height" => block_info.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "consensus_hash" => %block_info.block.header.consensus_hash,
                "timestamp" => block_info.block.header.timestamp,
                "signed_group" => block_info.signed_group,
                "signed_self" => block_info.signed_self,
                "valid" => ?block_info.valid
            );
            return false;
        }
```

**File:** stacks-signer/src/v0/signer.rs (L1560-1571)
```rust
        } else {
            info!(
                "{self}: received a block proposal for this block before, but our rejection reason allows us to reconsider";
                "reject_reason" => ?block_info.reject_reason,
                "signer_signature_hash" => %signer_signature_hash,
                "block_id" => %block_proposal.block.block_id(),
                "block_height" => block_proposal.block.header.chain_length,
                "burn_height" => block_proposal.burn_height,
                "consensus_hash" => %block_proposal.block.header.consensus_hash
            );
        }
        true
```

**File:** stacks-signer/src/v0/signer.rs (L1652-1668)
```rust
        crate::monitoring::actions::increment_block_proposals_received();
        // Creating a new proposal will overwrite any prior proposal info on the block if it exists, e.g. validity, signed_timestamps, etc.
        let mut block_info = BlockInfo::from(block_proposal.clone());

        // Get sortition view if we don't have it
        if sortition_state.is_none() {
            *sortition_state =
                SortitionsView::fetch_view(self.proposal_config.clone(), stacks_client)
                    .inspect_err(|e| {
                        warn!(
                            "{self}: Failed to update sortition view: {e:?}";
                            "signer_signature_hash" => %signer_signature_hash,
                            "block_id" => %block_proposal.block.block_id(),
                        )
                    })
                    .ok();
        }
```
