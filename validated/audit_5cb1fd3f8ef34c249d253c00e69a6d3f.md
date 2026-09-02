### Title
Unprivileged Front-Running of Lockup Address Squats Victims Out of Their Intended Vesting Lockup - ([File: lockup-factory/src/lib.rs])

### Summary
`LockupFactory::create()` in `lockup-factory/src/lib.rs` derives the sub-account address of a to-be-created lockup contract deterministically from nothing but the `owner_account_id` supplied by the caller — `sha256(owner_account_id) + "." + factory_account_id` — and lets **any account** pay the minimum deposit to deploy a lockup contract at that address with arbitrary lockup/vesting parameters for that `owner_account_id`. Because the resulting NEAR account name depends only on the victim's `owner_account_id`, an unprivileged attacker can pre-emptively create (front-run) an unfavorable lockup (e.g. no vesting schedule, or a maximally long `lockup_duration`) at the exact address the foundation/employer would later use for that same employee, permanently squatting the namespace and blocking the intended, favorable vesting lockup from ever being deployed for that victim — the direct analog of the `STPV2` "force another user out of a tier" bug.

### Finding Description
`create()` computes the lockup account id purely from the caller-supplied `owner_account_id`: [1](#0-0) 

There is no check that:
- the caller is the `foundation_account_id`,
- the caller is the `owner_account_id` themselves, or
- the `owner_account_id` (i.e. the target address) has consented in any way.

Anyone can call `create` with `owner_account_id = victim`, attach the (small) `MIN_ATTACHED_BALANCE`, and choose `vesting_schedule: None` and an arbitrarily long `lockup_duration`, deploying a functioning but disadvantageous lockup contract at `sha256(victim)[..20].factory`. [2](#0-1) 

Because NEAR account IDs cannot be recreated once they exist, and the `LockupContract` exposes no owner- or foundation-callable method that deletes/self-destructs the account (only `terminate_vesting`/`termination_withdraw`, which operate on an *existing* vesting schedule and never remove the account), the address is permanently occupied: [3](#0-2) [4](#0-3) 

The binding that is broken: `deployed_lockup_terms_for(owner_account_id) == foundation_intended_terms_for(owner_account_id)`. An attacker can make this false by winning the race to the deterministic address, and there is no mechanism to restore equality afterward.

### Impact Explanation
This is a **wrongly parameterised deployment** that is unrecoverable: the victim (an intended employee/token recipient) can be permanently denied the vesting/lockup terms the foundation meant to grant them, since the foundation's later `create` call for the same `owner_account_id` will always fail (the account already exists) and simply refund the foundation's deposit via `on_lockup_create`: [5](#0-4) 

This matches the Critical severity bucket for "a wrongly whitelisted or wrongly parameterised deployment."

### Likelihood Explanation
The attack requires only knowledge of the target `owner_account_id` (which is often publicly known ahead of an announced grant/lockup) and the minimum attached deposit (`MIN_ATTACHED_BALANCE`, ~3.5 NEAR) — trivial for any unprivileged attacker to front-run before the foundation's legitimate transaction lands.

### Recommendation
Require that the deterministic lockup address cannot be squatted by unrelated third parties: e.g. restrict `create()` to be callable only by `foundation_account_id` (or by `owner_account_id` itself for self-funded lockups), or incorporate a nonce/salt controlled by the foundation into the derived account id so that an attacker cannot predict/pre-empt the address for an arbitrary victim.

### Proof of Concept
1. Foundation announces intent to grant `alice.near` a 4-year vesting lockup via `lockup-factory`.
2. Before the foundation's transaction executes, attacker calls:
   `create({"owner_account_id": "alice.near", "lockup_duration": "<huge>", "vesting_schedule": None}, attached_deposit = MIN_ATTACHED_BALANCE)`
   from `lockup-factory/src/lib.rs` `create()`.
3. This deploys a `LockupContract` at `sha256("alice.near")[..20].factory`, owned by `alice.near`, but with no vesting and an extreme lockup duration.
4. The foundation subsequently calls `create` with the intended proper vesting schedule for `alice.near`; the `create_account` promise fails because the account already exists, and `on_lockup_create` refunds the foundation's deposit — the intended vesting lockup is never created, and `alice.near` is permanently stuck with the attacker-deployed, unfavorable contract at that address.

### Citations

**File:** lockup-factory/src/lib.rs (L47-52)
```rust
#[near_bindgen]
#[derive(BorshDeserialize, BorshSerialize)]
pub struct LockupFactory {
    whitelist_account_id: AccountId,
    foundation_account_id: AccountId,
}
```

**File:** lockup-factory/src/lib.rs (L107-139)
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
```

**File:** lockup-factory/src/lib.rs (L168-198)
```rust
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

**File:** lockup/src/foundation.rs (L15-47)
```rust
    pub fn terminate_vesting(
        &mut self,
        vesting_schedule_with_salt: Option<VestingScheduleWithSalt>,
    ) {
        self.assert_called_by_foundation();
        let vesting_schedule = self.assert_vesting(vesting_schedule_with_salt);
        let unvested_amount = self.get_unvested_amount(vesting_schedule);
        assert!(unvested_amount.0 > 0, "The account is fully vested");

        env::log(
            format!(
                "Terminating vesting. The remaining unvested balance is {}",
                unvested_amount.0
            )
            .as_bytes(),
        );

        let deficit = unvested_amount
            .0
            .saturating_sub(self.get_account_balance().0);
        // If there is deficit of liquid balance and also there is a staking pool selected, then the
        // contract will try to withdraw everything from this staking pool to cover deficit.
        let status = if deficit > 0 && self.staking_information.is_some() {
            TerminationStatus::VestingTerminatedWithDeficit
        } else {
            TerminationStatus::ReadyToWithdraw
        };

        self.vesting_information = VestingInformation::Terminating(TerminationInformation {
            unvested_amount,
            status,
        });
    }
```

**File:** lockup/src/foundation.rs (L129-174)
```rust
    /// FOUNDATION'S METHOD
    ///
    /// Requires 75 TGas (3 * BASE_GAS)
    ///
    /// Withdraws the unvested amount from the early termination of the vesting schedule.
    pub fn termination_withdraw(&mut self, receiver_id: AccountId) -> Promise {
        self.assert_called_by_foundation();
        assert!(
            env::is_valid_account_id(receiver_id.as_bytes()),
            "The receiver account ID is invalid"
        );
        assert_eq!(
            self.get_termination_status(),
            Some(TerminationStatus::ReadyToWithdraw),
            "Termination status is not ready to withdraw"
        );

        let amount = std::cmp::min(
            self.get_terminated_unvested_balance().0,
            self.get_account_balance().0,
        );
        assert!(
            amount > 0,
            "The account doesn't have enough liquid balance to withdraw any amount"
        );

        env::log(
            format!(
                "Termination Step: Withdrawing {} of terminated unvested balance to account @{}",
                amount, receiver_id
            )
            .as_bytes(),
        );

        self.set_termination_status(TerminationStatus::WithdrawingFromAccountInProgress);

        Promise::new(receiver_id.clone()).transfer(amount).then(
            ext_self_foundation::on_withdraw_unvested_amount(
                amount.into(),
                receiver_id,
                &env::current_account_id(),
                NO_DEPOSIT,
                gas::foundation_callbacks::ON_WITHDRAW_UNVESTED_AMOUNT,
            ),
        )
    }
```
