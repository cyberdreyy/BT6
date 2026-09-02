### Title
Unprivileged front-running of `LockupFactory::create` lets an attacker plant an attacker-controlled `staking_pool_whitelist_account_id` into a victim's deterministic lockup - ([File: lockup-factory/src/lib.rs])

### Summary
`LockupFactory::create` computes the lockup's account ID deterministically from `sha256(owner_account_id)` and accepts a caller-supplied, unchecked `whitelist_account_id: Option<ValidAccountId>` that is written verbatim into the deployed `LockupContract`'s `staking_pool_whitelist_account_id` field. Because `create` has no restriction on who the caller (predecessor) is and only requires attaching `MIN_ATTACHED_BALANCE` (3.5 NEAR), an unprivileged attacker can race ("front-run") the rightful creator (the foundation/funder) and be first to create the deterministic lockup account for `owner_account_id = "victim.near"`, substituting their own whitelist contract for the factory's default.

### Finding Description
Binding claimed: `lockup.staking_pool_whitelist_account_id == self.whitelist_account_id` (the factory's own whitelist, which is what the rightful creator/foundation is entitled to have used unless they themselves deliberately pass an override). After the attacker's transaction, the actual stored value is `attacker-whitelist.near`, not the factory default and not anything the victim's legitimate funder chose.

Code path:
- `lockup_account_id` is fully deterministic: `format!("{}.{}", hex::encode(sha256(owner_account_id)[..20]), env::current_account_id())` [1](#0-0) .
- `whitelist_account_id: Option<ValidAccountId>` is an attacker-controlled function argument used directly, falling back to `self.whitelist_account_id` only if omitted: [2](#0-1) .
- `create` has no `assert_owner`/`assert_called_by_foundation`/predecessor check at all — the only guard is the attached-deposit assertion: [3](#0-2) .
- The chosen value is passed straight into the `LockupArgs` sent to `LockupContract::new`, which stores it unchanged into `staking_pool_whitelist_account_id`, only validating that it is a syntactically valid account ID (not that it equals the factory whitelist or is controlled by the foundation): [4](#0-3) .
- Later, `LockupContract::select_staking_pool` (owner-only) validates the chosen staking pool exclusively against `self.staking_pool_whitelist_account_id` via `ext_whitelist::is_whitelisted`, and `on_whitelist_is_whitelisted` blindly trusts the callback result: [5](#0-4) [6](#0-5) .

Root cause: NEAR account creation is atomic and unique — the account ID for a given `owner_account_id` can only be created once. Since `create()` imposes no restriction on the caller and the lockup account name is fully predictable from `owner_account_id`, whichever transaction lands first (attacker's or the legitimate funder's) permanently fixes the lockup's parameters, including `staking_pool_whitelist_account_id`. If the attacker wins the race, the legitimate funder's later `create()` call for the same `owner_account_id` fails at `create_account()` (account already exists), the whole promise chain fails, and `on_lockup_create` refunds the legitimate funder's deposit rather than creating a second lockup — confirmed by the existing rollback test pattern: [7](#0-6) , [8](#0-7) .

Exploit flow:
1. Attacker deploys a contract at `attacker-whitelist.near` whose `is_whitelisted` always returns `true` (allowed — attacker may deploy and name any contract they control, per the rules).
2. Attacker observes (or predicts) that a lockup for `owner_account_id = "victim.near"` is about to be created and calls `LockupFactory::create(owner_account_id = "victim.near", ..., whitelist_account_id = Some("attacker-whitelist.near"))`, attaching ≥ `MIN_ATTACHED_BALANCE`, before the legitimate funder's transaction executes.
3. The deterministic account `<hash(victim.near)>.<factory>` is created with `staking_pool_whitelist_account_id = "attacker-whitelist.near"`.
4. The legitimate funder's later `create()` call for the same `owner_account_id` fails (`create_account` on an already-existing account), and the deposit is refunded via `on_lockup_create`'s rollback branch, but the lockup with the poisoned whitelist persists.
5. When `victim.near` (the true owner) eventually calls `select_staking_pool(attacker_pool)`, the check is made only against `attacker-whitelist.near`, which returns `true` for any pool name, including an attacker-operated staking pool, defeating the intended NEAR-Foundation-controlled whitelist.

No existing guard (`assert_owner`, `assert_self`, `is_promise_success`, `is_valid_account_id`, etc.) prevents this because none of them check that the caller of `LockupFactory::create` is the foundation/rightful funder, nor that `whitelist_account_id` equals the factory's own `self.whitelist_account_id`.

### Impact Explanation
This breaks the invariant that "an account [is] whitelisted or a lockup [is] deployed with parameters its rightful creator never chose" — explicitly a Critical-severity category. Once `victim.near`'s owner later selects a staking pool via `select_staking_pool`, the attacker's forged whitelist unconditionally approves it, letting the owner unknowingly delegate stake to an attacker-controlled staking pool that never went through real NEAR Foundation vetting. From there, funds delegated to that malicious pool can be withheld or manipulated by the pool operator (the attacker), i.e. NEAR ultimately controlled by the victim's lockup can end up under an unvetted validator the attacker operates. The attack is repeatable across any `owner_account_id` value the attacker can predict/race, at a fixed cost of `MIN_ATTACHED_BALANCE` (3.5 NEAR, refunded on failure/lost only on success of the race) per attempt.

### Likelihood Explanation
Preconditions: attacker must win a race against the rightful funder's `create()` transaction for the same `owner_account_id`. This is feasible because: (a) the lockup account name is fully deterministic and computable off-chain in advance by anyone who knows the intended `owner_account_id` (e.g., observed from a pending/mempool transaction, a public announcement of upcoming grants, or simple prediction of common employee/account naming); (b) the minimum deposit (3.5 NEAR) is cheap and refunded if the race is lost; (c) no whitelist/permission is required to call `create`, matching the documented design ("It allows any user to create and fund the lockup contract" per `lockup-factory/README.md`). The likelihood is tied to the attacker's ability to front-run a specific transaction, which is a standard blockchain race condition, not a theoretical concern.

### Recommendation
Restrict the `whitelist_account_id` override in `LockupFactory::create` to the foundation only (e.g., `assert_eq!(env::predecessor_account_id(), self.foundation_account_id)` when `whitelist_account_id.is_some()`), or remove the caller-supplied override entirely and always use `self.whitelist_account_id`. Additionally, consider requiring that `create` for a given `owner_account_id` can only be funded by the foundation or by the `owner_account_id` itself, to prevent unauthorized parties from claiming/poisoning the deterministic lockup account before the rightful funder.

### Proof of Concept
```rust
// lockup-factory/src/lib.rs tests module (near-sdk-sim / near-workspaces style)
#[test]
fn test_attacker_frontrun_custom_whitelist() {
    // 1. Deploy factory with default whitelist = "whitelist.near"
    // 2. Deploy attacker's fake whitelist contract "attacker-whitelist.near"
    //    with is_whitelisted() hardcoded to always return true.
    // 3. Attacker calls:
    //    factory.create(
    //        owner_account_id: "victim.near",
    //        lockup_duration: ...,
    //        lockup_timestamp: None,
    //        vesting_schedule: None,
    //        release_duration: None,
    //        whitelist_account_id: Some("attacker-whitelist.near"),
    //    )
    //    attaching MIN_ATTACHED_BALANCE, predecessor = attacker.near
    // 4. Assert the deterministic lockup account
    //    <sha256("victim.near")[..20]>.<factory> now exists with
    //    staking_pool_whitelist_account_id == "attacker-whitelist.near"
    //    (NOT equal to factory.whitelist_account_id).
    //
    // 5. Simulate the legitimate funder's later create() call for the same
    //    owner_account_id -> account already exists -> create_account fails
    //    -> on_lockup_create rolls back and refunds the legitimate funder,
    //    proving the attacker's lockup is the one that persists.
    //
    // 6. As "victim.near" (owner), call select_staking_pool("attacker_pool")
    //    -> triggers is_whitelisted on "attacker-whitelist.near" -> true
    //    -> on_whitelist_is_whitelisted(true, "attacker_pool") succeeds.
    // assert_eq!(lockup.get_staking_pool_account_id(), Some("attacker_pool".to_string()));
    // This succeeds ONLY because staking_pool_whitelist_account_id was
    // substituted by the attacker, proving the binding
    // `staking_pool_whitelist_account_id == self.whitelist_account_id` is broken.
}
```

### Citations

**File:** lockup-factory/src/lib.rs (L107-117)
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
```

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

**File:** lockup-factory/src/lib.rs (L352-388)
```rust
    #[test]
    fn test_create_lockup_rollback() {
        let mut context = VMContextBuilder::new()
            .current_account_id(account_factory())
            .predecessor_account_id(account_near())
            .finish();
        testing_env!(context.clone());

        let mut contract = LockupFactory::new(
            whitelist_account_id(),
            foundation_account_id(),
        );

        const LOCKUP_DURATION: u64 = 63036000000000000; /* 24 months */
        let lockup_duration: WrappedTimestamp = LOCKUP_DURATION.into();

        context.is_view = false;
        context.predecessor_account_id = String::from(account_tokens_owner());
        context.attached_deposit = ntoy(35);
        testing_env!(context.clone());
        contract.create(account_tokens_owner(), lockup_duration, None, None, None, None);

        context.predecessor_account_id = account_factory();
        context.attached_deposit = ntoy(0);
        context.account_balance += ntoy(35);
        testing_env_with_promise_results(context.clone(), PromiseResult::Failed);
        let res = contract.on_lockup_create(
            lockup_account(),
            ntoy(35).into(),
            String::from(account_tokens_owner()),
        );

        match res {
            true => panic!("Unexpected result, should return false"),
            false => assert!(true),
        };
    }
```

**File:** lockup/src/lib.rs (L188-243)
```rust
        staking_pool_whitelist_account_id: AccountId,
        foundation_account_id: Option<AccountId>,
    ) -> Self {
        assert!(
            env::is_valid_account_id(owner_account_id.as_bytes()),
            "The account ID of the owner is invalid"
        );
        assert!(
            env::is_valid_account_id(staking_pool_whitelist_account_id.as_bytes()),
            "The staking pool whitelist account ID is invalid"
        );
        if let TransfersInformation::TransfersDisabled {
            transfer_poll_account_id,
        } = &transfers_information
        {
            assert!(
                env::is_valid_account_id(transfer_poll_account_id.as_bytes()),
                "The transfer poll account ID is invalid"
            );
        }
        let lockup_information = LockupInformation {
            lockup_amount: env::account_balance(),
            termination_withdrawn_tokens: 0,
            lockup_duration: lockup_duration.0,
            release_duration: release_duration.map(|d| d.0),
            lockup_timestamp: lockup_timestamp.map(|d| d.0),
            transfers_information,
        };
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
        assert!(
            vesting_information == VestingInformation::None ||
                env::is_valid_account_id(foundation_account_id.as_ref().unwrap().as_bytes()),
            "Foundation account should be added for vesting schedule"
        );

        Self {
            owner_account_id,
            lockup_information,
            vesting_information,
            staking_information: None,
            staking_pool_whitelist_account_id,
            foundation_account_id,
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

**File:** lockup/src/owner_callbacks.rs (L7-25)
```rust
    pub fn on_whitelist_is_whitelisted(
        &mut self,
        #[callback] is_whitelisted: bool,
        staking_pool_account_id: AccountId,
    ) -> bool {
        assert_self();
        assert!(
            is_whitelisted,
            "The given staking pool account ID is not whitelisted"
        );
        self.assert_staking_pool_is_not_selected();
        self.assert_no_termination();
        self.staking_information = Some(StakingInformation {
            staking_pool_account_id,
            status: TransactionStatus::Idle,
            deposit_amount: 0.into(),
        });
        true
    }
```
