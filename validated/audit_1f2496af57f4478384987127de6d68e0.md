### Title
Vote account `pending_delegator_rewards` is never decremented after block-revenue distribution, permanently locking withdrawer funds - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The SIMD-0123 block-revenue-sharing feature adds a `pending_delegator_rewards` counter to `VoteStateV4` that is incremented via `DepositDelegatorRewards` and read every epoch by `calculate_block_reward` to compute per-stake-account block rewards during partitioned epoch reward distribution. The vote program's `withdraw()` instruction reserves `pending_delegator_rewards` lamports as non-withdrawable at all times. Across the reachable distribution code (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs`, `distribution.rs`, `sysvar.rs`) and the vote-state mutation helpers (`programs/vote/src/vote_state/handler.rs`), the only mutator of `pending_delegator_rewards` is `add_pending_delegator_rewards` (a `checked_add`); no code path decrements it once the corresponding block reward has actually been paid out to delegators. This mirrors the ggAVAX `totalReleasedAssets`/`syncRewards` bug class: an accounting variable used to gate withdrawal permanently over-reserves funds because it is not updated in lockstep with the reward-distribution lifecycle.

### Finding Description
`deposit_delegator_rewards` (`programs/vote/src/vote_state/mod.rs:936-988`) transfers lamports into the vote account and calls `vote_state.add_pending_delegator_rewards(deposit)` (`programs/vote/src/vote_state/handler.rs:196-208`), which only ever `checked_add`s the deposit onto `pending_delegator_rewards`.

At epoch-reward time, `calculate_block_reward` (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs:173-232`) reads `vote_state.pending_delegator_rewards()` and computes each delegator's share:
```
(pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
    .try_into().unwrap_or(u64::MAX)
    .min(pending_delegator_rewards)
```
That `block_reward` is then credited directly to the stake account's lamports in `build_updated_stake_reward` (`runtime/src/bank/partitioned_epoch_rewards/distribution.rs:262-267`, `account.checked_add_lamports(partitioned_stake_reward.block_reward)`), and summed into capitalization/distribution counters elsewhere. Nowhere in this call path — nor anywhere else found via a repo-wide search for `pending_delegator_rewards` — is the vote account's `pending_delegator_rewards` field ever reduced by the `block_reward` amount actually paid out.

Meanwhile, `withdraw()` (`programs/vote/src/vote_state/mod.rs:1062-1128`) treats `pending_delegator_rewards` as funds that must always remain in the account:
```rust
let pending_delegator_rewards = vote_state.pending_delegator_rewards();
if remaining_balance == 0 {
    if pending_delegator_rewards > 0 {
        return Err(InstructionError::InsufficientFunds);
    }
    ...
} else {
    let min_balance = min_rent_exempt_balance
        .checked_add(pending_delegator_rewards)
        .ok_or(InstructionError::ArithmeticOverflow)?;
    if remaining_balance < min_balance {
        return Err(InstructionError::InsufficientFunds);
    }
}
```
Because `pending_delegator_rewards` only grows (via new `DepositDelegatorRewards` calls) and is never reduced when the funds it represents are actually distributed out as block rewards, the authorized withdrawer's legitimately spendable balance is permanently reduced by every reward pool that has already been paid to delegators. This is the direct analog of the ggAVAX bug: an on-chain accounting counter meant to track "not-yet-distributed" rewards is not kept in sync with the distribution mechanism, so a legitimate actor (the vote account's authorized withdrawer) is blocked from withdrawing funds they are entitled to, with the lock persisting indefinitely (unlike the ggAVAX case, which self-heals at `rewardsCycleEnd`; here there is no such reset).

### Impact Explanation
The authorized withdrawer of a vote account that has received `DepositDelegatorRewards` deposits and participated in at least one epoch of block-revenue distribution will find part or all of its own lamports (rent aside) permanently non-withdrawable via `Withdraw`, even though those lamports have already fulfilled their reward-distribution purpose. This is a direct, on-chain state-mutation/fund-lock impact triggerable purely by validator/operator-level (not privileged-node) transactions: `DepositDelegatorRewards` and normal epoch-boundary reward distribution, both part of the standard, unprivileged transaction/consensus flow. Severity is Medium: no consensus divergence and no loss of the underlying protocol's total lamports, but the specific account owner's funds become stuck, similarly to the code4rena Medium-severity finding cited.

### Likelihood Explanation
Any validator identity using the SIMD-0123 delegator-rewards deposit flow and receiving at least one epoch of block-revenue-shared distribution will accumulate a `pending_delegator_rewards` balance that never decreases. Given block-revenue sharing is designed to run every epoch as a normal part of consensus operation, the condition is not an edge case — it is the expected steady-state outcome of the feature as implemented, making the likelihood high once the feature is active.

### Recommendation
Decrement `pending_delegator_rewards` by the exact `block_reward` amounts computed and distributed in `calculate_block_reward` / `build_updated_stake_reward`, persisting the updated vote-account state as part of (or immediately following) `distribute_epoch_rewards_in_partition`, so that `withdraw()`'s reserved-balance check in `programs/vote/src/vote_state/mod.rs` only ever reserves rewards that are still outstanding.

### Proof of Concept
Not independently executable from static analysis alone; the following steps describe how the issue would be exercised, based on the code paths cited above:
1. Vote account authority calls `DepositDelegatorRewards` to deposit `D` lamports, incrementing `pending_delegator_rewards` to `D` (`programs/vote/src/vote_state/mod.rs:936-988`).
2. At the next epoch boundary, `block_revenue_sharing` is active, so `calculate_block_reward` computes and pays out (via `distribute_epoch_rewards_in_partition` → `build_updated_stake_reward`) block rewards to delegators totaling up to `D` lamports, while the vote account's `pending_delegator_rewards` field remains `D` (no decrement path found).
3. The authorized withdrawer calls `Withdraw` for the vote account's full spendable balance; `withdraw()` computes `min_balance = min_rent_exempt_balance + pending_delegator_rewards` (still `D`) and rejects any withdrawal that would leave less than `D` lamports reserved, even though those `D` lamports were already paid out to delegators from the reward-distribution mechanism, not from the vote account's real balance obligations.
4. This reservation persists across all future epochs unless a code path explicitly resets/decrements `pending_delegator_rewards`, which the repository search did not locate.

Note: due to index-size limits on this ask-mode search, it is possible a decrement of `pending_delegator_rewards` exists elsewhere in the codebase that was not surfaced by search; a background Devin session with full repository access would be needed to conclusively confirm the absence of any such decrement path before treating this as fully verified.