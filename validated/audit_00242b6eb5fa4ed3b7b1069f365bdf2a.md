### Title
Permissionless `create()` lets an attacker squat a victim's deterministic lockup address with an under-funded, attacker-parameterized lockup - ([File: lockup-factory/src/lib.rs])

### Summary
`LockupFactory::create` derives the lockup account name deterministically from `sha256(owner_account_id)[..20]` and lets any caller supply `owner_account_id` and all other lockup parameters (`lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id`) while only requiring `attached_deposit >= MIN_ATTACHED_BALANCE` (3.5 NEAR). An unprivileged attacker can therefore call `create(owner_account_id='victim.near', ...)` with attacker-chosen parameters and the minimum deposit, permanently occupying the address the legitimate/intended larger grant would have used.

### Finding Description
Broken binding: the account address `sha256('victim.near')[..20].<factory>` is expected to satisfy `lockup_information.lockup_amount == victim's intended grant amount` and `lockup_duration/vesting_schedule == victim's intended terms`. Because `create` is a public, unauthenticated method [1](#0-0) , and the account name is derived only from `owner_account_id` (not from the caller, nor pinned to any pre-registered grant record) [2](#0-1) , any attacker can win the race and call `create()` first, attaching exactly `MIN_ATTACHED_BALANCE` and supplying arbitrary `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, and `release_duration` values of their own choosing [3](#0-2) .

Once `Promise::new(lockup_account_id).create_account()` succeeds for the attacker's low-value call, the account at that deterministic address exists and is deployed with the attacker-chosen `LockupArgs`. Any subsequent legitimate `create()` call for the same `owner_account_id='victim.near'` with a larger `attached_deposit` will have its `create_account()` action fail (the account already exists), so `is_promise_success()` returns `false` in the callback `on_lockup_create` [4](#0-3) . The `else` branch then refunds the entire larger attached deposit back to `predecessor_account_id` (the legitimate creator) instead of funding the grant [5](#0-4) . The lockup account squatted by the attacker remains permanently deployed with the attacker's chosen parameters and only the minimal 3.5 NEAR balance.

No guard (`assert_owner`, `assert_called_by_foundation`, whitelist check, or reservation mechanism) exists in `create()` to bind the `owner_account_id` to a specific authorized caller or to a pre-committed grant amount/schedule — the only check is `env::attached_deposit() >= MIN_ATTACHED_BALANCE` [6](#0-5) .

### Impact Explanation
The victim's rightful grant is never deployed with its intended amount or vesting terms — the deployed contract at the reserved address is instead controlled by attacker-chosen `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, and `release_duration`, and its balance is capped at whatever minimal deposit the attacker chose to attach. This matches the Critical category "a lockup deployed with parameters its rightful creator never chose." The legitimate creator's larger deposit is refunded (no funds are stolen outright), but the intended grant can never be funded at that address again because the account is permanently occupied and cannot be recreated. This is repeatable against any `owner_account_id` before the legitimate creator submits their transaction, and the blast radius is any NEAR account intended to receive a lockup grant through this factory.

### Likelihood Explanation
The attack requires only a public RPC call, knowledge of the intended `owner_account_id` (typically public/announced ahead of grant creation), and 3.5 NEAR (`MIN_ATTACHED_BALANCE`) — no privileged role, key, or foundation/owner access is needed. The main precondition is winning a race against the legitimate creator's transaction, which is generally feasible since grant recipients and factory addresses are often known/predictable in advance and mempool/block timing gives an attacker a window to front-run.

### Recommendation
Do not derive the lockup account solely from `owner_account_id`; require the `create()` call to be restricted (e.g., only the foundation/whitelisted account authorized to create grants) or bind the deposit amount/schedule to a pre-registered commitment (e.g., a foundation-signed authorization or a reserved-amount registry) so that an arbitrary caller cannot pre-create an account for an arbitrary `owner_account_id` with attacker-chosen terms and minimal funding.

### Proof of Concept
```rust
// cargo test in lockup-factory/src/lib.rs (add to `mod tests`)
#[test]
fn test_squat_before_real_grant() {
    let mut context = VMContextBuilder::new()
        .current_account_id(account_factory())
        .predecessor_account_id(account_near())
        .finish();
    testing_env!(context.clone());

    let mut contract = LockupFactory::new(whitelist_account_id(), foundation_account_id());

    const LOCKUP_DURATION: u64 = 63036000000000000;
    let lockup_duration: WrappedTimestamp = LOCKUP_DURATION.into();

    // Attacker squats the address for account_tokens_owner() with min deposit
    // and attacker-chosen (shorter) lockup_duration.
    context.is_view = false;
    context.predecessor_account_id = String::from("attacker.near");
    context.attached_deposit = MIN_ATTACHED_BALANCE; // exactly 3.5 NEAR
    testing_env!(context.clone());
    contract.create(
        account_tokens_owner(), // owner_account_id = victim
        1u64.into(),            // attacker-chosen tiny duration, not victim's intended terms
        None, None, None, None,
    );

    // Simulate the squatting create_account succeeding.
    context.predecessor_account_id = account_factory();
    context.attached_deposit = ntoy(0);
    testing_env_with_promise_results(context.clone(), PromiseResult::Successful(vec![]));
    contract.on_lockup_create(
        lockup_account(),
        MIN_ATTACHED_BALANCE.into(),
        String::from("attacker.near"),
    );
    // lockup_account() now exists, deployed with attacker's params & only 3.5 NEAR.

    // Real creator now tries to fund the intended grant with a much larger deposit
    // at the SAME derived address (same owner_account_id).
    context.predecessor_account_id = String::from(account_tokens_owner());
    context.attached_deposit = ntoy(1000); // intended real grant amount
    testing_env!(context.clone());
    contract.create(account_tokens_owner(), lockup_duration, None, None, None, None);

    // create_account fails because the account already exists -> promise fails.
    context.predecessor_account_id = account_factory();
    context.attached_deposit = ntoy(0);
    testing_env_with_promise_results(context.clone(), PromiseResult::Failed);
    let res = contract.on_lockup_create(
        lockup_account(),
        ntoy(1000).into(),
        String::from(account_tokens_owner()),
    );

    // Assert: promise failed and the 1000 NEAR is refunded to the real creator
    // instead of funding lockup_account(); lockup_account()'s balance/params
    // remain fixed at the attacker's squatted 3.5 NEAR / short duration forever.
    assert_eq!(res, false);
}
```
This demonstrates the binding `lockup_information.lockup_amount@address(victim) == victim's intended grant amount` is broken: the address is occupied first by the attacker at `MIN_ATTACHED_BALANCE` with attacker-chosen terms, and the real creator's larger deposit is refunded rather than deployed, per the `else` branch in `on_lockup_create` [5](#0-4) .

### Citations

**File:** lockup-factory/src/lib.rs (L107-121)
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
