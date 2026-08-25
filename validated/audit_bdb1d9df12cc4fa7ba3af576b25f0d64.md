### Title
Vote-account VAT burn can underflow-panic if withdraw() drains lamports between admission filtering and epoch-boundary burn - ([File: runtime/src/bank.rs])

### Summary
`Bank::maybe_burn_vat_from_staked_accounts` unconditionally subtracts `vat_to_burn_per_epoch` from every admitted vote account's lamport balance using `checked_sub(...).expect(...)`, relying entirely on an earlier filtering step (`clone_and_filter_for_vat`) having guaranteed "enough balance for admission." If a vote account's balance is reduced after that filtering point but before this subtraction actually executes at the epoch boundary (e.g., via the vote program's ordinary, user-invocable `Withdraw` instruction), the `expect()` fires and the validator panics, mirroring the `ConcentratedLiquidityPoolManager` bug class where a claimed/derived amount exceeds the actual remaining balance and the raw subtraction underflows.

### Finding Description
`maybe_burn_vat_from_staked_accounts` performs, for every vote account selected for Validator Admission Ticket (VAT) burning: [1](#0-0) 

```rust
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
```

The correctness of the `checked_sub` depends entirely on an *earlier, separate* invariant established by `clone_and_filter_for_vat` — the doc comment explicitly states this function "must ONLY be called after the vote accounts have been filtered ... to the top `MAX_ALPENGLOW_VOTE_ACCOUNTS` that contain enough balance for admission." [2](#0-1) 

This is structurally identical to the reported bug class: a value used to authorize a deduction (`secondsInside`/liquidity-derived reward) is computed or validated at one point, while the actual subtraction is performed later against the (possibly changed) live balance (`incentive.rewardsUnclaimed`), and the code has no fallback other than the raw checked subtraction plus a panic/expect. Here, the "balance sufficiency" check happens at admission-filtering time against `epoch_stakes`, but the debit happens later against the account's *live* lamports fetched via `self.get_account(vote_pubkey)`. Any lamport-reducing operation on the vote account between those two points — most notably the standard, user-callable vote `Withdraw` instruction — invalidates the assumption: [3](#0-2) 

The withdraw path checks only against the vote account's own withdrawal rules (rent-exemption plus `pending_delegator_rewards`); it has no knowledge of, or accounting for, a pending VAT burn obligation computed from a separate admission snapshot. This is analogous to how the Trident pool tracked `secondsInside` independent of the live `rewardsUnclaimed`, so a legitimate action (large liquidity / here, a legitimate withdrawal) can make the later subtraction underflow.

### Impact Explanation
If reachable, the `expect()` on `checked_sub` will panic deterministically for every validator processing the same block/epoch-boundary state, since account state (lamports) is part of consensus. This is not a soft/local error path — it is an unconditional panic during bank freeze/epoch-boundary processing, which is on the block-replay path. A panic here would crash the validator process (or otherwise halt replay), i.e., a replay-path panic/DoS reachable indirectly via an ordinary, unprivileged vote-withdraw transaction, rather than "just" losing a reward as in the original Trident finding — the Agave analog is strictly more severe (validator crash vs. failed reward claim).

### Likelihood Explanation
Likelihood depends on exact timing details I could not fully verify within available context: specifically, whether `clone_and_filter_for_vat`'s balance-sufficiency check and `maybe_burn_vat_from_staked_accounts`'s subtraction are always evaluated against the *same* point-in-time account state (in which case there is no window for a withdrawal to intervene), or whether `epoch_stakes` reflects an older snapshot (e.g., stakes computed at a prior epoch boundary with the usual Solana stake-weight lookback) while the burn reads the *current* live account via `self.get_account(vote_pubkey)`. The code and comments strongly suggest the filtering and the burn are not necessarily performed on the identical state snapshot (the filtering operates on `epoch_stakes` used for consensus/admission purposes, while the burn re-fetches live account data), which would leave a window for an intervening `Withdraw` instruction issued by the vote account's authorized withdrawer within the epoch. I was not able to trace `clone_and_filter_for_vat` and `slot_params.rs` fully in the remaining budget to conclusively confirm or rule out this timing gap.

### Recommendation
- Replace the `checked_sub(...).expect(...)` with a graceful fallback (e.g., `saturating_sub` clamped to available balance, or skip/reduce the VAT burn for that account) so that a legitimate, unrelated balance-reducing transaction cannot induce a validator panic.
- Re-validate sufficiency of balance for the VAT burn against the *same* live account state that is actually being debited, immediately before the subtraction, rather than relying solely on an earlier filtering pass against a potentially stale snapshot.
- Add an explicit invariant check/test that exercises a vote-account withdrawal occurring between VAT admission filtering and the epoch-boundary burn to confirm whether the panic is actually reachable, since this could not be fully confirmed with static reading alone.

### Proof of Concept
Conceptual reproduction (requires runtime verification, not confirmed by test execution here):
1. Enable Alpenglow (`feature_snapshot.alpenglow == true`) so `maybe_burn_vat_from_staked_accounts` is exercised.
2. Get a vote account admitted into the top `MAX_ALPENGLOW_VOTE_ACCOUNTS` by `clone_and_filter_for_vat`, satisfying the "sufficient balance for admission" check at that point in time.
3. Before the epoch boundary at which `maybe_burn_vat_from_staked_accounts` actually executes, submit an ordinary `Withdraw` vote-program transaction (`programs/vote/src/vote_state/mod.rs` `withdraw`, invocable e.g. via `cli/src/vote.rs::process_withdraw_from_vote_account`) that drains the vote account down to just above its own rent-exempt/`pending_delegator_rewards` requirement — a state the withdraw path permits since it has no knowledge of the pending VAT debit.
4. At the epoch boundary, `maybe_burn_vat_from_staked_accounts` calls `account.lamports().checked_sub(vat_to_burn_per_epoch).expect(...)` on the now-reduced balance, and if `vat_to_burn_per_epoch` exceeds the remaining lamports, the `expect()` panics. [4](#0-3) [5](#0-4)

### Citations

**File:** runtime/src/bank.rs (L2644-2652)
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
```

**File:** runtime/src/bank.rs (L2660-2676)
```rust
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
```

**File:** programs/vote/src/vote_state/mod.rs (L1062-1082)
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
```

**File:** programs/vote/src/vote_state/mod.rs (L1112-1124)
```rust
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
```
