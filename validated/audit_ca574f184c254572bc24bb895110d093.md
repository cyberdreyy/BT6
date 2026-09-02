### Title
Lockup owner's withdrawable balance is set from an unvalidated staking-pool-reported total balance - (File: `lockup/src/owner_callbacks.rs`)

### Summary
The Chainlink oracle bug is a class of "trust external numeric input without bounds/sanity validation." The closest reachable analog in this repository is `LockupContract::on_get_account_total_balance` in `lockup/src/owner_callbacks.rs`, which accepts a `total_balance` value reported by the external staking-pool contract and stores it verbatim as `deposit_amount`, with no range or monotonicity check, even though this value directly drives how much NEAR the owner is allowed to withdraw from the lockup account via `transfer`.

### Finding Description
`refresh_staking_pool_balance` (`lockup/src/owner.rs`) calls `ext_staking_pool::get_account_total_balance` on the externally selected (whitelisted) staking pool and pipes the result into the callback: [1](#0-0) 

The callback blindly overwrites `deposit_amount` with whatever value the staking pool returned, without validating it against the previously known deposit amount, the amount actually deposited/staked historically, or any plausible upper bound: [2](#0-1) 

This is structurally identical to the D3Oracle bug: an external, only-trusted-by-whitelist data source (`priceFeed`/staking pool) returns a number that is accepted as ground truth ("price > 0" / "callback succeeded") without checking it is within a sane range (`minAnswer`/`maxAnswer` equivalent: it should never regress below, or jump wildly above, the last known deposited+staked amount plus plausible reward accrual). `get_known_deposited_balance` (`lockup/src/getters.rs`, lines 20-30) explicitly documents that this value is used to compute the owner's withdrawable ("liquid") balance, and the accompanying test (`lockup/src/lib.rs`, lines 738-816) shows `get_owners_balance`/`get_liquid_owners_balance`/`transfer` are all derived directly from this trusted number.

The account binding broken is: "an account trusted as a pool ... versus the code and arguments that trust was granted for." The lockup contract trusts a whitelisted staking pool account to only ever report a `total_balance` that reflects tokens genuinely deposited/staked plus legitimately accrued rewards. Nothing in `on_get_account_total_balance` enforces that equality — the code accepts any `u128` returned from the promise result as if it satisfied that trust.

### Impact Explanation
If a whitelisted staking pool (buggy, compromised, or with a since-changed/malicious implementation not re-checked by the whitelist) returns an inflated `total_balance`, the lockup contract's `deposit_amount` becomes an overstated claim relative to NEAR the lockup contract can actually recover. Because `deposit_amount` feeds `get_owners_balance`/`get_liquid_owners_balance`, which in turn gate `transfer`, the owner could subsequently withdraw NEAR out of the lockup account balance that is not backed by real recoverable stake — i.e., claims recorded (`deposit_amount`) diverge from NEAR actually held/recoverable by the pool, and NEAR could move out of the lockup account (via `transfer`) in excess of what is actually backed, leaving the lockup contract insolvent for the remaining vesting/lockup obligations. This matches the "Critical: claims exceeding assets held" / "funds permanently frozen for other parties" category.

### Likelihood Explanation
This requires a whitelisted staking-pool account to misreport its balance (via bug or later malicious behavior), which is a narrower trigger than a fully unprivileged attacker, since pool selection is gated by `is_whitelisted`. However, once a pool is selected, no code in the lockup contract independently verifies the reported balance is plausible on every refresh — the trust boundary is enforced only at selection time, not at every use, so any deviation of the selected pool from its expected behavior (bug, upgrade, compromise) is silently and permanently accepted into `deposit_amount` with no sanity check at the point where trust matters most.

### Recommendation
In `on_get_account_total_balance`, validate the reported `total_balance` against reasonable bounds before accepting it — e.g., assert it is not less than the current `deposit_amount` (rewards should never make the balance decrease) and not implausibly larger than `deposit_amount` plus a bounded reward-rate ceiling for the elapsed time, mirroring the "check the latest answer against reasonable limits" recommendation from the oracle report. Reject or flag values outside these bounds instead of unconditionally overwriting `deposit_amount`.

### Proof of Concept
1. Owner selects a whitelisted staking pool and deposits/stakes `X` NEAR via `deposit_to_staking_pool` / `stake`, so `deposit_amount == X` (`lockup/src/owner_callbacks.rs` lines 27-62, `lockup/src/lib.rs` lines 738-800 test flow).
2. The selected staking pool (whitelisted, but buggy/compromised or later modified) reports `get_account_total_balance` far larger than `X` plus any plausible reward, e.g., `X * 10`.
3. Owner calls `refresh_staking_pool_balance`; `on_get_account_total_balance` sets `deposit_amount = X * 10` with no validation (`lockup/src/owner_callbacks.rs` lines 281-294).
4. `get_owners_balance`/`get_liquid_owners_balance` now compute an inflated withdrawable amount, and the owner calls `transfer` to move NEAR out of the lockup account (`lockup/src/lib.rs` transfer flow demonstrated at lines 803-815), extracting NEAR not actually backed by real staked/recoverable funds, leaving the lockup contract insolvent relative to its remaining lockup/vesting obligations.

### Citations

**File:** lockup/src/owner.rs (L176-208)
```rust
    pub fn refresh_staking_pool_balance(&mut self) -> Promise {
        self.assert_owner();
        self.assert_staking_pool_is_idle();
        self.assert_no_termination();

        env::log(
            format!(
                "Fetching total balance from the staking pool @{}",
                self.staking_information
                    .as_ref()
                    .unwrap()
                    .staking_pool_account_id
            )
            .as_bytes(),
        );

        self.set_staking_pool_status(TransactionStatus::Busy);

        ext_staking_pool::get_account_total_balance(
            env::current_account_id(),
            &self
                .staking_information
                .as_ref()
                .unwrap()
                .staking_pool_account_id,
            NO_DEPOSIT,
            gas::staking_pool::GET_ACCOUNT_TOTAL_BALANCE,
        )
        .then(ext_self_owner::on_get_account_total_balance(
            &env::current_account_id(),
            NO_DEPOSIT,
            gas::owner_callbacks::ON_GET_ACCOUNT_TOTAL_BALANCE,
        ))
```

**File:** lockup/src/owner_callbacks.rs (L280-294)
```rust
    /// Called after the request to get the current total balance from the staking pool.
    pub fn on_get_account_total_balance(&mut self, #[callback] total_balance: WrappedBalance) {
        assert_self();
        self.set_staking_pool_status(TransactionStatus::Idle);

        env::log(
            format!(
                "The current total balance on the staking pool is {}",
                total_balance.0
            )
            .as_bytes(),
        );

        self.staking_information.as_mut().unwrap().deposit_amount = total_balance;
    }
```
