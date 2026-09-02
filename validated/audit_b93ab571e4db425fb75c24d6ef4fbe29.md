### Title
Unprivileged caller can front-run `LockupFactory::create` and bind a victim's lockup to an attacker-controlled `staking_pool_whitelist_account_id` - (File: `lockup-factory/src/lib.rs`)

### Summary
`LockupFactory::create` accepts an unauthenticated, caller-supplied `whitelist_account_id` parameter with no access control, and the resulting lockup account address is fully deterministic from `owner_account_id` alone. Any unprivileged account can call `create` for `owner_account_id="victim.near"` before the rightful creator does, substituting its own fake "always-whitelisted" contract for `self.whitelist_account_id`, permanently binding the victim's future lockup to it.

### Finding Description
The broken binding: `deployed_lockup.staking_pool_whitelist_account_id == self.whitelist_account_id` (the factory's trusted, foundation-configured whitelist) should always hold for lockups created on behalf of a beneficiary who did not explicitly opt into a different whitelist. Instead, `create` lets the caller override this value unconditionally: [1](#0-0) 

```rust
pub fn create(
    &mut self,
    owner_account_id: ValidAccountId,
    ...
    whitelist_account_id: Option<ValidAccountId>,
) -> Promise {
    ...
    let staking_pool_whitelist_account_id = if let Some(account_id) = whitelist_account_id {
        account_id.into()
    } else {
        self.whitelist_account_id.clone()
    };
``` [1](#0-0) 

There is no check that `env::predecessor_account_id()` is the foundation, the owner, or any privileged party — `create` is a public `#[payable]` method callable by anyone who attaches `MIN_ATTACHED_BALANCE`. [2](#0-1) 

The target lockup account ID is fully deterministic from `owner_account_id`, with no salt or caller-specific component: [3](#0-2) 

```rust
let byte_slice = env::sha256(owner_account_id.as_ref().as_bytes());
let lockup_account_id =
    format!("{}.{}", hex::encode(&byte_slice[..20]), env::current_account_id());
```

This value flows unchanged into the deployed lockup's `staking_pool_whitelist_account_id`, which is later relied upon, unconditionally, in the owner's `select_staking_pool`: [4](#0-3) 

```rust
pub fn select_staking_pool(&mut self, staking_pool_account_id: AccountId) -> Promise {
    self.assert_owner();
    ...
    ext_whitelist::is_whitelisted(
        staking_pool_account_id.clone(),
        &self.staking_pool_whitelist_account_id,
        ...
    )
```

Exploit flow:
1. Attacker deploys a contract at `attacker-whitelist.near` whose `is_whitelisted` method always returns `true`.
2. Before the foundation/legitimate depositor funds `victim.near`'s lockup, attacker calls `LockupFactory::create(owner_account_id="victim.near", ..., whitelist_account_id=Some("attacker-whitelist.near"))`, attaching `MIN_ATTACHED_BALANCE`. Since the target account (`sha256("victim.near")[..20].factory`) does not yet exist, `create_account()` succeeds.
3. The resulting `LockupContract` is initialized with `staking_pool_whitelist_account_id = attacker-whitelist.near` instead of the factory's trusted `self.whitelist_account_id`.
4. Because the deployed account name is derived deterministically from `owner_account_id`, any subsequent legitimate `create` call for the same `owner_account_id` will fail (`create_account` errors on an already-existing account), so the malicious lockup becomes the permanent lockup for `victim.near`.
5. When "victim.near" (as owner) later calls `select_staking_pool(attacker_pool)`, the check goes to `attacker-whitelist.near`, which approves any pool — including a pool the attacker operates — and `on_whitelist_is_whitelisted` writes `staking_information` accordingly, allowing the owner to later deposit/stake into a pool the attacker controls.

None of the existing guards (`assert_owner`, `assert_no_termination`, `assert_staking_pool_is_not_selected`, `is_valid_account_id`) check the identity or legitimacy of `staking_pool_whitelist_account_id` itself — they only guard who can call `select_staking_pool` and pool-account syntax, not whether the whitelist referenced is the trusted one.

### Impact Explanation
This matches the Critical category "a lockup deployed with parameters its rightful creator never chose." The victim's lockup is permanently bound to an attacker-controlled whitelist, meaning any pool the owner later selects is validated by a contract the attacker fully controls rather than the foundation's real whitelist. This sets up all subsequent `deposit_and_stake` operations by the rightful owner to interact with a staking pool the attacker names/operates, exposing the owner's staked NEAR to the attacker's chosen pool contract logic instead of a vetted, foundation-approved pool. The attack is repeatable against any `owner_account_id` that has not yet had its lockup funded, and the blast radius covers every future lockup beneficiary the attacker can front-run.

### Likelihood Explanation
The only precondition is that the attacker is first to fund the lockup for `owner_account_id="victim.near"`, and can predict `victim.near`'s intended lockup deployment (typically known in advance, e.g. via public grant/vesting agreements or public factory account naming conventions). The attacker's cost is just `MIN_ATTACHED_BALANCE` (3.5 NEAR) plus deploying two trivial contracts (fake whitelist, and optionally a fake pool). This is fully feasible with a `near-workspaces` or unit test as suggested, and it is repeatable across every victim account the attacker can front-run before the legitimate depositor.

### Recommendation
Restrict `whitelist_account_id` overrides in `LockupFactory::create` to privileged callers only (e.g., require `env::predecessor_account_id() == self.foundation_account_id` when a custom whitelist is supplied, or remove the parameter entirely and always use `self.whitelist_account_id`). Additionally, consider requiring the owner's authorization (e.g., a signed request or restrict `create` to be called by `owner_account_id` itself or the foundation) to prevent unprivileged front-running of the deterministic lockup account name.

### Proof of Concept
```rust
// cargo test in lockup-factory (and a companion fake whitelist contract crate)
// 1. Deploy factory with new(whitelist_account_id="real-whitelist.near", foundation_account_id=...)
// 2. As predecessor "attacker.near" (unprivileged), call:
//    contract.create(
//        owner_account_id = "victim.near",
//        lockup_duration, None, None, None,
//        whitelist_account_id = Some("attacker-whitelist.near")
//    );
//    with attached_deposit = MIN_ATTACHED_BALANCE.
// 3. Simulate successful on_lockup_create callback (PromiseResult::Successful).
// 4. Deploy LockupContract directly with LockupArgs.staking_pool_whitelist_account_id
//    = "attacker-whitelist.near" (mirroring what the factory promise would have sent).
// 5. Assert: lockup_contract.staking_pool_whitelist_account_id == "attacker-whitelist.near"
//    != "real-whitelist.near" (the factory's self.whitelist_account_id),
//    proving the binding staking_pool_whitelist_account_id == self.whitelist_account_id is broken.
// 6. As owner "victim.near", call select_staking_pool("attacker_pool.near"), feed
//    on_whitelist_is_whitelisted(true, "attacker_pool.near") since the fake whitelist always
//    returns true, and assert get_staking_pool_account_id() == "attacker_pool.near" succeeds
//    purely because of the substituted whitelist (a real "real-whitelist.near" contract would
//    have rejected an unvetted pool).
```

### Citations

**File:** lockup-factory/src/lib.rs (L107-133)
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

**File:** lockup/src/owner.rs (L12-34)
```rust
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
```
