### Title
`determine_response` signs a block off a stale persisted `valid` flag without re-validating against current chainstate - (File: stacks-signer/src/v0/signer.rs)

### Summary
`Signer::determine_response` treats `block_info.valid` as authoritative and, when `true`, immediately calls `create_block_acceptance`, producing a **fresh** signature. This function is reachable from `should_reevaluate_block` for a re-proposed block whose `state` is `LocallyAccepted` but whose `signed_self` was never actually set (because the block reached that state via the *group* signature-threshold path, not via this signer's own pre-commit), so a signature that was never previously produced can be minted from a stale `valid=true` verdict with no re-check of current chainstate.

### Finding Description
`determine_response` only inspects `block_info.valid`, which is set once by `mark_pre_committed`/`mark_locally_accepted(false)`/`mark_locally_rejected`, and never re-derived here: [1](#0-0) 

Crucially, `mark_locally_accepted(true)` (the "group observed enough signatures" path used in `store_and_process_block_signature`) advances `state` to `LocallyAccepted` and sets `signed_group`, but does **not** touch `valid` or `signed_self`: [2](#0-1) [3](#0-2) 

So it is possible for a `BlockInfo` to sit persisted in signerdb with `valid = Some(true)` (set earlier by this signer's own `mark_pre_committed` after node validation), `state = LocallyAccepted`, but `signed_self = None` — i.e., this specific signer validated the block once but never actually produced its own signature (e.g. it never crossed its own pre-commit weight threshold before the rest of the network reached the group threshold, or a restart interrupted it after `mark_pre_committed` but before its own pre-commit weight was reached).

When the block is re-proposed (miner resends the identical `BlockProposal`), `handle_block_proposal` routes to `should_reevaluate_block`: [4](#0-3) 

- `globally_approved_and_responded()` only blocks re-evaluation when `state == GloballyAccepted` **and** (`signed_self.is_some()` or `valid == Some(false)`) — it does not gate on `LocallyAccepted`: [5](#0-4) 
- `should_reevaluate_reject_reason` returns `false` when `reject_reason` is `None` (the normal case here), so the "no need to re-evaluate" branch is taken.
- The `state == PreCommitted` branch (which *does* re-invoke `handle_block_pre_commit` → `check_block_against_signer_db_state`, a genuine chainstate re-check) is skipped because `state == LocallyAccepted`, not `PreCommitted`.
- Control falls straight into `determine_response(block_info)`, which sees `valid == Some(true)` and calls `create_block_acceptance`, producing and broadcasting a **brand-new** signature — with **no** call to `check_block_against_signer_db_state` or any node re-validation.

This contrasts directly with every other path that turns a persisted "valid" verdict into a signature: `handle_block_validate_ok` and `handle_block_pre_commit` both explicitly re-run `check_block_against_signer_db_state` before signing, precisely because "the chain and signer db state may have changed materially since this block passed the proposal-time checks" (comment at signer.rs:1340-1344). `determine_response`'s only legitimate use, per the docs, is to "re-send previous response" for a decision that is already final and already broadcast (signed_self set or valid=false); it silently also covers the unintended case above, where no signature was ever actually made yet.

The restart scenario described in the audit sharpens this: signerdb persists `BlockInfo` (`valid`, `state`, `signed_self`, `signed_group`) across process restarts, so a signer that crashes/restarts after `mark_pre_committed` set `valid=true` but before it locally crossed the pre-commit weight threshold (or after another signer's slow gossip caused the group threshold to be crossed via `store_and_process_block_signature` first) will, on restart, read back exactly this `valid=true, state=LocallyAccepted, signed_self=None` record. Any re-proposal for the same `signer_signature_hash` (trivially reproducible by a miner — a single miner slot plus gossip is all that's required) then drives a genuinely new, unvalidated signature through `determine_response`.

### Impact Explanation
This breaks the FAIL-CLOSED invariant that every signature/acceptance corresponds to a validation performed against the *current* canonical chainstate: a fresh signature is minted purely from a possibly stale `valid` flag, bypassing `check_block_against_signer_db_state`, the tenure/parent/reorg checks, and any node re-validation. If a reorg has occurred since the original validation (e.g. the block's tenure/parent is no longer canonical), the signer can sign a now-invalid or non-canonical block, contributing weight toward a conflicting/invalid `BlockResponse` aggregation. This matches the Critical category: "a signer signing an invalid, non-canonical, or conflicting block."

### Likelihood Explanation
The trigger only requires: (1) a signer whose local `valid` flag was set to `true` by its own earlier validation but whose own signature was never actually produced (`signed_self` unset) while `state` advanced to `LocallyAccepted` via the group-threshold path, and (2) a re-proposal of the identical block (something any miner can do with ordinary gossip, at most one miner slot). No majority of signers, no compromised keys, and no auth token are required from the attacker; the race window (own-validation-without-own-signature) can be widened using an ordinary restart, which is exactly the scenario signerdb persistence is meant to support smoothly. This is repeatable per block/tenure.

### Recommendation
Do not let `determine_response` mint a signature purely from `block_info.valid` unless `signed_self`/`signed_group` already reflects that *this* decision was previously finalized and broadcast. Specifically:
- Gate the "resend" branch in `should_reevaluate_block` on `signed_self.is_some() || valid == Some(false)` (analogous to `globally_approved_and_responded`), not merely on `state != PreCommitted`.
- For any `LocallyAccepted` block where `signed_self` is `None` (group-signed but not yet self-signed), route back through the same re-validation path used for `PreCommitted` (`check_block_against_signer_db_state` via `handle_block_pre_commit`) before calling `create_block_acceptance`.

### Proof of Concept
Rust test plan (extending `stacks-signer/src/v0/tests.rs` patterns used in `run_sibling_scenario`):
1. Propose block `A` (tenure-start). Drive `handle_block_proposal` + `validate_ok(&hash_a)` so `block_info.valid = Some(true)`, `state = PreCommitted` (via `mark_pre_committed`), but stall/withhold this signer's own pre-commit weight from crossing threshold.
2. Simulate other signers' pre-commits/signatures arriving via `handle_block_signature`/`store_and_process_block_signature` such that `mark_locally_accepted(true)` fires: `state` becomes `LocallyAccepted`, `signed_group` set, `signed_self` still `None`, `valid` remains `Some(true)`.
3. Persist this `BlockInfo` and simulate a restart (`SignerDb::new` reopened on same path, fresh `Signer`), and simulate a reorg by having the mock node's `/v3/tenures/tip_metadata/*` respond such that block `A`'s tenure/parent is no longer canonical.
4. Re-submit the identical `BlockProposal` for block `A`.
5. Assert (bug present): `node.signer.process_event` results in a `BlockResponse::Accepted` being broadcast for `A`, i.e. `create_block_acceptance` was called and `signed_self` becomes `Some(_)` on the resulting `BlockInfo`, **without** `check_block_against_signer_db_state`/node re-validation ever being invoked for this re-proposal.
6. Assert (fix required): the signer instead re-runs `check_block_against_signer_db_state` (as in `handle_block_pre_commit`) and, given the simulated reorg, emits a `BlockResponse::Rejected` rather than a fresh acceptance.

### Citations

**File:** stacks-signer/src/v0/signer.rs (L458-471)
```rust
    fn determine_response(&mut self, block_info: &BlockInfo) -> Option<BlockResponse> {
        // We will only have the valid field set if we have already validated this block
        // against our stacks-node/local state.
        let valid = block_info.valid?;
        let response = if valid {
            debug!("{self}: Accepting block {}", block_info.block.block_id());
            self.create_block_acceptance(&block_info.block).into()
        } else {
            debug!("{self}: Rejecting block {}", block_info.block.block_id());
            self.create_block_rejection(RejectReason::RejectedInPriorRound, &block_info.block)
                .into()
        };
        Some(response)
    }
```

**File:** stacks-signer/src/v0/signer.rs (L1490-1532)
```rust
        let signer_signature_hash = block_info.block.header.signer_signature_hash();
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
        if !should_reevaluate_reject_reason(block_info) {
            if block_info.state == BlockState::PreCommitted {
                // We validated this block but haven't signed it. Signing requires the
                // pre-commit threshold and the conflict checks in `handle_block_pre_commit`.
                // Re-broadcast our pre-commit and re-run that evaluation instead of
                // responding with a signature directly, so a re-proposed block can't
                // bypass those checks.
                info!(
                    "{self}: received a block proposal for a block we have pre-committed to but not signed. Re-evaluating the pre-commit.";
                    "signer_signature_hash" => %signer_signature_hash,
                    "block_id" => %block_info.block.block_id(),
                    "block_height" => block_info.block.header.chain_length,
                    "burn_height" => block_proposal.burn_height,
                    "consensus_hash" => %block_info.block.header.consensus_hash
                );
                self.send_block_pre_commit(signer_signature_hash.clone());
                let address = self.stacks_address.clone();
                self.handle_block_pre_commit(
                    stacks_client,
                    sortition_state,
                    &address,
                    &signer_signature_hash,
                );
                return false;
            }
            if let Some(block_response) = self.determine_response(block_info) {
                self.send_block_response(&block_info.block, block_response);
                return false;
```

**File:** stacks-signer/src/v0/signer.rs (L2525-2532)
```rust
        // have enough signatures to broadcast!
        // move block to LOCALLY accepted state.
        // It is only considered globally accepted IFF we receive a new block event confirming it OR see the chain tip of the node advance to it.
        if let Err(e) = block_info.mark_locally_accepted(true) {
            if !block_info.has_reached_consensus() {
                warn!("{self}: Failed to mark block as locally accepted: {e:?}");
            }
        }
```

**File:** stacks-signer/src/signerdb.rs (L279-289)
```rust
    /// Mark this block as valid and the appropriate timestamps if they aren't already set, and attempt to mark it as locally accepted.
    pub fn mark_locally_accepted(&mut self, group_signed: bool) -> Result<(), String> {
        if group_signed {
            self.signed_group.get_or_insert(get_epoch_time_secs());
        } else {
            self.valid = Some(true);
            self.approved_time.get_or_insert(get_epoch_time_secs());
            self.signed_self.get_or_insert(get_epoch_time_secs());
        }
        self.move_to(BlockState::LocallyAccepted)
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
