### Title
Lockup owner can self-select an arbitrary, unvetted "staking pool" by supplying a custom `whitelist_account_id` at creation, allowing early release of locked/unvested tokens - (File: lockup-factory/src/lib.rs, lockup/src/owner.rs)

### Summary
`LockupFactory::create` lets the caller (the future lockup owner) pass an arbitrary `whitelist_account_id`, which is baked into the deployed lockup contract as `staking_pool_whitelist_account_id`. Since the lockup only trusts whatever whitelist account is stored in this field to answer `is_whitelisted`, and the caller controls that account, the owner can point their lockup at a whitelist contract they themselves control (or deploy) that always returns `true`. This breaks the guarantee: "only NEAR‑Foundation‑approved staking pools can receive delegated tokens from a lockup," letting the owner select an account they fully control as the "staking pool" and move locked/unvested balance there, then withdraw/redeem it outside of the lockup's schedule and vesting constraints.

### Finding Description
`lockup-factory/src/lib.rs` `create()` accepts an optional `whitelist_account_id: Option<ValidAccountId>` from the caller: [1](#0-0) 
If provided, this attacker-chosen value overrides the factory's canonical whitelist and is passed straight into the deployed lockup contract's `new()` as `staking_pool_whitelist_account_id`, with no validation that it is the NEAR Foundation's official whitelist: [2](#0-1) 

The lockup contract's `new()` only checks that the supplied account ID is syntactically valid, never that it matches any canonical/expected whitelist: [3](#0-2) 

Later, `select_staking_pool` (an owner method) trusts whatever is in `self.staking_pool_whitelist_account_id` to gate which accounts can be treated as a legitimate staking pool: [4](#0-3) 

Since the owner (the same party who called `create` and chose `whitelist_account_id`) can deploy their own trivial whitelist contract (one that always returns `true` for `is_whitelisted`), `select_staking_pool` will accept literally any account as the "staking pool," including an account the owner fully controls. The lockup/whitelist README explicitly documents that this whitelist binding is what prevents tokens from being "lost, locked, or stolen" and is supposed to be the NEAR‑Foundation‑approved list: [5](#0-4) 

Once a self-controlled account is selected as the "staking pool," the owner can call `deposit_and_stake`/`stake` to move locked/unvested balance out of the lockup account and into the attacker-controlled contract: [6](#0-5) 

This breaks the intended custody binding: "an account trusted as a staking pool" should equal "an account vetted through the NEAR Foundation whitelist," but here it equals "any account the owner names," because the owner also controls the whitelist source of truth for their own lockup.

### Impact Explanation
This matches the Critical impact bucket: "locked or unvested tokens released early" and "a wrongly whitelisted or wrongly parameterised deployment." The lockup contract's core guarantee — that locked/unvested tokens can only be delegated to a pool vetted by the NEAR Foundation and can always be recovered — is defeated because the owner controls the trust root (the whitelist account) used to validate the delegation target for their own contract. Locked/vesting NEAR that should be encumbered by the lockup/vesting schedule can instead be moved into and effectively "cashed out" through a self-controlled contract before the schedule permits it.

### Likelihood Explanation
Likelihood is high for any determined lockup beneficiary: the `create` function is public/permissionless, requires only the minimum attached deposit, and directly accepts a caller-supplied `whitelist_account_id` with no restriction that it be the canonical foundation whitelist. Deploying a trivial whitelist-mimicking contract (returning `true` for `is_whitelisted`) requires no special privilege — only standard account creation and contract deployment, both routinely available to an unprivileged actor creating their own lockup.

### Recommendation
Remove the caller-supplied `whitelist_account_id` override from `LockupFactory::create`, or restrict it so only the factory's `foundation_account_id` may specify a non-default whitelist account. At minimum, hardcode/lock the `staking_pool_whitelist_account_id` in every lockup created by the factory to the factory's own canonical whitelist account, eliminating any caller-controlled trust root for the "approved staking pool" concept.

### Proof of Concept
1. Attacker deploys `EvilWhitelist` contract exposing `is_whitelisted(_: AccountId) -> bool { true }`.
2. Attacker calls `lockup-factory::create(owner_account_id: <attacker>, ..., whitelist_account_id: Some(EvilWhitelist))`, attaching the minimum required deposit; this creates a new lockup contract owned by the attacker whose `staking_pool_whitelist_account_id` is `EvilWhitelist` (see `lockup-factory/src/lib.rs:107-165`).
3. Attacker deploys `EvilPool`, a staking-pool-like contract they fully control (e.g., one that simply lets them withdraw on demand).
4. Attacker, as the lockup owner, calls `select_staking_pool(EvilPool)` on their own lockup contract. The lockup calls `EvilWhitelist::is_whitelisted(EvilPool)`, which returns `true` (see `lockup/src/owner.rs:12-41`).
5. Attacker calls `deposit_and_stake` to move locked/unvested NEAR from the lockup into `EvilPool` (see `lockup/src/owner.rs:122-166`), then withdraws it from `EvilPool` at will — bypassing the lockup/vesting release schedule entirely.

### Citations

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

**File:** lockup/src/lib.rs (L190-198)
```rust
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

**File:** whitelist/README.md (L1-20)
```markdown
# Whitelist contract for staking pools

The purpose of this contract is to maintain the whitelist of the staking pool contracts account IDs that are approved
by NEAR Foundation.

In order for the lockup contracts to be able delegate to a staking pool, the staking pool should faithfully implement the spec.
The staking pool should guarantee that the delegated tokens can not be lost or locked, such as the lockup contract should be
able to recover delegated tokens back to the lockup from a staking pool. In order to enforce this, only approved (whitelisted)
accounts of staking pool contracts can receive delegated tokens from lockup contracts.

If NEAR Foundation has to approve every single staking pool account it might lead to a bottleneck and centralization
To address this NEAR Foundation can whitelist the account IDs of staking pool factory contracts.

The whitelisted staking pool factory contract will be able to whitelist accounts of staking pool contracts.
A factory contract creates and initializes a staking pool contract in a secure and permissionless way.
This allows anyone on the network to be able to create a staking pool contract for themselves without needing approval from the NEAR
Foundation. This is important to maintain the decentralization of the decision making and network governance.

To be able to address mistakes, NEAR Foundation has the ability to remove staking pools and staking pool factories from the whitelists.

```
