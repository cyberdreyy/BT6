### Title
`LockupFactory::create` lets the caller supply an unvalidated `whitelist_account_id`, letting the lockup owner stake locked/unvested NEAR with a self-controlled fake "staking pool" - (File: `lockup-factory/src/lib.rs`)

### Summary
`LockupFactory::create` accepts an optional `whitelist_account_id: Option<ValidAccountId>` parameter from the (unprivileged) caller and, without any check against the factory's own trusted `whitelist_account_id`, forwards it as `staking_pool_whitelist_account_id` into the newly deployed lockup contract's `new` call. [1](#0-0) 
The deployed `LockupContract` unconditionally trusts whatever account is stored in `staking_pool_whitelist_account_id` as the sole authority deciding which staking pool is safe to stake locked/unvested NEAR with, via `select_staking_pool`'s call to `ext_whitelist::is_whitelisted`. [2](#0-1) 
This mirrors the reported bug class exactly: an unvalidated, caller-supplied account (`p.tokenIn` / here `whitelist_account_id`) is trusted as an authority (`possibleAdapter` / here the "whitelist" contract) for a sensitive operation, without whitelisting or verification of its authenticity.

### Finding Description
The NEAR Foundation deploys `LockupFactory` once with a single trusted `whitelist_account_id` (the canonical staking-pool whitelist) meant to gate which staking pools lockup owners are allowed to stake with, preventing owners from routing locked/unvested tokens to arbitrary contracts they control and effectively unlocking them early via staking/unstaking mechanics. [3](#0-2) 

However, `create()` allows the transaction sender (typically the future lockup owner or anyone funding the deposit) to override this trusted value with an arbitrary `whitelist_account_id` argument: [4](#0-3) 
There is no assertion that the supplied `whitelist_account_id` equals the factory's canonical `self.whitelist_account_id`, nor any check that it is itself whitelisted/authorized. It is passed straight through to the deployed lockup's `staking_pool_whitelist_account_id` field: [5](#0-4) 

This is proven to be reachable and exploitable in the existing test suite itself, which demonstrates creating a lockup with a fully custom whitelist account: [6](#0-5) 

Once deployed, the lockup contract's owner calls `select_staking_pool`, which queries `is_whitelisted` only against this attacker-chosen `staking_pool_whitelist_account_id` — never against the real NEAR whitelist contract: [7](#0-6) 

If the owner deploys their own trivial "whitelist" contract that always answers `is_whitelisted = true`, they can select an attacker-controlled staking-pool contract, then call `deposit_and_stake`/`stake` to move locked/unvested NEAR balance out of the lockup and into that fully attacker-controlled pool contract: [8](#0-7) 
From there, the attacker-controlled "pool" contract can simply keep or forward the NEAR anywhere, since it is not a real staking pool subject to NEAR's staking/unstaking lockup — the safety property ("only stake with a pool vetted by the Foundation's whitelist") that the lockup design depends on is bypassed entirely.

The binding broken: `staking_pool_whitelist_account_id used by lockup == LockupFactory.whitelist_account_id (the Foundation-controlled trusted whitelist)`. After the exploit, `staking_pool_whitelist_account_id (attacker's fake contract) ≠ LockupFactory.whitelist_account_id`, so the "select_staking_pool" authorization boundary is not the one the Foundation configured.

### Impact Explanation
This matches the Critical impact category "locked or unvested tokens released early": an unprivileged lockup owner (the intended attacker in this analog, matching the report's "malicious contract inserted as trusted parameter") can bypass the staking-pool whitelist safety mechanism entirely and move locked/unvested NEAR to a contract they fully control, from which it can be extracted, effectively releasing time-locked/vesting-restricted NEAR before its schedule allows.

### Likelihood Explanation
High likelihood: `create()` is a public, unprivileged, `#[payable]` entry point requiring only the minimum attached deposit; the `whitelist_account_id` override is a first-class, documented parameter (with a dedicated success test `test_create_lockup_with_custom_whitelist_success`), so no special access, redeploy, or social engineering is required — only deploying a second, trivial "whitelist" contract and a "staking pool" contract, both of which are cheap for the attacker to write and deploy under their own control.

### Recommendation
Do not let the caller of `create()` choose an arbitrary whitelist account. Either:
- remove the `whitelist_account_id: Option<ValidAccountId>` parameter entirely and always use `self.whitelist_account_id` set at factory `new()`/init time, or
- if overriding must be supported, restrict it to a value drawn from a Foundation-maintained allow-list (e.g., require `predecessor_account_id == self.foundation_account_id`, or validate the supplied account against `factory_whitelist` in the `whitelist` contract) before using it as `staking_pool_whitelist_account_id`.

### Proof of Concept
1. NEAR Foundation deploys `LockupFactory` with a legitimate `whitelist_account_id` (call it `real-whitelist`).
2. An attacker (the intended lockup `owner_account_id`) calls `create()` on the factory, supplying `whitelist_account_id = Some("attacker-whitelist")`, per the accepted parameter at [9](#0-8)  — no check rejects this override, as shown by the successful `create()` flow at [10](#0-9) .
3. `attacker-whitelist` is a contract the attacker deployed, whose `is_whitelisted` always returns `true`.
4. `attacker-pool` is a contract the attacker deployed, implementing the staking-pool interface (`deposit_and_stake`, etc.) but simply keeping/forwarding any NEAR sent to it.
5. Once the lockup is live, the attacker (as lockup owner) calls `select_staking_pool("attacker-pool")`; the lockup queries `is_whitelisted` on `attacker-whitelist`, which returns `true`, per [2](#0-1) .
6. The attacker calls `deposit_and_stake(amount)` where `amount` is drawn from `get_account_balance()` (which includes locked/unvested NEAR held by the lockup account), transferring that NEAR to `attacker-pool`, per [8](#0-7) .
7. `attacker-pool`, being fully attacker-controlled, releases the NEAR to any account the attacker wants — bypassing the lockup/vesting schedule that the whitelist mechanism was meant to enforce.

### Citations

**File:** lockup-factory/src/lib.rs (L49-52)
```rust
pub struct LockupFactory {
    whitelist_account_id: AccountId,
    foundation_account_id: AccountId,
}
```

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

**File:** lockup-factory/src/lib.rs (L390-425)
```rust
    #[test]
    fn test_create_lockup_with_custom_whitelist_success() {
        let mut context = VMContextBuilder::new()
            .current_account_id(account_factory())
            .predecessor_account_id(account_near())
            .finish();
        testing_env!(context.clone());

        let mut contract = LockupFactory::new(whitelist_account_id(), foundation_account_id());

        const LOCKUP_DURATION: u64 = 63036000000000000; /* 24 months */
        let lockup_duration: WrappedTimestamp = LOCKUP_DURATION.into();

        context.is_view = false;
        context.predecessor_account_id = String::from(account_tokens_owner());
        context.attached_deposit = ntoy(35);
        testing_env!(context.clone());
        contract.create(
            account_tokens_owner(),
            lockup_duration,
            None,
            None,
            None,
            Some(custom_whitelist_account_id()),
        );

        context.predecessor_account_id = account_factory();
        context.attached_deposit = ntoy(0);
        testing_env_with_promise_results(context.clone(), PromiseResult::Successful(vec![]));
        println!("{}", lockup_account());
        contract.on_lockup_create(
            lockup_account(),
            ntoy(30).into(),
            String::from(account_tokens_owner()),
        );
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
