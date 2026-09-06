### Title
Missing-`approved_time` fallback lets `check_parent_tenure_choice` treat a well-timed tenure as "poorly timed," wrongly sanctioning a reorg - (File: stacks-signer/src/chainstate/mod.rs)

### Summary
`SortitionData::check_parent_tenure_choice` decides whether a miner is allowed to reorg away a tenure that already produced a globally-recognized block. The decision hinges on how much time elapsed between that block's proposal and the next sortition (`first_proposal_burn_block_timing`). When the local `BlockInfo.approved_time` for that block is absent, the code silently substitutes `0` for the elapsed time instead of treating the missing data as "unknown, reject the reorg." This mirrors the WithdrawHook pattern: a value that should gate a limit/timing check is left at its zero/uninitialized default on a specific code path, and that default trivially satisfies the check, letting through what the check exists to block.

### Finding Description
In `check_parent_tenure_choice`: [1](#0-0) 

```rust
let checked_proposal_timing = if let Some(sortition_state_received_time) = sortition_state_received_time {
    let proposal_to_sortition = if let Some(approved_at) = local_block_info.approved_time {
        sortition_state_received_time.saturating_sub(approved_at)
    } else {
        info!("We did not sign over the reorged tenure's first block, considering it as a late-arriving proposal");
        0
    };
    if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
        // ... permit the reorg, mark_tenure_superseded ...
        continue;
    }
    true
} else {
    false
};
```

`approved_time` is stamped either at pre-commit (`mark_pre_committed`) or at local acceptance when the signer independently validates the block (`mark_locally_accepted(false)`): [2](#0-1) 

But when a signer catches up to a block that has *already* reached the group/global signing threshold before this signer personally validated/pre-committed it, `mark_locally_accepted(true)` is used and `approved_time` is deliberately **not** set (only `signed_group` is):

```rust
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

`get_first_approved_block_in_tenure` still returns this `BlockInfo` (it was accepted, just not by this signer's own pre-commit path), so `check_parent_tenure_choice` reaches the `else` branch and hard-codes `proposal_to_sortition = 0`. Since `0 < first_proposal_burn_block_timing` is true for any non-zero configured timing, the code unconditionally treats the tenure as "poorly timed" and permits the reorg — even if, in reality, that tenure's block was proposed and confirmed well before the next sortition and should never qualify for the reorg exception. The comment ("considering it as a late-arriving proposal") acknowledges the substitution is a guess, but the guess is baked in as "reorg-permitted" rather than "reorg-refused," inverting the safe default that every other branch in this function uses (missing `sortition_state_received_time` → `checked_proposal_timing = false` → refuse; unable to fetch fork info → refuse; no local knowledge of block timing → refuse).

This breaks the "approved-parent vs canonical" equality the whole function exists to enforce: a legitimately, promptly-confirmed single-block tenure can be wrongly declared reorg-eligible on any signer whose local `approved_time` for that block happens to be unset, purely because of *how* that signer learned of the block's acceptance rather than *when* it actually happened.

### Impact Explanation
A single miner (a "one-slot" actor with no majority-signer collusion required) can exploit this by controlling delivery of the tenure-start block proposal to specific signers (e.g., via network timing, selective StackerDB broadcast delay, or simply the natural race where some signers observe group acceptance before their own validation completes). For any signer where the tenure-start block ends up recorded via `mark_locally_accepted(true)` (approved_time never stamped), that signer's `check_parent_tenure_choice`/`validate_tenure_change_payload` (v1) or `check_proposal` (v2) will accept a subsequent tenure-change block that reorgs away that legitimately-mined, on-time tenure — something the timing rule is specifically designed to prevent for tenures that were *not* poorly timed. Once `mark_tenure_superseded` records the permit, that signer's own prior signature over the reorged tenure's block stops counting as a conflict (`get_signed_conflicts`), so the same signer can go on to sign a competing/conflicting block for the replacement tenure. This is a "signer signing a non-canonical/conflicting block" outcome — Critical impact per the rules — triggered by a single miner's proposal-timing/broadcast manipulation and an ordinary race in message delivery, not by compromising any signer's key or requiring a majority.

### Likelihood Explanation
The precondition — a signer observing group/global acceptance of a block before completing its own pre-commit/validation of that exact block — is a normal, frequently-occurring race in a distributed signer set (slow node, momentarily busy signer, network jitter, or a miner that deliberately delays a proposal to a subset of signers). It requires no special access, no majority, and no key compromise; a single well-timed or lagging-signer-targeting miner is sufficient to make the `approved_time`-less path arise on the tenure whose reorg-eligibility later matters.

### Recommendation
Do not default the unknown proposal-to-sortition timing to `0` (the value most favorable to permitting the reorg). Instead, when `local_block_info.approved_time` is `None`, either:
- Fall back to a different definitely-known observed timestamp for the block (e.g., `signed_group`, or the tenure's burn-block receive time) rather than an unconditional `0`, or
- Treat the missing timestamp the same as the other "can't verify" branches in this function and refuse the reorg (`return Ok(false)`), consistent with the function's stated principle that an unresolvable question should keep the conflict blocking rather than resolving it in the exploitable direction.

### Proof of Concept
1. Miner M wins sortition for tenure A and proposes tenure-start block `B_A`.
2. Signer S is slow/delayed in independently validating `B_A`, but observes enough peer `BlockResponse` acceptances to reach the group signing threshold first; S calls `mark_locally_accepted(true)` for `B_A`, so `approved_time` is never stamped (only `signed_group`) — [3](#0-2) .
3. Miner M loses the very next sortition to Miner N, who deliberately does not build on tenure A (reorgs it) and proposes a tenure-change block whose `prev_tenure_consensus_hash` points behind tenure A.
4. On signer S, `check_parent_tenure_choice` is invoked; `get_first_approved_block_in_tenure(tenure A)` returns `B_A`'s `BlockInfo` with `approved_time = None` — [4](#0-3) .
5. Because `approved_time` is `None`, `proposal_to_sortition` is hard-set to `0` regardless of how much real time separated `B_A`'s proposal from the new sortition — [5](#0-4) .
6. `Duration::from_secs(0) < first_proposal_burn_block_timing` is true, so the reorg is sanctioned and tenure A is marked superseded on signer S, even though `B_A` was in fact confirmed well before the sortition boundary — [6](#0-5) .
7. Signer S subsequently signs Miner N's conflicting/reorging block, having been talked out of the conflict that should have blocked it, purely because of the accidental absence of `approved_time` rather than any legitimate timing fact.

### Citations

**File:** stacks-signer/src/chainstate/mod.rs (L225-245)
```rust
            let Some(first_block_mined) = &tenure.first_block_mined else {
                // The node saw no blocks in this tenure, so the reorg takes nothing away from
                // the canonical chain. We may still hold a signature over a block in it that
                // the node has never seen (a block we accept locally is not handed to the node
                // until the whole signer set has signed it), so the reorg must still be
                // recorded if it is permitted.
                superseded_tenures.push(tenure);
                continue;
            };
            let Some(local_block_info) =
                signer_db.get_first_approved_block_in_tenure(&tenure.consensus_hash)?
            else {
                warn!(
                    "Miner is not building off of most recent tenure, but a tenure they attempted to reorg has already mined blocks, and there is no local knowledge for that tenure's block timing.";
                    "parent_tenure" => %self.parent_tenure_id,
                    "last_sortition" => %self.prior_sortition,
                    "violating_tenure_id" => %tenure.consensus_hash,
                    "violating_tenure_first_block_id" => %first_block_mined,
                );
                return Ok(false);
            };
```

**File:** stacks-signer/src/chainstate/mod.rs (L247-278)
```rust
            let checked_proposal_timing = if let Some(sortition_state_received_time) =
                sortition_state_received_time
            {
                // how long was there between when the proposal was received and the next sortition started?
                let proposal_to_sortition = if let Some(approved_at) =
                    local_block_info.approved_time
                {
                    sortition_state_received_time.saturating_sub(approved_at)
                } else {
                    info!("We did not sign over the reorged tenure's first block, considering it as a late-arriving proposal");
                    0
                };
                if Duration::from_secs(proposal_to_sortition) < *first_proposal_burn_block_timing {
                    info!(
                        "Miner is not building off of most recent tenure. A tenure they reorg has already mined blocks, but the block was poorly timed, allowing the reorg.";
                        "parent_tenure" => %self.parent_tenure_id,
                        "last_sortition" => %self.prior_sortition,
                        "violating_tenure_id" => %tenure.consensus_hash,
                        "violating_tenure_first_block_id" => %first_block_mined,
                        "violating_tenure_proposed_time" => local_block_info.proposed_time,
                        "new_tenure_received_time" => sortition_state_received_time,
                        "new_tenure_burn_timestamp" => self.burn_header_timestamp,
                        "first_proposal_burn_block_timing_secs" => first_proposal_burn_block_timing.as_secs(),
                        "proposal_to_sortition" => proposal_to_sortition,
                    );
                    superseded_tenures.push(tenure);
                    continue;
                }
                true
            } else {
                false
            };
```

**File:** stacks-signer/src/signerdb.rs (L272-289)
```rust
    /// Mark this block as valid, record the approved time timestamp if not already set and attempt to mark it as pre-committed.
    pub fn mark_pre_committed(&mut self) -> Result<(), String> {
        self.valid = Some(true);
        self.approved_time.get_or_insert(get_epoch_time_secs());
        self.move_to(BlockState::PreCommitted)
    }

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
