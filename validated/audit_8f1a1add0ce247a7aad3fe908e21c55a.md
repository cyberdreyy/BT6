### Title
Lockup address squatting via `LockupFactory::create` lets an attacker permanently deploy a victim's lockup with no vesting schedule, destroying the foundation's termination right - (File: lockup-factory/src/lib.rs, lockup/src/lib.rs, lockup/src/getters.rs)

### Summary
`LockupFactory::create` derives the new lockup contract's account ID deterministically from `sha256(owner_account_id)` and lets *any* caller supply `owner_account_id` and `vesting_schedule` for that address. Because NEAR account creation fails if the account already exists, an attacker can pre-empt the real grant creator by calling `create` for a chosen victim account with `vesting_schedule=None`, permanently occupying the victim's canonical lockup address with no vesting/termination terms, before the legitimate lockup (with the intended vesting schedule and foundation termination right) can ever be deployed there.

### Finding Description
The broken binding: the lockup deployed at `sha256(owner_account_id).factory` for a given `owner_account_id` should equal the lockup parameters (in particular `vesting_information` and `foundation_account_id`) chosen by the entity that is entitled to grant that account's tokens (e.g. NEAR Foundation/HR). Instead:

`vesting_information_at(sha256(owner_account_id).factory) == VestingInformation::None`, `foundation_account_id == None`

for whichever caller's `create` transaction lands first for that `owner_account_id`, regardless of who was "supposed" to create it.

