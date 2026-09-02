## Analysis

This confirms the analog. The `select_staking_pool` function in `lockup/src/owner.rs` checks the staking pool against `self.staking_pool_whitelist_account_id`, which is a value hardcoded into the lockup contract at creation time via the `staking_pool_whitelist_account_id` field in `LockupArgs`. [1](#0-0)  The invariant the whole system relies on is that this field always points to the NEAR Foundation's canonical whitelist contract, since the whitelist is what enforces "the delegated tokens can not be lost or locked" per the staking-pool spec. [2](#0-1) 

However, `LockupFactory::create()` accepts an optional, caller-supplied `whitelist_account_id` parameter and uses it directly instead of the factory's configured default whenever provided: [3](#0-2) 

### Title
Lockup Factory `create()` Lets the Caller Override the Trusted Staking-Pool Whitelist - (File: lockup-factory/src/lib.rs)

### Summary
`LockupFactory::create()` takes an attacker-controllable `whitelist_account_id` argument that overrides the factory's canonical `self.whitelist_account_id` (the NEAR Foundation whitelist) when building the new lockup contract's `staking_pool_whitelist_account_id`. This breaks the custody/trust binding that `select_staking_pool` in the lockup contract relies on: "staking pool passed `is_whitelisted` check" is supposed to mean "vetted by the Foundation's whitelist," but with a substituted whitelist account it can mean "vetted by whatever contract the lockup owner deployed."

### Finding Description
`select_staking_pool` in the lockup contract defers all safety judgement about a staking pool to `ext_whitelist::is_whitelisted` called against `self.staking_pool_whitelist_account_id`. [4](#0-3)  That field is set once, at contract initialization, from the `staking_pool_whitelist_account_id` argument passed to `new`, which for factory-created lockups comes from `LockupArgs.staking_pool_whitelist_account_id` in `lockup-factory/src/lib.rs`. [5](#0-4) 

The factory's `create()` function computes this value as:
```
let staking_pool_whitelist_account_id = if let Some(account_id) = whitelist_account_id {
    account_id.into()
} else {
    self.whitelist_account_id.clone()
};
``` [6](#0-5) 

Any caller of `create()` (the "funding_account" per the README example) can supply an arbitrary `whitelist_account_id`, e.g. their own deployed account implementing a fake `is_whitelisted` that always returns `true`. [7](#0-6)  The lockup contract itself never re-validates that the whitelist passed at construction is the Foundation's whitelist — this is enforced nowhere else in the protocol; the whitelist README explicitly states the entire safety guarantee ("delegated tokens can not be lost or locked ... only approved (whitelisted) accounts of staking pool contracts can receive delegated tokens from lockup contracts") depends on this binding. [8](#0-7) 

### Impact Explanation
With a substituted whitelist, the lockup owner can get `select_staking_pool` to accept an arbitrary, owner-controlled "staking pool" contract that does not implement the real staking-pool guarantees (e.g. it can report inflated/fabricated `get_account_unstaked_balance` and `get_account_total_balance` values). The lockup owner then calls `deposit_and_stake`, followed by `unstake`/`withdraw_from_staking_pool`, whose callbacks (`on_get_account_unstaked_balance_to_withdraw_by_owner` → `withdraw`) blindly trust whatever the "staking pool" account reports back as the unstaked balance and transfer that amount out of the fake pool to the lockup, then ultimately to the owner. [9](#0-8)  This lets the owner extract locked/unvested NEAR before the lockup schedule would normally release it — an early release of locked funds — meeting the Critical impact bar ("locked or unvested tokens released early" / "a wrongly whitelisted or wrongly parameterised deployment").

### Likelihood Explanation
Any unprivileged user can call `create()` on the lockup factory and supply their own `whitelist_account_id`; no owner, foundation, or multisig privilege is required — `create()` is a public, `#[payable]` method reachable by anyone funding a lockup. [10](#0-9)  The only additional step is deploying a small malicious "whitelist" and "staking pool" contract, both of which are ordinary account deployments with no special permission.

### Recommendation
Remove the caller-supplied `whitelist_account_id` parameter from `create()` and always use the factory's own `self.whitelist_account_id` (the value set at factory `new()` initialization by the Foundation/master account) when constructing `LockupArgs`, so that every lockup produced by the factory is bound to the single, trusted whitelist contract.

### Proof of Concept
1. Attacker deploys `evil-whitelist` account with an `is_whitelisted` method that always returns `true`, and `evil-pool` account with a fake staking-pool interface (`get_account_unstaked_balance`, `withdraw`, etc.) that reports/pays out balances the attacker controls.
2. Attacker calls `lockup-factory.create({owner_account_id: attacker, lockup_duration: ..., whitelist_account_id: "evil-whitelist"})`, funding the new lockup with locked NEAR. [11](#0-10) 
3. As owner of the new lockup, attacker calls `select_staking_pool("evil-pool")`; the lockup calls `evil-whitelist.is_whitelisted("evil-pool")`, which returns `true`, so `evil-pool` is accepted. [1](#0-0) 
4. Attacker calls `deposit_and_stake` to move locked NEAR to `evil-pool`, then triggers `withdraw_all_from_staking_pool`; the lockup queries `evil-pool.get_account_unstaked_balance`, which returns an attacker-chosen value, and the lockup issues a `withdraw` promise for that amount back to itself, crediting the owner's liquid/known balance. [9](#0-8) 
5. Locked/unvested NEAR has now been released to the owner ahead of the lockup schedule, bypassing the intended time-lock — insolvency/early release relative to the schedule the lockup was supposed to enforce.

### Citations

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

**File:** lockup/src/owner_callbacks.rs (L296-339)
```rust
    /// Called after the request to get the current unstaked balance to withdraw everything by th
    /// owner.
    pub fn on_get_account_unstaked_balance_to_withdraw_by_owner(
        &mut self,
        #[callback] unstaked_balance: WrappedBalance,
    ) -> PromiseOrValue<bool> {
        assert_self();
        if unstaked_balance.0 > 0 {
            // Need to withdraw
            env::log(
                format!(
                    "Withdrawing {} from the staking pool @{}",
                    unstaked_balance.0,
                    self.staking_information
                        .as_ref()
                        .unwrap()
                        .staking_pool_account_id
                )
                .as_bytes(),
            );

            ext_staking_pool::withdraw(
                unstaked_balance,
                &self
                    .staking_information
                    .as_ref()
                    .unwrap()
                    .staking_pool_account_id,
                NO_DEPOSIT,
                gas::staking_pool::WITHDRAW,
            )
            .then(ext_self_owner::on_staking_pool_withdraw(
                unstaked_balance,
                &env::current_account_id(),
                NO_DEPOSIT,
                gas::owner_callbacks::ON_STAKING_POOL_WITHDRAW,
            ))
            .into()
        } else {
            env::log(b"No unstaked balance on the staking pool to withdraw");
            self.set_staking_pool_status(TransactionStatus::Idle);
            PromiseOrValue::Value(true)
        }
    }
```
