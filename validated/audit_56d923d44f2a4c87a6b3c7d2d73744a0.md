### Title
Unrestricted `create` in `LockupFactory` lets an attacker squat a victim's deterministic lockup address and force early/no-vesting release of tokens - ([File: lockup-factory/src/lib.rs])

### Summary
`LockupFactory::create` computes the lockup contract's account ID deterministically from `owner_account_id` and has no authorization check on the caller, mirroring the `vestFor` bug class: anyone can invoke a state-committing action "for" another account before the legitimate party does, using their own (small, fixed) deposit, and lock in unfavorable terms that the real owner cannot later override.

### Finding Description
`LockupFactory::create` is `#[payable]` and callable by any account with no whitelist/owner check: [1](#0-0) 

The target lockup account ID is derived solely from `owner_account_id`: [2](#0-1) 

Critically, `vesting_schedule`, `lockup_duration`, `release_duration`, and `lockup_timestamp` are all attacker-supplied parameters, and `foundation_account_id` is only set if a vesting schedule is provided: [3](#0-2) 

An attacker can call `create` for a `owner_account_id` belonging to a real employee/beneficiary who has *not yet* been onboarded by the legitimate funder (e.g., NEAR Foundation), attaching only `MIN_ATTACHED_BALANCE` (3.5 NEAR) and setting `vesting_schedule: None`, `lockup_duration: 0`, `release_duration: None`. Since `NEAR account creation is first-come-first-served`, this permanently occupies the deterministic address for that owner. When the legitimate funder later calls `create` for the same `owner_account_id` with a real vesting schedule, `Promise::new(lockup_account_id).create_account()` fails because the account already exists, and the deposit is refunded via `on_lockup_create`: [4](#0-3) 

The legitimate funder is then forced to either abandon proper vesting for that owner or fund the attacker-created contract directly via a plain transfer. Because the attacker's contract has no vesting schedule and `lockup_duration = 0` while `transfers_information` defaults to `TransfersEnabled` at the already-past `TRANSFERS_STARTED` timestamp: [5](#0-4) 

`get_locked_amount` computes `lockup_timestamp = max(transfers_timestamp + lockup_duration, lockup_timestamp)`. With `lockup_duration = 0` and no `lockup_timestamp`, `lockup_timestamp` resolves to the already-elapsed `TRANSFERS_STARTED`, so `block_timestamp` is already past it; with `release_duration = None`, `unreleased_amount = 0`, and with `vesting_information = VestingInformation::None`, `unvested_amount = 0`: [6](#0-5) 

Consequently `get_locked_amount()` returns `0` for any funds subsequently added to the account, meaning any amount transferred into this squatted contract is immediately withdrawable by the owner with no lockup and no vesting at all — the intended equality `locked/unvested balance == schedule-derived amount` is broken to `0` regardless of the funder's intent.

### Impact Explanation
This breaks the custody/schedule binding the same way as the referenced `vestFor` finding: an unprivileged, unauthenticated caller commits another account into an immutable schedule state (here, "no schedule at all") before the legitimate party can, and the legitimate party has no way to correct it because the deterministic account already exists. Per the stated impact criteria, this results in "locked or unvested tokens released early" (and, more broadly, "a wrongly parameterised deployment") — a Critical-class impact, since any tokens the real employer subsequently sends to that squatted lockup account for that beneficiary have no lock or vesting enforcement at all.

### Likelihood Explanation
Likelihood is moderate-to-high in the intended deployment flow: lockup accounts are commonly provisioned for known employee/beneficiary account names before funding, and `MIN_ATTACHED_BALANCE` (3.5 NEAR) is a small, fixed, and non-prohibitive cost for an attacker to grief/squat any target account name they can predict (e.g., from public onboarding communications) ahead of the legitimate `create` call.

### Recommendation
Restrict `LockupFactory::create` to a whitelist of authorized callers (e.g., the foundation or approved HR/ops accounts), or otherwise decouple the lockup account's derivation/creation from an unauthenticated actor's ability to fix its vesting parameters — e.g., require the vesting schedule/lockup parameters to be supplied only by an authorized principal, or use a commit-reveal/reservation scheme so the intended owner's authorized funder is guaranteed first-mover rights on the deterministic address.

### Proof of Concept
1. Attacker observes/learns a future beneficiary account ID `alice.near` intended to receive a vesting lockup from the foundation.
2. Before the foundation acts, attacker calls:
   `near call lockup-factory create '{"owner_account_id":"alice.near","lockup_duration":"0","vesting_schedule":null,"release_duration":null}' --accountId attacker.near --amount 3.5`
   This deploys `<hash(alice.near)>.lockup-factory` with `owner_account_id = alice.near`, no vesting schedule, `lockup_duration = 0`.
3. Foundation later calls the same `create` for `alice.near` with the real vesting schedule; `create_account()` fails (account already exists) and the deposit is refunded to the foundation via `on_lockup_create` [7](#0-6) .
4. Foundation, needing to fund `alice.near`'s lockup, instead sends NEAR directly to the already-deployed (attacker-parameterized) lockup contract.
5. `alice.near` (the owner) can immediately call `transfer`/withdraw the full amount since `get_locked_amount()` returns `0` [6](#0-5) , defeating the intended multi-year vesting schedule entirely.

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

**File:** lockup-factory/src/lib.rs (L135-153)
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
```

**File:** lockup-factory/src/lib.rs (L168-198)
```rust
    /// Callback after a lockup was created.
    /// Returns the promise if the lockup creation succeeded.
    /// Otherwise refunds the attached deposit and returns `false`.
    pub fn on_lockup_create(
        &mut self,
        lockup_account_id: AccountId,
        attached_deposit: U128,
        predecessor_account_id: AccountId,
    ) -> bool {
        assert_self();

        let lockup_account_created = is_promise_success();

        if lockup_account_created {
            env::log(
                format!("The lockup contract {} was successfully created.", lockup_account_id)
                    .as_bytes(),
            );
            true
        } else {
            env::log(
                format!(
                    "The lockup {} creation has failed. Returning attached deposit of {} to {}",
                    lockup_account_id, attached_deposit.0, predecessor_account_id
                )
                    .as_bytes(),
            );
            Promise::new(predecessor_account_id).transfer(attached_deposit.0);
            false
        }
    }
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