Code path:
- `LockupFactory::create` computes the target address purely from the attacker-suppliable `owner_account_id`, with no restriction on who may call it or what `owner_account_id`/`vesting_schedule` they may pass: [1](#0-0) 
- The address is deterministic and collision-checked implicitly by NEAR's `create_account` (an already-existing account causes the promise chain to fail): [2](#0-1) 
- The factory sets `foundation_account_id = None` whenever `vesting_schedule` is `None`, and `LockupContract::new` only requires a valid foundation account when vesting is present: [3](#0-2) [4](#0-3) 
- Once `VestingInformation::None` is baked in, `get_locked_amount` always uses `unvested_amount = U128(0)` for that branch, and `get_owners_balance` is computed directly off that locked amount: [5](#0-4) [6](#0-5) 

Exploit flow: attacker calls `create(owner_account_id=<victim>, lockup_duration=0, vesting_schedule=None, ...)` with the minimum deposit attached. Because the lockup account ID is `sha256(<victim>).factory` and does not yet exist, `create_account` + `deploy_contract` + `new()` succeed. When the real grant creator later attempts `create(owner_account_id=<victim>, vesting_schedule=Some(...))`, the identical target address already exists, `create_account` fails, `on_lockup_create` observes `is_promise_success() == false` and simply refunds the deposit — the legitimate vesting lockup is never created at that address: [7](#0-6) 

Existing guards don't stop this: `assert_owner` in the deployed contract only gates *owner* methods to `predecessor_account_id == owner_account_id`, so the victim (not the attacker) retains control of the squatted contract — but the vesting/termination terms are permanently the attacker's choice, not the real creator's, because the account name space collision was already claimed: [8](#0-7) 

### Impact Explanation
The attack doesn't let the attacker steal the victim's tokens (the victim retains owner-only control), but it permanently denies the NEAR Foundation the termination right that the real grant was supposed to carry, and it results in "a lockup deployed with parameters its rightful creator never chose" — matching the Critical impact category verbatim. If the real funder does not notice the squat and funds the wrong (squatted) lockup, all funds become immediately liquid to the owner with zero vesting enforcement (`get_locked_amount`/`get_owners_balance` never subtract any unvested amount), i.e. tokens intended to vest over years are released early. This is repeatable against any target account whose intended lockup has not yet been created, for the cost of only the minimum attached deposit (3.5 NEAR) per victim address.

### Likelihood Explanation
Feasibility is high: `create` is a fully public, `#[payable]` method with no allow-list on `owner_account_id` or caller, and the target address is fully deterministic from `owner_account_id` alone, so an attacker who knows (or guesses) which NEAR account will receive a foundation/employee grant can race to squat it before the real creation transaction. Cost is bounded to `MIN_ATTACHED_BALANCE` (3.5 NEAR) per squatted address, and the attack is repeatable across arbitrarily many target accounts.

### Recommendation
Restrict who may call `create` with a given `owner_account_id`/`vesting_schedule` combination — e.g., require `vesting_schedule` creation to be gated by (or co-signed by) the foundation account, or require the `owner_account_id`'s lockup account to be pre-registered/reserved by the foundation before any unprivileged caller can target it. At minimum, disallow arbitrary unprivileged callers from creating a lockup with `vesting_schedule=None` for an `owner_account_id` that a foundation-controlled off-chain process has scheduled for a real vesting grant, or make lockup creation itself a privileged (foundation-only) operation while allowing external funding via a separate deposit-only endpoint.

### Proof of Concept
`cargo test` plan (near-sdk-sim, in `lockup-factory` or `lockup` integration tests):
1. Deploy `LockupFactory` with a known `foundation_account_id`.
2. As `attacker`, call `create(owner_account_id="victim.testnet", lockup_duration=0.into(), lockup_timestamp=None, vesting_schedule=None, release_duration=None, whitelist_account_id=None)` with `MIN_ATTACHED_BALANCE` attached; assert the promise succeeds and the derived lockup account exists.
3. As `hr` (simulating the real grant creator), call `create(owner_account_id="victim.testnet", vesting_schedule=Some(VestingScheduleOrHash::VestingSchedule(real_schedule)))`; assert the `on_lockup_create` callback returns `false` and the deposit is refunded to `hr` (proving the real vesting lockup was never deployed).
4. Query the squatted contract: assert `get_vesting_information() == VestingInformation::None`, `get_termination_status() == None`, and `get_unvested_amount(any_schedule).0 == 0` for all timestamps.
5. Control case: repeat step 2-4 for a fresh `"control.testnet"` account where `hr` creates the lockup first with a real vesting schedule; assert `get_vesting_information() != VestingInformation::None` and `get_unvested_amount` decreases over time — contrasting the squatted victim lockup (always 0 unvested, no termination right) against the legitimate control lockup.

### Citations

**File:** lockup-factory/src/lib.rs (L117-126)
```rust
        assert!(env::attached_deposit() >= MIN_ATTACHED_BALANCE, "Not enough attached deposit");

        let byte_slice = env::sha256(owner_account_id.as_ref().as_bytes());
        let lockup_account_id =
            format!("{}.{}", hex::encode(&byte_slice[..20]), env::current_account_id());

        let mut foundation_account: Option<AccountId> = None;
        if vesting_schedule.is_some() {
            foundation_account = Some(self.foundation_account_id.clone());
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

**File:** lockup/src/lib.rs (L216-233)
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
        assert!(
            vesting_information == VestingInformation::None ||
                env::is_valid_account_id(foundation_account_id.as_ref().unwrap().as_bytes()),
            "Foundation account should be added for vesting schedule"
        );
```

**File:** lockup/src/getters.rs (L97-113)
```rust
                let unvested_amount = match &self.vesting_information {
                    VestingInformation::VestingSchedule(vs) => self.get_unvested_amount(vs.clone()),
                    VestingInformation::Terminating(terminating) => terminating.unvested_amount,
                    // Vesting is private, so we can assume the vesting started before lockup date.
                    _ => U128(0),
                };
                return std::cmp::max(
                    unreleased_amount
                        .saturating_sub(self.lockup_information.termination_withdrawn_tokens),
                    unvested_amount.0,
                )
                .into();
            }
        }
        // The entire balance is still locked before the lockup timestamp.
        (lockup_amount - self.lockup_information.termination_withdrawn_tokens).into()
    }
```

**File:** lockup/src/getters.rs (L163-167)
```rust
    pub fn get_owners_balance(&self) -> WrappedBalance {
        (env::account_balance() + self.get_known_deposited_balance().0)
            .saturating_sub(self.get_locked_amount().0)
            .into()
    }
```

**File:** lockup/src/internal.rs (L122-128)
```rust
    pub fn assert_owner(&self) {
        assert_eq!(
            &env::predecessor_account_id(),
            &self.owner_account_id,
            "Can only be called by the owner"
        )
    }
```
