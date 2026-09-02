### No vulnerability found for this question.

Verification: the claimed exploit describes an attacker calling `LockupFactory::create` with `owner_account_id=self` and `lockup_duration=0`, attaching exactly `MIN_ATTACHED_BALANCE`. Tracing this path:

1. `LockupFactory::create` only enforces `env::attached_deposit() >= MIN_ATTACHED_BALANCE` [1](#0-0) , and always injects the hardcoded past `TRANSFERS_STARTED` timestamp into `TransfersInformation::TransfersEnabled` [2](#0-1) [3](#0-2) .
2. With `lockup_duration=0` and no `lockup_timestamp`, `get_locked_amount()` in the deployed lockup does resolve to `0` immediately because `lockup_timestamp = max(TRANSFERS_STARTED + 0, 0)` is already in the past [4](#0-3) .
3. However, `get_owners_balance()` is capped by `get_liquid_owners_balance()`, which is `min(get_owners_balance(), get_account_balance())`, and `get_account_balance()` explicitly subtracts `MIN_BALANCE_FOR_STORAGE` (`3_500_000_000_000_000_000_000_000`, the exact same constant as `MIN_ATTACHED_BALANCE`) [5](#0-4) [6](#0-5) .
4. Since the attacker's attached deposit equals exactly `MIN_ATTACHED_BALANCE == MIN_BALANCE_FOR_STORAGE`, `get_account_balance()` computes to `0` via `saturating_sub`, so `get_liquid_owners_balance()` is `0`. Any subsequent `transfer()` call requires `amount.0 > 0` and asserts `get_liquid_owners_balance().0 >= amount.0` [7](#0-6) , which cannot succeed. So no NEAR is actually withdrawable early despite `get_locked_amount()` reading `0`.
5. Furthermore, `owner_account_id=self` means the attacker is both the funder (attached the deposit themselves) and the sole party entitled to call `transfer` via `assert_owner()` [8](#0-7) . There is no victim whose funds are moved without authorization — the attacker is spending and would-be-withdrawing only their own money, and even that is blocked by the storage-floor check.

The scoped claim ("MIN_ATTACHED_BALANCE itself becomes immediately withdrawable") does not hold: the storage-floor subtraction in `get_account_balance()` neutralizes it exactly because `MIN_ATTACHED_BALANCE` was chosen equal to `MIN_BALANCE_FOR_STORAGE`. There is also no unauthorized transfer of funds belonging to another party, so this does not meet any of the required Critical/High impact categories (no theft, no early release of another party's locked tokens, no freezing of anyone else's funds).

### Citations

**File:** lockup-factory/src/lib.rs (L13-13)
```rust
const TRANSFERS_STARTED: u64 = 1602614338293769340; /* 13 October 2020 18:38:58.293 */
```

**File:** lockup-factory/src/lib.rs (L117-117)
```rust
        assert!(env::attached_deposit() >= MIN_ATTACHED_BALANCE, "Not enough attached deposit");
```

**File:** lockup-factory/src/lib.rs (L135-148)
```rust
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
```

**File:** lockup/src/getters.rs (L65-113)
```rust
    pub fn get_locked_amount(&self) -> WrappedBalance {
        let lockup_amount = self.lockup_information.lockup_amount;
        if let TransfersInformation::TransfersEnabled {
            transfers_timestamp,
        } = &self.lockup_information.transfers_information
        {
            let lockup_timestamp = std::cmp::max(
                transfers_timestamp
                    .0
                    .saturating_add(self.lockup_information.lockup_duration),
                self.lockup_information.lockup_timestamp.unwrap_or(0),
            );
            let block_timestamp = env::block_timestamp();
            if lockup_timestamp <= block_timestamp {
                let unreleased_amount =
                    if let &Some(release_duration) = &self.lockup_information.release_duration {
                        let end_timestamp = lockup_timestamp.saturating_add(release_duration);
                        if block_timestamp >= end_timestamp {
                            // Everything is released
                            0
                        } else {
                            let time_left = U256::from(end_timestamp - block_timestamp);
                            let unreleased_amount = U256::from(lockup_amount) * time_left
                                / U256::from(release_duration);
                            // The unreleased amount can't be larger than lockup_amount because the
                            // time_left is smaller than total_time.
                            unreleased_amount.as_u128()
                        }
                    } else {
                        0
                    };

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

**File:** lockup/src/getters.rs (L163-178)
```rust
    pub fn get_owners_balance(&self) -> WrappedBalance {
        (env::account_balance() + self.get_known_deposited_balance().0)
            .saturating_sub(self.get_locked_amount().0)
            .into()
    }

    /// Returns total balance of the account including tokens deposited to the staking pool.
    pub fn get_balance(&self) -> WrappedBalance {
        (env::account_balance() + self.get_known_deposited_balance().0).into()
    }

    /// Returns the amount of tokens the owner can transfer from the account.
    /// Transfers have to be enabled.
    pub fn get_liquid_owners_balance(&self) -> WrappedBalance {
        std::cmp::min(self.get_owners_balance().0, self.get_account_balance().0).into()
    }
```

**File:** lockup/src/internal.rs (L8-14)
```rust
    /// The balance of the account excluding the storage staking balance.
    /// NOTE: The storage staking balance can't be transferred out without deleting this contract.
    pub fn get_account_balance(&self) -> WrappedBalance {
        env::account_balance()
            .saturating_sub(MIN_BALANCE_FOR_STORAGE)
            .into()
    }
```

**File:** lockup/src/owner.rs (L4-13)
```rust
#[near_bindgen]
impl LockupContract {
    /// OWNER'S METHOD
    ///
    /// Requires 75 TGas (3 * BASE_GAS)
    ///
    /// Selects staking pool contract at the given account ID. The staking pool first has to be
    /// checked against the staking pool whitelist contract.
    pub fn select_staking_pool(&mut self, staking_pool_account_id: AccountId) -> Promise {
        self.assert_owner();
```

**File:** lockup/src/owner.rs (L467-482)
```rust
    pub fn transfer(&mut self, amount: WrappedBalance, receiver_id: AccountId) -> Promise {
        self.assert_owner();
        assert!(amount.0 > 0, "Amount should be positive");
        assert!(
            env::is_valid_account_id(receiver_id.as_bytes()),
            "The receiver account ID is invalid"
        );
        self.assert_transfers_enabled();
        self.assert_no_staking_or_idle();
        self.assert_no_termination();
        assert!(
            self.get_liquid_owners_balance().0 >= amount.0,
            "The available liquid balance {} is smaller than the requested transfer amount {}",
            self.get_liquid_owners_balance().0,
            amount.0,
        );
```
