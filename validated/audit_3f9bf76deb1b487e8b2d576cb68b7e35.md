### Title
Owner-selected malicious staking pool can inflate `deposit_amount` via `refresh_staking_pool_balance`, unlocking real locked/unvested NEAR - ([File: lockup/src/owner.rs, lockup/src/owner_callbacks.rs])

### Summary
The lockup contract's `refresh_staking_pool_balance` / `on_get_account_total_balance` callback blindly trusts whatever number the selected staking pool reports and overwrites `staking_information.deposit_amount` with it, with no upper bound and no relation to real NEAR actually deposited. Because staking pools are permissionlessly whitelisted through `staking-pool-factory` (any account can deploy and auto-whitelist a pool it fully controls), a lockup owner can select a self-deployed malicious "pool," report an arbitrarily large total balance, and use the resulting inflated `deposit_amount` to make `get_liquid_owners_balance()` collapse to the entire real NEAR balance held by the lockup account, bypassing `get_locked_amount()`'s vesting/lockup subtraction.

### Finding Description
The broken binding: `deposit_amount` (as reported to the staking pool) should equal `min(real amount the pool can actually pay back, amount actually transferred out via deposit_to_staking_pool/deposit_and_stake)`. Instead, `on_get_account_total_balance` performs an unconditional overwrite: [1](#0-0) 

`refresh_staking_pool_balance` is gated only by `assert_owner`, `assert_staking_pool_is_idle`, `assert_no_termination` - it never validates the reported number against anything (e.g. previous `deposit_amount`, real deposits made, or independent proof): [2](#0-1) 

The `staking_pool_account_id` selected in `select_staking_pool` only needs to be whitelisted: [3](#0-2) 

but whitelisting is permissionless: `staking-pool-factory` lets any unprivileged account deploy and get a pool it fully controls auto-whitelisted, with no involvement from NEAR Foundation: [4](#0-3) [5](#0-4) 

Once `deposit_amount` is inflated, it flows into: [6](#0-5) 

`get_owners_balance = account_balance + known_deposited_balance - locked_amount`, and `get_liquid_owners_balance = min(owners_balance, account_balance)`. Since `known_deposited_balance` (i.e., `deposit_amount`) can be set to an arbitrarily large fake number by the attacker's own malicious pool, `owners_balance` becomes unboundedly large, which forces `get_liquid_owners_balance` to collapse to the full real `account_balance` of the lockup - completely nullifying the `get_locked_amount()` subtraction that is supposed to protect locked/unvested tokens. The owner can then call `transfer()`, which is only bounded by `get_liquid_owners_balance()`: [7](#0-6) 

`transfer()` requires `assert_transfers_enabled` and `assert_no_termination`, but neither of these checks the legitimacy of the reported staking-pool balance, so they don't stop this attack once transfers are globally enabled and no termination has yet occurred.

Exploit flow: the attacker (as the owner of their own lockup with an unvested/locked balance, e.g. an employee-beneficiary lockup created for them by the foundation) deploys a "staking pool" via `staking-pool-factory::create_staking_pool` (auto-whitelisted), calls `select_staking_pool` to point at it, then calls `refresh_staking_pool_balance`. Their own pool's `get_account_total_balance` view returns an arbitrary huge number, which `on_get_account_total_balance` writes into `deposit_amount` unconditionally. `get_liquid_owners_balance()` now equals the full real account balance, and the owner calls `transfer()` to move out the entire locked/unvested amount early, before the vesting schedule matures and before the foundation can call `terminate_vesting`.

### Impact Explanation
This lets a lockup's owner move NEAR that is contractually still locked/unvested (and, in vesting scenarios, still economically owned by/recoverable by the NEAR Foundation until vesting completes) out to themselves early, before entitlement. This matches the Critical category: "locked or unvested tokens released early." The attack is repeatable per-lockup and is fully self-contained (does not require compromising anyone else's keys); each lockup account the attacker owns and controls can be drained this way once transfers are enabled and vesting/termination has not yet occurred.

### Likelihood Explanation
Preconditions: the attacker must be the owner of a lockup with unvested/locked funds and be able to select a staking pool (no `assert_staking_pool_is_not_selected` failure) and network-wide transfers must be enabled (a state reachable without foundation cooperation, as it is decided by public vote and once enabled cannot be disabled again). Deploying a malicious pool via `staking-pool-factory` requires only the standard `MIN_ATTACHED_BALANCE` and no special permission. `assert_no_termination` is satisfiable as long as the foundation has not yet exercised `terminate_vesting` - i.e., the attacker just needs to act before being caught/terminated. This is a low-cost, fully unprivileged, deterministic exploit path.

### Recommendation
Do not let `on_get_account_total_balance` unconditionally overwrite `deposit_amount` with an externally-reported number with no bound. At minimum: (1) cap growth of `deposit_amount` reported via `refresh_staking_pool_balance` to a sane bound relative to prior deposits/withdrawals (e.g., disallow decreases beyond withdrawals and bound increases to a plausible staking-reward rate), and/or (2) decouple `get_owners_balance`/`get_liquid_owners_balance` from unverified `deposit_amount` growth so a report from the pool can never make the owner's transferable balance exceed the *actual* locked-amount-adjusted real balance still held on the lockup account. Fundamentally, `get_locked_amount()` should be enforced as a hard floor independent of any staking-pool-reported inflation, not something that can be bypassed once `owners_balance` exceeds `account_balance`.

### Proof of Concept
`cargo test` plan (unit test style, similar to existing `test_staking_pool_owner_balance`/`test_staking_pool_refresh_balance` in `lockup/src/lib.rs`):
1. Set up a lockup contract with `lockup_amount` fully locked/unvested (e.g., vesting schedule not yet at cliff, or before `lockup_timestamp`), with `account_owner()` as owner. Assert `get_owners_balance().0 == 0` and `get_liquid_owners_balance().0 == 0`.
2. Call `select_staking_pool("malicious_pool")`, then simulate `on_whitelist_is_whitelisted(true, "malicious_pool")` (representing that the attacker's factory-deployed pool got auto-whitelisted).
3. Skip any real `deposit_to_staking_pool` call (i.e., `deposit_amount` starts at `0`).
4. Call `refresh_staking_pool_balance()`, then simulate the callback `on_get_account_total_balance(huge_fake_amount.into())` with `huge_fake_amount` far larger than `lockup_amount` (representing the malicious pool's fabricated report).
5. Assert `get_known_deposited_balance().0 == huge_fake_amount`.
6. Assert `get_liquid_owners_balance().0 == env::account_balance()` (i.e., the full real lockup balance, including the amount that `get_locked_amount()` says should still be locked) - proving the locked-amount floor was bypassed.
7. Call `transfer(env::account_balance(), attacker_account())` (after enabling transfers in the test context) and assert it succeeds, moving out NEAR that `get_locked_amount()` still reports as locked/unvested - demonstrating the binding `deposit_amount ≤ what the pool can actually pay back` is violated and locked funds are released early.

### Citations

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

**File:** lockup/src/owner.rs (L12-41)
```rust
    pub fn select_staking_pool(&mut self, staking_pool_account_id: AccountId) -> Promise {
        self.assert_owner();
        assert!(
            env::is_valid_account_id(staking_pool_account_id.as_bytes()),
            "The staking pool account ID is invalid"
        );
        self.assert_staking_pool_is_not_selected();
        self.assert_no_termination();

        env::log(
            format!(
                "Selecting staking pool @{}. Going to check whitelist first.",
                staking_pool_account_id
            )
            .as_bytes(),
        );

        ext_whitelist::is_whitelisted(
            staking_pool_account_id.clone(),
            &self.staking_pool_whitelist_account_id,
            NO_DEPOSIT,
            gas::whitelist::IS_WHITELISTED,
        )
        .then(ext_self_owner::on_whitelist_is_whitelisted(
            staking_pool_account_id,
            &env::current_account_id(),
            NO_DEPOSIT,
            gas::owner_callbacks::ON_WHITELIST_IS_WHITELISTED,
        ))
    }
```

**File:** lockup/src/owner.rs (L176-209)
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
    }
```

**File:** lockup/src/owner.rs (L467-487)
```rust
    pub fn transfer(&mut self, amount: WrappedBalance, receiver_id: AccountId) -> Promise {
        self.assert_owner();
        assert!(amount.0 > 0, "Amount should be positive");
        assert!(
            env::is_valid_account_id(receiver_id.as_bytes()),
            "The receiver account ID is invalid"
        );
        self.assert_transfers_enabled();
        self.assert_no_staking_or_idle();
        self.assert_no_termination();
        assert!(
            self.get_liquid_owners_balance().0 >= amount.0,
            "The available liquid balance {} is smaller than the requested transfer amount {}",
            self.get_liquid_owners_balance().0,
            amount.0,
        );

        env::log(format!("Transferring {} to account @{}", amount.0, receiver_id).as_bytes());

        Promise::new(receiver_id).transfer(amount.0)
    }
```

**File:** staking-pool-factory/README.md (L1-17)
```markdown
# Staking Pool Factory Contract

This contract deploys and automatically whitelists new staking pool contracts.
It allows any user to create a new whitelisted staking pool.

The staking pool factory contract packages the binary of the staking pool contract within its own binary.
To create a new staking pool a user should issue a function call transaction and attach the required minimum deposit.
The entire deposit will be transferred to the newly created staking pool contract in order to cover the required storage.

When a user issues a function call towards the factory to create a new staking pool the factory internally checks that
the staking pool account ID does not exists, validates arguments for the staking pool initialization and then issues a
receipt that creates the staking pool. Once the receipt executes, the factory checks the status of the execution in the
callback. If the staking pool was created successfully, the factory then whitelists the newly created staking pool.
Otherwise, the factory returns the attached deposit back the users and returns `false`.

## Changelog

```

**File:** staking-pool-factory/src/lib.rs (L197-239)
```rust
    /// Callback after a staking pool was created.
    /// Returns the promise to whitelist the staking pool contract if the pool creation succeeded.
    /// Otherwise refunds the attached deposit and returns `false`.
    pub fn on_staking_pool_create(
        &mut self,
        staking_pool_account_id: AccountId,
        attached_deposit: U128,
        predecessor_account_id: AccountId,
    ) -> PromiseOrValue<bool> {
        assert_self();

        let staking_pool_created = is_promise_success();

        if staking_pool_created {
            env::log(
                format!(
                    "The staking pool @{} was successfully created. Whitelisting...",
                    staking_pool_account_id
                )
                .as_bytes(),
            );
            ext_whitelist::add_staking_pool(
                staking_pool_account_id,
                &self.staking_pool_whitelist_account_id,
                NO_DEPOSIT,
                gas::WHITELIST_STAKING_POOL,
            )
            .into()
        } else {
            self.staking_pool_account_ids
                .remove(&staking_pool_account_id);
            env::log(
                format!(
                    "The staking pool @{} creation has failed. Returning attached deposit of {} to @{}",
                    staking_pool_account_id,
                    attached_deposit.0,
                    predecessor_account_id
                ).as_bytes()
            );
            Promise::new(predecessor_account_id).transfer(attached_deposit.0);
            PromiseOrValue::Value(false)
        }
    }
```

**File:** lockup/src/getters.rs (L159-178)
```rust
    /// Returns the balance of the account owner. It includes vested and extra tokens that
    /// may have been deposited to this account, but excludes locked tokens.
    /// NOTE: Some of this tokens may be deposited to the staking pool.
    /// This method also doesn't account for tokens locked for the contract storage.
    pub fn get_owners_balance(&self) -> WrappedBalance {
        (env::account_balance() + self.get_known_deposited_balance().0)
            .saturating_sub(self.get_locked_amount().0)
            .into()
    }

    /// Returns total balance of the account including tokens deposited to the staking pool.
    pub fn get_balance(&self) -> WrappedBalance {
        (env::account_balance() + self.get_known_deposited_balance().0).into()
    }

    /// Returns the amount of tokens the owner can transfer from the account.
    /// Transfers have to be enabled.
    pub fn get_liquid_owners_balance(&self) -> WrappedBalance {
        std::cmp::min(self.get_owners_balance().0, self.get_account_balance().0).into()
    }
```
