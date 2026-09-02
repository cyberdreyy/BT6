### Title
Lockup Factory lets the funding account override the staking-pool whitelist even for vesting/foundation-recoverable grants, allowing tokens to be permanently frozen in an unvetted pool - (File: `lockup-factory/src/lib.rs`)

### Summary
`LockupFactory::create` lets whoever funds a new lockup pass an arbitrary `whitelist_account_id` that becomes the lockup's trusted staking-pool whitelist, with no restriction preventing this override for grants that carry a `vesting_schedule` (i.e. foundation-recoverable funds). This is structurally the same class of bug as the Fraxlend front-run report: a caller-supplied parameter is allowed to substitute for the protocol's intended trust anchor (the officially-approved whitelist/pair configuration), breaking the custody guarantee the whitelist system exists to enforce.

### Finding Description
`create()` accepts an optional `whitelist_account_id` and, if present, uses it instead of the factory's configured whitelist: [1](#0-0) 

This value is baked into the deployed lockup's `staking_pool_whitelist_account_id` regardless of whether the lockup also carries a `vesting_schedule` (which triggers `foundation_account_id` being set, i.e. this is a foundation-recoverable grant, not a purely self-owned lockup): [2](#0-1) 

The deployed lockup contract later trusts whatever address is stored as `staking_pool_whitelist_account_id` to gate `select_staking_pool`: [3](#0-2) 

and unconditionally accepts the boolean the whitelist contract returns: [4](#0-3) 

The whitelist system's documented purpose is precisely to guarantee delegated tokens cannot be lost or locked — only foundation-approved pools (or pools whitelisted by a foundation-approved factory) are supposed to be reachable: [5](#0-4) 

Because `create()` lets the funding account (an ordinary, unprivileged NEAR account — the README explicitly advertises "Lockups can be funded from any account" and "No need to have access to the foundation keys to create lockup") choose a *different* whitelist contract, they can deploy their own trivial contract whose `is_whitelisted` always returns `true`, pass its account ID as `whitelist_account_id`, and fund a lockup with a `vesting_schedule` (so the foundation is a real stakeholder in the unvested portion). As `owner_account_id` they then call `select_staking_pool` against a malicious/unaudited staking pool of their choosing, which the lockup will accept because it queries the attacker's fake whitelist instead of the foundation's real one.

Equality that should hold: `lockup.staking_pool_whitelist_account_id == foundation_configured_whitelist` for any lockup where `vesting_schedule.is_some()` (i.e., where the foundation has a recovery interest). Equality that actually holds: `lockup.staking_pool_whitelist_account_id == whitelist_account_id (attacker-chosen)` whenever that optional parameter is supplied to `create`, with no gating on `vesting_schedule`.

### Impact Explanation
If the selected "pool" is malicious or simply never returns delegated NEAR (deposit and stake but withhold on withdraw/unstake), the lockup's `unselect_staking_pool`/termination flow cannot recover the funds (it only performs best-effort accounting and depends on the pool's cooperation to unstake/withdraw). Since termination logic must unstake from the selected pool before the foundation can reclaim unvested tokens, both the grantee's vested share and the foundation's unvested/terminated share become frozen indefinitely inside a pool the funder fully controls — this maps to the "funds permanently frozen" / "wrongly whitelisted... deployment" Critical/High impact categories, and in the worst case (attacker's pool contract that self-destructs or refuses to ever release funds) it is an irrecoverable loss of custody, not merely delay.

### Likelihood Explanation
No privileged role is required: any account can call `lockup-factory`'s `create` with a custom `whitelist_account_id`, and any account can deploy a trivial "always whitelisted" contract to serve as that override. The vesting-schedule path exists specifically for foundation grants, so this is directly reachable in the intended real-world usage of the factory (grant creation), not a contrived edge case.

### Recommendation
Disallow the `whitelist_account_id` override entirely whenever `vesting_schedule.is_some()` (i.e., whenever `foundation_account_id` would be set), forcing such lockups to always use the factory's foundation-configured whitelist. If a custom whitelist must remain supported for non-vesting/self-funded lockups, explicitly document and enforce that it only applies when there is no foundation recovery interest, and consider requiring the custom whitelist itself be foundation-approved (e.g., checked against `factory_whitelist` in the whitelist contract) rather than accepted unconditionally from the caller.

### Proof of Concept
1. Attacker deploys `fake-whitelist.testnet` implementing `is_whitelisted` that always returns `true`.
2. Attacker calls `lockup-factory.create` with `owner_account_id = attacker`, `vesting_schedule = Some(...)` (so `foundation_account_id` is set per `lockup-factory/src/lib.rs:123-126`), and `whitelist_account_id = Some("fake-whitelist.testnet")`.
3. The deployed lockup is initialized with `staking_pool_whitelist_account_id = "fake-whitelist.testnet"` (`lockup-factory/src/lib.rs:128-151`).
4. As owner, attacker calls `select_staking_pool("attacker-pool.testnet")`; the lockup queries `fake-whitelist.testnet.is_whitelisted(...)`, gets `true`, and accepts the pool (`lockup/src/owner.rs:12-41`, `lockup/src/owner_callbacks.rs:7-25`).
5. Attacker calls `deposit_to_staking_pool` / `stake` to move the lockup's NEAR (including the unvested/foundation-recoverable portion) into `attacker-pool.testnet`, which never honors `unstake`/`withdraw`.
6. Both the grantee's future vested tokens and the foundation's termination claim on unvested tokens are now stuck in a pool outside foundation control, with no on-chain path to force release.

### Citations

**File:** lockup-factory/src/lib.rs (L107-157)
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
                    .unwrap(),
                NO_DEPOSIT,
                gas::LOCKUP_NEW,
            )
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

**File:** whitelist/README.md (L6-9)
```markdown
In order for the lockup contracts to be able delegate to a staking pool, the staking pool should faithfully implement the spec.
The staking pool should guarantee that the delegated tokens can not be lost or locked, such as the lockup contract should be
able to recover delegated tokens back to the lockup from a staking pool. In order to enforce this, only approved (whitelisted)
accounts of staking pool contracts can receive delegated tokens from lockup contracts.
```
