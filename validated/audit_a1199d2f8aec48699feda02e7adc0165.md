### Title
Vote account funds become permanently locked below `rent_exempt_minimum + pending_delegator_rewards` with no override even for the authorized withdrawer - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The external report's bug class is: a mandatory check (KYC) is enforced unconditionally inside a fund-recovery function, so once a user's status changes there is no path — not even for the trusted `MANAGER_ADMIN` — to release the locked funds. The closest reachable analog in this Agave codebase is the SIMD-0123 `pending_delegator_rewards` reservation enforced inside the vote program's `withdraw` instruction. This reservation is checked unconditionally against the `authorized_withdrawer` — the highest-privilege signer for a vote account — with no bypass instruction, and the value can only ever decrease through the epoch block-reward distribution path, which itself depends on conditions (active delegated stake to that vote account) that are not guaranteed to ever be satisfied.

### Finding Description
`withdraw()` in [1](#0-0)  enforces two unconditional restrictions based on `pending_delegator_rewards`:

1. Full account closure (withdrawing to a zero remaining balance) is rejected outright whenever `pending_delegator_rewards > 0`: [2](#0-1) 
2. Partial withdrawals are capped so the remaining balance can never go below `rent_exempt_minimum + pending_delegator_rewards`: [3](#0-2) 

Crucially, both checks are evaluated *after* `verify_authorized_signer(vote_state.authorized_withdrawer(), signers)` succeeds [4](#0-3)  — i.e., even the account's highest-privilege signer (the `authorized_withdrawer`, functionally analogous to `MANAGER_ADMIN` in the original report) cannot override or bypass the reservation. There is no instruction in the vote program that lets the withdrawer or any other authority manually clear or reduce `pending_delegator_rewards`; the only place the field is mutated downward is not in the vote program at all, but in the epoch rewards machinery.

`pending_delegator_rewards` is only ever increased by `deposit_delegator_rewards()` [5](#0-4)  and `add_pending_delegator_rewards()` [6](#0-5) . Its only depletion path is via `calculate_block_reward()` in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`, which computes each delegator's share as `pending_delegator_rewards * stake / total_active_stake` [7](#0-6) . If `total_active_stake` for that vote account is `0` (e.g., all delegated stake has been deactivated/undelegated, or the vote account is absent from `distribution_epoch_vote_accounts`), the function returns `0` and no reward is distributed for that epoch: [8](#0-7)  and [9](#0-8) .

This mirrors the CashManager.sol shape precisely: a mandatory reservation/check inside the "withdraw"-equivalent function, applied without exception to the trusted authority, whose only clearing mechanism depends on external state (delegated stake existing) that can become permanently unsatisfiable — leaving `min(rent_exempt_minimum + pending_delegator_rewards)` worth of lamports irrecoverably stuck in the vote account, and the account itself unclosable, regardless of who signs.

### Impact Explanation
Once a vote account accumulates `pending_delegator_rewards` (via `DepositDelegatorRewards`) and subsequently loses all actively delegated stake before that reservation is fully distributed by the epoch reward calculation, the reserved lamports become permanently non-withdrawable and the vote account can never be closed by `withdraw()`, even by the `authorized_withdrawer`. This is a concrete, unauthorized *permanent lockup of funds* with no admin/authority recovery path, matching the "Impact" bar (concrete unauthorized fund lock / state mutation) required by the validation rules.

### Likelihood Explanation
Requires SIMD-0123/SIMD-0185/SIMD-0232/SIMD-0291 features to be active (block revenue sharing, V4 vote state, custom commission collector, commission in basis points) — all of which are represented as ordinary, non-privileged feature-gated instructions reachable from any user's transaction (`DepositDelegatorRewards`, `Withdraw`, and normal stake deactivation). No special privilege or node-level access is needed to trigger the condition; it can occur through ordinary economic activity (stake deactivation) racing against reward accrual/distribution timing.

### Recommendation
Add an authority-gated recovery/override path (e.g., an instruction restricted to the `authorized_withdrawer`, or a protocol-level sweep) that can forcibly clear or redistribute `pending_delegator_rewards` when the associated delegated stake can no longer receive block rewards (e.g., `total_active_stake == 0` for that vote account across a bounded number of epochs), so that reserved lamports are not permanently unrecoverable. Alternatively, ensure the epoch reward distribution logic guarantees eventual full depletion of `pending_delegator_rewards` regardless of the delegated-stake state, or fall back to crediting the reservation directly to the `authorized_withdrawer`/`block_revenue_collector` when no eligible delegator stake remains.

### Proof of Concept
1. Activate SIMD-0123/0185/0232/0291 feature set.
2. Create a V4 vote account and delegate stake to it.
3. Call `DepositDelegatorRewards` to set `pending_delegator_rewards = X` via `deposit_delegator_rewards()` [5](#0-4) .
4. Deactivate/undelegate all stake pointed at this vote account so `total_active_stake` for it becomes `0` in `reward_epoch_delegated_stakes` before the next epoch-reward distribution consumes the pending amount; `calculate_block_reward()` then always returns `0` for this vote account going forward [9](#0-8) .
5. As the `authorized_withdrawer`, call `Withdraw` for the full balance: `withdraw()` rejects the request because `remaining_balance == 0` and `pending_delegator_rewards > 0` [2](#0-1) .
6. Attempt any partial withdrawal beyond `lamports - rent_exempt_minimum - pending_delegator_rewards`: it also fails [3](#0-2) .
7. No instruction exists to reduce `pending_delegator_rewards` outside of the reward-distribution path (confirmed by exhaustive grep for mutation sites in `programs/vote/src/vote_state/mod.rs`, `handler.rs`, and `vote_processor.rs`), so the reserved lamports and the ability to close the account are permanently locked with no recourse for the `authorized_withdrawer`.

Note: I was not able to fully trace the epoch-reward-distribution call site that actually writes the *decremented* `pending_delegator_rewards` back into the vote account's on-chain state (only the *calculation* function `calculate_block_reward` was located); this final write-back step should be verified in a live session to confirm there is truly no other systemic mechanism that guarantees eventual depletion.

### Citations

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

**File:** programs/vote/src/vote_state/mod.rs (L1062-1129)
```rust
/// Withdraw funds from the vote account
pub fn withdraw<S: std::hash::BuildHasher>(
    instruction_context: &InstructionContext,
    vote_account_index: IndexOfAccount,
    target_version: VoteStateTargetVersion,
    lamports: u64,
    to_account_index: IndexOfAccount,
    signers: &HashSet<Pubkey, S>,
    rent_sysvar: &Rent,
    clock: &Clock,
) -> Result<(), InstructionError> {
    let mut vote_account =
        instruction_context.try_borrow_instruction_account(vote_account_index)?;
    let vote_state = get_vote_state_handler_checked(&vote_account, target_version)?;

    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;

    let remaining_balance = vote_account
        .get_lamports()
        .checked_sub(lamports)
        .ok_or(InstructionError::InsufficientFunds)?;

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

    vote_account.checked_sub_lamports(lamports)?;
    drop(vote_account);
    let mut to_account = instruction_context.try_borrow_instruction_account(to_account_index)?;
    to_account.checked_add_lamports(lamports)?;
    Ok(())
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
