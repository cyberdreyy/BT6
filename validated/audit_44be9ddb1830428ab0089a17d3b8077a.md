### Title
`LockupFactory::create` lets the caller supply an arbitrary staking-pool whitelist contract, letting an owner delegate unvested/locked tokens to a self-controlled "pool" - (File: `lockup-factory/src/lib.rs`)

### Summary
The lockup factory's `create` function accepts a caller-supplied `whitelist_account_id` parameter and, if present, uses it *verbatim* as the `staking_pool_whitelist_account_id` baked into the newly deployed lockup contract, instead of forcing the factory's own vetted `self.whitelist_account_id`. [1](#0-0) 

### Finding Description
The whole security model of the staking-pool whitelist system is documented in `whitelist/README.md`: only accounts approved by the NEAR Foundation (directly, or via an approved factory) can receive delegated tokens from a lockup contract, because the whitelist is what guarantees delegated tokens "can not be lost or locked" and are recoverable by the lockup/foundation. [2](#0-1) 

The lockup contract enforces this by checking `is_whitelisted` on `self.staking_pool_whitelist_account_id` before letting the owner select a staking pool: [3](#0-2) [4](#0-3) 

This binding only holds if `staking_pool_whitelist_account_id` is the trustworthy, foundation-controlled whitelist contract. However, `LockupFactory::create` lets the caller pass their own `whitelist_account_id`, which is used instead of the factory's `self.whitelist_account_id` whenever supplied: [5](#0-4) 

This value is written straight into `LockupArgs.staking_pool_whitelist_account_id` and passed to the deployed lockup contract's `new`, becoming the permanent, unauthenticated trust anchor for that lockup instance's staking decisions: [6](#0-5) [7](#0-6) 

Because `create` is `#[payable]` and callable by anyone (the caller only needs to attach `MIN_ATTACHED_BALANCE` and specify themselves as `owner_account_id`), the account that becomes lockup owner (or, in the vesting case, the account creating the vesting schedule) can deploy a trivial contract of their own that always answers `is_whitelisted -> true`, then pass that account as `whitelist_account_id`. The lockup contract then trusts it unconditionally: the binding "staking pool selected == account vetted by NEAR Foundation" is broken because "vetted by NEAR Foundation" is replaced by "vetted by attacker-controlled contract."

### Impact Explanation
Once the owner's fake whitelist always returns `true`, `select_staking_pool` will accept any account (including one written by the same attacker) as the "staking pool", and the lockup contract will freely `deposit_and_stake` locked/unvested tokens to it via `deposit_to_staking_pool` / `stake`. For plain lockups this merely lets a user avoid a foundation-curated pool, but for **vesting lockups** (`vesting_schedule.is_some()`), it directly undermines the "recoverable delegated tokens" guarantee that the whitelist exists to enforce: the NEAR Foundation relies on being able to terminate vesting and recover the *unvested* balance even if it is staked. If the "staking pool" is attacker-controlled and simply never returns funds (or reports fabricated balances) on unstake/withdraw, the foundation's termination/clawback flow (`lockup/src/foundation.rs`) can be defeated, letting the owner retain unvested tokens that should have been reclaimed. This matches the Critical impact category "a wrongly whitelisted or wrongly parameterised deployment" / "locked or unvested tokens released early."

### Likelihood Explanation
`create` is a public, permissionless, payable method; any account (including the eventual lockup owner) can invoke it and supply the optional `whitelist_account_id`. No check in `lockup-factory/src/lib.rs` restricts this parameter to the factory's own `self.whitelist_account_id`, and no check in the deployed `lockup` contract validates that `staking_pool_whitelist_account_id` matches a canonical/foundation address. The only prerequisite is deploying one extra trivial helper contract, which is well within reach of any unprivileged attacker.

### Recommendation
Remove the caller-controlled `whitelist_account_id` override (or restrict it to a small foundation-approved allow-list) in `lockup-factory/src/lib.rs::create`, and always use `self.whitelist_account_id` for lockups that carry a `vesting_schedule` (i.e., where the foundation must be able to recover unvested funds). If a per-deployment override is genuinely needed for non-vesting lockups, gate it behind a foundation-only call path rather than accepting it from the same payer who creates the lockup.

### Proof of Concept
1. Attacker deploys `evil-whitelist.attacker.near` implementing `is_whitelisted(...)-> bool { true }`.
2. Attacker calls `lockup-factory.create(owner_account_id: attacker, ..., vesting_schedule: Some(...), whitelist_account_id: Some("evil-whitelist.attacker.near"))`, attaching `MIN_ATTACHED_BALANCE`. [1](#0-0) 
3. The resulting lockup contract is deployed with `staking_pool_whitelist_account_id = "evil-whitelist.attacker.near"`. [6](#0-5) 
4. Attacker (as owner) calls `select_staking_pool("evil-pool.attacker.near")`; the cross-contract check hits the fake whitelist and returns `true`, so the pool is accepted. [3](#0-2) [4](#0-3) 
5. Attacker stakes locked/unvested tokens into `evil-pool.attacker.near`, a contract they fully control and which can refuse to return funds during the foundation's vesting termination/unstake-for-termination flow, defeating clawback.

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

**File:** lockup/src/lib.rs (L119-132)
```rust
#[near_bindgen]
#[derive(BorshDeserialize, BorshSerialize)]
pub struct LockupContract {
    /// The account ID of the owner.
    pub owner_account_id: AccountId,

    /// Information about lockup schedule and the amount.
    pub lockup_information: LockupInformation,

    /// Information about vesting including schedule or termination status.
    pub vesting_information: VestingInformation,

    /// Account ID of the staking pool whitelist contract.
    pub staking_pool_whitelist_account_id: AccountId,
```
