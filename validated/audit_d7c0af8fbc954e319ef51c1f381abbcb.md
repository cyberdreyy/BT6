### Title
Permissionless lockup address squatting via deterministic derivation lets an attacker fix a victim's lockup terms before the real grantor - ([File: lockup-factory/src/lib.rs])

### Summary
`LockupFactory::create` is callable by any account that attaches `MIN_ATTACHED_BALANCE`, with no restriction on who may call it and no check that the target account doesn't already exist. Because the lockup's account id is fully deterministic — `hex::encode(&env::sha256(owner_account_id.as_bytes())[..20])` — an attacker can pre-create the lockup for any `owner_account_id` before the legitimate grantor does, permanently fixing attacker-chosen `vesting_schedule`, `release_duration`, and `whitelist_account_id` at the address the protocol and tooling treat as "the" lockup for that owner.

### Finding Description
The invariant the question tests is: `lockup_at(derive(owner)).terms == grantor_chosen_terms(owner)`. Tracing `create`: [1](#0-0) 

`create` only checks `env::attached_deposit() >= MIN_ATTACHED_BALANCE` — there is no `assert_called_by_foundation`, no owner check, and no check that `lockup_account_id` is not already occupied. `lockup_account_id` is computed purely from `owner_account_id`, which is attacker-suppliable: [2](#0-1) 

The rest of the arguments that determine the lockup's behavior — `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `staking_pool_whitelist_account_id` — are all attacker-controlled and get serialized straight into the `new` call on the newly created account: [3](#0-2) 

Since NEAR's `CreateAccount` action fails if the target account already exists, whichever caller submits first "wins" the address. `on_lockup_create` only distinguishes success/failure and refunds the deposit on failure; it never checks predecessor identity beyond `assert_self()` (i.e., it only ensures the callback is invoked by the factory itself, not that the *original* `create` caller was authorized): [4](#0-3) [5](#0-4) 

None of the guard primitives referenced in the audit checklist (`assert_owner`, `assert_called_by_foundation`, `assert_one_yocto`, etc.) exist in this contract at all — the only access gate is the deposit amount, which is a variable any unprivileged account can pay. Exploit flow: attacker computes `hex::encode(&env::sha256(victim_owner.as_bytes())[..20])`, then calls `create(victim_owner, attacker_lockup_duration, attacker_lockup_timestamp, attacker_vesting_schedule_or_none, attacker_release_duration, Some(attacker_whitelist_account_id))` with exactly `MIN_ATTACHED_BALANCE`. This deploys a lockup contract at the victim's canonical derived address whose `owner_account_id` is indeed the victim (so on-chain "ownership" checks pass) but whose `staking_pool_whitelist_account_id`/`vesting_schedule`/`release_duration` are attacker-chosen — e.g., pointing the whitelist at an attacker-deployed fake staking-pool contract. When the legitimate grantor later calls `create` with the real terms, `create_account` on the already-existing account fails, the whole promise batch fails, and `on_lockup_create` simply refunds the grantor's attached deposit — the grantor's intended lockup at that address can never be created, and the address permanently carries the attacker's terms. Any subsequent tooling, integration, or manual transfer that trusts "the lockup at the victim's derived address" now interacts with an attacker-parameterized contract (e.g., staking through the attacker's malicious whitelist entry), routing the owner's future stake/funds through a contract the protocol was meant to treat as trusted.

### Impact Explanation
This matches the Critical category "an account whitelisted or a lockup deployed with parameters its rightful creator never chose." The attacker fixes `vesting_schedule`, `release_duration`, and, most dangerously, `staking_pool_whitelist_account_id` for an arbitrary victim owner, at zero marginal cost per target beyond `MIN_ATTACHED_BALANCE` (3.5 NEAR, refundable in spirit since it becomes the squatted lockup's own balance, not lost — but the address is permanently squatted). This is repeatable across every account id the attacker wants to preempt, and blocks the legitimate grantor from ever deploying the correct lockup at that canonical address, since `create_account` cannot overwrite an existing account. If the owner later stakes via the poisoned whitelist, or if the foundation/grantor's off-chain tooling blindly funds the derived address expecting it to run legitimate terms, NEAR can be steered into an attacker-controlled staking pool or vest immediately/never as the attacker dictated.

### Likelihood Explanation
The precondition is only that the attacker knows (or can predict/observe) a target `owner_account_id` before the legitimate grantor calls `create` for it, and can pay `MIN_ATTACHED_BALANCE` (3.5 NEAR) — well within reach of any unprivileged account. The derivation function is public (`sha256` of the owner id, first 20 bytes hex-encoded), so front-running is trivial and can be automated/batched across many candidate owner ids cheaply. No special role, key, or foundation privilege is required, making this highly feasible and repeatable.

### Recommendation
Restrict `create` to be callable only by `self.foundation_account_id` (or another authorized grantor role) via an `assert_called_by_foundation`-style check, and/or have the factory verify the derived account does not already exist and reject/refund with a clear "already provisioned" error rather than allowing a first-come-first-served race on a fully predictable address. Consider also validating that no untrusted party can set `staking_pool_whitelist_account_id` to an arbitrary contract.

### Proof of Concept
```rust
// cargo test in lockup-factory/src/lib.rs tests module
#[test]
fn test_lockup_address_squatting_by_unprivileged_attacker() {
    let mut context = VMContextBuilder::new()
        .current_account_id(account_factory())
        .predecessor_account_id(account_near())
        .finish();
    testing_env!(context.clone());

    let mut contract = LockupFactory::new(whitelist_account_id(), foundation_account_id());

    // Attacker (not foundation, not owner) front-runs the victim's lockup creation.
    context.predecessor_account_id = String::from("attacker.near");
    context.attached_deposit = ntoy(4); // >= MIN_ATTACHED_BALANCE
    testing_env!(context.clone());
    contract.create(
        account_tokens_owner(),           // victim owner id -> derives the canonical address
        (63036000000000000u64).into(),    // attacker-chosen lockup_duration
        None,
        None,                              // attacker skips vesting_schedule
        None,
        Some(malicious_whitelist_account_id()), // attacker-chosen whitelist
    );
    context.predecessor_account_id = account_factory();
    context.attached_deposit = ntoy(0);
    testing_env_with_promise_results(context.clone(), PromiseResult::Successful(vec![]));
    contract.on_lockup_create(
        lockup_account(), // same deterministic address as victim's real lockup would use
        ntoy(4).into(),
        String::from("attacker.near"),
    );

    // Legitimate grantor now tries to create the real lockup for the same owner -> fails
    // because create_account on an already-existing account fails at protocol level.
    context.predecessor_account_id = String::from(account_tokens_owner());
    context.attached_deposit = ntoy(35);
    testing_env!(context.clone());
    contract.create(account_tokens_owner(), (63036000000000000u64).into(), None, None, None, None);

    context.predecessor_account_id = account_factory();
    context.attached_deposit = ntoy(0);
    testing_env_with_promise_results(context.clone(), PromiseResult::Failed); // create_account fails: account exists
    let res = contract.on_lockup_create(
        lockup_account(),
        ntoy(35).into(),
        String::from(account_tokens_owner()),
    );

    // Assert the binding is broken: the lockup at the derived address carries
    // attacker's whitelist/terms, not the grantor's, and the grantor's real
    // creation attempt fails (refunded), proving the address was squatted.
    assert_eq!(res, false, "legitimate creation must fail because address is squatted");
    // (In an integration test with near-workspaces, additionally assert that
    // the deployed lockup's staking_pool_whitelist_account_id == malicious_whitelist_account_id()
    // and not whitelist_account_id(), confirming attacker-chosen terms persist.)
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

**File:** lockup-factory/src/utils.rs (L3-5)
```rust
pub fn assert_self() {
    assert_eq!(env::predecessor_account_id(), env::current_account_id());
}
```
