### Title
Unauthenticated address-squatting in `LockupFactory::create` lets any account fix the lockup terms for a victim owner before the real grantor - ([File: lockup-factory/src/lib.rs])

### Summary
`LockupFactory::create` computes the target account deterministically as `hex::encode(&env::sha256(owner_account_id.as_bytes())[..20]).<factory>` and performs no check tying `env::predecessor_account_id()` to `owner_account_id` or to any privileged caller. Any unprivileged account can call `create` first with a victim's `owner_account_id` and attacker-chosen `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, and `whitelist_account_id`, permanently claiming that address before the legitimate grantor does.

### Finding Description
The binding that should hold is: `terms(lockup_account_at(sha256(owner)[..20])) == terms_chosen_by(owner's real grantor)`. The code never enforces this.

`create` is `#[payable]` and callable by any account holding `MIN_ATTACHED_BALANCE` (3.5 NEAR): [1](#0-0) [2](#0-1) 

There is no `assert_called_by_foundation`, no relationship check between `predecessor_account_id` and `owner_account_id`, and no reservation/commit-reveal step. All the sensitive parameters — `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, `whitelist_account_id` — are attacker-supplied function arguments: [3](#0-2) 

Since NEAR account creation via `create_account()` fails atomically if the account already exists, whichever caller (attacker or the legitimate grantor) reaches `Promise::new(lockup_account_id).create_account()` first wins that address permanently. The loser's whole batch (deploy + transfer + `new` call) fails, and `on_lockup_create` only refunds the attached deposit — it cannot undo the fact that the address is now occupied by the attacker's contract: [4](#0-3) 

`assert_self()` and `is_promise_success()` only gate the refund-on-failure logic; they do nothing to prevent the initial front-run, and there is no check anywhere that the `owner_account_id` hasn't already been claimed before dispatching the promise chain.

Exploit flow:
1. Attacker learns/guesses that `victim.near` is slated to receive a grant whose real lockup has not been created yet.
2. Attacker calls `create(owner_account_id = "victim.near", lockup_duration = <attacker value>, vesting_schedule = <attacker value or None, or an unrevealable hash>, whitelist_account_id = <attacker value>, ...)` with `MIN_ATTACHED_BALANCE` attached.
3. The lockup contract is deployed at `hex(sha256("victim.near")[..20]).<factory>` with `owner_account_id = "victim.near"` (so it superficially looks correct) but with attacker-chosen `vesting_schedule`/`lockup_duration`/`whitelist_account_id`/`foundation_account_id` semantics.
4. When the real grantor later calls `create` with the correct, intended parameters for the same `owner_account_id`, `create_account()` fails because the account exists; the deposit is refunded via `on_lockup_create`, but the address is permanently unusable for the correctly-parameterized lockup.
5. Any funds that later flow to that address (directly via `Promise::transfer` top-ups, which the lockup contract's balance accounting is designed to accept) are now governed by attacker-chosen terms — e.g., an unrevealable `VestingScheduleOrHash::VestingHash` that no real schedule will ever match, or a `foundation_account_id` mismatch that prevents any future termination/clawback — permanently freezing or corrupting the grant's intended lifecycle.

### Impact Explanation
This directly matches the listed Critical category "a lockup deployed with parameters its rightful creator never chose." The attacker cannot become the `owner_account_id` of the squatted contract (that's fixed to the victim's id to make the address collide), so this is not straightforward self-enrichment, but it is a real, repeatable, low-cost mechanism to (a) permanently deny the correct grantor the ability to create the intended lockup at the canonical derived address for that owner, and (b) if any real deposit is later routed to that address (e.g. via a raw transfer top-up, which the lockup's balance model accepts unauthenticated), those funds inherit attacker-fixed vesting/whitelist/duration terms, up to and including an unresolvable vesting hash that can never be revealed/terminated by the real foundation — i.e., funds permanently frozen. The blast radius is any account name for which a real grant is expected but not yet created; the attack is repeatable against every future grant address that can be predicted or guessed.

### Likelihood Explanation
The only precondition is knowledge (or a guess) of an `owner_account_id` that will receive a future lockup grant, plus 3.5 NEAR (`MIN_ATTACHED_BALANCE`) to pay for the squatting deposit. No special privilege, key, or role is required — `create` is open to any predecessor. This is cheap and fully repeatable across any number of target account IDs, limited only by the attacker's willingness to lock up 3.5 NEAR per squatted address (which is not refundable to the attacker once deployed, since they are not the owner).

### Recommendation
Restrict `create` so that only the intended owner (`predecessor_account_id == owner_account_id`) or an explicitly authorized/whitelisted grantor (e.g. `assert_called_by_foundation`-style check) can create a lockup for a given `owner_account_id`, and/or require the derived address to incorporate a value only the legitimate grantor knows/controls (e.g., a factory-issued nonce or a grantor signature) rather than being purely `sha256(owner_account_id)`.

### Proof of Concept
`cargo test` plan in `lockup-factory/src/lib.rs` tests module:
1. Set up `LockupFactory::new(...)` as in existing tests.
2. From an arbitrary unprivileged `predecessor_account_id` (attacker, not the foundation and not `owner_account_id`), call `contract.create(victim_account_id, attacker_lockup_duration, attacker_lockup_timestamp, attacker_vesting_schedule, attacker_release_duration, Some(attacker_whitelist_account_id))` with `MIN_ATTACHED_BALANCE` attached; drive the promise to success with `testing_env_with_promise_results(..., PromiseResult::Successful(vec![]))` and call `on_lockup_create` to confirm `true`/deployment success at `lockup_account_id = hex(sha256(victim_account_id)[..20]).factory`.
3. Then, from the legitimate grantor's `predecessor_account_id`, call `contract.create(victim_account_id, legit_lockup_duration, legit_lockup_timestamp, legit_vesting_schedule, legit_release_duration, None)` for the same `victim_account_id`; simulate `PromiseResult::Failed` (account-already-exists) and call `on_lockup_create`, asserting it returns `false` and the deposit is refunded to the legit grantor.
4. Assert the equality binding fails: the parameters embedded in the deployed `LockupArgs` at `lockup_account_id` equal the attacker's values, not the legit grantor's values — demonstrating that "the lockup living at the owner's derived address" does not carry "the terms that owner's grantor chose."

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
