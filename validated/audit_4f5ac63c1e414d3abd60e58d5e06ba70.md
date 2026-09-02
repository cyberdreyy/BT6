### Title
Front-runnable deterministic lockup address lets an unprivileged funder permanently choose `staking_pool_whitelist_account_id` for any `owner_account_id` - ([File: lockup-factory/src/lib.rs])

### Summary
`LockupFactory::create` derives the lockup's account ID deterministically as `sha256(owner_account_id)[..20].<factory>` and lets **any** caller who pays `MIN_ATTACHED_BALANCE` pick an arbitrary `whitelist_account_id` that gets baked immutably into the deployed lockup's `staking_pool_whitelist_account_id`. Because NEAR account names are globally unique and `create_account` fails if the target already exists, whoever calls `create()` first for a given `owner_account_id` permanently owns that address and its whitelist choice; the legitimate party's later `create()` attempt for the same `owner_account_id` fails and is merely refunded, never overwriting the attacker's deployment.

### Finding Description
Binding that should hold: `lockup(sha256(<victim>)).staking_pool_whitelist_account_id == whitelist the rightful owner/funder of <victim> actually chose`. In practice it equals whatever `whitelist_account_id` the first successful caller of `create()` supplied, and that caller need not be, and need not be authorized by, `<victim>`.

