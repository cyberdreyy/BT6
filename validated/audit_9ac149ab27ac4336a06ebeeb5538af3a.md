### Title
`pending_delegator_rewards` in the vote program is incremented on every deposit but never decremented after block rewards are distributed, causing vote-account fund lock-up — ([File: programs/vote/src/vote_state/handler.rs])

### Summary
Vote accounts (SIMD‑0123 / SIMD‑0185 v4 vote state) track a `pending_delegator_rewards` counter that is incremented whenever a delegator (or validator) deposits block-revenue-sharing rewards into the vote account via `DepositDelegatorRewards`. This counter is read at epoch-boundary reward distribution to compute each stake account's share of the block reward, and it is also used by `withdraw()` to compute the minimum balance the vote account must retain. However, unlike the deposit path, there is no code path that decrements `pending_delegator_rewards` after the corresponding lamports have actually been paid out to delegators as block rewards, mirroring the Ribbon Finance `totalPending` bug where a "pending" accounting counter is only ever incremented and never reconciled against real outflows.

### Finding Description
`add_pending_delegator_rewards` is the only mutator of the field and only ever calls `checked_add`: [1](#0-0) 

It is invoked from `deposit_delegator_rewards`, which transfers lamports into the vote account and then increments the counter: [2](#0-1) 

At epoch-reward time, `calculate_block_reward` reads `pending_delegator_rewards` and computes each stake account's proportional share of it (`pending_delegator_rewards * stake / total_active_stake`), which is then paid out as a stake reward: [3](#0-2) 

Nothing in this distribution path (`calculate_block_reward`, `calculate_stake_rewards_and_commissions`, `distribute_reward_commissions`, `store_stake_accounts_in_partition`) writes back to the vote account to reduce `pending_delegator_rewards` by the amount that was just paid out. A repository-wide search for any subtraction/reset of the field (`pending_delegator_rewards.*sub`, `set_pending_delegator_rewards`) returned no matches — the only setter is the additive one in `handler.rs`.

Meanwhile, `withdraw()` treats `pending_delegator_rewards` as still-owed money that must remain in the vote account: [4](#0-3) 

Specifically:
- If the withdrawal would zero the account, it is rejected outright whenever `pending_delegator_rewards > 0`.
- If not fully closing, the withdrawer must always keep `rent_exempt_minimum + pending_delegator_rewards` in the account.

Since `pending_delegator_rewards` is monotonically increasing (every deposit adds to it, no distribution ever subtracts from it), the reserved balance requirement grows without bound across epochs even though the underlying lamports have already been paid out to stakers as rewards. This is the exact analog of the Ribbon `vaultState.totalPending` bug: an accounting variable meant to represent "amount not yet processed" that is updated on the deposit/increase side but never reconciled on the processing/decrease side, causing calculations downstream (here, `withdraw`'s minimum-balance and full-close checks) to increasingly diverge from the real, physical state of the account.

### Impact Explanation
Once `pending_delegator_rewards` exceeds the vote account's actual lamport balance minus rent-exemption (which it inevitably will after enough distribution epochs, since the balance backing it has already been paid out to stakers and is no longer in the account), the vote account:
- Can never be fully closed (`Withdraw` to zero balance is always rejected while `pending_delegator_rewards > 0`), permanently locking the residual rent-exempt lamports.
- Can have its withdrawable amount computation `min_rent_exempt_balance + pending_delegator_rewards` (`checked_add`) legitimately succeed arithmetically, but the resulting `min_balance` requirement will exceed real available funds, causing every `Withdraw` instruction to fail with `InstructionError::InsufficientFunds`, freezing the authorized withdrawer's ability to retrieve funds that are rightfully theirs.

This is a fund/state-accounting bug reachable purely through ordinary user transactions to a builtin program (`programs/vote`), matching the "critical loss of functionality" and "funds locked" impact class from the source report.

### Likelihood Explanation
`DepositDelegatorRewards` is callable by anyone (any signer can be the depositor/source), so the counter grows through fully permissionless activity, and epoch reward distribution runs automatically every epoch once `block_revenue_sharing` and the SIMD-0123/0185/0291/0232 feature set is active. No malicious action is even required — normal operation of the revenue-sharing feature will cause `pending_delegator_rewards` to diverge from the real "still owed" balance over time. However, this depends on features (`commission_rate_in_basis_points`, `custom_commission_collector`, `block_revenue_sharing`, V4 vote state) that appear to still be gated/in-progress in this codebase, so the practical likelihood is contingent on those features being activated on a live cluster; I could not fully confirm from the index whether a decrement occurs somewhere outside the files I found (e.g., a separate reconciliation path not indexed), so this should be verified against the full source before treating it as fully confirmed.

### Recommendation
Whenever a stake account's block reward is derived from `pending_delegator_rewards` and actually paid out during `store_stake_accounts_in_partition` / `distribute_reward_commissions`, decrement the vote account's `pending_delegator_rewards` by the exact amount paid out (analogous to correctly decrementing `vaultState.totalPending` in the Ribbon fix), persisting the updated vote account state. Add test coverage for multi-epoch deposit/distribution/withdraw sequences to ensure `pending_delegator_rewards` never diverges from the actual undistributed reward balance, and ensure `withdraw()`'s minimum-balance and full-close logic is checked against the reconciled value.

### Proof of Concept
1. Feature-activate SIMD‑0123/0185/0232/0291 (`block_revenue_sharing`, `commission_rate_in_basis_points`, `custom_commission_collector`) on a test validator/bank and create a V4 vote account with delegated stake.
2. Call `VoteInstruction::DepositDelegatorRewards { deposit: X }` from any funded account — `pending_delegator_rewards` becomes `X` (see `programs/vote/src/vote_state/mod.rs:974-988`).
3. Advance an epoch so `begin_partitioned_rewards` → `calculate_block_reward` computes and pays out a share of `X` to delegators via `store_stake_accounts_in_partition` (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs:173-231`).
4. Inspect the vote account state after distribution: `pending_delegator_rewards` is unchanged at `X`, even though (up to) all of `X` was already paid to stakers.
5. Repeat deposits/distributions over multiple epochs; `pending_delegator_rewards` keeps growing while real backing lamports have left the account.
6. Attempt `VoteInstruction::Withdraw` for the full account balance — it fails with `InstructionError::InsufficientFunds` because `pending_delegator_rewards` (now stale/inflated) is added to the required minimum balance (`programs/vote/src/vote_state/mod.rs:1112-1121`), and full closure is unconditionally blocked while `pending_delegator_rewards > 0` (`programs/vote/src/vote_state/mod.rs:1087-1092`), locking the authorized withdrawer out of funds that are rightfully withdrawable.

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

**File:** programs/vote/src/vote_state/mod.rs (L974-988)
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
}
```

**File:** programs/vote/src/vote_state/mod.rs (L1084-1121)
```rust
    // Always zero until SIMD-0123 is activated.
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();

    if remaining_balance == 0 {
        // SIMD-0123: vote account cannot be closed if
        // pending_delegator_rewards > 0.
        if pending_delegator_rewards > 0 {
            return Err(InstructionError::InsufficientFunds);
        }

        let reject_active_vote_account_close = vote_state
            .epoch_credits()
            .last()
            .map(|(last_epoch_with_credits, _, _)| {
                let current_epoch = clock.epoch;
                // if current_epoch - last_epoch_with_credits < 2 then the validator has received credits
                // either in the current epoch or the previous epoch. If it's >= 2 then it has been at least
                // one full epoch since the validator has received credits.
                current_epoch.saturating_sub(*last_epoch_with_credits) < 2
            })
            .unwrap_or(false);

        if reject_active_vote_account_close {
            return Err(VoteError::ActiveVoteAccountClose.into());
        } else {
            // Deinitialize upon zero-balance
            VoteStateHandler::deinitialize_vote_account_state(&mut vote_account, target_version)?;
        }
    } else {
        // SIMD-0123: withdrawable balance when pending_delegator_rewards > 0
        // is lamports - pending_delegator_rewards - rent_exempt_minimum.
        let min_rent_exempt_balance = rent_sysvar.minimum_balance(vote_account.get_data().len());
        let min_balance = min_rent_exempt_balance
            .checked_add(pending_delegator_rewards)
            .ok_or(InstructionError::ArithmeticOverflow)?;
        if remaining_balance < min_balance {
            return Err(InstructionError::InsufficientFunds);
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L188-231)
```rust
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
