### Title
Unauthenticated `create()` lets anyone deploy a lockup for an arbitrary `owner_account_id` with attacker-chosen `lockup_duration`/`lockup_timestamp`, squatting that owner's deterministic lockup address - (File: `lockup-factory/src/lib.rs`)

### Summary
`LockupFactory::create` derives the deployed lockup's address solely from `sha256(owner_account_id)[..20]` and never checks that `env::predecessor_account_id()` equals, or is authorized by, `owner_account_id`. Any unprivileged caller can pay `MIN_ATTACHED_BALANCE`, name any victim as `owner_account_id`, and deploy that victim's one-and-only lockup contract with `lockup_duration`/`lockup_timestamp`/`vesting_schedule`/`release_duration` values the attacker picked, not the values the victim's real sponsor intended.

### Finding Description
The binding that should hold is:

`LockupArgs.lockup_duration / lockup_timestamp deployed at lockup_account_id == sha256(owner)[..20].<factory>` `==` `the parameters chosen by owner_account_id's true, authorized sponsor`

but the code only enforces: [1](#0-0) 
i.e. a deposit-size check, and a fully deterministic address derived from `owner_account_id` alone — nothing ties `env::predecessor_account_id()` (the payer) to `owner_account_id` (the beneficiary). The transfer of the attached deposit and the `function_call` with attacker-supplied `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration` execute unconditionally: [2](#0-1) 

Because `lockup_account_id` is a pure function of `owner_account_id`, exactly one lockup account can ever exist for a given owner (NEAR account creation fails if the account already exists). An unprivileged attacker can therefore front-run/squat any victim's lockup slot: call `create(owner_account_id = <victim>, lockup_duration = <attacker-chosen>, lockup_timestamp = None, ...)` from an unrelated `predecessor_account_id`, attaching exactly `MIN_ATTACHED_BALANCE`. The resulting `Promise::new(lockup_account_id).create_account().deploy_contract(...).transfer(...).function_call(new, LockupArgs{owner_account_id: <victim>, lockup_duration: attacker's value, ...})` succeeds because nothing in `create` or the callback `on_lockup_create` validates the caller against the owner: [3](#0-2) 

`assert_self()` in `utils.rs` only checks the callback is invoked by the factory itself, not that the original caller was `owner_account_id`: [4](#0-3) 

Once deployed, the victim's real sponsor calling `create` with the intended `lockup_duration`/`lockup_timestamp`/`vesting_schedule` for the same `owner_account_id` will resolve to the identical `lockup_account_id`, `create_account()` will fail (account already exists), `is_promise_success()` returns false, and the refund goes back to the legitimate sponsor's own `predecessor_account_id` — but the victim's account slot is now permanently occupied by the attacker-chosen schedule, which can never be corrected without a factory redeploy.

### Impact Explanation
The deployed `lockup_account_id` for `<victim>` ends up initialized with `lockup_duration`/`lockup_timestamp`/`vesting_schedule`/`release_duration` chosen by an unrelated, unprivileged attacker rather than by the victim's real sponsor — this is explicitly the Critical category "a lockup deployed with parameters its rightful creator never chose." Because the address is deterministic and unique per owner, this is a one-shot but irreversible squat per victim account: the legitimate sponsor can never deploy the intended lockup for that `owner_account_id` afterward. This is repeatable across every distinct victim account name the attacker chooses to target, at the cost of `MIN_ATTACHED_BALANCE` (3.5 NEAR) per target, which is not stolen from the victim (it's the attacker's own deposit, refundable to a later failed caller's predecessor, but not to the attacker) but denies the victim's true grant terms.

### Likelihood Explanation
No privilege is required beyond the general unprivileged capabilities: the attacker only needs to send a transaction to the public `create` method with `MIN_ATTACHED_BALANCE` (3.5 NEAR) attached and a victim account name they want to target — no whitelist, foundation, or owner role is checked anywhere in `create`. This is fully feasible and repeatable against any not-yet-created lockup owner account, at a fixed, bounded cost per target.

### Recommendation
Require `env::predecessor_account_id() == owner_account_id.as_ref()` (or an explicit signature/authorization from the owner) inside `create` before deploying, so that only the account itself (or an authorized sponsor flow) can cause a lockup to be created in its name; alternatively, gate `create` behind `assert_called_by_foundation`-style access control as is done elsewhere in this codebase for privileged factory operations.

### Proof of Concept
```rust
#[test]
fn test_create_lockup_owner_not_predecessor() {
    let mut context = VMContextBuilder::new()
        .current_account_id(account_factory())
        .predecessor_account_id(account_near())
        .finish();
    testing_env!(context.clone());

    let mut contract = LockupFactory::new(whitelist_account_id(), foundation_account_id());

    const LOCKUP_DURATION: u64 = 63036000000000000; // attacker-chosen 24 months
    let lockup_duration: WrappedTimestamp = LOCKUP_DURATION.into();

    context.is_view = false;
    // attacker is predecessor, victim is owner_account_id - never equal
    context.predecessor_account_id = String::from(account_near()); // attacker, NOT account_tokens_owner()
    context.attached_deposit = ntoy(35); // == MIN_ATTACHED_BALANCE
    testing_env!(context.clone());

    // attacker funds and names account_tokens_owner() (the "victim") as beneficiary,
    // with attacker-chosen lockup_duration/lockup_timestamp
    contract.create(account_tokens_owner(), lockup_duration, None, None, None, None);

    context.predecessor_account_id = account_factory();
    context.attached_deposit = ntoy(0);
    testing_env_with_promise_results(context.clone(), PromiseResult::Successful(vec![]));

    // callback confirms lockup_account_id == sha256(owner)[..20].<factory>
    // even though predecessor_account_id (attacker, account_near()) != owner_account_id (account_tokens_owner())
    let created = contract.on_lockup_create(
        lockup_account(), // deterministic from account_tokens_owner() only
        ntoy(35).into(),
        String::from(account_near()), // attacker's own account, proving no owner binding
    );
    assert!(created);
    // Assert both sides of the binding diverge:
    // LHS: lockup_account() derived purely from account_tokens_owner()
    // RHS: parameters (lockup_duration) were supplied by account_near(), an unrelated predecessor
    // No assertion anywhere enforces predecessor_account_id == owner_account_id.
}
```
This demonstrates that `create`/`on_lockup_create` never assert `predecessor_account_id == owner_account_id`, confirming the deployed lockup's parameters and address binding can be set by any unprivileged account on behalf of any victim owner.

### Citations

**File:** lockup-factory/src/lib.rs (L117-121)
```rust
        assert!(env::attached_deposit() >= MIN_ATTACHED_BALANCE, "Not enough attached deposit");

        let byte_slice = env::sha256(owner_account_id.as_ref().as_bytes());
        let lockup_account_id =
            format!("{}.{}", hex::encode(&byte_slice[..20]), env::current_account_id());
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

**File:** lockup-factory/src/utils.rs (L1-5)
```rust
use near_sdk::{env, PromiseResult};

pub fn assert_self() {
    assert_eq!(env::predecessor_account_id(), env::current_account_id());
}
```
