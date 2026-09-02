## Title
Lockup Factory lets the deposit-funding caller override the trusted staking-pool whitelist, allowing early release of locked/vested NEAR - (File: `lockup-factory/src/lib.rs`)

### Summary
`LockupFactory::create` accepts an optional `whitelist_account_id` parameter that fully overrides the foundation-configured `staking_pool_whitelist_account_id` used by the deployed lockup contract, with no restriction on who may set it or what it may point to. [1](#0-0) 

### Finding Description
The whitelist contract's entire purpose is to guarantee that a lockup contract can only delegate to staking pools that are safe (i.e., guaranteed to return funds, not release them early): "In order to enforce this, only approved (whitelisted) accounts of staking pool contracts can receive delegated tokens from lockup contracts." [2](#0-1) 

The lockup contract's owner-only `select_staking_pool` trusts whatever whitelist contract is stored in `staking_pool_whitelist_account_id` at initialization time and has no other constraint on which staking pool can be chosen once that whitelist approves it: [3](#0-2) 

`LockupFactory::create` is `#[payable]` and callable by anyone who attaches the minimum deposit; it takes `whitelist_account_id: Option<ValidAccountId>` from the caller and, if supplied, uses it verbatim as the `staking_pool_whitelist_account_id` for the newly deployed lockup contract instead of the factory's own trusted `self.whitelist_account_id`: [1](#0-0) 

This is confirmed as an intended, tested code path (`test_create_lockup_with_custom_whitelist_success`), not an edge case: [4](#0-3) 

Because the funding/`owner_account_id`-controlling caller of `create` can point the lockup at a whitelist contract they themselves control (or any attacker-deployed whitelist), the "trust boundary" the whitelist is supposed to enforce — that only the NEAR Foundation (or a foundation-approved factory) decides which staking pools are safe — is broken for that lockup instance. The equality that should hold is:

`staking_pool_whitelist_account_id (used by lockup) == foundation-trusted whitelist`

but the factory allows:

`staking_pool_whitelist_account_id (used by lockup) == arbitrary_account_chosen_by_deployer`

Once the lockup contract trusts an attacker-controlled whitelist, the owner can call `select_staking_pool` to whitelist and select a malicious "staking pool" contract they control, then `deposit_to_staking_pool` / `deposit_and_stake`, then immediately `unstake_all` and `withdraw_from_staking_pool` / `withdraw_all_from_staking_pool` — all owner-only methods gated solely by `assert_owner`, `assert_staking_pool_is_idle`, and `assert_no_termination`, none of which restrict the amount to only the vested/unlocked portion: [5](#0-4) [6](#0-5) 

Because the counterparty "staking pool" is fully attacker-controlled, it can return 100% of the deposited amount on `withdraw` immediately (no real unstaking delay), letting the owner move locked/unvested NEAR out of the lockup's staking flow and back to a liquid balance far earlier than the `lockup_timestamp`/`release_duration`/vesting schedule would normally allow, then use `transfer` (once transfers are enabled) to send it out.

### Impact Explanation
This matches the report's core bug class — a party can weaponize a whitelist-style trust binding for purposes the trust was never intended to cover, resulting in a wrongly-parameterised deployment (per the Impact rubric: *"a wrongly whitelisted or wrongly parameterised deployment... locked or unvested tokens released early"*, Critical severity). The lockup's entire security model for staking (as opposed to plain lockup/vesting release) depends on the whitelist being foundation-controlled; the factory silently lets the deployer substitute their own whitelist for the trusted one.

### Likelihood Explanation
Requires only a normal `create()` call with the optional `whitelist_account_id` argument populated — no privileged role, no compromised keys, no redeploy of the lockup or factory contract itself, and no reliance on ignoring the documented initialization (this is a documented, tested parameter). Any account controlling the `owner_account_id` used at creation (which for self-funded lockups is often the deployer itself) can exploit this.

### Recommendation
Remove the caller-supplied `whitelist_account_id` override in `LockupFactory::create`, or restrict it to a foundation-approved allow-list validated by the factory itself (analogous to how `staking-pool-factory` only whitelists pools it created). The lockup contract should always be deployed with the factory's own trusted `whitelist_account_id`.

### Proof of Concept
1. Attacker deploys their own `WhitelistContract` (`whitelist/src/lib.rs`) with themselves as `foundation_account_id`.
2. Attacker deploys a malicious "staking pool" contract that implements `deposit`, `deposit_and_stake`, `unstake_all`, and `withdraw`/`get_account_unstaked_balance` to simply always report/return the full deposited balance immediately (no real 4-epoch unstaking delay).
3. Attacker (as `owner_account_id`) calls `lockup_factory.create(owner_account_id=attacker, ..., whitelist_account_id=Some(attacker_whitelist))` with the minimum attached deposit plus the locked NEAR amount, per `LockupFactory::create`: [1](#0-0) 
4. On the deployed lockup contract, attacker calls `select_staking_pool(malicious_pool)`, which passes because it is checked against the attacker's own whitelist (`ext_whitelist::is_whitelisted`), not the foundation's: [3](#0-2) 
5. Attacker calls `deposit_and_stake(locked_amount)`, then `unstake_all()`, then `withdraw_all_from_staking_pool()`; the malicious pool returns the full amount immediately, restoring it to the lockup's liquid, non-staking balance well before the natural `lockup_timestamp`/`release_duration`/vesting cliff would have permitted.
6. Once transfers are enabled (`TransfersEnabled` is set immediately for factory-created lockups per `transfers_information: TransfersInformation::TransfersEnabled`), attacker calls `transfer()` to move the funds out.

### Citations

**File:** lockup-factory/src/lib.rs (L107-133)
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

**File:** lockup-factory/src/lib.rs (L390-425)
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

        context.predecessor_account_id = account_factory();
        context.attached_deposit = ntoy(0);
        testing_env_with_promise_results(context.clone(), PromiseResult::Successful(vec![]));
        println!("{}", lockup_account());
        contract.on_lockup_create(
            lockup_account(),
            ntoy(30).into(),
            String::from(account_tokens_owner()),
        );
    }
```

**File:** whitelist/README.md (L1-9)
```markdown
# Whitelist contract for staking pools

The purpose of this contract is to maintain the whitelist of the staking pool contracts account IDs that are approved
by NEAR Foundation.

In order for the lockup contracts to be able delegate to a staking pool, the staking pool should faithfully implement the spec.
The staking pool should guarantee that the delegated tokens can not be lost or locked, such as the lockup contract should be
able to recover delegated tokens back to the lockup from a staking pool. In order to enforce this, only approved (whitelisted)
accounts of staking pool contracts can receive delegated tokens from lockup contracts.
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

**File:** lockup/src/owner.rs (L216-294)
```rust
    pub fn withdraw_from_staking_pool(&mut self, amount: WrappedBalance) -> Promise {
        self.assert_owner();
        assert!(amount.0 > 0, "Amount should be positive");
        self.assert_staking_pool_is_idle();
        self.assert_no_termination();

        env::log(
            format!(
                "Withdrawing {} from the staking pool @{}",
                amount.0,
                self.staking_information
                    .as_ref()
                    .unwrap()
                    .staking_pool_account_id
            )
            .as_bytes(),
        );

        self.set_staking_pool_status(TransactionStatus::Busy);

        ext_staking_pool::withdraw(
            amount,
            &self
                .staking_information
                .as_ref()
                .unwrap()
                .staking_pool_account_id,
            NO_DEPOSIT,
            gas::staking_pool::WITHDRAW,
        )
        .then(ext_self_owner::on_staking_pool_withdraw(
            amount,
            &env::current_account_id(),
            NO_DEPOSIT,
            gas::owner_callbacks::ON_STAKING_POOL_WITHDRAW,
        ))
    }

    /// OWNER'S METHOD
    ///
    /// Requires 175 TGas (7 * BASE_GAS)
    ///
    /// Tries to withdraws all unstaked balance from the staking pool
    pub fn withdraw_all_from_staking_pool(&mut self) -> Promise {
        self.assert_owner();
        self.assert_staking_pool_is_idle();
        self.assert_no_termination();

        env::log(
            format!(
                "Going to query the unstaked balance at the staking pool @{}",
                self.staking_information
                    .as_ref()
                    .unwrap()
                    .staking_pool_account_id
            )
            .as_bytes(),
        );

        self.set_staking_pool_status(TransactionStatus::Busy);

        ext_staking_pool::get_account_unstaked_balance(
            env::current_account_id(),
            &self
                .staking_information
                .as_ref()
                .unwrap()
                .staking_pool_account_id,
            NO_DEPOSIT,
            gas::staking_pool::GET_ACCOUNT_UNSTAKED_BALANCE,
        )
        .then(
            ext_self_owner::on_get_account_unstaked_balance_to_withdraw_by_owner(
                &env::current_account_id(),
                NO_DEPOSIT,
                gas::owner_callbacks::ON_GET_ACCOUNT_UNSTAKED_BALANCE_TO_WITHDRAW_BY_OWNER,
            ),
        )
    }
```
