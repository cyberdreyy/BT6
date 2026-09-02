### Title
Owner-supplied `whitelist_account_id` in `LockupFactory::create` lets the lockup owner substitute a fake staking-pool whitelist, defeating the pool-vetting guarantee and enabling early release of locked/unvested NEAR - (File: `lockup-factory/src/lib.rs`, `lockup/src/owner.rs`, `lockup/src/owner_callbacks.rs`, `lockup/src/getters.rs`)

### Summary
`LockupFactory::create` lets the account paying for lockup creation override the factory's Foundation-controlled staking-pool whitelist with an **arbitrary account ID** via the optional `whitelist_account_id` parameter. [1](#0-0)  The deployed `LockupContract` then trusts whatever account is stored in `staking_pool_whitelist_account_id` as the source of truth when the owner calls `select_staking_pool`, and blindly accepts the `is_whitelisted` result from that account. [2](#0-1) [3](#0-2)  This is the exact analog to the external report's root cause: a security-critical "was this contract really vetted by the linked factory?" check is satisfiable with an attacker/owner-supplied contract instead of the legitimate registry.

### Finding Description
The whitelist contract's entire purpose is to guarantee that a staking pool "faithfully implement[s] the spec" so that "the lockup contract should be able to recover delegated tokens back to the lockup from a staking pool." [4](#0-3)  The `LockupContract::select_staking_pool` owner method enforces this by querying `self.staking_pool_whitelist_account_id` before accepting a staking pool selection: [2](#0-1) 

