### Title
Lockup Factory allows caller to override the trusted staking pool whitelist, letting an owner delegate locked/vesting NEAR to an attacker-controlled fake "staking pool" - (File: `lockup-factory/src/lib.rs`)

### Summary
`LockupFactory::create()` accepts a caller-supplied `whitelist_account_id` parameter that overrides the factory's configured, presumably NEAR-Foundation-controlled `staking_pool_whitelist_account_id`. The value chosen by the (unprivileged) caller is baked into the newly deployed lockup contract's `staking_pool_whitelist_account_id` field, which the lockup later trusts unconditionally when deciding whether a "staking pool" is safe to delegate funds to. This mirrors the MIMO finding's bug class: a factory-level trust binding (the canonical registry/whitelist) can be bypassed by calling the factory directly with attacker-chosen arguments, producing a deployed instance whose "trusted whitelist" is not actually the trusted whitelist.

### Finding Description
`LockupFactory::create()` lets the caller optionally supply `whitelist_account_id`, which — if present — is used instead of the factory's own `self.whitelist_account_id` (set at `new()` init time, intended to be the Foundation's canonical whitelist): [1](#0-0) 

This value is passed straight into the deployed lockup contract's constructor as `staking_pool_whitelist_account_id`: [2](#0-1) 

The lockup contract's `select_staking_pool()` (an owner method) trusts `self.staking_pool_whitelist_account_id` completely — it queries whatever account is stored there for the `is_whitelisted` verdict and proceeds if it returns `true`, with no further check that this account is the Foundation's real whitelist: [3](#0-2) 

Once a pool is selected via this compromised "whitelist," the owner can deposit and stake locked/unvested NEAR to that pool without further verification, e.g. via `deposit_and_stake`: [4](#0-3) 

The custody binding this breaks is: `lockup.staking_pool_whitelist_account_id == foundation_whitelist_account_id` (the invariant the whole whitelist system in `whitelist/src/lib.rs` is designed to guarantee, see `whitelist/README.md` explaining that only Foundation-approved staking pools should ever receive delegated lockup funds). Because `create()` lets any funder/deployer substitute an arbitrary account for this field, the equality no longer holds for any lockup created this way, and the "whitelist" enforced inside the lockup can be a contract fully controlled by the same party who created the lockup (or colludes with the owner).

### Impact Explanation
An attacker who is both funder and owner of a newly created lockup (or who colludes with the owner) can:
1. Call `LockupFactory::create()` with `whitelist_account_id` pointing to their own deployed mock contract that always returns `true` from `is_whitelisted`.
2. As the lockup's owner, call `select_staking_pool()` targeting an attacker-controlled "staking pool" account; the check against the fake whitelist passes.
3. Call `deposit_and_stake` / `deposit_to_staking_pool` to move locked/unvested NEAR balance to that attacker-controlled account, which simply keeps the tokens instead of implementing real staking-pool semantics (recoverability, unstaking, etc.).

This effectively releases locked/unvested NEAR early and moves it to a party not entitled to it under the vesting/lockup schedule — matching the Critical impact category "locked or unvested tokens released early" / "NEAR moved by a party not entitled to it."

### Likelihood Explanation
`create()` is a public, payable function callable by any account with no restriction tying `whitelist_account_id` to the Foundation's canonical whitelist, and no on-chain enforcement that vesting-schedule lockups must use the canonical whitelist. Any user who can pay the minimum deposit can trigger this path with a single transaction plus a trivially simple attacker-deployed mock whitelist contract.

### Recommendation
Remove the caller-suppliable `whitelist_account_id` override in `LockupFactory::create()` (or restrict it to be immutably fixed at factory initialization / settable only by the Foundation), so that every lockup deployed through the factory is bound to the single canonical whitelist account, analogous to how the MIMO fix consolidated proxy deployment and registration into a single trusted code path.

### Proof of Concept
1. Deploy a trivial "fake whitelist" contract exposing `is_whitelisted(staking_pool_account_id) -> bool` that always returns `true`.
2. Call `lockup_factory.create({ owner_account_id: <attacker>, lockup_duration, vesting_schedule: <some schedule>, whitelist_account_id: <fake_whitelist_account> })` with the minimum attached deposit, per [1](#0-0) .
3. As the resulting lockup's owner, call `select_staking_pool(<attacker_pool_account>)`; the `is_whitelisted` call resolves against the fake whitelist and succeeds, per [3](#0-2) .
4. Call `deposit_and_stake(<locked_amount>)` to transfer locked/unvested NEAR to `<attacker_pool_account>`, per [4](#0-3) , where the attacker keeps the funds instead of the tokens remaining locked per the vesting schedule.

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

**File:** lockup-factory/src/lib.rs (L135-157)
```rust
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

**File:** lockup/src/owner.rs (L122-166)
```rust
    /// OWNER'S METHOD
    ///
    /// Requires 125 TGas (5 * BASE_GAS)
    ///
    /// Deposits and stakes the given extra amount to the selected staking pool
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
