### Title
Fee-collector rent-exemption health check permanently bypassed once collector account has any non-zero balance - ([File: runtime/src/bank/fee_distribution.rs])

### Summary
`Bank::collector_type_checked` is supposed to guarantee that any custom block-revenue collector (SIMD-0232) ends up rent-exempt after receiving its transaction-fee commission. The guard that enforces this is gated on `pre_lamports == 0`, so once the collector account has *any* non-zero balance, the rent-exemption requirement is silently skipped on every subsequent `processReport`-equivalent call (`deposit_fees`/`collector_type_checked`). This is the same bug class as the Tulpea `processReport()` finding: a security check that should always be enforced is instead conditioned on a "previous value" baseline, and the guard's boolean logic disables the check for the common (non-initial) case instead of the edge case.

### Finding Description
`collector_type_checked` enforces:

```rust
if !rent.is_exempt(account.lamports(), account.data().len())
    && (!relax_post_execution_balance_checks || pre_lamports == 0)
{
    Err(DepositFeeError::InvalidRentPayingAccount)
} else {
    Ok(ExternalCollectorType::SystemAccount)
}
``` [1](#0-0) 

When `relax_post_execution_balance_checks` is `true` (the SIMD-0392 relaxation feature), the error condition reduces to `!is_exempt && pre_lamports == 0`. This means:

- On the **first** deposit to a collector (`pre_lamports == 0`), the exemption requirement is enforced.
- On **every subsequent** deposit (`pre_lamports != 0`), the rent-exemption requirement is unconditionally skipped, *regardless of whether the account is currently rent-exempt or how depleted it has become*.

This differs from the generic path used for fee payers and non-custom collectors, `check_static_account_rent_state_transition`, which evaluates a full pre/post `RentState` transition via `transition_allowed`, correctly disallowing an account from staying `RentPaying` if it is credited: [2](#0-1) . `collector_type_checked` bypasses that stricter, general logic entirely for the custom-commission-collector path, using only a `pre_lamports == 0` baseline check that is functionally identical in structure to the Tulpea vault's `previousDebt > 0` gate.

`deposit_fees` is invoked from `deposit_or_burn_fee`, which is called once per slot from `distribute_transaction_fee_details` for every block a custom-commission-collector leader produces [3](#0-2) [4](#0-3) . The collector address is fully attacker-controlled: SIMD-0232 lets the leader designate any `block_revenue_collector` address in their vote state, and that address is only required to be a system-program-owned, non-reserved account [5](#0-4) .

### Impact Explanation
A leader running with a custom commission collector can:
1. Set `block_revenue_collector` in its vote state to a system-owned account it controls.
2. Fund that account to exactly reach rent-exemption on its very first commission deposit (satisfying the `pre_lamports == 0` branch).
3. Immediately drain nearly all the lamports out of that account via an ordinary system-program transfer, leaving a dust balance (`pre_lamports != 0` from then on).
4. From that point forward, every subsequent per-slot fee-commission deposit into the drained, non-rent-exempt account is accepted unconditionally — the `InvalidRentPayingAccount` health check never fires again — and the leader can repeat the drain after every block.

This permanently defeats the "rent-exempt after depositing inflation rewards commission" invariant that SIMD-0232 was designed to guarantee [6](#0-5) , letting a collector account exist indefinitely in a sub-rent-exempt (garbage-collectible) state while still continuously receiving fee revenue — unauthorized persistent state that violates the documented accounting invariant of the fee-distribution path.

### Likelihood Explanation
Exploitation only requires being a leader that has enabled the `custom_commission_collector` feature and controls a system-owned account of its choosing, plus the `relax_post_exec_min_balance_check`/SIMD-0392 feature being active. Both are ordinary, unprivileged leader-rotation mechanics available to any staked validator — no special node access, key leakage, or consensus attack is needed. The exploit is deterministic and repeatable every slot the attacker leads.

### Recommendation
Do not key the exemption check on `pre_lamports == 0`. Always evaluate the post-deposit `RentState` transition using the same generic pre/post logic used elsewhere (`get_pre_exec_account_rent_state` / `get_post_exec_account_rent_state` / `transition_allowed`), so that an already-funded, non-exempt collector cannot be perpetually excused from becoming rent-exempt:

```rust
let min_balance = rent.minimum_balance(account.data().len());
let pre_state = get_pre_exec_account_rent_state(pre_lamports, account.data().len(), min_balance, false);
let post_state = get_post_exec_account_rent_state(
    account.lamports(), account.data().len(), min_balance, &pre_state, pre_lamports,
    relax_post_execution_balance_checks,
);
if !transition_allowed(&pre_state, &post_state) {
    return Err(DepositFeeError::InvalidRentPayingAccount);
}
```

### Proof of Concept
1. Enable `custom_commission_collector` and `relax_post_exec_min_balance_check` features.
2. As leader `L`, set `vote_state.block_revenue_collector = C`, where `C` is a system-owned account `L` controls with `0` lamports.
3. Produce a block; `deposit_fees(C, fee)` runs with `pre_lamports == 0`; ensure `fee >= rent.minimum_balance(0)` so the deposit succeeds and `C` becomes rent-exempt.
4. Submit an ordinary `system_instruction::transfer` from `C` to drain it down to `1` lamport (non-zero, non-exempt).
5. Produce further blocks; each subsequent `deposit_fees(C, fee')` call now has `pre_lamports == 1 != 0`, so `collector_type_checked` returns `Ok(ExternalCollectorType::SystemAccount)` even though `!rent.is_exempt(...)` is true — no `InvalidRentPayingAccount` error, no burn, and `C` keeps receiving fee commission indefinitely while remaining a dust, non-rent-exempt account. [7](#0-6)

### Citations

**File:** runtime/src/bank/fee_distribution.rs (L69-77)
```rust
    pub(super) fn distribute_transaction_fee_details(&self) {
        let fee_details = self.collector_fee_details.read().unwrap();

        let FeeDistribution { deposit, burn } =
            self.calculate_reward_and_burn_fee_details(&fee_details);

        let total_burn = self.deposit_or_burn_fee(deposit).saturating_add(burn);
        self.capitalization.fetch_sub(total_burn, Relaxed);
    }
```

**File:** runtime/src/bank/fee_distribution.rs (L130-151)
```rust
        let feature_snapshot = self.feature_set.snapshot();
        let collector_id = if feature_snapshot.custom_commission_collector {
            let vote_account = self
                .epoch_stakes
                .get(&self.epoch)
                .and_then(|stakes| {
                    stakes
                        .stakes()
                        .vote_accounts()
                        .get(&self.leader.vote_address)
                })
                .expect("The vote account for the leader must exist");
            // Protection in case the leader is on a vote state without a
            // collector id, which can happen if a dormant pre-v4 vote state
            // accrues stake.
            vote_account
                .vote_state_view()
                .block_revenue_collector()
                .unwrap_or(&self.leader.id)
        } else {
            &self.leader.id
        };
```

**File:** runtime/src/bank/fee_distribution.rs (L183-269)
```rust
    fn deposit_fees(&self, collector_id: &Pubkey, fees: u64) -> Result<u64, DepositFeeError> {
        let mut account = self
            .get_account_with_fixed_root_no_cache(collector_id)
            .unwrap_or_default();

        let feature_snapshot = self.feature_set.snapshot();
        if feature_snapshot.custom_commission_collector {
            let pre_lamports = account.lamports();
            account
                .checked_add_lamports(fees)
                .map_err(|_| DepositFeeError::LamportOverflow)?;
            if collector_id != &self.leader.vote_address {
                Bank::collector_type_checked(
                    collector_id,
                    pre_lamports,
                    &account,
                    &self.reserved_account_keys,
                    &self.rent_collector().rent,
                    feature_snapshot.relax_post_exec_min_balance_check,
                )?;
            }
        } else {
            if !system_program::check_id(account.owner()) {
                return Err(DepositFeeError::InvalidAccountOwner);
            }

            let pre_balance = account.lamports();
            let distribution = account.checked_add_lamports(fees);
            if distribution.is_err() {
                return Err(DepositFeeError::LamportOverflow);
            }

            // rent state transition must be checked in case the account receiving the distribution
            // doesn't exist yet.
            if check_static_account_rent_state_transition(
                pre_balance,
                account.lamports(),
                account.data().len(),
                &self.rent_collector().rent,
                0, // account index isn't relevant and only used for error message
                feature_snapshot.relax_post_exec_min_balance_check,
            )
            .is_err()
            {
                return Err(DepositFeeError::InvalidRentPayingAccount);
            }
        }

        self.store_account(collector_id, &account);
        Ok(account.lamports())
    }

    /// Checks if a collector account adheres to the rules outlined in SIMD-0232:
    /// * system program owned account
    /// * rent-exempt after depositing inflation rewards commission
    /// * not a reserved account
    ///
    /// Returns the kind of collector
    pub(super) fn collector_type_checked(
        collector_id: &Pubkey,
        pre_lamports: u64,
        account: &AccountSharedData,
        reserved_account_keys: &ReservedAccountKeys,
        rent: &Rent,
        relax_post_execution_balance_checks: bool,
    ) -> Result<ExternalCollectorType, DepositFeeError> {
        if !system_program::check_id(account.owner()) {
            return Err(DepositFeeError::InvalidAccountOwner);
        }

        if reserved_account_keys.is_reserved(collector_id) {
            return Err(DepositFeeError::ReservedCollector);
        }

        // Don't perform rent check on the incinerator, so that the deposit
        // always works. The incinerator is run at the end of a block
        if *collector_id == incinerator::id() {
            Ok(ExternalCollectorType::Incinerator)
        } else {
            if !rent.is_exempt(account.lamports(), account.data().len())
                && (!relax_post_execution_balance_checks || pre_lamports == 0)
            {
                Err(DepositFeeError::InvalidRentPayingAccount)
            } else {
                Ok(ExternalCollectorType::SystemAccount)
            }
        }
```

**File:** svm/src/rent_calculator.rs (L188-206)
```rust
pub fn transition_allowed(pre_rent_state: &RentState, post_rent_state: &RentState) -> bool {
    match post_rent_state {
        RentState::Uninitialized | RentState::RentExempt => true,
        RentState::RentPaying {
            data_size: post_data_size,
            lamports: post_lamports,
        } => {
            match pre_rent_state {
                RentState::Uninitialized | RentState::RentExempt => false,
                RentState::RentPaying {
                    data_size: pre_data_size,
                    lamports: pre_lamports,
                } => {
                    // Cannot remain RentPaying if resized or credited.
                    post_data_size == pre_data_size && post_lamports <= pre_lamports
                }
            }
        }
    }
```