Code path:
- `lockup-factory/src/lib.rs` `create()` computes the lockup address purely from `owner_account_id` with no signature or authorization check tying it to `<victim>`'s keys: [1](#0-0) 
- The resulting `Promise::new(lockup_account_id).create_account()...` batch is atomic; if the account already exists, the whole batch fails, and `on_lockup_create` simply refunds the caller's deposit and returns `false` — it never overwrites an existing deployment: [2](#0-1) 
- The `staking_pool_whitelist_account_id` is set once at `new` time and stored as an immutable field; `owner.rs` has no setter to change it later — only `select_staking_pool`/`unselect_staking_pool` exist, and `select_staking_pool` unconditionally trusts the stored value: [3](#0-2) 
- `on_whitelist_is_whitelisted` accepts whatever the whitelist contract (attacker-controlled `fake_whitelist`) answers, with no further validation: [4](#0-3) 
- `get_known_deposited_balance()` merely echoes `staking_information.deposit_amount`, which is correctly bookkept but is meaningless as a "recoverable balance" once the pool behind an attacker-chosen whitelist is not a genuine staking pool: [5](#0-4) 

Attacker action: call `lockup-factory.create(owner_account_id=<victim>, whitelist_account_id=Some(<fake_whitelist>))` with the minimum attached deposit, before the legitimate funder/foundation does so for `<victim>`. This permanently occupies the deterministic lockup address for `<victim>` and bakes in the attacker's whitelist. `assert_owner()` still protects `select_staking_pool` from being called directly by the attacker, but it does nothing to protect the *choice of whitelist*, which was fixed at deployment by an unprivileged third party, not by `<victim>`. When `<victim>` later (legitimately, using their own keys) calls `select_staking_pool(attacker_pool)`, the lockup checks `attacker_pool` only against the attacker's own `fake_whitelist`, which returns `true` unconditionally, so a non-functional/malicious pool is accepted as if vetted.

This matches the explicitly listed Critical category "a lockup deployed with parameters its rightful creator never chose" — here `staking_pool_whitelist_account_id` is exactly such a parameter, chosen by an unprivileged attacker rather than `<victim>` or the intended funder, and it cannot be corrected after the fact since there is no setter and the legitimate `create()` for the same `owner_account_id` can never succeed once squatted.

### Impact Explanation
Every `<victim>` account name is squattable pre-emptively at negligible cost (`MIN_ATTACHED_BALANCE`, refundable list-price ~3.5 NEAR) by observing/guessing intended `owner_account_id`s (e.g., known employee accounts, well-known testnets/mainnet naming conventions). The permanent effect is: (a) the legitimate lockup for `<victim>` can never be created (any later legitimate `create()` fails and refunds, denying `<victim>` a lockup entirely), or (b) `<victim>` unknowingly operates a lockup whose staking-pool trust anchor was chosen by an adversary, so any staking pool the attacker's fake whitelist "approves" is accepted without further validation — setting up loss of whatever NEAR later ends up in that lockup account. This is repeatable against any target `owner_account_id` and scales to the whole factory, so blast radius is broad. It matches the Critical bucket ("a lockup deployed with parameters its rightful creator never chose").

### Likelihood Explanation
No privileged access is required: any unprivileged account can call the public, payable `create()` method with attacker-chosen `owner_account_id` and `whitelist_account_id` and the minimum deposit, which is refundable if front-running fails and non-refundable-but-cheap if it succeeds. The only precondition is winning the race against the legitimate funder/foundation for a given target `owner_account_id`, which is entirely feasible since target names (employees, grant recipients) are often known or predictable in advance of the real deployment.

### Recommendation
Do not derive the lockup account id purely from `owner_account_id` reachable by arbitrary funders; require the `create()` caller to be an authorized/whitelisted funder (e.g., restricted to `foundation_account_id` or a signed authorization from `owner_account_id`), or bind the deterministic address to a value only the legitimate party can produce (e.g., include a secret/salt known only to the real owner/funder, or require `predecessor_account_id == owner_account_id` for self-service creation). Additionally, do not allow the funder to freely choose `staking_pool_whitelist_account_id`; it should default to a single foundation-controlled value with no override, or require the lockup owner to explicitly opt into/confirm the whitelist post-creation via an owner-authenticated call.

### Proof of Concept
`near-sdk-sim` / `near-workspaces` test in `lockup-factory/tests`:
1. Deploy `LockupFactory` with legitimate `whitelist_account_id = real_whitelist`.
2. As `attacker`, call `create(owner_account_id = "victim", whitelist_account_id = Some("fake_whitelist"))` with `MIN_ATTACHED_BALANCE`; assert the lockup account `sha256("victim")[..20].factory` is created and `on_lockup_create` returns `true`.
3. As `foundation` (the legitimate funder), call `create(owner_account_id = "victim", whitelist_account_id = None)` with proper deposit for the same target; assert the `create_account` action fails, `on_lockup_create` returns `false`, and the deposit is refunded to `foundation` — proving the legitimate lockup for `victim` can never be established.
4. View the squatted lockup's `get_owner_account_id()` == `"victim"` and confirm (via inspecting the deployed `staking_pool_whitelist_account_id`, e.g. through a subsequent `select_staking_pool` call against `fake_whitelist` returning unconditional `true`) that the whitelist baked in is `fake_whitelist`, not `real_whitelist`, i.e. `lockup.staking_pool_whitelist_account_id (fake_whitelist) != whitelist "victim"/foundation ever chose (real_whitelist)`.

### Citations

**File:** lockup-factory/src/lib.rs (L117-133)
```rust
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

**File:** lockup-factory/src/lib.rs (L136-198)
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
            .then(ext_self::on_lockup_create(
                lockup_account_id,
                env::attached_deposit().into(),
                env::predecessor_account_id(),
                &env::current_account_id(),
                NO_DEPOSIT,
                gas::CALLBACK,
            ))
    }

    /// Callback after a lockup was created.
    /// Returns the promise if the lockup creation succeeded.
    /// Otherwise refunds the attached deposit and returns `false`.
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

**File:** lockup/src/getters.rs (L20-30)
```rust
    /// Returns the amount of tokens that were deposited to the staking pool.
    /// NOTE: The actual balance can be larger than this known deposit balance due to staking
    /// rewards acquired on the staking pool.
    /// To refresh the amount the owner can call `refresh_staking_pool_balance`.
    pub fn get_known_deposited_balance(&self) -> WrappedBalance {
        self.staking_information
            .as_ref()
            .map(|info| info.deposit_amount.0)
            .unwrap_or(0)
            .into()
    }
```
