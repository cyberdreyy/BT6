### Title
Deterministic, unauthenticated lockup address allows front-running a victim's vesting-hash commitment - (`lockup-factory/src/lib.rs`)

### Summary
`LockupFactory::create` derives the lockup account address purely from `sha256(owner_account_id)` and performs no check that the caller is entitled to create a lockup for that `owner_account_id`, nor any reservation/uniqueness guard beyond the NEAR account-creation itself. Any unprivileged caller can call `create` first with `owner_account_id = victim.near` and a `VestingScheduleOrHash::VestingHash` computed from a vesting schedule/salt of their own choosing, permanently occupying the one deterministic address the real grantor's later `create` call would target.

### Finding Description
Binding claimed: `deployed_lockup(victim.near).vesting_information` (`VestingInformation::VestingHash`) `== VestingScheduleWithSalt{ grantor_schedule, grantor_salt }.hash()`, i.e. the hash the entitled grantor actually agreed on.

Code path:
- `LockupFactory::create` computes the target account deterministically and with no ownership check: [1](#0-0) 
- The lockup's `vesting_schedule` argument (attacker-supplied `VestingScheduleOrHash::VestingHash(...)`) is forwarded verbatim into `LockupArgs` and passed to the new contract's `new`: [2](#0-1) 
- `LockupContract::new` stores whatever hash it receives as `VestingInformation::VestingHash(hash)` with no cross-check against any other party's expectation: [3](#0-2) 
- `VestingScheduleWithSalt::hash()` is a pure, unauthenticated `sha256` of borsh-serialized `(vesting_schedule, salt)` — anyone can compute it for any schedule/salt they invent: [4](#0-3) 
- On collision, `Promise::new(lockup_account_id).create_account()` fails for the second (legitimate) caller because the account already exists; the callback detects this via `is_promise_success()` and simply refunds the deposit instead of deploying: [5](#0-4) 

Exploit flow: the attacker (any unprivileged account) calls `create(owner_account_id="victim.near", ..., vesting_schedule=Some(VestingScheduleOrHash::VestingHash(VestingScheduleWithSalt{attacker_schedule, attacker_salt}.hash())), ...)` attaching `MIN_ATTACHED_BALANCE`. This deploys a lockup at the deterministic address `hex(sha256("victim.near")[..20]).<factory>` whose `VestingInformation::VestingHash` is the attacker's hash. When the legitimate grantor later calls `create` with the same `owner_account_id` and the real hash, `create_account()` fails (account already exists), `on_lockup_create` observes `is_promise_success() == false`, refunds the grantor's deposit, and returns `false` — the real hash is never stored anywhere reachable at that canonical address.

No existing guard (`assert_self`, `is_promise_success`, `assert!(attached_deposit >= MIN_ATTACHED_BALANCE)`, `is_valid_account_id`) restricts *who* may name `owner_account_id`, so this divergence is not prevented anywhere in the call path. [6](#0-5) 

### Impact Explanation
The squatted lockup is still owned by `victim.near` (the `owner_account_id` field is unaffected), so this is not a direct fund-theft primitive — the attacher's own attached deposit ends up in a contract the victim controls. The concrete harm is that a lockup gets deployed with vesting parameters ("hash") its rightful creator never chose, and the real grantor's intended vesting commitment can never be bound to that address again, since the address is already occupied and NEAR account creation cannot be retried at that path. This matches the Critical category "a lockup deployed with parameters its rightful creator never chose." It is repeatable for every distinct `owner_account_id` an attacker wants to preempt, at the cost of one `MIN_ATTACHED_BALANCE` (3.5 NEAR) deposit per victim, and blocks the intended grantor-controlled vesting/termination semantics (e.g., `terminate_vesting`'s hash check in `lockup/src/internal.rs::assert_vesting`) from ever applying to that account. Note: because `get_locked_amount` treats any `VestingInformation::VestingHash` as contributing `0` to `unvested_amount` until `terminate_vesting` reveals it, the hash mismatch by itself does not change day-to-day owner liquidity; the primary damage is address squatting / permanent inability to bind the legitimate vesting commitment, not early token release.

### Likelihood Explanation
Trivial and cheap: no privileged role, no victim key, and no coordination is required. The attacker only needs to know or guess the `owner_account_id` they want to squat and front-run the legitimate `create` call with the minimum deposit before it lands. This is fully repeatable across any number of target accounts and requires no specific epoch or contract-state precondition.

### Recommendation
Add an explicit authorization check in `LockupFactory::create` binding `predecessor_account_id` to `owner_account_id` (or require a signature/allowlist from the intended owner/foundation) before allowing account creation at the deterministic address, or otherwise make the lockup address depend on an unpredictable/attacker-uncontrollable component (e.g., a nonce chosen by an authorized party) so it cannot be squatted ahead of the legitimate deployment.

### Proof of Concept
```rust
// lockup-factory/src/lib.rs test module
#[test]
fn test_vesting_hash_squatting() {
    let mut context = VMContextBuilder::new()
        .current_account_id(account_factory())
        .predecessor_account_id(account_near())
        .finish();
    testing_env!(context.clone());
    let mut contract = LockupFactory::new(whitelist_account_id(), foundation_account_id());

    let lockup_duration: WrappedTimestamp = 63036000000000000u64.into();

    // Attacker precomputes their own hash for a schedule the victim/grantor never agreed to.
    let attacker_schedule = new_vesting_schedule(999);
    let attacker_hash = VestingScheduleWithSalt {
        vesting_schedule: attacker_schedule,
        salt: b"attacker_salt".to_vec().into(),
    }.hash();

    context.predecessor_account_id = String::from("attacker.near");
    context.attached_deposit = ntoy(35);
    testing_env!(context.clone());
    contract.create(
        account_tokens_owner(), // owner_account_id == "victim" alias used by test_utils
        lockup_duration,
        None,
        Some(VestingScheduleOrHash::VestingHash(attacker_hash.clone().into())),
        None,
        None,
    );
    context.predecessor_account_id = account_factory();
    context.attached_deposit = ntoy(0);
    testing_env_with_promise_results(context.clone(), PromiseResult::Successful(vec![]));
    contract.on_lockup_create(lockup_account(), ntoy(30).into(), String::from("attacker.near"));

    // Real grantor computes the legitimate hash and tries to create at the same address.
    let real_schedule = new_vesting_schedule(10);
    let real_hash = VestingScheduleWithSalt {
        vesting_schedule: real_schedule,
        salt: b"real_salt".to_vec().into(),
    }.hash();
    assert_ne!(attacker_hash, real_hash);

    context.predecessor_account_id = String::from("grantor.near");
    context.attached_deposit = ntoy(35);
    testing_env!(context.clone());
    contract.create(
        account_tokens_owner(),
        lockup_duration,
        None,
        Some(VestingScheduleOrHash::VestingHash(real_hash.clone().into())),
        None,
        None,
    );
    context.predecessor_account_id = account_factory();
    context.attached_deposit = ntoy(0);
    // create_account fails: account already exists from the attacker's deployment.
    testing_env_with_promise_results(context.clone(), PromiseResult::Failed);
    let created = contract.on_lockup_create(lockup_account(), ntoy(35).into(), String::from("grantor.near"));
    assert_eq!(created, false); // legitimate deployment never happens

    // Assert: only the attacker's hash is (and can ever be) authoritative at that address.
    // In an integration test (near-workspaces) this is verified by viewing the deployed
    // lockup's `get_vesting_information()` and checking it equals
    // VestingInformation::VestingHash(attacker_hash), never real_hash.
}
```

### Citations

**File:** lockup-factory/src/lib.rs (L108-126)
```rust
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

**File:** lockup/src/lib.rs (L216-228)
```rust
        let vesting_information = match vesting_schedule {
            None => {
                assert!(
                    foundation_account_id.is_none(),
                    "Foundation account can't be added without vesting schedule"
                );
                VestingInformation::None
            }
            Some(VestingScheduleOrHash::VestingHash(hash)) => VestingInformation::VestingHash(hash),
            Some(VestingScheduleOrHash::VestingSchedule(vs)) => {
                VestingInformation::VestingSchedule(vs)
            }
        };
```

**File:** lockup/src/types.rs (L196-209)
```rust
/// Contains a vesting schedule with a salt.
#[derive(BorshSerialize, Deserialize, Serialize, Clone, Debug)]
#[serde(crate = "near_sdk::serde")]
pub struct VestingScheduleWithSalt {
    /// The vesting schedule
    pub vesting_schedule: VestingSchedule,
    /// Salt to make the hash unique
    pub salt: Base64VecU8,
}

impl VestingScheduleWithSalt {
    pub fn hash(&self) -> Hash {
        env::sha256(&self.try_to_vec().expect("Failed to serialize"))
    }
```
