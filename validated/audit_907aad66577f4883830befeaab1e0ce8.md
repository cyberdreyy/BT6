### No vulnerability found for this question.

**Rationale:** The code path in question (`on_staking_pool_withdraw_for_termination`, called via `termination_prepare_to_withdraw` → `terminate_vesting`) is only reachable through `assert_called_by_foundation()` [1](#0-0) [2](#0-1) [3](#0-2) . The attack scenario requires the foundation to initiate withdrawal-for-termination against a staking pool that returns an inflated balance — but the threat model explicitly excludes the NEAR Foundation as an attacker or accomplice, and requires the attacker to remain unprivileged.

Additionally, for the "attacker's fake pool" premise to hold, the victim's lockup must have selected that pool via `select_staking_pool`, which is gated by the whitelist contract's `is_whitelisted` check — an unprivileged attacker cannot get an arbitrary pool whitelisted without foundation/whitelist-owner cooperation.

Finally, the `saturating_sub` on `deposit_amount` is explicitly flagged in the code as expected behavior ("Due to staking rewards the deposit amount can become negative") [4](#0-3) , and the same pattern exists in the non-termination path `on_staking_pool_withdraw` [5](#0-4) . `deposit_amount` is only a bookkeeping estimate refreshed via `refresh_staking_pool_balance`/`on_get_account_total_balance` [6](#0-5)  and does not itself authorize any NEAR transfer out of the lockup account — actual transfers are bounded by the lockup's own account balance and owner/foundation-privileged calls, not by this counter. Since the exploit requires privileged actors excluded from the attacker model and does not demonstrate NEAR leaving the contract to an unentitled unprivileged party, it is out of scope.

### Citations

**File:** lockup/src/foundation.rs (L15-20)
```rust
    pub fn terminate_vesting(
        &mut self,
        vesting_schedule_with_salt: Option<VestingScheduleWithSalt>,
    ) {
        self.assert_called_by_foundation();
        let vesting_schedule = self.assert_vesting(vesting_schedule_with_salt);
```

**File:** lockup/src/foundation.rs (L58-60)
```rust
    pub fn termination_prepare_to_withdraw(&mut self) -> Promise {
        self.assert_called_by_foundation();
        self.assert_staking_pool_is_idle();
```

**File:** lockup/src/internal.rs (L110-120)
```rust
    pub fn assert_called_by_foundation(&self) {
        if let Some(foundation_account_id) = &self.foundation_account_id {
            assert_eq!(
                &env::predecessor_account_id(),
                foundation_account_id,
                "Can only be called by NEAR Foundation"
            )
        } else {
            env::panic(b"No NEAR Foundation account is specified in the contract");
        }
    }
```

**File:** lockup/src/foundation_callbacks.rs (L151-158)
```rust
            {
                let staking_information = self.staking_information.as_mut().unwrap();
                // Due to staking rewards the deposit amount can become negative.
                staking_information.deposit_amount.0 = staking_information
                    .deposit_amount
                    .0
                    .saturating_sub(amount.0);
            }
```

**File:** lockup/src/owner_callbacks.rs (L111-119)
```rust
        if withdraw_succeeded {
            {
                let staking_information = self.staking_information.as_mut().unwrap();
                // Due to staking rewards the deposit amount can become negative.
                staking_information.deposit_amount.0 = staking_information
                    .deposit_amount
                    .0
                    .saturating_sub(amount.0);
            }
```

**File:** lockup/src/owner_callbacks.rs (L281-294)
```rust
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
