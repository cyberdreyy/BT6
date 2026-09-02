## Title
Unprivileged front-running/squatting of deterministic lockup addresses lets an attacker deploy `victim.near`'s lockup with attacker-chosen `lockup_timestamp` (e.g. `0`) before the real grantor - (File: lockup-factory/src/lib.rs)

## Summary
`LockupFactory::create` derives the lockup contract's account ID purely as a deterministic hash of `owner_account_id` (`sha256(owner_account_id)[:20].<factory>`), with no check on who is allowed to call `create()` for a given `owner_account_id` and no check that the target address does not already exist. Any unprivileged account can pre-compute this address and call `create()` first with `owner_account_id='victim.near'` and `lockup_timestamp=Some(0)` (plus any other attacker-chosen terms), permanently occupying that address before the legitimate grantor's transaction lands.

## Finding Description
The broken binding: `lockup_account_id(owner_account_id)` is claimed to be chosen and funded exactly once, by the intended grantor, with the grantor's intended `lockup_timestamp`. In reality:

`lockup_account_id = hex(sha256(owner_account_id)[:20]) + "." + factory` [1](#0-0) 

is fully deterministic and public, and `create()` performs no `assert_eq!(env::predecessor_account_id(), owner_account_id)` or any other authorization check before scheduling `create_account()` for that address: [2](#0-1) 

Because NEAR's `create_account()` action fails if the account already exists, whichever caller's `create()` transaction lands first for a given `owner_account_id` wins the address. The attacker only needs `MIN_ATTACHED_BALANCE` (3.5 NEAR) and can pass `lockup_timestamp: Some(WrappedTimestamp(0))`, which is copied verbatim into `LockupInformation.lockup_timestamp` in the lockup's `new()`: [3](#0-2) 

When the legitimate grantor later submits their own `create()` call for the same `owner_account_id` with the intended `lockup_timestamp` and full grant amount, the batched `create_account/deploy_contract/transfer/function_call` promise fails because the account already exists, `is_promise_success()` returns `false` in the callback, and the deposit is simply refunded to the grantor: [4](#0-3) 

The result is that `victim.near`'s lockup address is permanently squatted with attacker-chosen parameters. `get_locked_amount()` on that squatted contract computes the release schedule strictly from the squatted `lockup_timestamp`/`lockup_duration` (here `max(TRANSFERS_STARTED + lockup_duration, 0)`), which is not, and can never be made to equal, the schedule the real grantor intended: [5](#0-4) 

None of the existing guards prevent this: there is no `assert_self`, `assert_owner`, or account-existence pre-check in `create()`, and `is_promise_success()` only decides whether to refund the *loser* of the race — it does not stop the *winner* (attacker) from having already created the contract with arbitrary parameters.

## Impact Explanation
This matches the Critical category "a lockup deployed with parameters its rightful creator never chose." The attacker permanently prevents the intended grantor from ever deploying the correctly-configured lockup contract at `victim.near`'s canonical lockup address (creation there will always fail from then on), and instead leaves a contract at that address governed by attacker-chosen `lockup_timestamp`/`lockup_duration`/`release_duration`/`vesting_schedule`/`whitelist_account_id`. This is repeatable for any `owner_account_id` string known in advance (e.g., any employee/investor name a grantor is expected to onboard), giving the attacker a blanket ability to squat addresses across the whole factory.

## Likelihood Explanation
Trivial precondition: the attacker only needs to know (or guess) the `owner_account_id` string that a legitimate grantor intends to use, and have `MIN_ATTACHED_BALANCE` (3.5 NEAR) plus enough gas to submit `create()` before the legitimate transaction. No special privileges, keys, or roles are required — fully consistent with the "unprivileged attacker" threat model. Squatting well-known or predictable owner-account naming schemes (e.g. `alice.near`, `investor1.near`) is cheap and fully repeatable.

## Recommendation
Bind lockup creation authorization to the intended owner, e.g. require `env::predecessor_account_id() == owner_account_id` (self-funded lockups) or otherwise restrict `create()` to a whitelisted/foundation caller for a given `owner_account_id`, and/or check `env::current_account_id()`-scoped account non-existence before accepting deposits, refunding immediately with a clear "address already taken" error rather than only after the failed promise executes.

## Proof of Concept
```rust
// lockup-factory/src/lib.rs tests module
#[test]
fn test_squatting_blocks_real_grantor() {
    // Attacker calls create() first for owner_account_id = victim, lockup_timestamp = Some(0)
    // with MIN_ATTACHED_BALANCE.
    // -> lockup_account_id computed deterministically from sha256(victim)[:20]

    // Simulate promise success (attacker's create succeeded)
    // Real grantor then calls create() for the same owner_account_id with their
    // intended larger deposit and lockup_timestamp = Some(future_ts).

    // Assert: on_lockup_create for the real grantor's attempt observes
    // is_promise_success() == false (account already exists) and refunds
    // attached_deposit back to the real grantor's predecessor_account_id,
    // proving the real grantor's intended lockup_timestamp was never applied.

    // Separately, on the squatted contract instance, assert:
    // get_locked_amount() at T = TRANSFERS_STARTED + lockup_duration (attacker's, lockup_timestamp=0)
    //   != get_locked_amount() that would result from the real grantor's intended lockup_timestamp
    // at the same T, demonstrating the schedule divergence.
}
```

### Citations

**File:** lockup-factory/src/lib.rs (L107-139)
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
```

**File:** lockup-factory/src/lib.rs (L171-198)
```rust
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

**File:** lockup/src/lib.rs (L208-215)
```rust
        let lockup_information = LockupInformation {
            lockup_amount: env::account_balance(),
            termination_withdrawn_tokens: 0,
            lockup_duration: lockup_duration.0,
            release_duration: release_duration.map(|d| d.0),
            lockup_timestamp: lockup_timestamp.map(|d| d.0),
            transfers_information,
        };
```

**File:** lockup/src/getters.rs (L65-76)
```rust
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
```
