## Analog Found [1](#0-0) , [2](#0-1) 

### Title
Validator can bypass the one-epoch commission-increase timelock by closing and re-initializing its own vote account - (File: `programs/vote/src/vote_state/mod.rs`, `runtime/src/stakes.rs`, `runtime/src/bank/partitioned_epoch_rewards/mod.rs`)

### Summary
The `HoldefiSettings` report describes an owner bypassing a parameter-change timelock by removing and re-adding an entity rather than modifying it in place, since the remove/re-add path skips the "time-off" check applied to in-place updates. Agave's vote program has the same structural weakness for vote-account commission: `UpdateCommission` enforces a strict timelock (`is_commission_update_allowed`) on commission *increases*, and the reward-calculation path additionally delays the effect of any commission change by a full epoch (`delay_commission_updates`) to prevent "last minute commission rugs." However, both protections key off the vote account's *continuous* history, and a validator can erase that history — and therefore both timelocks — simply by fully withdrawing the vote account (which zeroes/deinitializes it) and then calling `InitializeAccount`/`InitializeAccountV2` again on the same pubkey with a new, arbitrarily high commission, neither of which routes through `update_commission`'s time checks.

### Finding Description
`update_commission` gates commission **increases** with `is_commission_update_allowed`, which only allows raising commission in the first half of an epoch: [3](#0-2) [4](#0-3) 

This check is only reachable from the `UpdateCommission`/`UpdateCommissionBps` instructions in `vote_processor.rs`. It is never consulted by `initialize_account`/`initialize_account_v2`, which set the commission field directly from the caller-supplied `VoteInit`/`VoteInitV2` with no epoch restriction at all: [5](#0-4) 

A vote account can be fully withdrawn (deinitialized) as long as it hasn't earned credits in the last 2 epochs: [6](#0-5) 

Once its lamports hit zero, `StakesCache::check_and_store` unconditionally removes the account from the `VoteAccounts` cache used to build epoch-boundary snapshots: [2](#0-1) 

This "disappear/reappear" behavior is explicitly exercised in `test_stakes_vote_account_disappear_reappear`, confirming a zero-lamport vote account is dropped from the cache and treated as brand-new on reappearance: [7](#0-6) 

Separately, the reward-distribution path implements a second, independent timelock: to prevent "last minute commission rugs," commission used for reward calculation is looked up from a `snapshot_epoch_vote_accounts` cache built one epoch in the past, falling back only when the vote pubkey is *not found* there (i.e., genuinely new accounts): [8](#0-7) [9](#0-8) 

Because the stakes cache erases the vote account's presence once it is fully withdrawn, a re-initialized vote account at the same pubkey is indistinguishable from a genuinely new one to this fallback path, and the delayed lookup is skipped in favor of the *current* commission — exactly what the delay was designed to prevent. This intentional "new account" fallback is confirmed by `test_calculate_stake_vote_rewards_new_vote_account`: [10](#0-9) 

### Impact Explanation
A validator can bypass both intended timelocks that protect delegators from sudden commission increases:
1. The gradual, epoch-midpoint restriction on `UpdateCommission` (`is_commission_update_allowed`).
2. The `delay_commission_updates` one-epoch-lag protection meant to guarantee delegators at least one epoch's notice before a new commission rate applies to their rewards.

By closing (`Withdraw` to zero balance) and reinitializing (`InitializeAccount`/`InitializeAccountV2`) their own vote account, a validator sets an arbitrary commission that takes effect immediately for reward computation in the very epoch it resumes voting, silently increasing the share of stake rewards diverted to the validator's commission at delegators' expense, without the notice period the protocol is designed to guarantee. This is a transaction-reachable stake/reward corruption class of impact — the delegators' rewards are systematically redirected to the validator commission account outside the rules the protocol advertises.

### Likelihood Explanation
Reachable with only account-owner authority (the vote account's `authorized_withdrawer`/node identity) and standard instructions (`Withdraw`, `InitializeAccount`/`InitializeAccountV2`) — no special privilege beyond controlling one's own vote account. The only precondition is that the vote account must be dormant (no credits) for 2+ epochs before it can be closed (`reject_active_vote_account_close`), which a validator can trivially arrange by pausing voting for a couple of epochs before executing the bypass, then resuming voting immediately with the new commission.

### Recommendation
Preserve continuity of the commission-change history across `Withdraw`-then-reinitialize on the same vote account pubkey: e.g., carry forward the last commission/epoch metadata (or the "last commission change" record) through deinitialization, or enforce `is_commission_update_allowed`-style checks inside `initialize_account`/`initialize_account_v2` when re-initializing a pubkey that recently held a vote account. Similarly, the `delay_commission_updates` reward-calculation fallback should not treat a re-initialized account at a previously-used pubkey as "new" for commission purposes.

### Proof of Concept
1. Validator's vote account stops earning credits (stops voting) for 2+ epochs so `reject_active_vote_account_close` is false.
2. Validator submits `Withdraw(all_lamports)` on the vote account — `programs/vote/src/vote_state/mod.rs::withdraw` deinitializes the account and `StakesCache::check_and_store` removes it entirely from `VoteAccounts`.
3. Validator funds the account back to rent-exemption and submits `InitializeAccount`/`InitializeAccountV2` with a high commission (e.g., 100%) — this path never calls `update_commission`/`is_commission_update_allowed`.
4. Validator resumes voting immediately. Because the vote pubkey is absent from `snapshot_epoch_vote_accounts`/`rewarded_epoch_vote_accounts` (removed at step 2), `CachedVoteAccounts` in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` falls back to the vote account's current (newly set, high) commission for reward distribution in that very epoch, bypassing both the epoch-midpoint increase restriction and the one-epoch commission delay.

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L797-815)
```rust
pub fn update_commission<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    commission: u8,
    signers: &HashSet<Pubkey, S>,
    epoch_schedule: &EpochSchedule,
    clock: &Clock,
    disable_commission_update_rule: bool,
) -> Result<(), InstructionError> {
    let vote_state_result = get_vote_state_handler_checked(vote_account, target_version);
    let enforce_commission_update_rule = !disable_commission_update_rule
        && match vote_state_result.as_ref() {
            Ok(decoded_vote_state) => commission > decoded_vote_state.commission(),
            Err(_) => true,
        };

    if enforce_commission_update_rule && !is_commission_update_allowed(clock.slot, epoch_schedule) {
        return Err(VoteError::CommissionUpdateTooLate.into());
    }
```

**File:** programs/vote/src/vote_state/mod.rs (L990-1004)
```rust
/// Given the current slot and epoch schedule, determine if a commission change
/// is allowed
pub fn is_commission_update_allowed(slot: Slot, epoch_schedule: &EpochSchedule) -> bool {
    // always allowed during warmup epochs
    if let Some(relative_slot) = slot
        .saturating_sub(epoch_schedule.first_normal_slot)
        .checked_rem(epoch_schedule.slots_per_epoch)
    {
        // allowed up to the midpoint of the epoch
        relative_slot.saturating_mul(2) <= epoch_schedule.slots_per_epoch
    } else {
        // no slots per epoch, just allow it, even though this should never happen
        true
    }
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

**File:** programs/vote/src/vote_state/mod.rs (L1131-1186)
```rust
/// Initialize the vote_state for a vote account using VoteInitV2
/// Assumes that the account is being init as part of a account creation or
/// balance transfer and that the transaction must be signed by the staker's
/// keys.
///
/// Also validates the inflation-rewards and block-revenue collector accounts
/// per SIMD-0464 (which delegates to the SIMD-0232 collector checks) and
/// verifies the BLS proof of possession for the authorized voter BLS pubkey.
pub fn initialize_account_v2<S: std::hash::BuildHasher, F>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    vote_init: &VoteInitV2,
    inflation_rewards_collector: NewCommissionCollector,
    block_revenue_collector: NewCommissionCollector,
    signers: &HashSet<Pubkey, S>,
    clock: &Clock,
    rent: &Rent,
    consume_pop_compute_units: F,
) -> Result<(), InstructionError>
where
    F: FnOnce() -> Result<(), InstructionError>,
{
    VoteStateHandler::check_vote_account_length(vote_account, target_version)?;
    let versioned = vote_account.get_state::<VoteStateVersions>()?;

    if !versioned.is_uninitialized() {
        return Err(InstructionError::AccountAlreadyInitialized);
    }

    // node must agree to accept this vote account
    verify_authorized_signer(&vote_init.node_pubkey, signers)?;

    // Per SIMD-0464, validate the collector accounts using the same checks as
    // `UpdateCommissionCollector` (SIMD-0232).
    let inflation_rewards_collector_key =
        inflation_rewards_collector.validate_and_resolve_key(vote_account, rent)?;
    let block_revenue_collector_key =
        block_revenue_collector.validate_and_resolve_key(vote_account, rent)?;

    // verify the BLS pubkey proof of possession
    verify_bls_proof_of_possession(
        vote_account.get_key(),
        &vote_init.authorized_voter_bls_pubkey,
        &vote_init.authorized_voter_bls_proof_of_possession,
        consume_pop_compute_units,
    )?;

    VoteStateHandler::init_vote_account_state_v2(
        vote_account,
        vote_init,
        &inflation_rewards_collector_key,
        &block_revenue_collector_key,
        clock,
        target_version,
    )
}
```

**File:** runtime/src/stakes.rs (L99-116)
```rust
        // Zero lamport accounts are not stored in accounts-db
        // and so should be removed from cache as well.
        if account.lamports() == 0 {
            if solana_vote_program::check_id(owner) {
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            } else if stake_program::check_id(owner) {
                let mut stakes = self.0.write().unwrap();
                stakes.remove_stake_delegation(
                    pubkey,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
            }
            return;
        }
```

**File:** runtime/src/stakes.rs (L1003-1042)
```rust
    #[test]
    fn test_stakes_vote_account_disappear_reappear() {
        let stakes_cache = StakesCache::new(Stakes {
            epoch: 4,
            ..Stakes::default()
        });
        let rent = Rent::default();

        let ((vote_pubkey, mut vote_account), (stake_pubkey, stake_account)) =
            create_staked_node_accounts(10, &rent);

        stakes_cache.check_and_store(&vote_pubkey, &vote_account, None, true);
        stakes_cache.check_and_store(&stake_pubkey, &stake_account, None, true);

        {
            let stakes = stakes_cache.stakes();
            let vote_accounts = stakes.vote_accounts();
            assert!(vote_accounts.get(&vote_pubkey).is_some());
            assert_eq!(vote_accounts.get_delegated_stake(&vote_pubkey), 10);
        }

        vote_account.set_lamports(0);
        stakes_cache.check_and_store(&vote_pubkey, &vote_account, None, true);

        {
            let stakes = stakes_cache.stakes();
            let vote_accounts = stakes.vote_accounts();
            assert!(vote_accounts.get(&vote_pubkey).is_none());
            assert_eq!(vote_accounts.get_delegated_stake(&vote_pubkey), 0);
        }

        vote_account.set_lamports(1);
        stakes_cache.check_and_store(&vote_pubkey, &vote_account, None, true);

        {
            let stakes = stakes_cache.stakes();
            let vote_accounts = stakes.vote_accounts();
            assert!(vote_accounts.get(&vote_pubkey).is_some());
            assert_eq!(vote_accounts.get_delegated_stake(&vote_pubkey), 10);
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L305-319)
```rust
pub(super) struct CachedVoteAccounts<'a> {
    /// Snapshot of vote account state from the beginning of the epoch prior to
    /// the rewarded epoch. This snapshot state is saved a full epoch before
    /// being used to prevent last minute commission rugs.
    ///
    /// Developer note: This field is `Option` to handle large bank warps
    pub(super) snapshot_epoch_vote_accounts: Option<&'a VoteAccounts>,
    /// Vote account state from the beginning of the rewarded epoch.
    ///
    /// Developer note: This field is `Option` to handle large bank warps
    pub(super) rewarded_epoch_vote_accounts: Option<&'a VoteAccounts>,
    /// Vote account state from the end of the rewarded epoch / beginning of the
    /// distribution epoch.
    pub(super) distribution_epoch_vote_accounts: &'a VoteAccounts,
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L701-724)
```rust
        let vote_state = vote_account.vote_state_view();

        // Fetch the voter commission from past epochs to attempt to
        // delay the effect of commission updates by at least one
        // full epoch.
        // When `commission_rate_in_basis_points` is true, use the new field
        // `inflation_rewards_commission_bps`; otherwise use the legacy
        // percentage field and convert to basis points by multiplying by 100.
        let commission_bps = if delay_commission_updates {
            let vote_state_for_commission = snapshot_epoch_vote_accounts
                .and_then(|eva| eva.get(&vote_pubkey))
                .or_else(|| rewarded_epoch_vote_accounts.and_then(|eva| eva.get(&vote_pubkey)))
                .map(|vote_account| vote_account.vote_state_view())
                .unwrap_or(vote_state);
            if commission_rate_in_basis_points {
                vote_state_for_commission.inflation_rewards_commission()
            } else {
                vote_state_for_commission.commission() as u16 * 100
            }
        } else if commission_rate_in_basis_points {
            vote_state.inflation_rewards_commission()
        } else {
            vote_state.commission() as u16 * 100
        };
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1976-2015)
```rust
    #[test_case(true; "delay_commission_updates")]
    #[test_case(false; "instant_commission_updates")]
    fn test_calculate_stake_vote_rewards_new_vote_account(delay_commission_updates: bool) {
        let GenesisConfigInfo {
            mut genesis_config, ..
        } = genesis_utils::create_genesis_config_with_leader(
            1_000_000 * LAMPORTS_PER_SOL,
            &Pubkey::new_unique(),
            42 * LAMPORTS_PER_SOL,
        );

        genesis_config.epoch_schedule = EpochSchedule::new(SLOTS_PER_EPOCH);
        if !delay_commission_updates {
            deactivate_features(&mut genesis_config, &vec![delay_commission_updates::id()]);
        }

        let (bank, bank_forks) =
            Bank::new_for_tests(&genesis_config).wrap_with_bank_forks_for_tests();
        let vote_address = Pubkey::new_unique();

        // No reward should be given in the epoch that a vote account is
        // delegated to for the first time
        let mut bank = apply_epoch_operations(
            bank,
            bank_forks.as_ref(),
            EpochOperations {
                epoch: 0,
                vote_operations: vec![(
                    vote_address,
                    VoteOperations {
                        create_with_balance: Some(LAMPORTS_PER_SOL),
                        new_commission: Some(1),
                        earned_credits: Some(1000),
                        delegate_stake_amount: Some(LAMPORTS_PER_SOL),
                        ..VoteOperations::default()
                    },
                )],
            },
        );

```
