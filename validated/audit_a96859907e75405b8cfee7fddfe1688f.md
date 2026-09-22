## Analysis

The LP-TVL mispricing bug pattern — a critical eligibility/valuation threshold being evaluated against an **instantaneously readable, freely mutable balance** rather than a time-weighted or committed value — has a direct analog in agave's Validator Admission Ticket (VAT) balance check used to admit vote accounts into the Alpenglow-eligible set.

### Title
Validator Admission Ticket (VAT) eligibility can be gamed via a flash lamport deposit into the vote account before the epoch-boundary balance snapshot - (File: `vote/src/vote_account.rs`, `runtime/src/bank.rs`)

### Summary
`VoteAccounts::clone_and_filter_for_vat` admits a vote account into the VAT-eligible, reward- and consensus-participation-eligible set solely by comparing the vote account's *current* lamport balance against `minimum_vote_account_balance_for_vat` at the moment the epoch-boundary snapshot is taken [1](#0-0) . Because ordinary lamport transfers to any account (including a vote-program-owned account) are unrestricted, this "collateral" balance can be deposited immediately before the snapshot and withdrawn immediately after, without ever being at risk.

### Finding Description
The admission logic filters vote accounts by BLS pubkey presence, non-zero stake, and `vote_account.lamports() >= minimum_vote_account_balance` [2](#0-1) . This filtered set is produced once per epoch boundary in `compute_new_epoch_caches_and_rewards`, which reads the *current* `stakes_cache` state (i.e., whatever lamport balance the vote account happens to have at that instant) and feeds it both into `update_epoch_stakes` (the leader-schedule/consensus-eligible stake set) and into reward calculation [3](#0-2) . The resulting VAT-filtered snapshot is later relied upon by `maybe_burn_vat_from_staked_accounts`, which explicitly assumes "Vote accounts have already been filtered ... to only include accounts with non-zero stake and sufficient balance" [4](#0-3) , and by `get_vat_health_for_next_epoch`, which performs the identical instantaneous-balance comparison [5](#0-4) .

Because the check only inspects the balance at one fixed evaluation point rather than requiring it to be sustained, an operator can:
1. Submit an ordinary system transfer that credits lamports to their own vote account (any account can receive lamports via `SystemInstruction::Transfer` regardless of who owns it) so its balance clears `minimum_vote_account_balance_for_vat` right before the epoch-boundary snapshot is taken.
2. Once admitted into the filtered set (and thus into `epoch_stakes`, into the alpenglow-eligible validator set, and into the reward computation, and having "survived" the VAT burn in `maybe_burn_vat_from_staked_accounts`), submit a `Withdraw` vote instruction to reclaim the lamports. `withdraw()` only checks `remaining_balance`, rent-exempt minimum, and `pending_delegator_rewards` — it has no dependency on VAT admission state [6](#0-5) .

This is structurally identical to the LP-pool bug: a value used for a high-stakes gating decision (LP price / VAT eligibility) is computed from mutable state that the party being evaluated fully controls and can restore, defeating the intended "must hold collateral" invariant with no lasting cost.

### Impact Explanation
A validator that does not actually maintain the balance required by SIMD-357 can still gain admission to the bounded (`MAX_ALPENGLOW_VOTE_ACCOUNTS`) VAT-eligible set, participate in Alpenglow consensus, and be included in stake/vote reward distribution for the epoch, while its vote account is effectively unfunded outside the narrow snapshot window. This corrupts the VAT admission invariant (an economic collateral/anti-spam requirement gating the consensus-participant set) and can result in reward accrual to accounts that never bore the intended lamport cost — a stake/reward accounting corruption reachable purely via ordinary signed transactions (transfer + withdraw) that any holder of the vote account's authority can submit.

### Likelihood Explanation
Likelihood is high for any validator operator willing to time two ordinary transactions (a transfer into their vote account and a subsequent withdraw) around a known, deterministic epoch boundary; no special leader privilege, network position, or race condition is required — only knowledge of when the boundary snapshot is taken, which is derivable from `epoch_schedule`.

### Recommendation
Do not gate VAT admission (or any other economic eligibility check) on an instantaneous, attacker-controlled balance read. Options include: requiring the balance to have been continuously held for a minimum window (e.g., checked across multiple slots/epochs, similar to how commission changes and vote-account state are already delayed a full epoch to prevent "last minute ... rugs" [7](#0-6) ), or tying eligibility to a value that cannot be flash-deposited (e.g., actual bonded/locked stake rather than free vote-account lamports).

### Proof of Concept
1. Operator controls a vote account `V` with `authorized_withdrawer` key `W`, currently below `minimum_vote_account_balance_for_vat`.
2. Immediately before the last slot of the current epoch is processed (before `update_epoch_stakes`/`compute_new_epoch_caches_and_rewards` runs for the boundary), submit a `SystemInstruction::Transfer` crediting `V` with enough lamports to clear `minimum_vote_account_balance_for_vat`.
3. The epoch-boundary bank calls `clone_and_filter_for_vat`, admitting `V` into `filtered_distribution_vote_accounts` / `epoch_stakes` per [1](#0-0) ; `maybe_burn_vat_from_staked_accounts` deducts only `vat_to_burn_per_epoch`, leaving the rest of the deposited lamports intact [8](#0-7) .
4. In the very next slot, `W` submits `VoteInstruction::Withdraw` to reclaim the deposited lamports (minus the VAT burn and rent-exempt minimum) via `withdraw()` [9](#0-8) , restoring `V` to its pre-deposit economic state while remaining admitted to the eligible/reward-earning set for the whole epoch.

### Citations

**File:** vote/src/vote_account.rs (L212-232)
```rust
    pub fn clone_and_filter_for_vat(
        &self,
        max_vote_accounts: usize,
        minimum_vote_account_balance: u64,
    ) -> VoteAccounts {
        assert!(max_vote_accounts > 0, "max_vote_accounts must be > 0");
        let capacity = max_vote_accounts.min(self.vote_accounts.len());
        let mut entries_to_sort: Vec<(&Pubkey, &VoteAccount, u64)> = Vec::with_capacity(capacity);
        for (pubkey, (stake, vote_account)) in self.vote_accounts.iter() {
            let has_bls = vote_account
                .vote_state_view()
                .bls_pubkey_compressed()
                .is_some();
            let has_stake = *stake != 0u64;
            let has_balance = vote_account.lamports() >= minimum_vote_account_balance;

            if !has_bls || !has_stake || !has_balance {
                continue;
            }
            entries_to_sort.push((pubkey, vote_account, *stake));
        }
```

**File:** runtime/src/bank.rs (L1731-1736)
```rust
        // Snapshot of vote account state from the beginning of the epoch prior to
        // the rewarded epoch. This snapshot state is saved a full epoch before
        // being used to prevent last minute commission rugs.
        let snapshot_epoch_vote_accounts = self
            .epoch_stakes(rewarded_epoch)
            .map(|epoch_stakes| epoch_stakes.stakes().vote_accounts());
```

**File:** runtime/src/bank.rs (L1781-1792)
```rust
        // Apply stake rewards and commission using the VAT-filtered distribution
        // vote-account snapshot.
        let filtered_distribution_vote_accounts = unfiltered_distribution_vote_accounts
            .clone_and_filter_for_vat(
                MAX_ALPENGLOW_VOTE_ACCOUNTS,
                self.minimum_vote_account_balance_for_vat(),
            );
        if AlpenglowEpochType::is_alpenglow_or_migration_epoch(self, rewarded_epoch) {
            reward_epoch_delegated_stakes.set(self, &filtered_distribution_vote_accounts);
        }
        let cached_vote_accounts =
            self.get_cached_vote_accounts(rewarded_epoch, &filtered_distribution_vote_accounts);
```

**File:** runtime/src/bank.rs (L2644-2689)
```rust
    /// Burn the Validator Admission ticket from each vote account if Alpenglow is enabled
    ///
    /// Note: This must ONLY be called after the vote accounts have been filtered (`clone_and_filter_for_vat`)
    /// to the top `MAX_ALPENGLOW_VOTE_ACCOUNTS` that contain enough balance for admission.
    fn maybe_burn_vat_from_staked_accounts(&mut self, epoch_stakes: &VersionedEpochStakes) {
        let feature_snapshot = self.feature_set.snapshot();
        if !feature_snapshot.alpenglow {
            return;
        }

        let vat_to_burn_per_epoch = self.vat_to_burn_per_epoch();
        let vote_accounts = epoch_stakes.stakes().vote_accounts();
        debug_assert!(vote_accounts.len() <= 2000);
        // +1 for the incinerator account
        let mut accounts_to_store: Vec<(Pubkey, AccountSharedData)> =
            Vec::with_capacity(vote_accounts.len() + 1);
        let mut total_vat = 0u64;

        // Vote accounts have already been filtered by clone_and_filter_for_vat to only include
        // accounts with non-zero stake and sufficient balance.
        for (vote_pubkey, _stake) in vote_accounts.delegated_stakes() {
            let mut account = self.get_account(vote_pubkey).unwrap();
            total_vat += vat_to_burn_per_epoch;
            account.set_lamports(
                account
                    .lamports()
                    .checked_sub(vat_to_burn_per_epoch)
                    .expect(
                        "Vote accounts should have already been filtered to contain enough \
                         balance for the VAT",
                    ),
            );
            accounts_to_store.push((*vote_pubkey, account));
        }

        // Per SIMD-0357, transfer collected VAT to the incinerator account.
        let mut incinerator_account = self.get_account(&incinerator::id()).unwrap_or_default();
        incinerator_account.set_lamports(
            incinerator_account
                .lamports()
                .checked_add(total_vat)
                .unwrap(),
        );
        accounts_to_store.push((incinerator::id(), incinerator_account));

        self.store_accounts((self.slot, accounts_to_store.as_slice()), None);
```

**File:** runtime/src/bank.rs (L2837-2865)
```rust
    pub fn get_vat_health_for_next_epoch(
        &self,
        vote_account_pubkey: &Pubkey,
    ) -> std::result::Result<(), VATHealthError> {
        let vote_accounts = self.vote_accounts();

        let Some((_, vote_account)) = vote_accounts.get(vote_account_pubkey) else {
            return Err(VATHealthError::VoteAccountNotFound);
        };

        if vote_account
            .vote_state_view()
            .bls_pubkey_compressed()
            .is_none()
        {
            return Err(VATHealthError::NoBLSPubkey);
        }

        let my_balance = vote_account.lamports();
        let minimum_vote_account_balance_for_vat = self.minimum_vote_account_balance_for_vat();
        if vote_account.lamports() < minimum_vote_account_balance_for_vat {
            return Err(VATHealthError::InsufficientFundsInVoteAccount(
                my_balance,
                minimum_vote_account_balance_for_vat,
            ));
        }

        Ok(())
    }
```

**File:** programs/vote/src/vote_state/mod.rs (L1063-1128)
```rust
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
```
