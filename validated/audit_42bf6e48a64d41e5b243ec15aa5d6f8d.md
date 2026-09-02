### Title
Unvalidated `whitelist_account_id` in `LockupFactory::create` lets a self-funded lockup owner forge the staking-pool whitelist and exfiltrate locked/unvested NEAR - (File: `lockup-factory/src/lib.rs`)

### Summary
`LockupFactory::create` lets the caller supply an arbitrary `whitelist_account_id` that is baked into the deployed lockup contract as `staking_pool_whitelist_account_id`, with no check that this account is the canonical, Foundation-controlled whitelist contract. This mirrors the reported bug class ("no sanity check that a referenced ID actually corresponds to a legitimate, existing entity before binding trust to it"), except here the trusted entity is the staking-pool whitelist rather than a security policy.

### Finding Description
`LockupFactory::create` accepts an optional `whitelist_account_id` parameter and, if provided, uses it verbatim as `staking_pool_whitelist_account_id` for the newly deployed lockup contract, falling back to the factory's default only if omitted: [1](#0-0) 

There is no assertion that this account ID corresponds to the real, Foundation-operated whitelist contract (`whitelist/src/lib.rs`), nor any restriction tying the override to a privileged caller — any funding account creating a lockup can set it.

Once deployed, the lockup contract permanently trusts `self.staking_pool_whitelist_account_id` as the arbiter of which staking pools are safe to delegate to: [2](#0-1) 

The whole point of this whitelist, per the whitelist contract's own documentation, is to guarantee delegated tokens (including locked/unvested ones) "can not be lost or locked" — i.e. it is the custody binding protecting NEAR that has been staked out of the lockup: [3](#0-2) 

If the whitelist account address is attacker-controlled instead of the Foundation's real whitelist, `is_whitelisted` can be made to always return `true`, so `select_staking_pool` will accept any pool the owner names — including a pool fully controlled by the same party, which never unstakes or returns funds. `deposit_and_stake` then moves NEAR out of the lockup account to that pool with only a balance check, not a "funds are still recoverable" check: [4](#0-3) 

### Impact Explanation
The invariant the whitelist is supposed to enforce — `staking_pool_whitelist_account_id == Foundation's real whitelist` — is what keeps delegated locked/unvested NEAR recoverable and clawback-able by the Foundation on termination. Because the factory lets the creator substitute an arbitrary account for this binding, an owner can route locked/unvested tokens to a pool that never returns them, permanently moving NEAR the Foundation should be able to reclaim outside the custody boundary the whitelist was designed to enforce. This lines up with the Critical impact bucket ("claims exceeding assets held" / "funds permanently frozen").

### Likelihood Explanation
The path requires no privileged actor: any account funding a `create()` call chooses `whitelist_account_id`, and the resulting owner (who may be the same party) can immediately call `select_staking_pool` → `deposit_and_stake` using their own forged whitelist. No foundation, multisig, or victim key involvement is needed.

### Recommendation
`LockupFactory::create` should not allow the caller to freely specify an arbitrary `whitelist_account_id`; it should always use the factory's own configured (Foundation-controlled) whitelist, or, if per-lockup overrides are intentionally supported, restrict who may set them and validate that the target account is an approved whitelist contract before binding it — analogous to adding a "genuine policy exists" check as recommended for `PaymentProcessor.setCollectionSecurityPolicy`.

### Proof of Concept
1. Attacker deploys a trivial contract exposing `is_whitelisted(...) -> bool { true }` at account `fake-whitelist.attacker`.
2. Attacker calls `LockupFactory::create(owner_account_id = attacker's own account, ..., whitelist_account_id = Some("fake-whitelist.attacker"))`, funding it with locked/vesting NEAR (using the standard vesting-hash mechanism) — see `create` at [5](#0-4) .
3. As owner of the new lockup, attacker calls `select_staking_pool("attacker-pool.near")`; the lockup queries `is_whitelisted` on `fake-whitelist.attacker`, which returns `true` — see [6](#0-5) .
4. Attacker calls `deposit_and_stake(amount = full locked balance)`; NEAR is transferred to `attacker-pool.near`, a contract fully controlled by attacker that never unstakes/withdraws — see [4](#0-3) .
5. The locked/unvested NEAR is now outside the lockup contract's balance and outside any Foundation clawback mechanism, effectively extracted by the attacker.

### Citations

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

**File:** lockup/src/owner.rs (L127-166)
```rust
    pub fn deposit_and_stake(&mut self, amount: WrappedBalance) -> Promise {
        self.assert_owner();
        assert!(amount.0 > 0, "Amount should be positive");
        self.assert_staking_pool_is_idle();
        self.assert_no_termination();
        assert!(
            self.get_account_balance().0 >= amount.0,
            "The balance that can be deposited to the staking pool is lower than the extra amount"
        );

        env::log(
            format!(
                "Depositing and staking {} to the staking pool @{}",
                amount.0,
                self.staking_information
                    .as_ref()
                    .unwrap()
                    .staking_pool_account_id
            )
            .as_bytes(),
        );

        self.set_staking_pool_status(TransactionStatus::Busy);

        ext_staking_pool::deposit_and_stake(
            &self
                .staking_information
                .as_ref()
                .unwrap()
                .staking_pool_account_id,
            amount.0,
            gas::staking_pool::DEPOSIT_AND_STAKE,
        )
        .then(ext_self_owner::on_staking_pool_deposit_and_stake(
            amount,
            &env::current_account_id(),
            NO_DEPOSIT,
            gas::owner_callbacks::ON_STAKING_POOL_DEPOSIT_AND_STAKE,
        ))
    }
```

**File:** whitelist/README.md (L6-9)
```markdown
In order for the lockup contracts to be able delegate to a staking pool, the staking pool should faithfully implement the spec.
The staking pool should guarantee that the delegated tokens can not be lost or locked, such as the lockup contract should be
able to recover delegated tokens back to the lockup from a staking pool. In order to enforce this, only approved (whitelisted)
accounts of staking pool contracts can receive delegated tokens from lockup contracts.
```
