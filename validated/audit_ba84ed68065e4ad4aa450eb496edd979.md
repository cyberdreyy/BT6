### Title
Unauthenticated caller can override the trusted staking-pool whitelist for another owner's lockup - ([File: lockup-factory/src/lib.rs])

### Summary
`LockupFactory::create` in `lockup-factory/src/lib.rs` accepts a caller-supplied `whitelist_account_id: Option<ValidAccountId>` and, if present, uses it verbatim as the `staking_pool_whitelist_account_id` baked into the newly deployed lockup contract, instead of always using the factory's own vetted `self.whitelist_account_id`. Since the deployed lockup account name is fully deterministic from `owner_account_id` (`sha256(owner_account_id)[..20] + "." + factory`), and `create` is a public, unauthenticated, payable method callable by anyone for any `owner_account_id`, an attacker can race the legitimate deployment and register a lockup contract for a victim's `owner_account_id` while substituting their own malicious "whitelist" contract in place of the Foundation-controlled one.

### Finding Description
The lockup contract's entire security model for delegating locked/vesting NEAR to a staking pool depends on `staking_pool_whitelist_account_id` pointing at the NEAR Foundation's real whitelist contract: [1](#0-0) 
`select_staking_pool` calls `ext_whitelist::is_whitelisted` on whatever account was set as `staking_pool_whitelist_account_id` at lockup initialization, and if it returns `true`, the owner is allowed to deposit/stake locked funds into that pool.

In `lockup-factory/src/lib.rs`, this value is decided per-lockup at creation time: [2](#0-1) 
The `whitelist_account_id` parameter is fully attacker-controlled — there is no check that it equals `self.whitelist_account_id`, and no restriction on which `owner_account_id` a caller may target (the deposit is simply paid by whoever calls `create`, not necessarily the owner): [3](#0-2) 

Because the deployed lockup account ID is a deterministic hash of `owner_account_id` (`sha256(owner_account_id)[..20].<factory>`), the account name for any target owner is publicly predictable before it is created. An attacker can front-run the Foundation's legitimate `create` call for a given employee/owner by calling `create` first with the same `owner_account_id` but a self-controlled `whitelist_account_id`, paying only the minimum attached deposit. The resulting lockup contract at the deterministic account is deployed, permanently binding the attacker's fake whitelist contract as `staking_pool_whitelist_account_id`, and the subsequent legitimate `create` call from the Foundation will fail because `create_account()` at that same deterministic name will error (the account already exists).

This breaks the trust binding described by "an account trusted as a pool or whitelist versus the code and arguments that trust was granted for": the whitelist contract is supposed to be a single Foundation-controlled gate that guarantees any staking pool a lockup can delegate to is safe (`lockup/README.md` "Guarantees" section: the owner can not lose or lock tokens via the staking flow because the whitelist is trustworthy). Substituting an attacker-controlled contract for that gate means `is_whitelisted` will report `true` for a malicious staking pool contract chosen by the attacker, and the real owner (who has no idea the whitelist was swapped) can later be induced/tricked into delegating locked/vested NEAR to that malicious pool via `select_staking_pool` + `deposit_and_stake`, from which funds can be permanently stolen (whitelisted contract need not guarantee recoverable delegated tokens as the real whitelist would).

### Impact Explanation
This matches the Critical category: "a wrongly whitelisted or wrongly parameterised deployment" — the lockup contract for a legitimate owner is deployed with a wrongly parameterised, attacker-controlled trust anchor (`staking_pool_whitelist_account_id`) instead of the Foundation's real whitelist. Once staked/deposited into a pool that a fraudulent whitelist approved, the owner's locked NEAR can be irrecoverably lost, and the legitimate deployment for that owner is permanently blocked (denial of the correct lockup deployment for that account name).

### Likelihood Explanation
Any account can call `create()` on the lockup factory with an arbitrary `owner_account_id` and an arbitrary `whitelist_account_id`, paying only the `MIN_ATTACHED_BALANCE` (refunded on failure, only lost if the attacker's own creation succeeds, which it will since it's the attacker's own call). No special privilege or key belonging to the victim is required; the attacker only needs to predict/observe which `owner_account_id` a legitimate deployment will target (e.g., known employee accounts) and race the transaction. This is a purely economic/timing race rather than a cryptographic or governance bypass, making it a realistic, unprivileged attack vector.

### Recommendation
Remove the caller-suppliable `whitelist_account_id` override from `create`, or if flexibility is required, restrict it to be settable only by `self.foundation_account_id` (i.e., gate the parameter behind `assert_eq!(env::predecessor_account_id(), self.foundation_account_id)`), always falling back to `self.whitelist_account_id` for any unauthenticated caller. Additionally, consider preventing/disincentivizing account-name front-running (e.g., requiring the deposit/predecessor to match `owner_account_id`, or having the Foundation pre-reserve accounts).

### Proof of Concept
1. Determine a target `owner_account_id` (e.g., `alice.near`, a known future lockup recipient) — the deterministic lockup account name `sha256("alice.near")[..20].<factory>` is publicly computable.
2. Attacker deploys `evil-whitelist` contract that always returns `true` from `is_whitelisted`.
3. Attacker calls, from any account, before the Foundation does:
```
near call <factory> create '{
  "owner_account_id": "alice.near",
  "lockup_duration": "0",
  "whitelist_account_id": "evil-whitelist.attacker.near"
}' --deposit 3.5 --accountId attacker.near
``` [3](#0-2) 
4. The lockup contract at the deterministic account is created with `staking_pool_whitelist_account_id = evil-whitelist.attacker.near`, per `LockupArgs` construction: [4](#0-3) 
5. When the Foundation later transfers tokens/vesting rights to `alice.near` and expects the standard whitelist to be in place, `select_staking_pool` will validate against `evil-whitelist.attacker.near` instead: [1](#0-0) 
6. Any staking pool account the attacker registers on `evil-whitelist` will be reported as whitelisted, allowing the owner (or an attacker who compromises/social-engineers a delegation decision) to move locked NEAR into an attacker-controlled pool.

### Citations

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
