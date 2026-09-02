### Title
Deterministic lockup account ID excludes `vesting_schedule`/`whitelist_account_id`, letting an attacker front-run and deploy an unfair lockup for a victim owner - ([File: lockup-factory/src/lib.rs])

### Summary
`LockupFactory::create` derives the deployed lockup contract's account ID solely from `owner_account_id` (`sha256(owner_account_id)` prefixed to the factory account), while all the terms that actually matter for the lockup's fairness — `vesting_schedule`, `lockup_duration`, `release_duration`, and `staking_pool_whitelist_account_id` — are excluded from that identifier. [1](#0-0)  Because "any user" is permitted to call `create` for any `owner_account_id` and attach only the minimum deposit, [2](#0-1)  an attacker can pre-emptively claim the deterministic address for a victim's future lockup with attacker-chosen (unfair) parameters before the legitimate funder/foundation does so, exactly analogous to the reported `startSqrtPriceX96` issue where an arbitrary, attacker-supplied value was left out of the identifying key.

### Finding Description
`create()` computes the lockup's account id purely from the owner:
```rust
let byte_slice = env::sha256(owner_account_id.as_ref().as_bytes());
let lockup_account_id = format!("{}.{}", hex::encode(&byte_slice[..20]), env::current_account_id());
``` [3](#0-2) 

but the constructor arguments sent to the newly deployed lockup — `lockup_duration`, `vesting_schedule`, `release_duration`, `staking_pool_whitelist_account_id` — are all caller-supplied and not bound to that address at all: [4](#0-3)  and [5](#0-4) 

Because account creation on NEAR is a one-shot, deterministic-address operation, whichever `create()` call lands first for a given `owner_account_id` permanently occupies that address; any subsequent legitimate call for the same owner fails (the `create_account()` sub-promise fails) and the deposit is refunded via `on_lockup_create`. [6](#0-5) 

This lets an unprivileged attacker:
1. Watch for/anticipate a victim `owner_account_id` that a foundation or employer intends to fund with a real vesting lockup.
2. Call `create(owner_account_id = victim, ..., vesting_schedule = None, whitelist_account_id = Some(<attacker-controlled whitelist>))` with only the `MIN_ATTACHED_BALANCE`, claiming the deterministic address first.
3. Because `vesting_schedule` is `None`, the lockup is created with `foundation_account_id = None` and full `TransfersEnabled` at `TRANSFERS_STARTED`, i.e. no lock/vesting logic at all. [7](#0-6) 
4. The attacker also sets `staking_pool_whitelist_account_id` to an attacker-controlled whitelist contract instead of the factory's configured one. [8](#0-7) 

The legitimate funder's later `create()` call for the same owner, carrying the correct `vesting_schedule`/`release_duration`/official whitelist and the real deposit, fails outright (address already taken) and the funds bounce back — but any party who is unaware of this and instead sends NEAR directly to the well-known deterministic address (rather than going through the factory) would be funding a contract with none of the intended lock terms and a malicious staking-pool whitelist. This breaks the intended custody binding: *tokens sent to "the vesting lockup for owner X" == tokens actually restricted by X's schedule*. In the attacker's version, that equality fails — funds can be released immediately (no vesting) and can be staked only with whitelisted pools the attacker chose.

### Impact Explanation
This matches the Critical impact category: "locked or unvested tokens released early" and "a wrongly whitelisted or wrongly parameterised deployment." An owner/funder relying on the deterministic, owner-derived address believes the resulting contract enforces a specific vesting schedule and a trusted staking-pool whitelist, but the actual deployed code's parameters were chosen entirely by an unrelated attacker, since none of those parameters are part of the identifier that was supposed to uniquely represent "the lockup for owner X."

### Likelihood Explanation
`create` is a public, unprivileged, payable method — any account can call it for any `owner_account_id` by only providing `MIN_ATTACHED_BALANCE` (3.5 NEAR). [9](#0-8) [10](#0-9)  No special role, victim key, or redeploy is required — the attack requires only knowing (or predicting) the intended `owner_account_id` ahead of the legitimate funding transaction, which is realistic given lockup creations are typically publicly announced/scripted (e.g., `scripts/deploy_lockup.sh` prompts for a known owner account before submission).

### Recommendation
Bind the deterministic lockup address to the parameters that determine its fairness — e.g., derive `lockup_account_id` from a hash including `vesting_schedule`, `release_duration`, `lockup_duration`, and `staking_pool_whitelist_account_id` (not just `owner_account_id`), or require an explicit reservation/allow-list step controlled by the foundation before any address for a given owner can be claimed, or use a nonce/`owner_account_id` + salt scheme controlled by a trusted party so an unprivileged third party cannot pre-claim a victim's lockup address with arbitrary terms.

### Proof of Concept
1. Attacker (unprivileged) calls `LockupFactory::create` with `owner_account_id = "victim.near"`, `lockup_duration = 0`, `vesting_schedule = None`, `release_duration = None`, `whitelist_account_id = Some("attacker-whitelist.near")`, attaching `MIN_ATTACHED_BALANCE`.
2. The factory deploys the lockup at `sha256("victim.near")[..20].<factory>` with `TransfersInformation::TransfersEnabled`, no vesting/foundation lock, and the attacker's whitelist. [5](#0-4) 
3. The legitimate employer later attempts `create(owner_account_id = "victim.near", vesting_schedule = Some(real_schedule), ...)` — this fails because the account already exists at that same deterministic address; `on_lockup_create` refunds the deposit. [6](#0-5) 
4. If the employer (or anyone) instead sends NEAR directly to the known deterministic address believing it enforces vesting, the funds sit in a contract with no vesting schedule and an attacker-controlled staking whitelist, i.e., funds can be released/staked outside the intended terms.

### Citations

**File:** lockup-factory/src/lib.rs (L34-34)
```rust
const MIN_ATTACHED_BALANCE: Balance = 3_500_000_000_000_000_000_000_000;
```

**File:** lockup-factory/src/lib.rs (L107-153)
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

**File:** lockup-factory/README.md (L9-11)
```markdown
To create a new lockup contract a user should issue a transaction and 
attach the required minimum deposit. The entire deposit will be transferred to 
the newly created lockup contract including to cover the storage.
```
