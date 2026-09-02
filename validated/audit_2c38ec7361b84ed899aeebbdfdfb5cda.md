### Title
Unprivileged funder can bind a victim's lockup contract to an attacker-controlled staking-pool whitelist, enabling theft of delegated NEAR - (File: lockup-factory/src/lib.rs)

### Summary
`LockupFactory::create` lets **any caller**, not just the future owner, choose the `staking_pool_whitelist_account_id` that gets permanently baked into a newly deployed lockup contract for an arbitrary `owner_account_id`. Because the lockup contract never exposes a way to change this whitelist after `new()`, a funder who deploys (and pays for) a lockup on behalf of a victim can silently substitute a malicious whitelist contract. The victim owner, trusting the "whitelisted" status check before delegating funds, can then be tricked into staking their locked NEAR with an attacker-controlled fake staking pool.

### Finding Description
`create()` accepts an optional `whitelist_account_id` parameter supplied by the caller (the funder), defaulting to the factory's configured whitelist only if omitted: [1](#0-0) 

This value is passed straight into the lockup's `new()` constructor as `staking_pool_whitelist_account_id` and stored immutably: [2](#0-1) 

No owner method exists to change `staking_pool_whitelist_account_id` afterward — inspecting all owner-callable methods shows only `select_staking_pool`, `unselect_staking_pool`, staking operations, `transfer`, and `add_full_access_key`, none of which touch this field: [3](#0-2) 

When the owner later wants to delegate their locked tokens, `select_staking_pool` performs exactly one trust check — a cross-contract call to `is_whitelisted` on whichever account is stored in `staking_pool_whitelist_account_id`: [4](#0-3) 

Because the funder — not the foundation and not necessarily the owner — chooses this whitelist account at deployment time, and the owner has no way to inspect or override it before using `select_staking_pool`, the binding "the whitelist consulted == the NEAR Foundation's staking-pool whitelist" can be broken by an unprivileged attacker at deployment time.

### Impact Explanation
If an attacker calls `create()` for a victim's `owner_account_id`, funding the lockup themselves and supplying a `whitelist_account_id` pointing to an attacker-deployed contract that always returns `true` from `is_whitelisted`, then:
- The victim owner, unaware the whitelist was tampered with, calls `select_staking_pool` with an attacker-controlled "staking pool" account.
- The malicious whitelist confirms it as whitelisted, bypassing the legitimate NEAR Foundation whitelist entirely.
- The owner then calls `deposit_and_stake`/`stake`, transferring locked NEAR to the attacker's fake staking pool contract, which is under no obligation (and has no incentive) to ever return the funds via `unstake`/`withdraw`.

This is a "wrongly parameterised deployment" leading to NEAR being moved to and retained by a party not entitled to it — a Critical-severity outcome per the custody-binding classes in scope (trusted whitelist vs. the arguments trust was actually granted for).

### Likelihood Explanation
The lockup-factory intentionally allows funding by any account for any owner (per its README: "Lockups can be funded from any account", "No need to have access to the foundation keys to create lockup"). The `whitelist_account_id` override parameter is a documented, first-class feature of `create()`, not a misuse of an undocumented code path, and requires no privileged role, victim key, or redeploy by the victim — only that the owner later calls `select_staking_pool` without independently verifying the whitelist account bound to their lockup (which the contract offers no straightforward way to inspect).

### Recommendation
Remove the caller-suppliable `whitelist_account_id` override in `LockupFactory::create`, or restrict it so only the `owner_account_id` (or the foundation) can specify a non-default whitelist. Additionally, expose a view method on the lockup contract to surface `staking_pool_whitelist_account_id` so owners can verify it before staking, and/or hardcode the factory's own configured whitelist for all lockups it deploys.

### Proof of Concept
1. Attacker deploys `EvilWhitelist` contract implementing `is_whitelisted(_) -> true` for every input.
2. Attacker calls `lockup-factory.create({owner_account_id: "victim", lockup_duration: ..., whitelist_account_id: "evilwhitelist.attacker"})` and pays the `MIN_ATTACHED_BALANCE` (funder role, no privilege needed) — per `create()` at [1](#0-0) .
3. The lockup contract is deployed with `staking_pool_whitelist_account_id = "evilwhitelist.attacker"` baked in permanently, per `LockupContract::new` at [2](#0-1) .
4. Victim ("owner_account_id") later calls `select_staking_pool("fakepool.attacker")`; the contract queries `is_whitelisted` on `evilwhitelist.attacker`, which returns `true`, per [4](#0-3) .
5. Victim calls `deposit_and_stake`, transferring locked NEAR to `fakepool.attacker`, which never allows withdrawal — funds are effectively stolen/frozen permanently.

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

**File:** lockup/src/lib.rs (L180-243)
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
        if let TransfersInformation::TransfersDisabled {
            transfer_poll_account_id,
        } = &transfers_information
        {
            assert!(
                env::is_valid_account_id(transfer_poll_account_id.as_bytes()),
                "The transfer poll account ID is invalid"
            );
        }
        let lockup_information = LockupInformation {
            lockup_amount: env::account_balance(),
            termination_withdrawn_tokens: 0,
            lockup_duration: lockup_duration.0,
            release_duration: release_duration.map(|d| d.0),
            lockup_timestamp: lockup_timestamp.map(|d| d.0),
            transfers_information,
        };
        let vesting_information = match vesting_schedule {
            None => {
                assert!(
                    foundation_account_id.is_none(),
                    "Foundation account can't be added without vesting schedule"
                );
                VestingInformation::None
            }
            Some(VestingScheduleOrHash::VestingHash(hash)) => VestingInformation::VestingHash(hash),
            Some(VestingScheduleOrHash::VestingSchedule(vs)) => {
                VestingInformation::VestingSchedule(vs)
            }
        };
        assert!(
            vesting_information == VestingInformation::None ||
                env::is_valid_account_id(foundation_account_id.as_ref().unwrap().as_bytes()),
            "Foundation account should be added for vesting schedule"
        );

        Self {
            owner_account_id,
            lockup_information,
            vesting_information,
            staking_information: None,
            staking_pool_whitelist_account_id,
            foundation_account_id,
        }
    }
```

**File:** lockup/src/owner.rs (L1-41)
```rust
use crate::*;
use near_sdk::{near_bindgen, AccountId, Promise, PublicKey};

#[near_bindgen]
impl LockupContract {
    /// OWNER'S METHOD
    ///
    /// Requires 75 TGas (3 * BASE_GAS)
    ///
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
