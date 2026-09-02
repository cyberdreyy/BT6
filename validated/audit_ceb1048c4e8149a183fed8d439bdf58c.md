### Title
Missing re-initialization guard in `LockupContract::new` allows owner takeover and fund theft - (File: `lockup/src/lib.rs`)

### Summary
Unlike every other contract in this codebase, `LockupContract::new` never checks whether the contract has already been initialized. Any account can call the public `new` function on an already-deployed, funded lockup contract and overwrite `owner_account_id` (and all other state) to itself, then use the owner-only methods to drain the contract's NEAR.

### Finding Description
Every other `#[init]` constructor in the repo guards against re-initialization with `assert!(!env::state_exists(), ...)`:
- `staking-pool/src/lib.rs`: `assert!(!env::state_exists(), "Already initialized");` [1](#0-0) 
- `lockup-factory/src/lib.rs`: `assert!(!env::state_exists(), "The contract is already initialized");` [2](#0-1) 
- `staking-pool-factory`, `voting`, `whitelist`, `multisig` all contain the same `state_exists` guard [3](#0-2) 

`lockup/src/lib.rs::new` contains no such check. It only validates account-ID formats and vesting-schedule consistency before unconditionally overwriting `Self { owner_account_id, lockup_information, vesting_information, staking_information: None, staking_pool_whitelist_account_id, foundation_account_id }`: [4](#0-3) 

Since `#[near_bindgen]` exposes `new` as a normal public call method (near-sdk-rs of this era does not auto-inject a re-init guard — that's exactly why every other contract adds it manually), an attacker can call `new` a second time on a live, funded lockup account, supplying their own `owner_account_id`, a favorable `transfers_information` (e.g., `TransfersEnabled` at a past timestamp), and no vesting schedule. This resets the contract's `owner_account_id` field, which is the sole authorization check (`self.assert_owner()`) gating every privileged method in `lockup/src/owner.rs`, including `transfer()` (moves NEAR to an arbitrary receiver once transfers are enabled) [5](#0-4)  and `add_full_access_key()` (grants a full-access key to the account, letting the attacker take the account outright) [6](#0-5) .

Breaking the binding: `owner_account_id` (identity entitled to move funds) must equal the account the foundation originally designated as the beneficiary. After the attacker's `new` call, `owner_account_id_after != owner_account_id_before`, while the NEAR balance held by the contract is unchanged — the attacker is now the party entitled to call `transfer`/`add_full_access_key`, i.e. "recorded claim" over the funds no longer matches the entity the contract was created for.

### Impact Explanation
This is Critical: NEAR held by an already-deployed lockup contract is moved to (or made spendable by) a party never entitled to it. The attacker fully overrides the owner identity binding and can subsequently call `transfer()` to move liquid balance out, or `add_full_access_key()` to seize the entire account (subject to the lockup's own guard conditions on those specific calls, which the attacker controls via the re-init parameters they choose, e.g. setting `vesting_schedule: None` and `transfers_information: TransfersEnabled{...}` in the past).

### Likelihood Explanation
`new` is a plain public contract method with no access-control decorator and no `state_exists` guard, callable by anyone who knows the lockup contract's account ID (which is public/discoverable, e.g. via `lockup-factory`'s deterministic derivation `sha256(owner_account_id) + "." + factory`). No privileged role, redeploy, or off-chain compromise is required — a single unprivileged function call suffices.

### Recommendation
Add the same guard used elsewhere in the codebase to `LockupContract::new`:
```rust
assert!(!env::state_exists(), "The contract has already been initialized");
```
placed at the top of the function in `lockup/src/lib.rs`, mirroring `staking-pool/src/lib.rs:179` and `lockup-factory/src/lib.rs:80`.

### Proof of Concept
1. A lockup contract `alice.lockup-factory.near` is deployed and initialized normally with `owner_account_id: "alice.near"`, holding 1000 NEAR.
2. Attacker `mallory.near` calls:
```bash
near call alice.lockup-factory.near new '{
  "owner_account_id": "mallory.near",
  "lockup_duration": "0",
  "lockup_timestamp": null,
  "transfers_information": {"TransfersEnabled": {"transfers_timestamp": "1"}},
  "vesting_schedule": null,
  "release_duration": null,
  "staking_pool_whitelist_account_id": "staking-pool-whitelist",
  "foundation_account_id": null
}' --accountId mallory.near
```
Because there is no `state_exists` check, this succeeds and overwrites `owner_account_id` to `mallory.near`, with lockup already released (`transfers_timestamp` in the past, `lockup_duration`/`lockup_timestamp` zero/none).
3. Attacker calls `transfer` as the new owner:
```bash
near call alice.lockup-factory.near transfer '{"amount": "1000000000000000000000000000", "receiver_id": "mallory.near"}' --accountId mallory.near
```
`self.assert_owner()` now passes because `owner_account_id == "mallory.near"`, and the NEAR is transferred out of the original lockup contract.

### Citations

**File:** staking-pool/src/lib.rs (L178-179)
```rust
    ) -> Self {
        assert!(!env::state_exists(), "Already initialized");
```

**File:** lockup-factory/src/lib.rs (L79-80)
```rust
    ) -> Self {
        assert!(!env::state_exists(), "The contract is already initialized");
```

**File:** staking-pool-factory/src/lib.rs (L1-1)
```rust
use near_sdk::borsh::{self, BorshDeserialize, BorshSerialize};
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

**File:** lockup/src/owner.rs (L467-487)
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

        env::log(format!("Transferring {} to account @{}", amount.0, receiver_id).as_bytes());

        Promise::new(receiver_id).transfer(amount.0)
    }
```

**File:** lockup/src/owner.rs (L502-514)
```rust
    pub fn add_full_access_key(&mut self, new_public_key: Base58PublicKey) -> Promise {
        self.assert_owner();
        self.assert_transfers_enabled();
        self.assert_no_staking_or_idle();
        self.assert_no_termination();
        assert_eq!(self.get_locked_amount().0, 0, "Tokens are still locked/unvested");

        env::log(b"Adding a full access key");

        let new_public_key: PublicKey = new_public_key.into();

        Promise::new(env::current_account_id()).add_full_access_key(new_public_key)
    }
```
