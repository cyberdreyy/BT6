Given the constraints of this task, I've reached the limit of tool calls but have gathered sufficient evidence to complete the analysis.

### Title
Lockup Factory Lets an Untrusted Caller Override the Staking-Pool Whitelist, Bypassing the Vetting That Protects Locked/Unvested NEAR - (File: `lockup-factory/src/lib.rs`)

### Summary
`LockupFactory::create()` accepts an optional `whitelist_account_id` parameter that, if supplied, replaces the factory's own vetted `staking_pool_whitelist_account_id` as the `staking_pool_whitelist_account_id` baked into the newly deployed lockup contract. Because `create()` is a public, unprivileged, payable method with no check restricting who may pass this override, any caller funding a lockup can point the deployed lockup at a whitelist contract they fully control — completely undermining the purpose of the whitelist check inside `lockup/src/owner.rs`'s `select_staking_pool`, which is the sole authorization gate that is supposed to guarantee only NEAR-Foundation-approved staking pools can receive delegated (and possibly still-locked/unvested) tokens.

### Finding Description
The whitelist contract's own README states the security invariant this bug breaks: "In order for the lockup contracts to be able [to] delegate to a staking pool, the staking pool should faithfully implement the spec... In order to enforce this, only approved (whitelisted) accounts of staking pool contracts can receive delegated tokens from lockup contracts." [1](#0-0) 

`select_staking_pool` in the lockup contract enforces this by calling out to `self.staking_pool_whitelist_account_id` and only accepting the selection if `is_whitelisted` returns true: [2](#0-1) . That whitelist account ID is set once at lockup initialization time and is treated internally as the trust anchor gating which pools may hold delegated funds: [3](#0-2) .

The trust anchor is supposed to always be the NEAR-Foundation-controlled whitelist. However, `LockupFactory::create()` lets the caller override it: [4](#0-3) 
```
pub fn create(
    &mut self,
    owner_account_id: ValidAccountId,
    ...
    whitelist_account_id: Option<ValidAccountId>,
) -> Promise {
    ...
    // Defaults to the whitelist account ID given on init call.
    let staking_pool_whitelist_account_id = if let Some(account_id) = whitelist_account_id {
        account_id.into()
    } else {
        self.whitelist_account_id.clone()
    };
```
There is no `assert_owner`/foundation check on `create()`, and no restriction preventing `whitelist_account_id` from pointing at an attacker-deployed contract whose `is_whitelisted` always returns `true`. This value is passed straight into the deployed lockup's `new()` as `staking_pool_whitelist_account_id`, permanently binding that lockup's trust anchor to whatever the funder chose: [5](#0-4) .

Once the lockup's owner controls (or colludes with the creator of) a fake whitelist that always approves, `select_staking_pool` will accept any attacker-controlled "staking pool" account: [6](#0-5) . `deposit_to_staking_pool` then moves real NEAR out of the lockup account to that attacker-controlled account, gated only by `get_account_balance()` (the lockup's actual native balance) — not by the vested/liquid portion — so locked/unvested NEAR can be sent out: [7](#0-6) . Since the destination account is attacker-controlled rather than a vetted staking pool that is contractually guaranteed to return delegated funds, the tokens effectively leave the lockup schedule's custody permanently, defeating both the lockup/vesting release schedule and the termination/foundation-recovery mechanism.

This is the same bug class as the `FCFSMint()` finding: a function whose specification requires validating against an approved/whitelisted party (here, the NEAR-Foundation whitelist; there, the wallet whitelist) is reachable by an unprivileged caller who can supply/point to an untrusted substitute, because the code never checks that the caller (or the parameter they control) is actually the intended, foundation-controlled entity.

### Impact Explanation
This crosses the "wrongly whitelisted or wrongly parameterised deployment" / "locked or unvested tokens released early" Critical bar: a lockup contract can be deployed with a whitelist trust-anchor that is not the NEAR Foundation's, letting its owner extract locked/unvested NEAR early through a self-controlled fake "staking pool," bypassing the entire lockup/vesting/termination protection the contract family exists to provide.

### Likelihood Explanation
Any account can call the payable `create()` method with the minimum attached deposit and simply supply their own `whitelist_account_id`; no special privilege, redeploy of core contracts, or foundation key is needed. The only additional step is deploying a trivial "always-whitelisted" contract and a fake staking-pool contract, both of which are ordinary, permissionless account-level actions available to any attacker who is also the intended lockup owner (or colludes with the owner).

### Recommendation
Remove the caller-supplied `whitelist_account_id` override from `LockupFactory::create()` (or restrict it to be set only by the factory owner/foundation at `new()` time), so every lockup deployed by the factory is always bound to the single, foundation-controlled `staking_pool_whitelist_account_id` stored in the factory's own state.

### Proof of Concept
1. Attacker deploys `EvilWhitelist` with `is_whitelisted` and `is_factory_whitelisted` hardcoded to return `true`.
2. Attacker deploys `EvilPool`, a contract mimicking the staking-pool interface (`deposit`, `get_account_total_balance`, etc.) but withholding funds/fabricating balances.
3. Attacker (as funder) calls `lockup-factory.create(owner_account_id: <attacker>, ..., whitelist_account_id: Some(EvilWhitelist))`, attaching the funding deposit (including unvested/locked NEAR under a vesting schedule).
4. The new lockup is deployed with `staking_pool_whitelist_account_id = EvilWhitelist` per [8](#0-7) .
5. As owner, attacker calls `select_staking_pool(EvilPool)`; the whitelist callback approves it regardless of real vetting [6](#0-5) .
6. Attacker calls `deposit_to_staking_pool(full_locked_balance)`, moving the entire (including unvested) NEAR balance to `EvilPool`, which the attacker fully controls and never returns [7](#0-6) .
7. The locked/unvested tokens are now outside the lockup schedule's custody, permanently released early to the attacker, with no foundation-approved staking pool ever having guaranteed their return.

### Citations

**File:** whitelist/README.md (L6-9)
```markdown
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

**File:** lockup/src/lib.rs (L180-198)
```rust
    #[init]
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

**File:** lockup-factory/src/lib.rs (L107-153)
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

**File:** lockup/src/owner_callbacks.rs (L7-25)
```rust
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
