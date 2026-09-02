### Title
Lockup deployment trusts a caller-supplied staking-pool whitelist instead of a verified one, enabling locked NEAR to be staked with an attacker-controlled pool - (File: lockup-factory/src/lib.rs)

### Summary
`LockupFactory::create()` lets the caller who is deploying the lockup pass an arbitrary `whitelist_account_id` that becomes the `staking_pool_whitelist_account_id` baked into the newly deployed lockup contract, with no verification that this account is the legitimate, Foundation-controlled staking-pool whitelist.

### Finding Description
The bug report describes `PoolFactory.deployPool()` trusting a caller-supplied `oracleWrapper` contract's self-reported `deployer()` value instead of verifying the contract's authenticity out-of-band. The analog here is `LockupFactory::create()`: [1](#0-0) 

```
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
```

The factory has its own trusted, canonical `whitelist_account_id` set at `#[init]` time [2](#0-1) , which in production points to the NEAR-Foundation-controlled `WhitelistContract` that vets staking pools before they can be used [3](#0-2) . However, `create()` accepts an *optional, caller-supplied* `whitelist_account_id` and, if present, uses it verbatim as the `staking_pool_whitelist_account_id` passed into the deployed lockup contract's `new()` call — with zero validation that this account is a legitimate whitelist, let alone the Foundation's [4](#0-3) .

The lockup contract subsequently relies on `staking_pool_whitelist_account_id.is_whitelisted(...)` (referenced throughout `lockup/src/owner.rs` and `lockup/src/owner_callbacks.rs`) as the authorization gate before allowing the lockup owner to select a staking pool and move locked NEAR into it. Because the factory never checks that the supplied "whitelist" account is the real, immutable Foundation whitelist, the lockup owner can supply a self-deployed contract whose `is_whitelisted()` always returns `true` — exactly mirroring the oracle-wrapper issue where `deployer()` could be spoofed to return any value including `msg.sender`.

### Impact Explanation
The staking-pool whitelist exists specifically to prevent locked/unvested NEAR from being routed to unvetted or malicious staking-pool contracts. If the "whitelist" used by a given lockup is attacker/owner-controlled rather than the real one, the owner can register a fake staking pool as "whitelisted," `select_staking_pool` it, and then `deposit_and_stake` locked NEAR into it. Since the fake pool is fully controlled by the deployer, the NEAR transferred to it escapes the lockup contract's release-schedule enforcement entirely — effectively releasing locked/unvested tokens early, or diverting funds subject to a vesting schedule that a foundation/beneficiary was relying on to be protected by the whitelist gate. This matches the "wrongly parameterised deployment" / "locked or unvested tokens released early" Critical-impact category.

### Likelihood Explanation
`whitelist_account_id` is a normal, unprivileged, caller-controlled parameter to the public `create()` entry point — no special permission is required, and there is no on-chain check tying it to the Foundation's whitelist. Any account creating a lockup for itself (or on behalf of another owner who trusts the default configuration) can supply a look-alike whitelist contract at deployment time.

### Recommendation
Do not allow the caller to override `staking_pool_whitelist_account_id` in `LockupFactory::create()`; always use the factory's own trusted `self.whitelist_account_id` set at `#[init]`, or, if overriding must remain supported, restrict it to an allowlist of Foundation-approved whitelist accounts checked on-chain by the factory before deployment.

### Proof of Concept
1. Deploy a minimal contract `FakeWhitelist` exposing `is_whitelisted(account_id) -> bool { true }`.
2. Call `LockupFactory::create(owner_account_id, ..., whitelist_account_id: Some(FakeWhitelist))`.
3. The deployed lockup contract stores `FakeWhitelist` as its `staking_pool_whitelist_account_id` (`lockup-factory/src/lib.rs:129-133,151`).
4. As lockup owner, call `select_staking_pool(malicious_pool)` — the lockup's whitelist check against `FakeWhitelist` passes for any pool.
5. `deposit_and_stake` transfers locked NEAR to `malicious_pool`, which is attacker-controlled and can retain or return the funds outside the lockup's release schedule.

### Citations

**File:** lockup-factory/src/lib.rs (L76-90)
```rust
    pub fn new(
        whitelist_account_id: ValidAccountId,
        foundation_account_id: ValidAccountId,
    ) -> Self {
        assert!(!env::state_exists(), "The contract is already initialized");
        assert!(
            env::current_account_id().len() <= 23,
            "The account ID of this contract can't be more than 23 characters"
        );

        Self {
            whitelist_account_id: whitelist_account_id.into(),
            foundation_account_id: foundation_account_id.into(),
        }
    }
```

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

**File:** whitelist/src/lib.rs (L72-88)
```rust
    /// Adds the given staking pool account ID to the whitelist.
    /// Returns `true` if the staking pool was not in the whitelist before, `false` otherwise.
    /// This method can be called either by the NEAR foundation or by a whitelisted factory.
    pub fn add_staking_pool(&mut self, staking_pool_account_id: AccountId) -> bool {
        assert!(
            env::is_valid_account_id(staking_pool_account_id.as_bytes()),
            "The given account ID is invalid"
        );
        // Can only be called by a whitelisted factory or by the foundation.
        if !self
            .factory_whitelist
            .contains(&env::predecessor_account_id())
        {
            self.assert_called_by_foundation();
        }
        self.whitelist.insert(&staking_pool_account_id)
    }
```
