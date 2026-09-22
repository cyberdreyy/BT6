### Title
Unprivileged `DepositDelegatorRewards` griefing permanently blocks authorized-withdrawer from fully withdrawing/closing a vote account - (File: `programs/vote/src/vote_state/mod.rs`)

### Summary
The vote program's `DepositDelegatorRewards` instruction (SIMD-0123) can be invoked by *any* transaction sender against *any* V4 vote account, with no relationship to that account's authorized withdrawer or staker. The instruction increments the account's `pending_delegator_rewards` reserve. The `withdraw()` instruction handler treats `pending_delegator_rewards` as an un-withdrawable reserve and explicitly refuses to fully close a vote account (`remaining_balance == 0`) while it is non-zero. Because `pending_delegator_rewards` can only be reduced by the bank's own partitioned-epoch-rewards distribution logic (not by any user-callable instruction), an attacker can permissionlessly and repeatedly "poison" any vote account with a trivial deposit to prevent its authorized withdrawer from ever fully withdrawing/closing the account, and can specifically front-run a pending close/withdraw transaction to make it fail.

### Finding Description
`VoteInstruction::DepositDelegatorRewards` is dispatched in `programs/vote/src/vote_processor.rs` and calls `vote_state::deposit_delegator_rewards`: [1](#0-0) 

The implementation only requires that the caller-designated "source" account sign the transfer — there is no check that the caller is the vote account's authorized withdrawer, staker, or any other privileged party. Any unprivileged transaction sender can supply their own keypair as the source and target an arbitrary vote account: [2](#0-1) 

The `withdraw()` function then treats `pending_delegator_rewards` as a mandatory reserve that must remain in the account: [3](#0-2) 

Specifically:
- Full closure (`remaining_balance == 0`) is rejected outright if `pending_delegator_rewards > 0`.
- Partial withdrawals are capped so that `remaining_balance >= rent_exempt_minimum + pending_delegator_rewards`.

`pending_delegator_rewards` is incremented solely by `deposit_delegator_rewards` (attacker-controlled, permissionless) and is only ever decremented by the runtime's own epoch reward distribution in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` — there is no user-invokable instruction to clear or reduce it. This is confirmed by the test suite, which shows the value is only ever set via deposit or manipulated directly in bank-internal reward calculation code, never by an instruction reachable by the withdrawer: [4](#0-3) 

This is structurally identical to the reported SecondSwap bug class: an unprivileged actor can call a permissionless function (`listVesting` / `DepositDelegatorRewards`) that shifts funds/state into a protected "reserved" bucket, which then causes the accounting check inside a privileged operation (`transferVesting` / vote `withdraw` full-close) to fail — and this can be triggered via front-running a pending privileged transaction.

### Impact Explanation
Any transaction sender can permanently prevent the legitimate authorized withdrawer of any vote account from ever fully closing/withdrawing that vote account, simply by depositing a trivial amount (e.g. 1 lamport) as delegator rewards. Because the only path to reduce `pending_delegator_rewards` is the bank's own reward distribution cycle (which the attacker does not control and which the attacker can re-trigger by depositing again after each distribution), this is a persistent, unprivileged griefing/DoS primitive against a specific account state transition (vote account decommissioning), and it can be timed via front-running to specifically defeat an in-flight `Withdraw` transaction attempting a full close.

### Likelihood Explanation
Trivially reachable: it requires only a single signed transaction from any funded account, a target vote account, and a minimal (even 1 lamport) deposit. The feature is gated behind `commission_rate_in_basis_points`, `custom_commission_collector`, and `block_revenue_sharing` feature flags (SIMD-0185/0291/0232 rollout), so it is only exploitable once these features are activated on a cluster, but there is no other precondition.

### Recommendation
Restrict when/how `pending_delegator_rewards` can be griefed by an unrelated third party — e.g., require the deposit to originate only from the block-revenue/commission collector program or bank-driven reward distribution path rather than an arbitrary signer, or allow the authorized withdrawer to explicitly reject/return unsolicited deposits, or exempt/cap the amount an arbitrary depositor may contribute per transaction so it cannot be weaponized purely to block closure.

### Proof of Concept
1. Attacker observes (or anticipates) that a vote account's authorized withdrawer intends to fully withdraw/close the account via `VoteInstruction::Withdraw(all_lamports)`.
2. Attacker submits `VoteInstruction::DepositDelegatorRewards { deposit: 1 }` targeting the same vote account, signing with their own throwaway funded keypair as `source`, with no privileged relationship to the vote account required (see accounts layout in the instruction dispatch, `programs/vote/src/vote_processor.rs:409-426`).
3. This succeeds unconditionally (subject only to feature-gate checks), setting `pending_delegator_rewards = 1` on the target vote account.
4. The withdrawer's subsequent full-close `Withdraw` transaction now fails with `InstructionError::InsufficientFunds` per the check in `programs/vote/src/vote_state/mod.rs:1087-1092`, exactly mirroring the test `test_withdraw_with_pending_delegator_rewards`'s "Full close blocked when pending > 0" case at `programs/vote/src/vote_state/mod.rs:5898-5920`.
5. The attacker can repeat this after every epoch's reward distribution clears the reserve, indefinitely denying the withdrawer the ability to close the account.

### Citations

**File:** programs/vote/src/vote_processor.rs (L409-426)
```rust
        VoteInstruction::DepositDelegatorRewards { deposit } => {
            // SIMD-0123: Deposit delegator rewards.
            // Requires:
            // * SIMD-0185: Vote State V4
            // * SIMD-0291: Commission in Basis Points
            // * SIMD-0232: Custom Commission Collector
            let feature_set = invoke_context.get_feature_set();
            if !feature_set.commission_rate_in_basis_points
                || !feature_set.custom_commission_collector
                || !feature_set.block_revenue_sharing
            {
                return Err(InstructionError::InvalidInstructionData);
            }

            instruction_context.check_number_of_instruction_accounts(2)?;
            drop(me);
            vote_state::deposit_delegator_rewards(invoke_context, 0, 1, deposit, &signers)
        }
```

**File:** programs/vote/src/vote_processor.rs (L5110-5140)
```rust
        // Success
        let resulting_accounts = process_instruction_with_cu_check(
            VoteProgramFeatures::all_enabled(),
            &instruction_data,
            transaction_accounts.clone(),
            instruction_accounts.clone(),
            Ok(()),
            DEPOSIT_DELEGATOR_REWARDS_COMPUTE_UNITS,
        );

        // Vote account should have been credited `deposit_amount`.
        // Source account should have been debited `deposit_amount`.
        // Vote state's `pending_delegator_rewards` should be updated.
        let vote_account_starting_lamports = vote_account_v4.lamports();
        let source_account_starting_lamports = source_lamports;
        let resulting_vote_account = &resulting_accounts[0];
        let resulting_source_account = &resulting_accounts[1];
        let vote_state =
            deserialize_vote_state_for_test(resulting_vote_account.data(), &vote_pubkey);
        assert_eq!(
            resulting_vote_account.lamports(),
            vote_account_starting_lamports + deposit_amount,
        );
        assert_eq!(
            resulting_source_account.lamports(),
            source_account_starting_lamports - deposit_amount,
        );
        assert_eq!(
            vote_state.as_ref_v4().pending_delegator_rewards,
            deposit_amount,
        );
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
