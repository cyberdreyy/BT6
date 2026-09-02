### Title
Lockup contract does not re-verify staking pool whitelist status after initial selection, allowing continued delegation to a delisted staking pool - (File: `lockup/src/owner.rs`)

### Summary
The lockup contract only validates a staking pool against the whitelist contract at the moment `select_staking_pool` is called. Once a pool is selected, none of the subsequent owner methods that move funds to or interact with that pool (`deposit_to_staking_pool`, `deposit_and_stake`, `stake`, `unstake`, `withdraw_from_staking_pool`, `refresh_staking_pool_balance`) re-check `is_whitelisted` against the whitelist contract before proceeding.

### Finding Description
`select_staking_pool` performs the whitelist check via a cross-contract call to `ext_whitelist::is_whitelisted` before storing the pool in `staking_information`: [1](#0-0) 

After that, `deposit_and_stake`, `deposit_to_staking_pool`, and `stake` only assert `assert_owner`, `assert_staking_pool_is_idle`, `assert_no_termination`, and balance sufficiency — they never call `ext_whitelist::is_whitelisted` again before sending additional funds to `staking_information.staking_pool_account_id`: [2](#0-1) [3](#0-2) [4](#0-3) 

The README explicitly states the guarantee that only whitelisted staking pools should be able to receive delegated tokens from lockup contracts, precisely to protect the owner ("the lockup contract should be able to recover delegated tokens back to the lockup from a staking pool"): [5](#0-4) 

The custody binding being asserted by the whitelist is: *funds delegated by a lockup == funds delegated only to a pool currently on the foundation's whitelist*. Before the foundation calls `remove_staking_pool` on a malicious/broken pool, this equality holds. After removal, the equality is broken because `staking_information` still references the delisted pool and the lockup owner (who has an unprivileged position relative to the whitelist decision — the owner does not control the whitelist and cannot re-validate it) can continue calling `deposit_and_stake`/`stake`/`deposit_to_staking_pool` to push more locked/unvested NEAR into that now-untrusted pool.

### Impact Explanation
If the NEAR Foundation removes a staking pool from the whitelist (e.g., because it was found to be malicious, buggy, or otherwise unable to guarantee return of delegated funds — the exact reason the whitelist/removal mechanism exists), any lockup contract that had already selected that pool is not blocked from sending it more funds. This directly undermines the guarantee documented in the lockup and whitelist READMEs that "delegated tokens can not be lost or locked" because only whitelisted pools can receive them. This is a High-impact class of issue under the given framework: it can result in additional locked/unvested NEAR being frozen in (or lost to) a delisted pool — i.e., funds frozen or a custody guarantee broken for owners who continue to interact with the once-approved-now-removed pool.

### Likelihood Explanation
The only actor required is the lockup owner (unprivileged w.r.t. the whitelist), performing a normal, already-permitted action (`deposit_and_stake`/`stake`/`deposit_to_staking_pool`) on a pool they previously selected. No special conditions beyond "the foundation later removes the pool from the whitelist while the owner still has it selected" are required, which is exactly the scenario the whitelist removal mechanism (`remove_staking_pool`) is designed to prevent going forward.

### Recommendation
Before executing `deposit_to_staking_pool`, `deposit_and_stake`, and `stake` (any owner method that sends additional NEAR to the currently selected staking pool), re-verify `ext_whitelist::is_whitelisted(staking_pool_account_id)` and abort (or require `unselect_staking_pool`/migration flow) if the pool is no longer whitelisted, mirroring the check already performed in `select_staking_pool`.

### Proof of Concept
1. Owner calls `select_staking_pool("pool.factory")`; whitelist check passes and `staking_information` is set. See [1](#0-0) .
2. Owner calls `deposit_and_stake` and `stake`, delegating funds to `pool.factory`.
3. Foundation later determines `pool.factory` is compromised/malicious and calls `remove_staking_pool` on the whitelist contract: [6](#0-5) .
4. Owner calls `deposit_and_stake(amount)` again — this method performs no whitelist re-check and successfully sends additional locked/unvested NEAR to the now-delisted pool: [2](#0-1) .

### Citations

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

**File:** lockup/src/owner.rs (L127-166)
```rust
    pub fn deposit_and_stake(&mut self, amount: WrappedBalance) -> Promise {
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
                "Depositing and staking {} to the staking pool @{}",
                amount.0,
                self.staking_information
                    .as_ref()
                    .unwrap()
                    .staking_pool_account_id
            )
            .as_bytes(),
        );

        self.set_staking_pool_status(TransactionStatus::Busy);

        ext_staking_pool::deposit_and_stake(
            &self
                .staking_information
                .as_ref()
                .unwrap()
                .staking_pool_account_id,
            amount.0,
            gas::staking_pool::DEPOSIT_AND_STAKE,
        )
        .then(ext_self_owner::on_staking_pool_deposit_and_stake(
            amount,
            &env::current_account_id(),
            NO_DEPOSIT,
            gas::owner_callbacks::ON_STAKING_POOL_DEPOSIT_AND_STAKE,
        ))
    }
```

**File:** lockup/src/owner.rs (L301-337)
```rust
    pub fn stake(&mut self, amount: WrappedBalance) -> Promise {
        self.assert_owner();
        assert!(amount.0 > 0, "Amount should be positive");
        self.assert_staking_pool_is_idle();
        self.assert_no_termination();

        env::log(
            format!(
                "Staking {} at the staking pool @{}",
                amount.0,
                self.staking_information
                    .as_ref()
                    .unwrap()
                    .staking_pool_account_id
            )
            .as_bytes(),
        );

        self.set_staking_pool_status(TransactionStatus::Busy);

        ext_staking_pool::stake(
            amount,
            &self
                .staking_information
                .as_ref()
                .unwrap()
                .staking_pool_account_id,
            NO_DEPOSIT,
            gas::staking_pool::STAKE,
        )
        .then(ext_self_owner::on_staking_pool_stake(
            amount,
            &env::current_account_id(),
            NO_DEPOSIT,
            gas::owner_callbacks::ON_STAKING_POOL_STAKE,
        ))
    }
```

**File:** whitelist/README.md (L6-9)
```markdown
In order for the lockup contracts to be able delegate to a staking pool, the staking pool should faithfully implement the spec.
The staking pool should guarantee that the delegated tokens can not be lost or locked, such as the lockup contract should be
able to recover delegated tokens back to the lockup from a staking pool. In order to enforce this, only approved (whitelisted)
accounts of staking pool contracts can receive delegated tokens from lockup contracts.
```

**File:** whitelist/src/lib.rs (L94-104)
```rust
    /// Removes the given staking pool account ID from the whitelist.
    /// Returns `true` if the staking pool was present in the whitelist before, `false` otherwise.
    /// This method can only be called by the NEAR foundation.
    pub fn remove_staking_pool(&mut self, staking_pool_account_id: AccountId) -> bool {
        self.assert_called_by_foundation();
        assert!(
            env::is_valid_account_id(staking_pool_account_id.as_bytes()),
            "The given account ID is invalid"
        );
        self.whitelist.remove(&staking_pool_account_id)
    }
```
