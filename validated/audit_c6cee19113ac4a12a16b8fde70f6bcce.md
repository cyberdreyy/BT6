### Title
`pending_delegator_rewards` in vote accounts is never decremented after block-reward distribution, allowing the same pool to be repeatedly minted as rewards every epoch - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The vote program's SIMD-0123 "delegator rewards" mechanism adds lamports into a vote account's `pending_delegator_rewards` field via `deposit_delegator_rewards`, and each epoch `calculate_block_reward` uses that field as the "budget" to proportionally distribute block rewards to delegated stake accounts. Unlike the referenced Olympus bug (a cached-reward balance that is added to a payout but never zeroed, letting a user re-claim it), here `pending_delegator_rewards` is only ever incremented (`add_pending_delegator_rewards`) and read (`pending_delegator_rewards()`); no code path decrements it after it has been used as the source for a block-reward payout, so the same deposited amount is used as the distribution budget in every subsequent epoch's reward calculation, indefinitely.

### Finding Description
`add_pending_delegator_rewards` in `programs/vote/src/vote_state/handler.rs` only implements incrementing the field: [1](#0-0) 

`deposit_delegator_rewards` in `programs/vote/src/vote_state/mod.rs` transfers lamports into the vote account and calls `add_pending_delegator_rewards`, persisting the updated state: [2](#0-1) 

Each epoch, `calculate_block_reward` in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` reads `pending_delegator_rewards` from the vote account and uses it as the numerator/cap for a stake-proportional block reward: [3](#0-2) 

The resulting `block_reward` is then minted directly into the stake account's lamport balance via `checked_add_lamports` during `store_stake_accounts_in_partition` — it is not deducted from the vote account's balance or from `pending_delegator_rewards`: [4](#0-3) 

I was unable to locate any function (in `handler.rs`, `vote_state/mod.rs`, `calculation.rs`, or `distribution.rs`) that subtracts, resets, or otherwise reduces `pending_delegator_rewards` after it is consumed by `calculate_block_reward`. The only places the field is written are `add_pending_delegator_rewards` (increment) and direct test-only assignments (`vote_state.pending_delegator_rewards = pending_rewards`). This mirrors the `cachedUserRewards` bug class: a value representing an already-claimable/consumed reward pool is never zeroed out after being paid, so it remains available to be "claimed" (here, distributed) again in the next payout cycle.

### Impact Explanation
Because `pending_delegator_rewards` is never decremented, every epoch the full deposited amount is treated as available budget and used to compute `calculate_block_reward` for each stake delegation, minting new lamports into stake accounts via `checked_add_lamports`. This causes the same one-time deposit to be paid out repeatedly across epochs instead of being drawn down to zero, resulting in unbounded, unauthorized lamport minting (inflation of bank capitalization) rather than a bounded, one-time distribution of the deposited funds. This is a direct case of unauthorized fund creation, analogous to the "steal all rewards" impact in the source report, except here it manifests as unbacked lamport minting network-wide rather than a single actor draining a pool.

### Likelihood Explanation
This path is reachable purely through normal protocol operation: any account can call `DepositDelegatorRewards` to fund a vote account's `pending_delegator_rewards`, and reward calculation/distribution runs automatically every epoch once `block_revenue_sharing` (SIMD-0123/SIMD-0232 feature set) is active. No privileged access or malicious input is required — the flaw is in the missing reset invariant on state that the protocol itself mutates deterministically every epoch.

### Recommendation
Track and subtract the amount of `pending_delegator_rewards` actually consumed as `block_reward` during each epoch's distribution (or recalculation), writing the decremented value back to the vote account state, analogous to how the Olympus fix requires zeroing `cachedUserRewards` in `_claimInternalRewards`. Concretely, add a `subtract_pending_delegator_rewards` (or similar) method in `programs/vote/src/vote_state/handler.rs` and invoke it wherever `calculate_block_reward`'s output is committed to stake accounts in `runtime/src/bank/partitioned_epoch_rewards/distribution.rs` and `calculation.rs`, ensuring the outstanding pool shrinks by exactly the lamports distributed.

### Proof of Concept
1. Authorized withdrawer/delegator calls `DepositDelegatorRewards` to deposit `D` lamports into vote account `V`, setting `pending_delegator_rewards = D` (per `deposit_delegator_rewards`, `programs/vote/src/vote_state/mod.rs:936-987`).
2. At epoch boundary, `calculate_block_reward` computes each delegator's share of `D` and mints it into their stake accounts via `checked_add_lamports` in `store_stake_accounts_in_partition` (`runtime/src/bank/partitioned_epoch_rewards/distribution.rs:262-268`), without reducing `V`'s `pending_delegator_rewards`.
3. At the next epoch boundary, since `pending_delegator_rewards` is still `D` (unchanged), `calculate_block_reward` again computes and mints rewards based on the full `D`, repeating step 2.
4. This repeats every epoch indefinitely, so the one-time deposit `D` is effectively minted to stakers once per epoch forever, instead of being paid out once and exhausted — verified by the absence of any decrement call on `pending_delegator_rewards` across `handler.rs`, `vote_state/mod.rs`, `calculation.rs`, and `distribution.rs`.

### Citations

**File:** programs/vote/src/vote_state/handler.rs (L196-209)
```rust
    pub(crate) fn add_pending_delegator_rewards(
        &mut self,
        amount: u64,
    ) -> Result<(), InstructionError> {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => {
                v4.pending_delegator_rewards = v4
                    .pending_delegator_rewards
                    .checked_add(amount)
                    .ok_or(InstructionError::ArithmeticOverflow)?;
                Ok(())
            }
        }
    }
```

**File:** programs/vote/src/vote_state/mod.rs (L974-987)
```rust
    // CPI to System: Transfer from sender to vote account.
    invoke_context.native_invoke_signed(
        system_instruction::transfer(&source_address, &vote_address, deposit),
        &[],
    )?;

    // Update `pending_delegator_rewards`.
    let transaction_context = &invoke_context.transaction_context;
    let instruction_context = transaction_context.get_current_instruction_context()?;
    let mut vote_account =
        instruction_context.try_borrow_instruction_account(vote_account_index)?;

    vote_state.add_pending_delegator_rewards(deposit)?;
    vote_state.set_vote_account_state(&mut vote_account)
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L183-231)
```rust
    let vote_pubkey = delegation.voter_pubkey;
    let Some(vote_account) = distribution_epoch_vote_accounts.get(&vote_pubkey) else {
        debug!("could not find vote account {vote_pubkey} in cache");
        return 0;
    };
    let vote_state = vote_account.vote_state_view();
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();
    // NOTE: during recalculation, `distribution_epoch_vote_accounts` already
    // includes updated stake activation values from after the new epoch
    // calculation, so we need to use `RewardEpochDelegatedStakes` for the exact
    // values at the end of the reward epoch.
    let (AlpenglowEpochType::Alpenglow {
        reward_epoch_delegated_stakes,
        ..
    }
    | AlpenglowEpochType::MigrationEpoch {
        reward_epoch_delegated_stakes,
        ..
    }) = ag_epoch_type
    else {
        debug!("Alpenglow must be enabled for block reward calculation");
        return 0;
    };
    let total_active_stake = reward_epoch_delegated_stakes
        .delegated_stakes
        .get(&vote_pubkey)
        .copied()
        .unwrap_or(0);
    if total_active_stake == 0 {
        0
    } else {
        let stake = delegation_effective_stake(
            delegation,
            rewarded_epoch,
            stake_history,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        );
        // During recalculation, if stake account has already received rewards,
        // it's possible to have `stake > total_active_stake`. If
        // `pending_delegator_rewards` is a huge number, we could potentially
        // overflow a `u64`. We can also have individual rewards look greater
        // than the pending rewards. This is harmless in practice, but we
        // clamp it just to be safe
        (pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
            .try_into()
            .unwrap_or(u64::MAX)
            .min(pending_delegator_rewards)
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L262-268)
```rust
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;

```
