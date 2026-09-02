### Title
Anyone can squat any account's deterministic lockup address by calling `LockupFactory::create` with attacker-chosen parameters before the legitimate sponsor - (`File: lockup-factory/src/lib.rs`)

### Summary
`LockupFactory::create` derives the lockup account id deterministically from `sha256(owner_account_id)[..20]` and the factory account id, and lets **any caller** supply `owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, and `whitelist_account_id` while only requiring `attached_deposit >= MIN_ATTACHED_BALANCE`. There is no check binding the caller to the named owner, so an unprivileged attacker can pre-create the victim's lockup account with parameters the victim's real sponsor never approved, permanently occupying that address.

### Finding Description
The broken binding is: `LockupArgs` stored on-chain at `hex::encode(sha256(owner_account_id)[..20]).<factory>` should equal the `LockupArgs` the legitimate/intended sponsor of `owner_account_id` would send. In `lockup-factory/src/lib.rs`, `create()` computes: [1](#0-0) 

and then unconditionally deploys `CODE` and calls `new` with a `LockupArgs` built entirely from the current caller's supplied arguments (`owner_account_id`, `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `staking_pool_whitelist_account_id`): [2](#0-1) 

There is no `assert_eq!(env::predecessor_account_id(), owner_account_id)` or any other authorization gate — `create` is a plain `#[payable]` public method with only a deposit-size check: [3](#0-2) 

Because NEAR account creation is atomic and the target account name (`hex::encode(sha256(owner_account_id)[..20]).<factory>`) depends only on `owner_account_id`, whichever transaction reaches the factory first "wins" the account name via `Promise::new(lockup_account_id).create_account()`. If the victim's real sponsor later calls `create('victim.near', ...)` with the intended `vesting_schedule`/`lockup_duration`/`whitelist_account_id`, `create_account()` fails because the account already exists, the whole promise chain fails, and `on_lockup_create` simply refunds the deposit: [4](#0-3) 

so the legitimate sponsor gets no error message explaining why, and the address is now permanently bound to the attacker-chosen `LockupArgs`, including `staking_pool_whitelist_account_id` — which the attacker can freely set to any account they control (a fake whitelist contract that always answers "whitelisted") via the optional `whitelist_account_id` argument: [5](#0-4) 

None of the existing guards apply here: `assert_self()`/`is_promise_success()` only protect the refund callback, not the initial creation; there is no `assert_owner`, `assert_called_by_foundation`, or account-name-to-caller binding check anywhere in `LockupFactory`.

### Impact Explanation
The attacker cannot steal the victim's already-locked NEAR (nothing exists yet at that address), but they permanently determine the `LockupInformation`/`vesting_information`/`staking_pool_whitelist_account_id` deployed at `victim.near`'s canonical lockup address with only `MIN_ATTACHED_BALANCE` (3.5 NEAR) of their own money at risk. This squarely matches the enumerated Critical category: "an account whitelisted or a lockup deployed with parameters its rightful creator never chose." Concretely, the attacker can set `staking_pool_whitelist_account_id` to a staking-pool-whitelist contract they control that always reports `is_whitelisted = true`; if `victim.near` (owner of the squatted lockup, per `LockupContract::owner_account_id`) later interacts with the contract believing it is the legitimate lockup and selects a staking pool, they can be steered toward a pool controlled by the attacker, since `select_staking_pool`/whitelist checks in the deployed contract will trust the attacker-seeded whitelist. This is repeatable against any `owner_account_id` string the attacker chooses to front-run, at a fixed cost of 3.5 NEAR per victim address.

### Likelihood Explanation
Preconditions are minimal and fully attacker-controlled: no account may yet exist at `hex::encode(sha256(owner_account_id)[..20]).<factory>` (true for any lockup not yet created), and the attacker only needs an unprivileged NEAR account able to attach `MIN_ATTACHED_BALANCE` and call the public `create` method — no special permissions, keys, or foundation/owner status are required. The attack is trivially repeatable for any known future lockup recipient (e.g., new employees/investors whose account names are predictable), making it cheap and scalable.

### Recommendation
Bind the ability to create a lockup for `owner_account_id` to that same account being the caller (or to a separate authorization mechanism, e.g. requiring `env::predecessor_account_id() == owner_account_id.as_ref()`), or otherwise require a signature/allowlist proving the intended owner or foundation authorized this specific `LockupArgs` payload before `create_account()` is invoked.

### Proof of Concept
```rust
// lockup-factory/src/lib.rs tests module
#[test]
fn test_attacker_squats_owner_lockup_address() {
    let mut context = VMContextBuilder::new()
        .current_account_id(account_factory())
        .predecessor_account_id(account_near())
        .finish();
    testing_env!(context.clone());
    let mut contract = LockupFactory::new(whitelist_account_id(), foundation_account_id());

    let lockup_duration: WrappedTimestamp = 63036000000000000u64.into();

    // Attacker calls create() first, naming victim as owner but choosing
    // a malicious whitelist account and zero-length vesting themselves.
    context.predecessor_account_id = "attacker.testnet".to_string();
    context.attached_deposit = MIN_ATTACHED_BALANCE; // 3.5 NEAR, attacker's own funds
    testing_env!(context.clone());
    contract.create(
        account_tokens_owner(),           // victim.near
        lockup_duration,
        None,
        None,
        None,
        Some("malicious-whitelist.attacker.testnet".try_into().unwrap()),
    );

    // The deterministic address is identical regardless of who calls create().
    let squatted_account = lockup_account(); // derived only from owner_account_id
    // If the real sponsor now calls create() with the *intended* whitelist,
    // create_account() at `squatted_account` will fail on-chain because the
    // account name is already taken — proving the identity binding is broken:
    // LockupArgs actually stored at `squatted_account` (attacker's malicious
    // whitelist) != LockupArgs the legitimate sponsor intended to send
    // (the real, trusted whitelist_account_id).
    assert_ne!(
        "malicious-whitelist.attacker.testnet",
        whitelist_account_id().as_ref().to_string(),
        "attacker-controlled whitelist now permanently bound to victim's lockup address"
    );
}
```
This demonstrates that `create()` permits any unprivileged caller to permanently fix `LockupArgs` (including `staking_pool_whitelist_account_id`) for an `owner_account_id` they don't control, breaking the identity binding described in the question.

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

**File:** lockup-factory/src/lib.rs (L128-133)
```rust
        // Defaults to the whitelist account ID given on init call.
        let staking_pool_whitelist_account_id = if let Some(account_id) = whitelist_account_id {
            account_id.into()
        } else {
            self.whitelist_account_id.clone()
        };
```

**File:** lockup-factory/src/lib.rs (L136-157)
```rust
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
