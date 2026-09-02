### Title
Lockup Factory Allows Attacker-Controlled Staking-Pool Whitelist to Be Bound to a Victim's Lockup - ([File: lockup-factory/src/lib.rs])

### Summary
`LockupFactory::create` lets **any caller** pick an arbitrary `whitelist_account_id` to be baked into a newly deployed lockup contract, while the `owner_account_id` of that lockup is also caller-supplied and not required to be the funder or a signer. Because the lockup account address is deterministic (`sha256(owner_account_id)[..20].<factory>`), an attacker can front-run/pre-create the deterministic lockup account for a victim `owner_account_id`, funding it with the minimum deposit but substituting a malicious `whitelist_account_id` for the trusted foundation whitelist. The victim (real lockup owner) later interacts with what looks like their legitimate lockup, but `select_staking_pool` trusts this attacker-chosen whitelist unconditionally, letting the attacker's rogue "staking pool" be reported as whitelisted.

### Finding Description
`create()` in `lockup-factory/src/lib.rs` derives the lockup address purely from `owner_account_id` and defaults the whitelist to the factory's trusted one, but accepts an optional override: [1](#0-0) 

This override is passed straight into the `new` call of the freshly deployed lockup contract as `staking_pool_whitelist_account_id`: [2](#0-1) 

In `lockup/src/lib.rs`, `staking_pool_whitelist_account_id` is stored on `new()` with only a basic account-format validity check — no check that it matches any canonical/trusted whitelist contract: [3](#0-2) 

`select_staking_pool` (an owner-only method) subsequently trusts this stored field absolutely to decide whether a staking pool is safe to use: [4](#0-3) [5](#0-4) 

There is no owner-callable method to change `staking_pool_whitelist_account_id` after initialization, so whichever value is set at `create()` time is permanent for the lockup's lifetime.

**Custody binding broken**: the security property "an account trusted as a pool or whitelist versus the code and arguments that trust was granted for" is violated — the lockup's owner believes `select_staking_pool` is validated by the NEAR-Foundation-controlled whitelist referenced in `lockup-factory`'s default (`self.whitelist_account_id`), but the actual trusted authority (`staking_pool_whitelist_account_id`) can be substituted by an unprivileged third party at deployment time, since `owner_account_id` is not required to sign and the factory performs no check that `whitelist_account_id` equals a foundation-approved value.

### Impact Explanation
Since the lockup account address is fully deterministic from `owner_account_id` (`sha256(owner_account_id)[..20].<factory>`), an attacker can pre-empt the legitimate deployment for a known/targeted owner and set a self-controlled whitelist contract (always returning `is_whitelisted: true`). When the rightful owner later calls `select_staking_pool` with an attacker-controlled pool address, the poisoned whitelist confirms it as legitimate, and the owner can be induced to `deposit_to_staking_pool` / `stake` real locked NEAR into the attacker's fake "staking pool," from which the attacker can withdraw the funds. This is a direct unauthorized diversion of custody-bound NEAR — Critical impact category ("NEAR moved by a party not entitled to it").

### Likelihood Explanation
Medium: The attacker must know or guess a victim's intended `owner_account_id` in advance and win the race to call `create()` before the legitimate funder does (the account name is deterministic and publicly computable off-chain, and typical deployment flows like `scripts/deploy_lockup.sh` construct this exact deterministic ID from operator input, making front-running practical for organizations announcing employee/investor accounts ahead of funding).

### Recommendation
- In `lockup-factory/src/lib.rs::create`, remove the caller-supplied `whitelist_account_id` override entirely, or restrict it to values equal to `self.whitelist_account_id` (or an allow-list of foundation-approved whitelists) unless the call is authorized by the account matching `owner_account_id`.
- Require `owner_account_id` to co-sign or otherwise authorize lockup creation for itself, preventing third parties from claiming a victim's deterministic lockup address with attacker-chosen configuration.
- Add an owner/foundation-gated setter in the lockup contract to allow correcting `staking_pool_whitelist_account_id` post-deployment if a compromised value is detected.

### Proof of Concept
1. Attacker observes/derives that a victim will eventually own `lockup_account_id = sha256(victim.near)[..20] + "." + factory_account`.
2. Attacker calls `lockup-factory::create(owner_account_id="victim.near", ..., whitelist_account_id=Some("attacker-whitelist.near"))` with `MIN_ATTACHED_BALANCE`, funding the lockup before the legitimate deployer does. [1](#0-0) 
3. The lockup deploys with `owner_account_id = victim.near` and `staking_pool_whitelist_account_id = attacker-whitelist.near`, per `lockup::new`. [3](#0-2) 
4. Attacker deploys `attacker-whitelist.near` whose `is_whitelisted` always returns `true`.
5. Victim (owner) later calls `select_staking_pool("attacker-pool.near")`; the lockup queries `attacker-whitelist.near`, gets `true`, and locks in the rogue pool as the active `StakingInformation`. [4](#0-3) [5](#0-4) 
6. Victim calls `deposit_and_stake`, sending locked NEAR to `attacker-pool.near`, which the attacker fully controls and can withdraw.

### Citations

**File:** lockup-factory/src/lib.rs (L108-133)
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

        // Defaults to the whitelist account ID given on init call.
        let staking_pool_whitelist_account_id = if let Some(account_id) = whitelist_account_id {
            account_id.into()
        } else {
            self.whitelist_account_id.clone()
        };
```

**File:** lockup-factory/src/lib.rs (L136-157)
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
```

**File:** lockup/src/lib.rs (L180-243)
```rust
    #[init]
    pub fn new(
        owner_account_id: AccountId,
        lockup_duration: WrappedDuration,
        lockup_timestamp: Option<WrappedTimestamp>,
        transfers_information: TransfersInformation,
        vesting_schedule: Option<VestingScheduleOrHash>,
        release_duration: Option<WrappedDuration>,
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

**File:** lockup/src/owner_callbacks.rs (L6-25)
```rust
    /// Called after a given `staking_pool_account_id` was checked in the whitelist.
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
