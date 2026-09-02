### Title
Lockup owner can substitute the staking-pool whitelist at creation to move locked/unvested NEAR to an unaudited pool - (File: `lockup-factory/src/lib.rs`)

### Summary
`LockupFactory::create` lets the account funding a lockup override the whitelist contract that the deployed `LockupContract` will trust, instead of forcing the factory's own vetted default. Because `select_staking_pool` in the lockup contract only checks whatever `staking_pool_whitelist_account_id` was baked in at `new()`, the “whitelisted staking pool” custody guarantee (that delegated tokens can never be lost or trapped) can be pointed at an attacker-controlled contract that always answers `is_whitelisted = true`, breaking the trust binding the whitelist is supposed to enforce.

### Finding Description
`LockupFactory::create` accepts an optional `whitelist_account_id` parameter supplied by the (unprivileged) caller and, if present, uses it instead of the factory's own `self.whitelist_account_id` when constructing `LockupArgs`: [1](#0-0) 

This value is passed straight through to `LockupContract::new` as `staking_pool_whitelist_account_id` with no validation beyond `is_valid_account_id`: [2](#0-1) 

Every subsequent trust decision inside the lockup contract is delegated entirely to whatever account sits at that address. `select_staking_pool` calls `ext_whitelist::is_whitelisted` on `self.staking_pool_whitelist_account_id` and, on a `true` response, unconditionally records the target as the selected staking pool — no code-hash check, no cross-reference to the real NEAR Foundation whitelist: [3](#0-2) [4](#0-3) 

The entire design intent of the whitelist — “only approved accounts of staking pool contracts can receive delegated tokens from lockup contracts” so that “delegated tokens can not be lost or locked” — is stated explicitly in the whitelist's own README: [5](#0-4) 

By supplying a custom `whitelist_account_id` at `create()` time, the caller (who is typically also configuring/owning the lockup on behalf of the eventual beneficiary) substitutes this trust anchor with a contract they control. The lockup then happily `deposit_and_stake`s the full unvested/locked balance into a pool that was never vetted by the NEAR Foundation: [6](#0-5) 

This is a structural analog of the SwapExecutor bug: the SwapFacade trusted "whatever SwapExecutor code the caller passed in" instead of a fixed, verified one; here, the LockupContract trusts "whatever whitelist account the factory caller passed in" instead of the factory's own vetted default, letting an unprivileged actor redefine which pool is treated as safe custody.

### Impact Explanation
Once tokens are staked with a pool that is not truly bound by the honest-pool guarantees (unstake-then-withdraw round trip, no ability to seize funds), the foundation's termination flow assumes it can always recover a staked balance: [7](#0-6) 

If the "whitelisted" pool is instead a contract crafted by the party that funded/created the lockup (e.g. one that immediately reports the deposit as withdrawable, or one that never actually locks funds against `unstake`/`withdraw` semantics), the funds that are supposed to be recoverable by the Foundation on termination, or that are supposed to remain locked/unvested per the vesting schedule, can effectively be pulled out through the malicious pool ahead of schedule — an early release of locked/unvested NEAR, which the rubric classifies as Critical impact ("locked or unvested tokens released early ... a wrongly whitelisted or wrongly parameterised deployment").

### Likelihood Explanation
No privileged key, foundation cooperation, or redeploy is required: any account calling `LockupFactory::create` (which is explicitly permissionless — "It allows any user to create and fund the lockup contract") can pass an arbitrary `whitelist_account_id`, as demonstrated by the existing repository test `test_create_lockup_with_custom_whitelist_success`, which exercises exactly this code path: [8](#0-7) 

This confirms the parameter is a first-class, intentionally-supported override, making the substitution trivially reachable by anyone funding a lockup for themselves.

### Recommendation
The lockup factory should not let the caller of `create()` override the whitelist account at all — `staking_pool_whitelist_account_id` should always be the factory's own vetted value (`self.whitelist_account_id`), set once at `LockupFactory::new` by the Foundation/deployer, with no per-call override parameter. If a per-lockup override is genuinely required for legitimate use cases, it should be restricted to the Foundation (e.g., only usable by a call whose predecessor is the foundation account), and the lockup contract itself could additionally hard-verify that any staking pool ever selected was created by a whitelisted factory (cross-checking code hash/`is_factory_whitelisted`) rather than trusting a single mutable `is_whitelisted` boolean sourced from an arbitrary account.

### Proof of Concept
1. Attacker deploys `EvilWhitelist`, a minimal contract exposing `is_whitelisted(staking_pool_account_id) -> bool` that always returns `true`, and a companion `EvilStakingPool` contract mimicking the `deposit_and_stake`/`get_account_unstaked_balance`/`withdraw` interface but that never actually restricts withdrawal timing or amount (or simply returns success without holding funds in escrow the way a real pool would).
2. Attacker (or an owner colluding with them) calls `LockupFactory::create(owner_account_id, lockup_duration, ..., whitelist_account_id: Some("evil-whitelist.attacker"))`, exactly as exercised in `lockup-factory/src/lib.rs`'s `test_create_lockup_with_custom_whitelist_success` test.
3. The deployed `LockupContract` is initialized with `staking_pool_whitelist_account_id = "evil-whitelist.attacker"` (see `lockup/src/lib.rs::new`).
4. Owner calls `select_staking_pool("evil-pool.attacker")`; the lockup contract calls `is_whitelisted` on `evil-whitelist.attacker`, gets `true`, and accepts the pool (`lockup/src/owner.rs::select_staking_pool`, `lockup/src/owner_callbacks.rs::on_whitelist_is_whitelisted`).
5. Owner calls `deposit_and_stake` to move the full locked/unvested NEAR balance to `evil-pool.attacker`.
6. Because `evil-pool.attacker` is not bound by the real staking-pool spec, funds can be extracted or reported as immediately unstaked/withdrawable, letting the owner realize locked/unvested NEAR before the vesting/lockup schedule permits, and preventing the Foundation's `terminate_vesting`/`termination_prepare_to_withdraw` flow from recovering the unvested portion as designed.

### Citations

**File:** lockup-factory/src/lib.rs (L128-153)
```rust
        // Defaults to the whitelist account ID given on init call.
        let staking_pool_whitelist_account_id = if let Some(account_id) = whitelist_account_id {
            account_id.into()
        } else {
            self.whitelist_account_id.clone()
        };

        let transfers_enabled: WrappedTimestamp = TRANSFERS_STARTED.into();
        Promise::new(lockup_account_id.clone())
            .create_account()
            .deploy_contract(CODE.to_vec())
            .transfer(env::attached_deposit())
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

**File:** lockup/src/lib.rs (L181-198)
```rust
    pub fn new(
        owner_account_id: AccountId,
        lockup_duration: WrappedDuration,
        lockup_timestamp: Option<WrappedTimestamp>,
        transfers_information: TransfersInformation,
        vesting_schedule: Option<VestingScheduleOrHash>,
        release_duration: Option<WrappedDuration>,
        staking_pool_whitelist_account_id: AccountId,
        foundation_account_id: Option<AccountId>,
    ) -> Self {
        assert!(
            env::is_valid_account_id(owner_account_id.as_bytes()),
            "The account ID of the owner is invalid"
        );
        assert!(
            env::is_valid_account_id(staking_pool_whitelist_account_id.as_bytes()),
            "The staking pool whitelist account ID is invalid"
        );
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

**File:** whitelist/README.md (L6-9)
```markdown
In order for the lockup contracts to be able delegate to a staking pool, the staking pool should faithfully implement the spec.
The staking pool should guarantee that the delegated tokens can not be lost or locked, such as the lockup contract should be
able to recover delegated tokens back to the lockup from a staking pool. In order to enforce this, only approved (whitelisted)
accounts of staking pool contracts can receive delegated tokens from lockup contracts.
```

**File:** lockup/src/foundation.rs (L49-127)
```rust
    /// FOUNDATION'S METHOD
    ///
    /// Requires 175 TGas (7 * BASE_GAS)
    ///
    /// When the vesting is terminated and there are deficit of the tokens on the account, the
    /// deficit amount of tokens has to be unstaked and withdrawn from the staking pool.
    /// Should be invoked twice:
    /// 1. First, to unstake everything from the staking pool;
    /// 2. Second, after 4 epochs (48 hours) to prepare to withdraw.
    pub fn termination_prepare_to_withdraw(&mut self) -> Promise {
        self.assert_called_by_foundation();
        self.assert_staking_pool_is_idle();

        let status = self.get_termination_status();

        match status {
            None => {
                env::panic(b"There is no termination in progress");
            }
            Some(TerminationStatus::UnstakingInProgress)
            | Some(TerminationStatus::WithdrawingFromStakingPoolInProgress)
            | Some(TerminationStatus::WithdrawingFromAccountInProgress) => {
                env::panic(b"Another transaction is already in progress.");
            }
            Some(TerminationStatus::ReadyToWithdraw) => {
                env::panic(b"The account is ready to withdraw unvested balance.")
            }
            Some(TerminationStatus::VestingTerminatedWithDeficit) => {
                // Need to unstake
                self.set_termination_status(TerminationStatus::UnstakingInProgress);
                self.set_staking_pool_status(TransactionStatus::Busy);
                env::log(b"Termination Step: Going to unstake everything from the staking pool");

                ext_staking_pool::get_account_staked_balance(
                    env::current_account_id(),
                    &self
                        .staking_information
                        .as_ref()
                        .unwrap()
                        .staking_pool_account_id,
                    NO_DEPOSIT,
                    gas::staking_pool::GET_ACCOUNT_STAKED_BALANCE,
                )
                .then(
                    ext_self_foundation::on_get_account_staked_balance_to_unstake(
                        &env::current_account_id(),
                        NO_DEPOSIT,
                        gas::foundation_callbacks::ON_GET_ACCOUNT_STAKED_BALANCE_TO_UNSTAKE,
                    ),
                )
            }
            Some(TerminationStatus::EverythingUnstaked) => {
                // Need to withdraw everything
                self.set_termination_status(
                    TerminationStatus::WithdrawingFromStakingPoolInProgress,
                );
                self.set_staking_pool_status(TransactionStatus::Busy);
                env::log(b"Termination Step: Going to withdraw everything from the staking pool");

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
                    ext_self_foundation::on_get_account_unstaked_balance_to_withdraw(
                        &env::current_account_id(),
                        NO_DEPOSIT,
                        gas::foundation_callbacks::ON_GET_ACCOUNT_UNSTAKED_BALANCE_TO_WITHDRAW,
                    ),
                )
            }
        }
    }
```
