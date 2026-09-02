This confirms the analysis. The `#[init]` `LockupContract::new` at [1](#0-0)  always sets `staking_information: None` unconditionally, and `StakingInformation`/`TransactionStatus` (defined at [2](#0-1)  and [3](#0-2) ) only come into existence after `select_staking_pool` is called post-initialization. Since `new` is a `#[init]` method that runs exactly once to create contract state from nothing, there is no way for a `TransactionStatus::Busy` in-flight promise to exist concurrently with the execution of `new` — no staking pool can exist to have a pending transaction before the contract itself exists.

Additionally, the public factory (`lockup-factory/src/lib.rs::create`) does not let a caller choose `transfers_information` at all — it hardcodes `TransfersInformation::TransfersEnabled { transfers_timestamp: TRANSFERS_STARTED.into() }` [4](#0-3) , so via the sanctioned factory path an attacker cannot inject an arbitrary past timestamp. Even if an attacker bypasses the factory and deploys

### Citations

**File:** lockup/src/lib.rs (L235-243)
```rust
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

**File:** lockup/src/types.rs (L75-80)
```rust
pub enum TransactionStatus {
    /// There are no transactions in progress.
    Idle,
    /// There is a transaction in progress.
    Busy,
}
```

**File:** lockup/src/types.rs (L82-94)
```rust
/// Contains information about current stake and delegation.
#[derive(BorshDeserialize, BorshSerialize)]
pub struct StakingInformation {
    /// The Account ID of the staking pool contract.
    pub staking_pool_account_id: AccountId,

    /// Contains status whether there is a transaction in progress.
    pub status: TransactionStatus,

    /// The amount of tokens that were deposited from this account to the staking pool.
    /// NOTE: The unstaked amount on the staking pool might be higher due to staking rewards.
    pub deposit_amount: WrappedBalance,
}
```

**File:** lockup-factory/src/lib.rs (L135-153)
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
                    vesting_schedule,
                    release_duration,
                    staking_pool_whitelist_account_id,
                    foundation_account_id: foundation_account,
                })
```
