### Title
Front-runnable lockup creation lets an unprivileged attacker set a hostile `staking_pool_whitelist_account_id` for a victim's lockup - (File: `lockup-factory/src/lib.rs`)

### Summary
`LockupFactory::create` is a public, unprivileged `#[payable]` method whose target lockup account is derived deterministically from `owner_account_id` alone, and it accepts an optional `whitelist_account_id` with no check that the caller is the NEAR Foundation or any other privileged party. An attacker can front-run the legitimate creation transaction for a known `owner_account_id`, supplying their own malicious whitelist contract, permanently binding that owner's lockup to an attacker-controlled "whitelist" that will approve any staking pool.

### Finding Description
The broken binding: for a given `owner_account_id`, the deployed lockup's `staking_pool_whitelist_account_id` should equal the value the rightful/intended creator (typically the NEAR Foundation) chose — i.e. `lockup(owner).staking_pool_whitelist_account_id == intended_whitelist`. Instead it equals whatever the first successful `create()` caller supplied.

Code path:
- `create()` computes the lockup account deterministically from only the `owner_account_id` hash: `lockup_account_id = sha256(owner_account_id)[..20] + factory_account` [1](#0-0) .
- `create()` has no `assert_called_by_foundation`, no owner/predecessor check, and lets the caller optionally override the canonical whitelist via `whitelist_account_id`, defaulting only if omitted [2](#0-1) .
- The chosen `staking_pool_whitelist_account_id` is baked into the `LockupArgs` sent to `new` on the newly created lockup account and is not adjustable afterward by anyone (no setter exists in the lockup contract; it is only read by `select_staking_pool`) [3](#0-2) [4](#0-3) .
- `on_lockup_create` only rolls back the deposit if the promise chain fails (e.g. because the account already exists); it performs no validation of who is entitled to create the lockup [5](#0-4) .

Exploit flow: since the target account name depends only on `owner_account_id` (public knowledge for any intended lockup recipient), an attacker submits `create(owner_account_id, ..., whitelist_account_id = Some(attacker_whitelist))` with `MIN_ATTACHED_BALANCE` one block ahead of the legitimate creator. NEAR account creation is first-come-first-served, so the attacker's `create_account()` succeeds and initializes the lockup with the hostile whitelist; the legitimate creator's later `create()` call fails at `create_account()` (already exists), refunding their deposit via `on_lockup_create`'s failure branch. The victim's lockup — now correctly owned by the intended `owner_account_id` — is permanently stuck consulting `attacker_whitelist` instead of the canonical `staking-pool-whitelist` when the owner later calls `select_staking_pool`, which calls `ext_whitelist::is_whitelisted` against `self.staking_pool_whitelist_account_id` [4](#0-3) . A malicious whitelist contract that always returns `true` defeats the entire pool-vetting invariant described in `whitelist/README.md`, which exists specifically so lockups only delegate to Foundation-approved pools [6](#0-5) .

No existing guard (`assert_self`, `assert_owner`, `is_promise_success`, etc.) prevents this because the flaw is authorization-at-creation, not any of the runtime invariants those guards protect.

### Impact Explanation
The owner of the affected lockup is permanently deprived of the Foundation-vetted whitelist safety check. Any staking pool account the owner later selects — including one the attacker deploys and controls — will be reported "whitelisted" regardless of its real trustworthiness, enabling delegated NEAR to be routed to and effectively lost in an attacker-parameterized contract that the lockup treats as trusted. This matches the Critical category "a lockup deployed with parameters its rightful creator never chose." The attack is repeatable against every lockup whose `owner_account_id` is predictable/public in advance and where the legitimate creation transaction can be outraced.

### Likelihood Explanation
Preconditions: attacker needs only `MIN_ATTACHED_BALANCE` (3.5 NEAR) and knowledge of the target `owner_account_id` ahead of the legitimate creation transaction landing on-chain — a realistic scenario since lockup owner IDs are typically known/announced in advance of Foundation-driven distribution. `create()` is fully public with no allowlist. Cost is low, and the race only requires the attacker's transaction to land in an earlier block than the legitimate one.

### Recommendation
Restrict `create()` to only allow overriding `whitelist_account_id` when called by the Foundation (`assert_called_by_foundation`-style check), or remove the caller-supplied `whitelist_account_id` parameter entirely and always use the factory's canonical `whitelist_account_id`. Additionally, consider deriving the lockup account id from a value that includes the caller's identity/authorization, or requiring a Foundation-signed pre-registration step, to prevent unprivileged front-running of lockup creation for a given owner.

### Proof of Concept
A `near-sdk-sim`/`near-workspaces` test would:
1. Initialize `LockupFactory` with a legitimate `whitelist_account_id` (canonical).
2. Deploy a "hostile" whitelist contract (e.g. reuse `whitelist/` contract but never call `add_factory`/`add_staking_pool` — instead deploy a mock that always returns `true` for `is_whitelisted`).
3. As an unprivileged attacker account, call `factory.create(owner_account_id, ..., whitelist_account_id: Some(hostile_whitelist))` with `MIN_ATTACHED_BALANCE`, succeeding before any legitimate call.
4. As the legitimate creator, call `factory.create(same owner_account_id, ..., whitelist_account_id: None)` and assert the promise fails on `create_account()` (account already exists), and that `on_lockup_create` refunds the legitimate caller (assert `false` returned, deposit transferred back).
5. As the (legitimate) `owner_account_id`, call `select_staking_pool(malicious_pool_account_id)` on the deployed lockup and assert it succeeds (`is_whitelisted` returns `true` from the hostile whitelist) even though `malicious_pool_account_id` was never added to the canonical `whitelist` contract — proving `lockup.staking_pool_whitelist_account_id != canonical_whitelist_account_id`, breaking the intended equality.

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

**File:** lockup/src/owner.rs (L12-41)
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
        .then(ext_self_owner::on_whitelist_is_whitelisted(
            staking_pool_account_id,
            &env::current_account_id(),
            NO_DEPOSIT,
            gas::owner_callbacks::ON_WHITELIST_IS_WHITELISTED,
        ))
    }
```

**File:** whitelist/README.md (L1-17)
```markdown
# Whitelist contract for staking pools

The purpose of this contract is to maintain the whitelist of the staking pool contracts account IDs that are approved
by NEAR Foundation.

In order for the lockup contracts to be able delegate to a staking pool, the staking pool should faithfully implement the spec.
The staking pool should guarantee that the delegated tokens can not be lost or locked, such as the lockup contract should be
able to recover delegated tokens back to the lockup from a staking pool. In order to enforce this, only approved (whitelisted)
accounts of staking pool contracts can receive delegated tokens from lockup contracts.

If NEAR Foundation has to approve every single staking pool account it might lead to a bottleneck and centralization
To address this NEAR Foundation can whitelist the account IDs of staking pool factory contracts.

The whitelisted staking pool factory contract will be able to whitelist accounts of staking pool contracts.
A factory contract creates and initializes a staking pool contract in a secure and permissionless way.
This allows anyone on the network to be able to create a staking pool contract for themselves without needing approval from the NEAR
Foundation. This is important to maintain the decentralization of the decision making and network governance.
```
