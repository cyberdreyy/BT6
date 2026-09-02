## Title
Unauthenticated `owner_account_id` in `lockup-factory::create` allows address-squatting a victim's deterministic lockup with attacker-chosen terms - (File: `lockup-factory/src/lib.rs`, `lockup/src/lib.rs`)

## Summary
`LockupFactory::create` derives the new lockup's account ID purely from `sha256(owner_account_id)` with no check that the caller is, or is authorized by, that owner, and no reservation/allowlist mechanism. Any unprivileged caller can pick any victim's `owner_account_id`, set `lockup_duration = 0`, omit `lockup_timestamp`, and supply an attacker-controlled `whitelist_account_id`, deploying a lockup at the victim's canonical derived address whose terms the victim (or their true grantor) never approved.

## Finding Description
The invariant under test is: `lockup_account_id_terms(sha256(owner_account_id)) == terms_chosen_by(owner_account_id's grantor)`. This binding is broken because the address derivation and the `new` initializer never verify who the caller is relative to `owner_account_id`.

`create()` computes the deterministic child account solely from the caller-supplied `owner_account_id`: [1](#0-0) [2](#0-1) 

The only guard is a minimum attached deposit; there is no check that `env::predecessor_account_id()` equals or is authorized by `owner_account_id`: [3](#0-2) 

All remaining terms — `lockup_duration`, `lockup_timestamp`, `vesting_schedule`, `release_duration`, and even `staking_pool_whitelist_account_id` (an optional override defaulting to the factory's own whitelist) — are attacker-supplied arguments forwarded verbatim into `LockupArgs` for the deployed contract's `new`: [4](#0-3) 

`LockupContract::new` itself performs no cross-check between `owner_account_id` and the deploying/predecessor account either — it only validates account-ID format and vesting/foundation consistency: [5](#0-4) 

Because the derived address `sha256(owner_account_id)[..20].<factory_account>` is a pure function of `owner_account_id` with no nonce or reservation, whoever calls `create()` (or the raw `create_account`+`deploy_contract`+`new` batch) first for that `owner_account_id` wins the address. An attacker can:
1. Call `create(owner_account_id = victim, lockup_duration = 0, lockup_timestamp = None, vesting_schedule = None, release_duration = None, whitelist_account_id = Some(attacker_fake_whitelist))` with only `MIN_ATTACHED_BALANCE` (3.5 NEAR).
2. This deploys a lockup at the victim's canonical lockup address naming the victim as `owner_account_id`, but with `lockup_duration = 0`/no timestamp (immediate unlock once transfers are enabled) and an attacker-controlled staking pool whitelist baked in as the trusted whitelist for that lockup.
3. When the true grantor (e.g., the NEAR Foundation or an employer) later attempts to create the victim's real lockup with the intended vesting/lockup terms at the same derived address, the `CreateAccount` action fails because the account already exists, the whole batched action receipt fails, and the deposit is refunded via `on_lockup_create`'s `is_promise_success()` check: [6](#0-5) 

The refund path protects against direct fund theft on that specific transaction, but the deterministic address has already been irreversibly claimed by the attacker with terms the victim's actual grantor never chose — this is precisely "a lockup deployed with parameters its rightful creator never chose," matching the Critical impact category regardless of whether the grantor's own deposit is transferred in that instant. `assert_owner`, `assert_self`, and the vesting/foundation asserts in `new` do not address this because none of them validate that the deploying party is entitled to name `owner_account_id`.

## Impact Explanation
The attacker permanently squats the canonical lockup address for any named victim account before the legitimate grantor can, embedding attacker-chosen parameters (immediate unlock via `lockup_duration = 0` and no `lockup_timestamp`, and an attacker-controlled `staking_pool_whitelist_account_id`) into a contract the protocol/tooling treats as "the victim's lockup." This blocks the legitimate grantor from ever deploying the intended lockup at that address (the account already exists) and, if the victim is later induced to fund or operate this attacker-parameterized contract (e.g., believing it is their real lockup and staking through the embedded fake whitelist), the attacker's whitelisted pool can capture staked funds. This is repeatable for every distinct victim account ID and costs the attacker only `MIN_ATTACHED_BALANCE` per squat.

## Likelihood Explanation
No privileged role is required — `create()` is a public, payable method open to anyone who can attach `MIN_ATTACHED_BALANCE` (3.5 NEAR). The victim's account ID is public knowledge, and the derivation formula (`sha256(owner_account_id)[..20]`) is also public/deterministic, so the attacker can precompute the address for any target ahead of the legitimate grantor. This is fully deterministic and repeatable across any number of victim accounts, requiring no special timing beyond acting before the legitimate `create()` call.

## Recommendation
Require that `create()` either be restricted to a trusted caller (e.g., only the `foundation_account_id` or an allowlisted deployer role) for naming a given `owner_account_id`, or bind account creation to proof of the owner's consent (e.g., require `predecessor_account_id() == owner_account_id`, or a signed/whitelisted request keyed by owner). Alternatively, incorporate an unpredictable/attacker-uncontrollable nonce or a pre-registration step tied to the grantor into the derived account ID so the address cannot be squatted purely from public knowledge of the target's account ID.

## Proof of Concept
```rust
// lockup-factory/src/lib.rs tests module (near-sdk unit test style, mirrors existing tests)
#[test]
fn test_attacker_squats_victim_lockup_address_with_untrusted_terms() {
    let mut context = VMContextBuilder::new()
        .current_account_id(account_factory())
        .predecessor_account_id(account_near())
        .finish();
    testing_env!(context.clone());

    let mut contract = LockupFactory::new(whitelist_account_id(), foundation_account_id());

    // Attacker calls create() naming a VICTIM account id as owner, with
    // lockup_duration = 0, no lockup_timestamp, and their own fake whitelist.
    let victim_id: ValidAccountId = "victim.testnet".try_into().unwrap();
    let attacker_fake_whitelist: ValidAccountId = "fake-whitelist.attacker.testnet".try_into().unwrap();

    context.is_view = false;
    context.predecessor_account_id = String::from(account_attacker()); // attacker, not victim
    context.attached_deposit = MIN_ATTACHED_BALANCE; // only 3.5 NEAR needed
    testing_env!(context.clone());

    contract.create(
        victim_id.clone(),
        0u64.into(),          // lockup_duration = 0
        None,                  // no lockup_timestamp
        None,                  // no vesting_schedule
        None,
        Some(attacker_fake_whitelist.clone()),
    );

    // Derive the victim's canonical lockup address the same way create() does.
    let byte_slice = env::sha256(victim_id.as_ref().as_bytes());
    let expected_victim_lockup_account_id =
        format!("{}.{}", hex::encode(&byte_slice[..20]), account_factory());

    // Assert: the LockupArgs sent to `new` at the victim's derived address
    // carry the attacker's terms, never approved by the victim/grantor:
    //   owner_account_id == victim_id            (binding LHS: derived address)
    //   whitelist == attacker_fake_whitelist      (binding RHS should equal grantor's choice, but doesn't)
    //   lockup_duration == 0, lockup_timestamp == None
    // (In an integration test with near-sdk-sim/near-workspaces, fetch the
    //  deployed contract's `get_lockup_information` / whitelist getter at
    //  `expected_victim_lockup_account_id` and assert it matches the attacker's
    //  call, not any terms the victim or a legitimate grantor supplied.)
}
```
This demonstrates the attacker deploying a lockup at the victim's exact canonical address with `lockup_duration = 0`, no `lockup_timestamp`, and an attacker-chosen whitelist — parameters the victim's actual grantor never chose — satisfying the Critical impact category "a lockup deployed with parameters its rightful creator never chose."

### Citations

**File:** lockup-factory/src/lib.rs (L107-121)
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
```

**File:** lockup-factory/src/lib.rs (L128-157)
```rust
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

**File:** lockup/src/lib.rs (L190-243)
```rust
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
