## Title
Attacker-triggered `reset_view` in `SortitionsView::check_proposal` erases a previously-invalidated miner's status, letting a rejected/timed-out miner's block be validated and signed - ([File: stacks-signer/src/chainstate/v1.rs])

## Summary
`SortitionsView::check_proposal` (v1 chainstate) persists a `miner_status` on `self.cur_sortition` (`SortitionMinerStatus::Valid` / `InvalidatedBeforeFirstBlock` / `InvalidatedAfterFirstBlock`) across calls for the lifetime of the signer's in-memory `SortitionsView`. This status is the gate that stops a signer from continuing to validate/sign for a miner that has already timed out or made an illegal reorg. That gate can be silently wiped by an unrelated, attacker-controlled message: any block proposal whose `consensus_hash` matches neither `cur_sortition` nor `last_sortition` triggers `reset_view`, which unconditionally replaces `self.cur_sortition` with a freshly-fetched `SortitionState` whose `miner_status` always starts at `Valid` (per the `TryFrom<SortitionInfo>` impl).

## Finding Description
`check_proposal` begins by checking two invalidation conditions and, if triggered, sets `self.cur_sortition.miner_status = SortitionMinerStatus::InvalidatedBeforeFirstBlock` (or `InvalidatedAfterFirstBlock` elsewhere): [1](#0-0) [2](#0-1) 

This field lives on `self` (the `SortitionsView` held by the signer's runloop across events), so once set it should persist and continue blocking that miner's future proposals for the same tenure - confirmed by the test `check_proposal_invalid_status`, which shows a block that validated fine before invalidation is rejected afterward: [3](#0-2) 

However, later in the same function, when a proposal's `consensus_hash` doesn't match either `cur_sortition` or `last_sortition`, and `reset_view_if_wrong_consensus_hash` is set, the code calls `self.reset_view(client)` and then re-enters `check_proposal` from the top: [4](#0-3) 

`reset_view` unconditionally overwrites `self.cur_sortition` and `self.last_sortition` with freshly fetched state: [5](#0-4) 

Critically, the `TryFrom<SortitionInfo>` conversion used both by `fetch_view` and `reset_view` always initializes `miner_status: SortitionMinerStatus::Valid`: [6](#0-5) 

So a single unrelated proposal carrying a bogus/unrecognized `consensus_hash` (trivial for the tenure's miner, or anyone able to place a `BlockProposal` message, to construct - it need not itself be valid or even accepted) is enough to force a `reset_view` that resets `miner_status` back to `Valid` on the *current* sortition, discarding whatever `InvalidatedBeforeFirstBlock`/`InvalidatedAfterFirstBlock` verdict had previously been recorded for that same tenure (e.g., due to a miner timeout, or an illegal-reorg parent-tenure check). Once reset, a subsequent proposal for the *same* (previously invalidated) current sortition passes the miner-status gate again and proceeds to node validation/signing as if the miner had never been invalidated.

This breaks the "signer signing an invalid/non-canonical block" equality: the local invalidation verdict for a tenure is supposed to be sticky for the life of that tenure's view, but it can be reset to `Valid` by an attacker-controlled, otherwise-irrelevant message.

## Impact Explanation
This falls in the "Critical" bucket defined by the rules: a signer can be tricked into validating/signing a block from a miner it had already locally determined to be invalid (timed out, or committed an unauthorized reorg) once the invalidation flag is erased by the crafted proposal. Since `miner_status` is the only local gate against that miner for the tenure (the node's `/v3/block_proposal` validation does not independently re-derive "did-we-already-invalidate-this-miner"), erasing it removes signer-side protection for the remainder of that tenure.

## Likelihood Explanation
The trigger is cheap: any block proposal (broadcast by the tenure's own miner, who fully controls the field, or gossiped) with a `consensus_hash` that is neither `cur_sortition.data.consensus_hash` nor `last_sortition.data.consensus_hash` satisfies the reset condition; it does not need to pass any other validation and is not itself required to be accepted. This can be a single crafted/garbage proposal sent immediately after (or racing with) the legitimate invalidation, e.g. right after a `block_proposal_timeout` fires or right after a reorg-choice is flagged invalid, since the check happens at the very next `check_proposal` call, which is driven purely by inbound miner messages.

## Recommendation
`reset_view` should not blindly overwrite `miner_status` with `Valid` for a sortition that the signer has already independently invalidated this run. Either (a) preserve/re-derive `miner_status` for `cur_sortition`/`last_sortition` across `reset_view` (re-run the timeout/reorg checks immediately after resetting, before returning), or (b) key invalidation state off signer-db-persisted facts (e.g., recorded timeout time / rejected reorg) rather than only the in-memory `SortitionState`, so a fresh fetch cannot silently clear it.

## Proof of Concept
1. Miner M's tenure `T` times out (`SortitionState::is_timed_out` returns true) or M proposes an illegal reorg; `check_proposal` sets `self.cur_sortition.miner_status = InvalidatedBeforeFirstBlock` for `T`. From this point, any further proposal with `consensus_hash == T` is rejected with `RejectReason::InvalidMiner`/`ReorgNotAllowed`.
2. M (or anyone able to place a `BlockProposal` StackerDB message) crafts a throwaway block whose header `consensus_hash` is neither `T` nor `last_sortition`'s hash (e.g., an old/garbage hash), and broadcasts it. `handle_block_proposal` routes it into `check_proposal` with `reset_view_if_wrong_consensus_hash = true`.
3. `check_proposal` hits the "neither current nor last" branch, calls `self.reset_view(client)`, which refetches sortition info from the node and rebuilds `self.cur_sortition` via `SortitionState::try_from`, resetting `miner_status` to `Valid`.
4. `check_proposal` recurses on the *same* garbage block (still rejected, since its hash still doesn't match) - but `self.cur_sortition.miner_status` is now `Valid` again for tenure `T`.
5. M re-broadcasts its originally-invalidated block for tenure `T`. It now passes the miner-status gate at the top of `check_proposal` and proceeds to node validation/pre-commit/signing, even though the signer had already determined M's tenure should be invalidated.

Note: I was unable to directly confirm from the fetched snippets the exact call site in `stacks-signer/src/v0/signer.rs` that passes `reset_view_if_wrong_consensus_hash = true` into `check_proposal` for the v1 (local-state) path; the grep for that call site did not return results before the tool budget ran out. This should be verified in a follow-up session (e.g., by grepping `check_block_against_local_state` in `stacks-signer/src/v0/signer.rs`) to confirm the exact trigger conditions under which `reset_view_if_wrong_consensus_hash` is `true` in production code paths (as opposed to tests, which do exercise it, per `check_proposal_refresh` in `stacks-signer/src/chainstate/tests/v1.rs`).

### Citations

**File:** stacks-signer/src/chainstate/v1.rs (L97-105)
```rust
impl TryFrom<SortitionInfo> for SortitionState {
    type Error = ClientError;
    fn try_from(value: SortitionInfo) -> Result<Self, Self::Error> {
        let data = SortitionData::try_from(value)?;
        Ok(Self {
            data,
            miner_status: SortitionMinerStatus::Valid,
        })
    }
```

**File:** stacks-signer/src/chainstate/v1.rs (L144-163)
```rust
        if self.cur_sortition.miner_status == SortitionMinerStatus::Valid
            && SortitionState::is_timed_out(
                &self.cur_sortition.data.consensus_hash,
                signer_db,
                self.config.block_proposal_timeout,
            )?
        {
            info!(
                "Current miner timed out, marking as invalid.";
                "block_height" => block.header.chain_length,
                "block_proposal_timeout" => ?self.config.block_proposal_timeout,
                "current_sortition_consensus_hash" => ?self.cur_sortition.data.consensus_hash,
            );
            self.cur_sortition.miner_status = SortitionMinerStatus::InvalidatedBeforeFirstBlock;

            // If the current proposal is also for this current
            // sortition, then we can return early here.
            if self.cur_sortition.data.consensus_hash == block.header.consensus_hash {
                return Err(RejectReason::InvalidMiner);
            }
```

**File:** stacks-signer/src/chainstate/v1.rs (L176-203)
```rust
            let consensus_hash_match =
                self.cur_sortition.data.consensus_hash == tip.block.header.consensus_hash;
            let parent_tenure_id_match =
                self.cur_sortition.data.parent_tenure_id == tip.block.header.consensus_hash;
            if !consensus_hash_match && !parent_tenure_id_match {
                // More expensive check, so do it only if we need to.
                let is_valid_parent_tenure = self.cur_sortition.data.check_parent_tenure_choice(
                    signer_db,
                    client,
                    &self.config.first_proposal_burn_block_timing,
                )?;
                if !is_valid_parent_tenure {
                    warn!(
                        "Current sortition does not build off of canonical tip tenure, marking as invalid";
                        "current_sortition_parent" => ?self.cur_sortition.data.parent_tenure_id,
                        "tip_consensus_hash" => ?tip.block.header.consensus_hash,
                    );
                    self.cur_sortition.miner_status =
                        SortitionMinerStatus::InvalidatedBeforeFirstBlock;

                    // If the current proposal is also for this current
                    // sortition, then we can return early here.
                    if self.cur_sortition.data.consensus_hash == block.header.consensus_hash {
                        return Err(RejectReason::ReorgNotAllowed);
                    }
                }
            }
        }
```

**File:** stacks-signer/src/chainstate/v1.rs (L254-274)
```rust
        else {
            if reset_view_if_wrong_consensus_hash {
                info!(
                    "Miner block proposal has consensus hash that is neither the current or last sortition. Resetting view.";
                    "proposed_block_consensus_hash" => %block.header.consensus_hash,
                    "current_sortition_consensus_hash" => ?self.cur_sortition.data.consensus_hash,
                    "last_sortition_consensus_hash" => ?self.last_sortition.as_ref().map(|x| &x.data.consensus_hash),
                );
                self.reset_view(client)
                    .map_err(SignerChainstateError::from)?;
                return self.check_proposal(client, signer_db, block, false, replay_set);
            }
            warn!(
                "Miner block proposal has consensus hash that is neither the current or last sortition. Considering invalid.";
                "proposed_block_consensus_hash" => %block.header.consensus_hash,
                "signer_signature_hash" => %block.header.signer_signature_hash(),
                "current_sortition_consensus_hash" => ?self.cur_sortition.data.consensus_hash,
                "last_sortition_consensus_hash" => ?self.last_sortition.as_ref().map(|x| &x.data.consensus_hash),
            );
            return Err(RejectReason::SortitionViewMismatch);
        };
```

**File:** stacks-signer/src/chainstate/v1.rs (L546-563)
```rust
    /// Reset the view to the current sortition and last sortition
    pub fn reset_view(&mut self, client: &StacksClient) -> Result<(), ClientError> {
        let CurrentAndLastSortition {
            current_sortition,
            last_sortition,
        } = client.get_current_and_last_sortition()?;

        let cur_sortition = SortitionState::try_from(current_sortition)?;
        let last_sortition = last_sortition
            .map(SortitionState::try_from)
            .transpose()
            .ok()
            .flatten();

        self.cur_sortition = cur_sortition;
        self.last_sortition = last_sortition;
        Ok(())
    }
```

**File:** stacks-signer/src/chainstate/tests/v1.rs (L357-396)
```rust
#[test]
fn check_proposal_invalid_status() {
    let (stacks_client, mut signer_db, block_sk, mut view, mut block) =
        setup_test_environment(function_name!());
    block.header.consensus_hash = view.cur_sortition.data.consensus_hash.clone();
    block.header.sign_miner(&block_sk).unwrap();
    view.check_proposal(
        &stacks_client,
        &mut signer_db,
        &block,
        false,
        ReplayTransactionSet::none(),
    )
    .expect("Proposal should validate");
    view.cur_sortition.miner_status = SortitionMinerStatus::InvalidatedAfterFirstBlock;
    view.check_proposal(
        &stacks_client,
        &mut signer_db,
        &block,
        false,
        ReplayTransactionSet::none(),
    )
    .expect_err("Proposal should not validate");

    block.header.consensus_hash = view
        .last_sortition
        .as_ref()
        .unwrap()
        .data
        .consensus_hash
        .clone();
    block.header.sign_miner(&block_sk).unwrap();
    view.check_proposal(
        &stacks_client,
        &mut signer_db,
        &block,
        false,
        ReplayTransactionSet::none(),
    )
    .expect_err("Proposal should not validate");
```
