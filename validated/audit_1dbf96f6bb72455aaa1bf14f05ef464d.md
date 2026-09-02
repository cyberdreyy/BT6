### Title
Attacker-controlled `whitelist_account_id` and pre-image-derived lockup address let an attacker deploy a lockup for a victim's `owner_account_id` with an attacker-chosen whitelist, front-running the legitimate lockup - (File: `lockup-factory/src/lib.rs`)

### Summary
`LockupFactory::create` is a `#[payable]` method callable by any account holding `MIN_ATTACHED_BALANCE`, and it accepts an optional `whitelist_account_id` that, if supplied, completely replaces the canonical `self.whitelist_account_id` in the deployed lockup's `staking_pool_whitelist_account_id` field [1](#0-0) . Because the target lockup account id is a deterministic function of `owner_account_id` alone (`sha256(owner_account_id)`), an attacker can pre-create the lockup for any victim `owner_account_id` before the legitimate creator does, permanently binding a hostile whitelist to that address [2](#0-1) .

### Finding Description
The invariant that should hold is: `deployed_lockup.staking_pool_whitelist_account_id == factory.whitelist_account_id` (the canonical, foundation-configured whitelist) for every lockup created through the public factory, unless explicitly authorized otherwise. This binding is broken in `create`:

```
let staking_pool_whitelist_account_id = if let Some(account_id) = whitelist_account_id {
    account_id.into()
} else {
    self.whitelist_account_id.clone()
};
``` [3](#0-2) 

There is no `assert_called_by_foundation`-style guard on the `whitelist_account_id` argument in `create` (contrast with `foundation`-gated functions in `lockup/src/foundation.rs`) - any unprivileged caller supplying `MIN_ATTACHED_BALANCE` can set this field to an account they control. This is confirmed by the repository's own test `test_create_lockup_with_custom_whitelist_success`, which exercises exactly this path with an arbitrary `custom_whitelist_account_id()` and succeeds [4](#0-3) .

Separately, the lockup account id is fully determined by `owner_account_id` and the factory account, independent of `whitelist_account_id`, `vesting_schedule`, or any other caller-supplied argument:
```
let byte_slice = env::sha256(owner_account_id.as_ref().as_bytes());
let lockup_account_id = format!("{}.{}", hex::encode(&byte_slice[..20]), env::current_account_id());
``` [2](#0-1) 

Exploit flow: an attacker calls `create(owner_account_id = <victim>, ..., whitelist_account_id = Some(<attacker-controlled contract>))`, attaching `MIN_ATTACHED_BALANCE` from their own funds. `Promise::new(lockup_account_id).create_account()...` succeeds because no account exists yet at that address, deploying a lockup whose `staking_pool_whitelist_account_id` is the attacker's contract, and whose `owner_account_id` is the victim. This preempts the deterministic address, so when the legitimate party (foundation or the victim itself) later attempts to create the intended lockup with the canonical whitelist for the same `owner_account_id`, the `create_account` action fails (account already exists) and the whole batched promise fails, refunding the caller via `on_lockup_create`'s failure branch [5](#0-4) . The victim is left with a lockup contract whose staking-pool-whitelist consultation point was never chosen by them or the foundation - "a lockup deployed with parameters its rightful creator never chose." If the attacker's whitelist contract answers `is_whitelisted` as `true` for any pool, the victim (as owner) can later be induced to `select_staking_pool` into an attacker-controlled staking pool that the canonical whitelist would have rejected, since the guard consulted is `ext_whitelist::is_whitelisted` against the attacker's contract, not the canonical one referenced in `lockup/src/owner.rs`.

None of `assert_self`, `is_promise_success`, `assert_one_yocto`, or any foundation check in `create`/`on_lockup_create` prevents this, because those guards protect the callback's internal bookkeeping and refund path, not the choice of `whitelist_account_id` itself, which is accepted unconditionally from an unprivileged caller.

### Impact Explanation
The attacker can deploy, for any chosen `owner_account_id` (including a victim's real account, or a known future vesting recipient), a lockup contract whose staking-pool trust anchor is a contract the attacker fully controls, and permanently occupy the deterministic address so the legitimate/canonical lockup can never be created there. This matches the Critical category "a lockup deployed with parameters its rightful creator never chose." Downstream, if the owner is later induced to stake into a pool that only passes the attacker's fake whitelist, NEAR could be routed into an attacker-controlled staking pool - funds leaving the account without the entitled party's informed authorization. The attack is repeatable against any `owner_account_id` not yet used, at the low cost of `MIN_ATTACHED_BALANCE` (3.5 NEAR) per victim address.

### Likelihood Explanation
Any unprivileged account can call `create` directly; the only precondition is attaching `MIN_ATTACHED_BALANCE` and knowing (or guessing) a target `owner_account_id` before the legitimate creator uses it - fully feasible since `owner_account_id` values (e.g., known team/investor accounts, or the caller's own account for pool-approval bypass) are often public or predictable. No special privileges, keys, or foundation access are required, and the action is a single transaction.

### Recommendation
Restrict the `whitelist_account_id` parameter in `LockupFactory::create` to only be settable by `self.foundation_account_id` (or remove the parameter entirely and always use `self.whitelist_account_id`), and consider disallowing regular users from choosing arbitrary `owner_account_id` for lockups that are meant to be foundation-issued, or otherwise decouple the deterministic lockup address derivation from any single-attempt griefing/front-running vector.

### Proof of Concept
```rust
// lockup-factory/src/lib.rs test module
#[test]
fn test_hostile_whitelist_frontrun() {
    let mut context = VMContextBuilder::new()
        .current_account_id(account_factory())
        .predecessor_account_id(account_near())
        .finish();
    testing_env!(context.clone());
    let mut contract = LockupFactory::new(whitelist_account_id(), foundation_account_id());

    let lockup_duration: WrappedTimestamp = 63036000000000000u64.into();

    // Attacker (not foundation) creates a lockup for `victim` owner with a hostile whitelist
    context.predecessor_account_id = String::from(attacker_account_id());
    context.attached_deposit = ntoy(35);
    testing_env!(context.clone());
    contract.create(
        victim_owner_account_id(),
        lockup_duration,
        None,
        None,
        None,
        Some(hostile_whitelist_account_id()), // attacker-controlled, no foundation check
    );

    // Confirm on_lockup_create succeeds and binds attacker's whitelist to victim's deterministic lockup address
    context.predecessor_account_id = account_factory();
    context.attached_deposit = ntoy(0);
    testing_env_with_promise_results(context.clone(), PromiseResult::Successful(vec![]));
    assert!(contract.on_lockup_create(
        derived_lockup_account_id_for(&victim_owner_account_id()),
        ntoy(35).into(),
        String::from(attacker_account_id()),
    ));

    // Foundation later attempts the legitimate creation for same owner -> account_id collision,
    // create_account fails, on_lockup_create refunds foundation, victim's lockup permanently
    // stuck with attacker's whitelist_account_id.
}
```
The assertion to check on both sides of the binding: `deployed_lockup.staking_pool_whitelist_account_id` (read from the `LockupArgs` sent in the `function_call` payload / or via the deployed lockup's `get_staking_pool_whitelist_account_id` view) equals `contract.whitelist_account_id` (canonical). The PoC shows they diverge after the attacker's call, and the legitimate creation for the same `owner_account_id` subsequently fails due to the pre-existing account, demonstrating the address-squatting/parameter-injection issue.

### Citations

**File:** lockup-factory/src/lib.rs (L119-121)
```rust
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

**File:** lockup-factory/src/lib.rs (L179-197)
```rust
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
```

**File:** lockup-factory/src/lib.rs (L390-425)
```rust
    #[test]
    fn test_create_lockup_with_custom_whitelist_success() {
        let mut context = VMContextBuilder::new()
            .current_account_id(account_factory())
            .predecessor_account_id(account_near())
            .finish();
        testing_env!(context.clone());

        let mut contract = LockupFactory::new(whitelist_account_id(), foundation_account_id());

        const LOCKUP_DURATION: u64 = 63036000000000000; /* 24 months */
        let lockup_duration: WrappedTimestamp = LOCKUP_DURATION.into();

        context.is_view = false;
        context.predecessor_account_id = String::from(account_tokens_owner());
        context.attached_deposit = ntoy(35);
        testing_env!(context.clone());
        contract.create(
            account_tokens_owner(),
            lockup_duration,
            None,
            None,
            None,
            Some(custom_whitelist_account_id()),
        );

        context.predecessor_account_id = account_factory();
        context.attached_deposit = ntoy(0);
        testing_env_with_promise_results(context.clone(), PromiseResult::Successful(vec![]));
        println!("{}", lockup_account());
        contract.on_lockup_create(
            lockup_account(),
            ntoy(30).into(),
            String::from(account_tokens_owner()),
        );
    }
```