`on_whitelist_is_whitelisted` then unconditionally trusts the boolean returned by that account and commits the staking pool selection: [3](#0-2) 

The binding this is meant to enforce is: `staking_pool_account_id used by owner == an account vetted by the Foundation-controlled whitelist/whitelisted-factory chain`. That equality only holds if `staking_pool_whitelist_account_id` itself is trustworthy. But `LockupFactory::create` lets the caller supply their own `whitelist_account_id`, silently overriding the factory's own trusted default: [1](#0-0) 

Since `create` has no restriction on `predecessor_account_id` and is payable by anyone, any unprivileged account can deploy their own lockup, pointing `staking_pool_whitelist_account_id` at a trivial contract they control that always returns `true` from `is_whitelisted`. This breaks the equality that `select_staking_pool` is supposed to enforce: instead of "vetted by Foundation," the check becomes "vetted by whoever created the lockup."

Once a self-controlled fake "staking pool" is selected this way, the lockup's staking-balance accounting is fed entirely from callbacks that trust return values from that fake pool (e.g. the `deposit_amount` recorded from `on_staking_pool_deposit`/`on_staking_pool_deposit_and_stake`, and the refreshed total balance from the `get_account_total_balance` callback declared via `ext_staking_pool`). These feed directly into the getters that gate how much NEAR the owner can withdraw/transfer: [5](#0-4) 

`get_owners_balance`/`get_liquid_owners_balance` compute the withdrawable amount as `account_balance + known_deposited_balance − locked_amount`, where `locked_amount` is exactly the lockup/vesting schedule's un-released portion. [6](#0-5)  Because `known_deposited_balance` is sourced from a pool contract that the owner fully controls (via the substituted fake whitelist), the owner can report an arbitrarily inflated "staked balance" from the fake pool, inflating `get_liquid_owners_balance` beyond what the true lockup/vesting schedule allows and beyond real NEAR ever deposited to a legitimate pool — breaking the binding "releasable amount == schedule's contractually unlocked amount."

### Impact Explanation
This falls under the Critical category explicitly listed in scope: "locked or unvested tokens released early" and "a wrongly whitelisted or wrongly parameterised deployment." The schedule binding (`locked_amount` from `lockup_information`/`vesting_information`) is supposed to be an inviolable ceiling on what `get_owners_balance`/`get_liquid_owners_balance` can expose for withdrawal. By substituting the Foundation's whitelist with a self-controlled one at lockup-creation time, the owner defeats the one external control (`is_whitelisted`) that is supposed to guarantee the selected pool can't be used to fabricate balances, letting them report and subsequently withdraw NEAR that the vesting/lockup schedule says should still be locked (and, in the vesting case, potentially subject to Foundation termination/clawback).

### Likelihood Explanation
Anyone can call `LockupFactory::create` and pay the minimum attached deposit; the vulnerable parameter (`whitelist_account_id`) is an explicit, unauthenticated, user-supplied argument in the public API, not a hidden or hard-to-reach code path. [7](#0-6)  No privileged role (foundation, multisig, owner-of-someone-else's-funds) is required to trigger the substitution — the only "privilege" involved is the caller being the owner of the very lockup they create, and the impact (early release of the locked/vesting-governed balance) is exactly the custody binding this component exists to protect.

### Recommendation
- In `LockupFactory::create`, remove the ability for the caller to specify an arbitrary `whitelist_account_id`; always use the factory's configured, Foundation-controlled whitelist account (or restrict overrides to a small foundation-approved allowlist).
- In `LockupContract`, consider hard-coding/pinning the whitelist account ID at genesis to a value that cannot be independently chosen per-deployment, or require any custom whitelist to itself be vetted (e.g., only allow whitelist accounts that are known/hard-coded, not arbitrary caller input).
- Do not trust externally reported staking-pool balances (`get_account_total_balance` callback) as unconditionally additive to `known_deposited_balance`; cross-check against amounts actually deposited/withdrawn, and/or require the underlying account's real NEAR balance movement to corroborate reported figures.

### Proof of Concept
1. Attacker deploys a trivial "whitelist" contract exposing `is_whitelisted(staking_pool_account_id) -> bool { true }`.
2. Attacker deploys a trivial "staking pool" contract implementing the `deposit`/`deposit_and_stake`/`get_account_total_balance` interface, where `get_account_total_balance` can be made to return an arbitrary inflated value.
3. Attacker calls `LockupFactory::create(owner_account_id: <attacker>, ..., whitelist_account_id: Some(<attacker's fake whitelist>))`, attaching the minimum deposit; a lockup contract is deployed with `staking_pool_whitelist_account_id` pointed at the fake whitelist. [8](#0-7) 
4. Attacker (as owner) calls `select_staking_pool(<attacker's fake pool>)`; the callback trusts the fake whitelist's `true` response and commits the selection. [2](#0-1) [3](#0-2) 
5. Attacker deposits a small real amount to the fake pool, then calls `refresh_staking_pool_balance`; the fake pool's `get_account_total_balance` returns an inflated value that is stored as `known_deposited_balance`.
6. `get_owners_balance`/`get_liquid_owners_balance` now report a balance far exceeding the true unlocked/vested portion, and the attacker withdraws/transfers real NEAR from the lockup account beyond the schedule's allowed ceiling. [5](#0-4)

### Citations

**File:** lockup-factory/src/lib.rs (L107-166)
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
                    .unwrap(),
                NO_DEPOSIT,
                gas::LOCKUP_NEW,
            )
            .then(ext_self::on_lockup_create(
                lockup_account_id,
                env::attached_deposit().into(),
                env::predecessor_account_id(),
                &env::current_account_id(),
                NO_DEPOSIT,
                gas::CALLBACK,
            ))
    }
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

**File:** whitelist/README.md (L6-9)
```markdown
In order for the lockup contracts to be able delegate to a staking pool, the staking pool should faithfully implement the spec.
The staking pool should guarantee that the delegated tokens can not be lost or locked, such as the lockup contract should be
able to recover delegated tokens back to the lockup from a staking pool. In order to enforce this, only approved (whitelisted)
accounts of staking pool contracts can receive delegated tokens from lockup contracts.
```

**File:** lockup/src/getters.rs (L64-113)
```rust
    /// Returns the amount of tokens that are locked in the account due to lockup or vesting.
    pub fn get_locked_amount(&self) -> WrappedBalance {
        let lockup_amount = self.lockup_information.lockup_amount;
        if let TransfersInformation::TransfersEnabled {
            transfers_timestamp,
        } = &self.lockup_information.transfers_information
        {
            let lockup_timestamp = std::cmp::max(
                transfers_timestamp
                    .0
                    .saturating_add(self.lockup_information.lockup_duration),
                self.lockup_information.lockup_timestamp.unwrap_or(0),
            );
            let block_timestamp = env::block_timestamp();
            if lockup_timestamp <= block_timestamp {
                let unreleased_amount =
                    if let &Some(release_duration) = &self.lockup_information.release_duration {
                        let end_timestamp = lockup_timestamp.saturating_add(release_duration);
                        if block_timestamp >= end_timestamp {
                            // Everything is released
                            0
                        } else {
                            let time_left = U256::from(end_timestamp - block_timestamp);
                            let unreleased_amount = U256::from(lockup_amount) * time_left
                                / U256::from(release_duration);
                            // The unreleased amount can't be larger than lockup_amount because the
                            // time_left is smaller than total_time.
                            unreleased_amount.as_u128()
                        }
                    } else {
                        0
                    };

                let unvested_amount = match &self.vesting_information {
                    VestingInformation::VestingSchedule(vs) => self.get_unvested_amount(vs.clone()),
                    VestingInformation::Terminating(terminating) => terminating.unvested_amount,
                    // Vesting is private, so we can assume the vesting started before lockup date.
                    _ => U128(0),
                };
                return std::cmp::max(
                    unreleased_amount
                        .saturating_sub(self.lockup_information.termination_withdrawn_tokens),
                    unvested_amount.0,
                )
                .into();
            }
        }
        // The entire balance is still locked before the lockup timestamp.
        (lockup_amount - self.lockup_information.termination_withdrawn_tokens).into()
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
