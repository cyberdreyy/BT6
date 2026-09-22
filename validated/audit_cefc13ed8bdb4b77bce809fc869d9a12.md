## Finding [1](#0-0) 

### Title
Vote account `pending_delegator_rewards` pool is never decremented after being distributed to stakers, causing repeated reward corruption - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`, `programs/vote/src/vote_state/mod.rs`)

### Summary
This is the closest reachable analog to the Rewards.sol bug class: a shared reward pool (`pending_delegator_rewards`) that any unprivileged account can fund, and that is distributed pro-rata to a set of "currently registered" participants (stake delegations), without the pool being consumed/decremented on distribution.

### Finding Description
Any unprivileged signer can grow a vote account's shared delegator-reward pool by calling `deposit_delegator_rewards`, which performs a signed transfer and then calls `add_pending_delegator_rewards`, which only ever increments the field: [2](#0-1) [3](#0-2) 

This is directly analogous to `addRewardsForAccessory()` in the reported bug — an unprivileged actor funds a shared pool associated with a "group" (the accessory / here, the validator's delegator set).

During epoch-reward calculation (SIMD-0123 block-revenue sharing), each stake delegation's share of this pool is computed as `pending_delegator_rewards * stake / total_active_stake`: [4](#0-3) 

This mirrors the flawed "unequip" pattern in `Rewards.sol`: each participant's payout is computed as a fraction of the *current, undiminished* pool balance rather than a pool that shrinks as it is paid out. I searched the entire indexed codebase for every mutation site of `pending_delegator_rewards` (`programs/vote/src/vote_state/mod.rs`, `programs/vote/src/vote_state/handler.rs`, `programs/vote/src/vote_processor.rs`, `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`, `vote/src/vote_state_view*`) and found only one mutator: `add_pending_delegator_rewards` (increment-only). No subtraction, reset, or "consume" of `pending_delegator_rewards` was found anywhere after `calculate_block_reward` computes and pays out `block_reward` to stake accounts in `build_updated_stake_reward`: [5](#0-4) 

The `withdraw` instruction only *reads* `pending_delegator_rewards` to gate withdrawable balance (treating it as a still-owed liability), it never reduces it: [6](#0-5) 

Consequently, the exact same `pending_delegator_rewards` figure is used as the numerator for every subsequent epoch's `calculate_block_reward` call for as long as the field is not zeroed, effectively re-paying the same nominal pool to stakers epoch after epoch — the accounting equivalent of the Rewards.sol flaw where a claimed share is computed from a pool that was never debited.

### Impact Explanation
If this liability field is truly never cleared/decremented anywhere in the code paths reachable from a normal transaction, block-reward lamports minted into stake accounts (`stake_reward_lamports_minted` tracked separately from `pending_delegator_rewards`) are not backed by a corresponding reduction of the vote account's recorded liability. This causes reward corruption: stakers are systematically paid against a stale, ever-growing (or non-shrinking) pool baseline, while the `withdraw` gate continues to treat the full un-decremented amount as reserved, misrepresenting the vote account's true obligations and enabling repeated overpayment of the same nominal pool across multiple epochs.

### Likelihood Explanation
This requires SIMD-0123 (`block_revenue_sharing`) to be active and an Alpenglow/migration epoch type, and requires a delegator (or anyone) to call `DepositDelegatorRewards`, both of which are reachable via a single, unprivileged, signed transaction plus the normal epoch-boundary reward pipeline that every validator executes deterministically. No leader or validator collusion is required.

### Recommendation
Verify (with full repository access, since Ask's index only exposes partial file contents) whether `pending_delegator_rewards` is decremented somewhere outside the paths inspected here — e.g., in `store_stake_accounts_in_partition`/`distribute_epoch_rewards_in_partition`, or in a vote-account-mutation step tied to `block_revenue_sharing` that was not captured by the index. If no decrement exists, `calculate_block_reward` and the corresponding vote-account state update must be changed to atomically subtract the amount actually distributed from `pending_delegator_rewards` (and correspondingly debit the vote account's lamports), so the pool is consumed exactly once, analogous to fixing the Rewards.sol design by tracking a per-claim consumed amount rather than re-deriving shares from a static, unconsumed pool balance.

### Proof of Concept
Not independently constructible with the tools available (read-only code search); the required PoC would exercise: (1) `VoteInstruction::DepositDelegatorRewards` to fund `pending_delegator_rewards` on a SIMD-0123-enabled vote account, (2) two or more epoch-boundary reward cycles with `block_revenue_sharing` active, and (3) an assertion that `pending_delegator_rewards` remains unchanged (or that the same amount is paid out again) across epochs. I could not run this against a live/test validator in this session — this would need to be validated in a full Devin session with build/test tooling.

**Confidence caveat:** I could not exhaustively rule out a decrement/reset of `pending_delegator_rewards` located in code outside the indexed search results (e.g., in files or line ranges excluded by Ask's index size limits). If you need certainty on this point, a Devin session with full repository access should grep the entire `programs/vote` and `runtime/src/bank` trees for every write to `pending_delegator_rewards` to confirm no consuming mutation exists.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L173-232)
```rust
/// Calculates block reward for a stake account based on SIMD-0123
fn calculate_block_reward(
    rewarded_epoch: Epoch,
    delegation: &Delegation,
    stake_history: &StakeHistory,
    distribution_epoch_vote_accounts: &VoteAccounts,
    ag_epoch_type: &AlpenglowEpochType,
    new_warmup_cooldown_rate_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> u64 {
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
}
```

**File:** programs/vote/src/vote_state/mod.rs (L936-988)
```rust
pub fn deposit_delegator_rewards<S: std::hash::BuildHasher>(
    invoke_context: &mut InvokeContext,
    vote_account_index: IndexOfAccount,
    sender_account_index: IndexOfAccount,
    deposit: u64,
    signers: &HashSet<Pubkey, S>,
) -> Result<(), InstructionError> {
    let transaction_context = &invoke_context.transaction_context;
    let instruction_context = transaction_context.get_current_instruction_context()?;

    let vote_address = *instruction_context.get_key_of_instruction_account(vote_account_index)?;
    let source_address =
        *instruction_context.get_key_of_instruction_account(sender_account_index)?;

    // Source account must sign the transfer.
    verify_authorized_signer(&source_address, signers)?;

    // SIMD-0123 states we must validate the vote account deserializes to a v4
    // *before* attempting CPI, then update the `pending_delegator_rewards`
    // field *last*.
    // We can deserialize it, and hold onto the deserialized payload in-memory.
    // This way, we can drop the account borrow but avoid re-deserializing
    // later, since we know only lamports will change.
    let mut vote_state = {
        let vote_account =
            instruction_context.try_borrow_instruction_account(vote_account_index)?;

        // Can't use `get_vote_state_handler_checked`, since it will convert
        // the underlying vote state to v4.
        // SIMD-0123 requires an *initialized v4*.
        let versioned = VoteStateVersions::deserialize(vote_account.get_data())?;
        if let VoteStateVersions::V4(vote_state_v4) = versioned {
            Ok(VoteStateHandler::new_v4(*vote_state_v4))
        } else {
            Err(InstructionError::InvalidAccountData)
        }
    }?;

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

**File:** programs/vote/src/vote_state/mod.rs (L1084-1122)
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
    }
```

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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L262-267)
```rust
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
```
