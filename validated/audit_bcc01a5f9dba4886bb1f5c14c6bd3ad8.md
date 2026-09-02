### Title
Owner-selectable staking-pool whitelist lets the lockup owner move locked/unvested NEAR out of the lockup account before vesting - (File: lockup-factory/src/lib.rs, lockup/src/owner.rs)

### Summary
`LockupFactory::create` lets the caller supply an arbitrary `whitelist_account_id` for the deployed lockup contract instead of forcing the canonical NEAR Foundation staking-pool whitelist. Combined with `deposit_to_staking_pool`'s balance check, which is gated on the *raw* account balance rather than on the vested/owner-entitled balance, the lockup owner can register their own "whitelist" that approves a pool contract they control and physically transfer the entire lockup balance — including still-locked/unvested tokens — out of the lockup account before the vesting schedule allows it.

### Finding Description
`create()` accepts an optional `whitelist_account_id` parameter and, if supplied, uses it verbatim as the lockup's `staking_pool_whitelist_account_id` instead of the factory's canonical foundation whitelist: [1](#0-0) 

That value is passed straight into the deployed lockup contract's `new` constructor as `staking_pool_whitelist_account_id`, which becomes the trust anchor `select_staking_pool` relies on: [2](#0-1) 

Inside the lockup contract, `select_staking_pool` only checks the pool against whatever whitelist contract was configured at construction — it never validates that this whitelist is the one operated by the NEAR Foundation: [3](#0-2) 

`deposit_to_staking_pool` then permits depositing (i.e. sending real NEAR out of the lockup account to the selected pool) up to `get_account_balance()`, which is simply the account's raw NEAR balance minus the storage reserve — not the owner's vested/liquid entitlement: [4](#0-3) [5](#0-4) 

The accounting invariant the contract relies on is `get_owners_balance = account_balance + known_deposited_balance - locked_amount`, and the transferable amount is capped by `min(owners_balance, account_balance)`: [6](#0-5) 

This invariant only holds if the "staking pool" is a trustworthy contract that will faithfully return principal on `withdraw_from_staking_pool`. Because the owner can pick their own whitelist contract (a feature explicitly supported and tested via `whitelist_account_id: Option<ValidAccountId>` and `test_create_lockup_with_custom_whitelist_success`), they can whitelist a pool contract they themselves control, then call `deposit_to_staking_pool` to move real NEAR — up to the full `get_account_balance()`, which includes locked/unvested tokens, not just the owner's vested share — out of the lockup account into that self-controlled pool. The tokens never need to come back; the lockup contract has no way to force `withdraw`.

### Impact Explanation
This lets the lockup owner remove locked/unvested tokens from the custody of the lockup contract early, into an account of their own choosing that isn't subject to the lockup/vesting schedule at all. This matches "Critical — locked or unvested tokens released early" from the accepted-impact list: the custody binding `get_account_balance() at t == funds actually recoverable under the vesting/lockup schedule` is broken as soon as an owner-controlled pool is whitelisted, because `deposit_to_staking_pool` moves real NEAR without regard to `get_locked_amount()`.

### Likelihood Explanation
Requires only actions available to the lockup owner themselves (no foundation, no multisig, no compromised key): (1) request lockup creation with a custom `whitelist_account_id` (a supported factory parameter), (2) deploy a trivial pool contract they control and whitelist it via that custom whitelist, (3) call `select_staking_pool` then `deposit_to_staking_pool` for the full account balance. All calls are ones the owner is already authorized to make on their own lockup contract.

### Recommendation
The lockup-factory should not allow the party requesting a lockup (particularly one with a `vesting_schedule`) to choose an arbitrary `staking_pool_whitelist_account_id`; it should always pin the canonical, foundation-operated whitelist for any lockup that includes vesting/lockup terms, or the custom whitelist option should only be permitted for lockups with no vesting/lock-up restrictions. Additionally, `deposit_to_staking_pool` should cap the depositable amount by the owner's currently unlocked/vested balance rather than the raw `get_account_balance()`, so that principal moved off-contract can never exceed what the owner is already entitled to withdraw.

### Proof of Concept
1. Foundation/anyone calls `lockup-factory::create(owner_account_id = attacker, vesting_schedule = Some(...), whitelist_account_id = Some(attacker_whitelist))` — a supported call path, as shown by `test_create_lockup_with_custom_whitelist_success` in `lockup-factory/src/lib.rs`.
2. Attacker deploys `attacker_whitelist` (their own account, not the NEAR Foundation's) and a fake pool contract `attacker_pool` that implements the staking-pool ABI (`deposit`, `get_account_total_balance`, etc.) but simply keeps or re-transfers received NEAR to another attacker-controlled account.
3. Attacker calls `attacker_whitelist.add_staking_pool("attacker_pool")` (whitelist's `assert_called_by_foundation` only checks the whitelist's own configured foundation account, which is the attacker here).
4. As lockup owner, attacker calls `select_staking_pool("attacker_pool")` → `on_whitelist_is_whitelisted` succeeds because it only trusts `staking_pool_whitelist_account_id` configured at construction, per `lockup/src/owner_callbacks.rs`.
5. Attacker calls `deposit_to_staking_pool(get_account_balance())`, moving the entire lockup account balance — including tokens still unvested per the vesting schedule — to `attacker_pool`, satisfying only the check `self.get_account_balance().0 >= amount.0` (`lockup/src/owner.rs` lines 81-120), with no reference to `get_locked_amount()`.
6. Funds now sit in `attacker_pool`, fully outside the lockup contract's control, before the vesting/lockup schedule would have released them.

### Citations

**File:** lockup-factory/src/lib.rs (L128-133)
```rust
        // Defaults to the whitelist account ID given on init call.
        let staking_pool_whitelist_account_id = if let Some(account_id) = whitelist_account_id {
            account_id.into()
        } else {
            self.whitelist_account_id.clone()
        };
```

**File:** lockup-factory/src/lib.rs (L140-157)
```rust
            .function_call(
                b"new".to_vec(),
                near_sdk::serde_json::to_vec(&LockupArgs {
                    owner_account_id,
                    lockup_duration,
                    lockup_timestamp,
                    transfers_information: TransfersInformation::TransfersEnabled {
                        transfers_timestamp: transfers_enabled,
                    },
                    vesting_schedule,
                    release_duration,
                    staking_pool_whitelist_account_id,
                    foundation_account_id: foundation_account,
                })
                    .unwrap(),
                NO_DEPOSIT,
                gas::LOCKUP_NEW,
            )
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

**File:** lockup/src/owner.rs (L81-120)
```rust
    pub fn deposit_to_staking_pool(&mut self, amount: WrappedBalance) -> Promise {
        self.assert_owner();
        assert!(amount.0 > 0, "Amount should be positive");
        self.assert_staking_pool_is_idle();
        self.assert_no_termination();
        assert!(
            self.get_account_balance().0 >= amount.0,
            "The balance that can be deposited to the staking pool is lower than the extra amount"
        );

        env::log(
            format!(
                "Depositing {} to the staking pool @{}",
                amount.0,
                self.staking_information
                    .as_ref()
                    .unwrap()
                    .staking_pool_account_id
            )
            .as_bytes(),
        );

        self.set_staking_pool_status(TransactionStatus::Busy);

        ext_staking_pool::deposit(
            &self
                .staking_information
                .as_ref()
                .unwrap()
                .staking_pool_account_id,
            amount.0,
            gas::staking_pool::DEPOSIT,
        )
        .then(ext_self_owner::on_staking_pool_deposit(
            amount,
            &env::current_account_id(),
            NO_DEPOSIT,
            gas::owner_callbacks::ON_STAKING_POOL_DEPOSIT,
        ))
    }
```

**File:** lockup/src/internal.rs (L10-14)
```rust
    pub fn get_account_balance(&self) -> WrappedBalance {
        env::account_balance()
            .saturating_sub(MIN_BALANCE_FOR_STORAGE)
            .into()
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
