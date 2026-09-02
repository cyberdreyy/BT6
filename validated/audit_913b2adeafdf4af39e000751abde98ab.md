### Title
LockupFactory.create() lets the funding caller inject an arbitrary staking-pool whitelist, letting a self-created lockup owner fabricate `known_deposited_balance` and release locked/unvested NEAR early - (File: lockup-factory/src/lib.rs)

### Summary
`LockupFactory::create()` accepts an optional `whitelist_account_id` parameter supplied by the (unprivileged) caller who pays the deposit, and — if present — uses it verbatim as the `staking_pool_whitelist_account_id` embedded into the newly deployed `LockupContract`, instead of forcing the factory's own trusted whitelist. [1](#0-0) 
This is confirmed as an accepted code path by the test `test_create_lockup_with_custom_whitelist_success`. [2](#0-1) 

### Finding Description
The lockup contract's staking-pool-selection safety relies entirely on `select_staking_pool()` verifying the candidate pool against `staking_pool_whitelist_account_id` before trusting it: [3](#0-2) [4](#0-3) 

This whitelist address is meant to be the NEAR Foundation's vetted whitelist contract, which only lists staking-pool WASM code known to correctly custody delegated funds (`whitelist/src/lib.rs`, restricted to foundation/whitelisted factories). [5](#0-4) 

However, when a lockup is created through the factory, the trust anchor itself (`staking_pool_whitelist_account_id`) is attacker-controllable: `LockupFactory::create()` lets the caller pass `whitelist_account_id: Option<ValidAccountId>`, defaulting only when omitted: [1](#0-0) 
The factory is explicitly permissionless — "any user" may call `create()` and fund/self-own a lockup, including one carrying a real `vesting_schedule` (which triggers the Foundation's clawback entitlement over unvested tokens, `foundation_account_id`). [6](#0-5) [7](#0-6) 

Once deployed with a custom whitelist, the owner can `select_staking_pool()` pointing at any account they fully control (a contract implementing the `ExtStakingPool` interface with arbitrary return values), because the malicious whitelist always answers `is_whitelisted == true`: [8](#0-7) 
The owner then deposits/stakes some real amount, and calls `refresh_staking_pool_balance` / the `on_get_account_total_balance` callback, letting the attacker-controlled pool report an arbitrarily inflated `total_balance`, which is written straight into `staking_information.deposit_amount` (i.e., `get_known_deposited_balance()`), with no sanity check against the real amount ever deposited.

This inflated `known_deposited_balance` directly feeds the custody-critical accounting functions: [9](#0-8) 
`get_owners_balance()` = `account_balance + known_deposited_balance - locked_amount`, and `get_liquid_owners_balance()` bounds only by `account_balance` (real NEAR on hand), which the owner can grow via `withdraw_from_staking_pool` calls that also trust the fake pool's reported withdrawable amount. The binding that should hold is:

`owners_balance (claimed, used to gate transfer()) == real NEAR actually vested/released and held by (or recoverable from) the pool`

An attacker who controls both the "whitelist" and the "staking pool" contracts can make the left side arbitrarily larger than the right side, exactly mirroring the stNXM root cause: an unvalidated address is trusted into a role (there, `tokenIdToPool`; here, `staking_pool_whitelist_account_id`) and later used to compute claimable value that is then paid out.

### Impact Explanation
This breaks a solvency/schedule custody binding and matches the Critical impact category "locked or unvested tokens released early" / "a wrongly whitelisted or wrongly parameterised deployment." Because `create()` lets the caller (who need not be the Foundation) both set the `vesting_schedule` and the `whitelist_account_id` for a lockup they can also own, a party who is supposed to be bound by a vesting/lockup schedule (and by the Foundation's termination clawback right over unvested funds) can bypass that schedule and drain more NEAR out via `transfer()` than is actually vested/released, defeating the entire purpose of the lockup contract and the Foundation's economic claim on unvested tokens.

### Likelihood Explanation
No privileged role is required. `create()` is explicitly designed to be permissionless ("no need to have access to the foundation keys to create lockup"), and nothing ties the `whitelist_account_id` override to who the `owner_account_id` is or whether a `vesting_schedule` is present, so any unprivileged actor can self-fund a lockup with a vesting schedule, pass their own whitelist contract, deploy their own fake staking-pool contract, and immediately begin inflating `known_deposited_balance`.

### Recommendation
Do not allow the `create()` caller to override `staking_pool_whitelist_account_id`; always use the factory's own trusted `self.whitelist_account_id` for every deployed lockup, or restrict the `whitelist_account_id` override to only be settable by the factory's `foundation_account_id` predecessor. Additionally, `on_get_account_total_balance` should sanity-check the reported balance is not less than deposits already recorded, but the primary fix is removing attacker control over the trust anchor itself.

### Proof of Concept
1. Attacker calls `LockupFactory::create()` with `owner_account_id = attacker`, `vesting_schedule = Some(...)` (any schedule with a future cliff/end), and `whitelist_account_id = Some(attacker_fake_whitelist)`, attaching `MIN_ATTACHED_BALANCE` (per `lockup-factory/src/lib.rs:107-134`, `390-414`).
2. Attacker deploys `attacker_fake_whitelist` (always returns `true` from `is_whitelisted`) and `attacker_fake_pool` (implements `ExtStakingPool`, returns arbitrary `get_account_total_balance`/`withdraw` amounts).
3. On the freshly deployed lockup, attacker (as owner) calls `select_staking_pool(attacker_fake_pool)` — passes because the embedded whitelist is attacker-controlled (`lockup/src/owner.rs:12-41`, `lockup/src/owner_callbacks.rs:7-25`).
4. Attacker deposits a small real amount via `deposit_and_stake`, then calls `refresh_staking_pool_balance`; the fake pool's callback reports a hugely inflated `total_balance`, which is stored as `known_deposited_balance` (`lockup/src/getters.rs:20-30`).
5. `get_owners_balance()`/`get_liquid_owners_balance()` now report values far larger than legitimately vested/released, letting the owner `transfer()` NEAR out beyond schedule, releasing locked/unvested tokens early and defeating the Foundation's termination claim.

### Citations

**File:** lockup-factory/src/lib.rs (L107-134)
```rust
    #[payable]
    pub fn create(
        &mut self,
        owner_account_id: ValidAccountId,
        lockup_duration: WrappedDuration,
        lockup_timestamp: Option<WrappedTimestamp>,
        vesting_schedule: Option<VestingScheduleOrHash>,
        release_duration: Option<WrappedDuration>,
        whitelist_account_id: Option<ValidAccountId>,
    ) -> Promise {
        assert!(env::attached_deposit() >= MIN_ATTACHED_BALANCE, "Not enough attached deposit");

        let byte_slice = env::sha256(owner_account_id.as_ref().as_bytes());
        let lockup_account_id =
            format!("{}.{}", hex::encode(&byte_slice[..20]), env::current_account_id());

        let mut foundation_account: Option<AccountId> = None;
        if vesting_schedule.is_some() {
            foundation_account = Some(self.foundation_account_id.clone());
        };

        // Defaults to the whitelist account ID given on init call.
        let staking_pool_whitelist_account_id = if let Some(account_id) = whitelist_account_id {
            account_id.into()
        } else {
            self.whitelist_account_id.clone()
        };

```

**File:** lockup-factory/src/lib.rs (L390-414)
```rust
    #[test]
    fn test_create_lockup_with_custom_whitelist_success() {
        let mut context = VMContextBuilder::new()
            .current_account_id(account_factory())
            .predecessor_account_id(account_near())
            .finish();
        testing_env!(context.clone());

        let mut contract = LockupFactory::new(whitelist_account_id(), foundation_account_id());

        const LOCKUP_DURATION: u64 = 63036000000000000; /* 24 months */
        let lockup_duration: WrappedTimestamp = LOCKUP_DURATION.into();

        context.is_view = false;
        context.predecessor_account_id = String::from(account_tokens_owner());
        context.attached_deposit = ntoy(35);
        testing_env!(context.clone());
        contract.create(
            account_tokens_owner(),
            lockup_duration,
            None,
            None,
            None,
            Some(custom_whitelist_account_id()),
        );
```

**File:** lockup/src/owner.rs (L10-41)
```rust
    /// Selects staking pool contract at the given account ID. The staking pool first has to be
    /// checked against the staking pool whitelist contract.
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

**File:** lockup/src/owner_callbacks.rs (L6-25)
```rust
    /// Called after a given `staking_pool_account_id` was checked in the whitelist.
    pub fn on_whitelist_is_whitelisted(
        &mut self,
        #[callback] is_whitelisted: bool,
        staking_pool_account_id: AccountId,
    ) -> bool {
        assert_self();
        assert!(
            is_whitelisted,
            "The given staking pool account ID is not whitelisted"
        );
        self.assert_staking_pool_is_not_selected();
        self.assert_no_termination();
        self.staking_information = Some(StakingInformation {
            staking_pool_account_id,
            status: TransactionStatus::Idle,
            deposit_amount: 0.into(),
        });
        true
    }
```

**File:** whitelist/src/lib.rs (L75-88)
```rust
    pub fn add_staking_pool(&mut self, staking_pool_account_id: AccountId) -> bool {
        assert!(
            env::is_valid_account_id(staking_pool_account_id.as_bytes()),
            "The given account ID is invalid"
        );
        // Can only be called by a whitelisted factory or by the foundation.
        if !self
            .factory_whitelist
            .contains(&env::predecessor_account_id())
        {
            self.assert_called_by_foundation();
        }
        self.whitelist.insert(&staking_pool_account_id)
    }
```

**File:** lockup-factory/README.md (L1-18)
```markdown
# Lockup Factory Contract

This contract deploys lockup contracts. 
It allows any user to create and fund the lockup contract.
The lockup factory contract packages the binary of the 
<a href="https://github.com/near/core-contracts/tree/master/lockup">lockup 
contract</a> within its own binary.

To create a new lockup contract a user should issue a transaction and 
attach the required minimum deposit. The entire deposit will be transferred to 
the newly created lockup contract including to cover the storage.

The benefits: 
1. Lockups can be funded from any account.
2. No need to have access to the foundation keys to create lockup.
3. Auto-generates the lockup from the owner account.
4. Refund deposit on errors.

```

**File:** lockup/src/lib.rs (L35-60)
```rust
#[ext_contract(ext_staking_pool)]
pub trait ExtStakingPool {
    fn get_account_staked_balance(&self, account_id: AccountId) -> WrappedBalance;

    fn get_account_unstaked_balance(&self, account_id: AccountId) -> WrappedBalance;

    fn get_account_total_balance(&self, account_id: AccountId) -> WrappedBalance;

    fn deposit(&mut self);

    fn deposit_and_stake(&mut self);

    fn withdraw(&mut self, amount: WrappedBalance);

    fn stake(&mut self, amount: WrappedBalance);

    fn unstake(&mut self, amount: WrappedBalance);

    fn unstake_all(&mut self);
}

#[ext_contract(ext_whitelist)]
pub trait ExtStakingPoolWhitelist {
    fn is_whitelisted(&self, staking_pool_account_id: AccountId) -> bool;
}

```

**File:** lockup/src/getters.rs (L163-178)
```rust
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
